/**
 * 定位采集（Web 实现）—— 页面只调这个模块，不直接碰 navigator.geolocation
 *
 * 设计目标（对应 docs/运动模块-地图与定位技术方案.md）：
 * 1. 预热：拿到 3~5 个精度够好的点再倒计时，避免前 10 秒全是垃圾数据
 * 2. 自适应滤波：精度差 / 抖动 / 瞬移三类点丢弃，再 EMA 平滑
 * 3. 暂停不污染：暂停期间不累计，恢复时把第一个点当新基准，不跨段连线
 * 4. 后台不跳表：页面隐藏自动暂停，回来提示“后台期间未记录”
 * 5. 草稿：每 5 秒写 localStorage，保存失败/误关页可恢复
 * 6. 演示模式：没有 GPS / 没有 HTTPS 时也能完整走一遍流程（答辩兜底）
 * 7. 幂等：每次开始生成 clientId，后端按 (user_id, client_id) 去重
 *
 * 数据口径（与后端 workout_calc 对齐）：
 * - t 是“有效运动秒数”，暂停期间不递增 → 后端按分段算配速时不会被暂停时间污染
 * - 距离、配速只用于实时展示；保存时后端会用同样的算法重算一遍
 */
import { computed, ref } from 'vue'
import { haversineM } from '@/utils/workout'

export interface TrackPoint {
  lat: number
  lng: number
  t: number
  acc?: number | null
}

export type TrackerState = 'idle' | 'locating' | 'countdown' | 'running' | 'paused' | 'stopped'
export type PauseReason = 'manual' | 'auto' | 'background' | null

export interface RunDraft {
  v: 1
  sport: string
  startedAt: string
  clientId: string
  points: TrackPoint[]
  distanceM: number
  elapsedSec: number
  updatedAt: number
}

const DRAFT_KEY = 'foodai.run.draft.v1'

/** 精度差于 50m 的点直接丢 */
const MAX_ACCURACY_M = 50
/** 跑步不可能超过 12m/s（2'20"/km） */
const MAX_SPEED_MPS = 12
/** 站立超过 20 秒自动暂停 */
const AUTO_PAUSE_MS = 20_000
/** 自动恢复的速度阈值 */
const AUTO_RESUME_MPS = 1.0

function uuid(): string {
  const c = (globalThis as any).crypto
  if (c?.randomUUID) return c.randomUUID()
  return `w_${Date.now()}_${Math.random().toString(36).slice(2, 10)}`
}

function geoErrorText(e: GeolocationPositionError | any): string {
  switch (e?.code) {
    case 1:
      return '定位权限被拒绝：请在浏览器地址栏左侧的权限图标里允许定位'
    case 2:
      return '暂时拿不到位置：到室外或窗边再试一次'
    case 3:
      return '定位超时：信号弱，请重试'
    default:
      return '定位失败，请检查系统定位是否开启'
  }
}

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms))

export function useRunTracker() {
  const state = ref<TrackerState>('idle')
  const points = ref<TrackPoint[]>([])
  const distanceM = ref(0)
  const elapsedSec = ref(0)
  const accuracy = ref<number | null>(null)
  const error = ref<string | null>(null)
  const speedMps = ref(0)
  const countdown = ref(0)
  const pauseReason = ref<PauseReason>(null)
  const autoPaused = ref(false)
  const gapNotice = ref('')
  const isDemo = ref(false)
  const autoPauseEnabled = ref(true)
  const draft = ref<RunDraft | null>(null)
  const clientId = ref('')
  const startedAt = ref<Date | null>(null)

  let watchId: number | null = null
  let timer: number | null = null
  let countdownTimer: number | null = null
  let demoTimer: number | null = null
  let demoTick = 0
  let wakeLock: any = null
  let lastTick = 0
  let anchor: TrackPoint | null = null
  let lastSmooth: TrackPoint | null = null
  let skipNext = false
  let lastAcceptAt = 0
  let lastDraftAt = 0

  const isSupported = typeof navigator !== 'undefined' && !!navigator.geolocation
  // 非安全上下文（http + 局域网 IP）浏览器会直接拒绝定位，提前提示比"一直转圈"好
  const isSecure = typeof window === 'undefined' ? true : window.isSecureContext

  const avgPaceSecKm = computed(() => {
    if (distanceM.value < 50 || elapsedSec.value <= 0) return null
    return elapsedSec.value / (distanceM.value / 1000)
  })

  /** 当前配速：取最近 30 秒窗口，刚起步不会显示离谱的数 */
  const currentPaceSecKm = computed(() => {
    const arr = points.value
    if (arr.length < 2) return null
    const tEnd = arr[arr.length - 1].t
    let i = arr.length - 1
    let dist = 0
    while (i > 0 && tEnd - arr[i - 1].t <= 30) {
      dist += haversineM(arr[i - 1], arr[i])
      i--
    }
    const dt = tEnd - arr[i].t
    if (!dist || dt <= 0 || dist < 15) return null
    return dt / (dist / 1000)
  })

  const accuracyLabel = computed(() => {
    if (accuracy.value == null) return '定位中'
    const a = Math.round(accuracy.value)
    if (a <= 10) return `信号强 ±${a}m`
    if (a <= 25) return `信号一般 ±${a}m`
    return `信号弱 ±${a}m`
  })

  /* ---------------- 计时器 ---------------- */

  function startTimer() {
    stopTimer()
    lastTick = performance.now()
    timer = window.setInterval(() => {
      const now = performance.now()
      let dt = (now - lastTick) / 1000
      lastTick = now
      if (state.value !== 'running') return
      // 后台节流/卡顿：单次最多补 1 秒，避免时长突然跳几十秒
      if (dt > 1) dt = 1
      elapsedSec.value += dt
      // 站立/丢星自动暂停：20 秒没有新的有效点
      if (autoPauseEnabled.value && points.value.length > 2
        && Date.now() - lastAcceptAt > AUTO_PAUSE_MS && state.value === 'running') {
        pause('auto')
      }
      // 每 5 秒落一次草稿：浏览器崩溃/被杀也能恢复
      if (Date.now() - lastDraftAt > 5000) saveDraft()
    }, 200)
  }

  function stopTimer() {
    if (timer !== null) {
      clearInterval(timer)
      timer = null
    }
  }

  function stopCountdown() {
    if (countdownTimer !== null) {
      clearInterval(countdownTimer)
      countdownTimer = null
    }
  }

  function stopDemo() {
    if (demoTimer !== null) {
      clearInterval(demoTimer)
      demoTimer = null
    }
  }

  /* ---------------- 屏幕常亮 ---------------- */

  async function requestWakeLock() {
    if (isDemo.value) return
    try {
      if (!wakeLock && (navigator as any).wakeLock?.request) {
        wakeLock = await (navigator as any).wakeLock.request('screen')
        wakeLock?.addEventListener?.('release', () => { wakeLock = null })
      }
    } catch {
      /* 不支持或不允许，忽略 */
    }
  }

  function releaseWakeLock() {
    try {
      wakeLock?.release?.()
    } catch {
      /* 忽略 */
    }
    wakeLock = null
  }

  /* ---------------- 滤波与采集 ---------------- */

  function smooth(p: TrackPoint): TrackPoint {
    if (!lastSmooth) {
      lastSmooth = { ...p }
      return lastSmooth
    }
    // 精度差时更信旧点，精度好时更信新点
    const alpha = p.acc && p.acc > 15 ? 0.2 : 0.35
    const s: TrackPoint = {
      lat: lastSmooth.lat * (1 - alpha) + p.lat * alpha,
      lng: lastSmooth.lng * (1 - alpha) + p.lng * alpha,
      t: p.t,
      acc: p.acc,
    }
    lastSmooth = s
    return s
  }

  function pushPoint(p: TrackPoint) {
    points.value.push(p)
  }

  /**
   * 所有来源（真实 GPS / 演示轨迹）都走这一条：
   * 返回 true 表示这个点被接受（进入了轨迹）
   */
  function acceptPoint(p: TrackPoint): boolean {
    const acc = p.acc ?? 99
    if (acc > MAX_ACCURACY_M) return false

    if (!anchor) {
      anchor = p
      pushPoint(smooth(p))
      lastAcceptAt = Date.now()
      return true
    }

    const d = haversineM(anchor, p)
    const dt = p.t - anchor.t
    if (dt <= 0) return false

    const v = d / dt
    // 2 秒跳 100m 必是漂移
    if (d > 100 && dt < 2) return false
    if (v > MAX_SPEED_MPS) return false

    // 自适应位移阈值：3m 对 10m 精度太低，会让站立抖动累计
    const minMove = Math.max(2.5, Math.min(8, acc * 0.4))
    if (d < minMove) return false

    speedMps.value = speedMps.value ? speedMps.value * 0.6 + v * 0.4 : v

    // 自动暂停中：检测到重新走动就恢复
    if (state.value === 'paused' && autoPaused.value) {
      if (v >= AUTO_RESUME_MPS) {
        resume()
      }
      return false
    }
    if (state.value !== 'running') return false

    // 恢复后的第一个点只当基准，不把暂停期间的路程算进来
    if (skipNext) {
      anchor = p
      skipNext = false
      lastAcceptAt = Date.now()
      return false
    }

    const prevSmooth = lastSmooth
    const s = smooth(p)
    // 注意：smooth() 会把 lastSmooth 更新成 s，所以要先存旧值再算距离
    if (prevSmooth) distanceM.value += haversineM(prevSmooth, s)
    pushPoint(s)
    anchor = p
    lastAcceptAt = Date.now()
    return true
  }

  function onPosition(pos: GeolocationPosition) {
    const acc = pos.coords.accuracy
    accuracy.value = acc
    const p: TrackPoint = {
      lat: pos.coords.latitude,
      lng: pos.coords.longitude,
      t: elapsedSec.value,
      acc,
    }
    if (state.value === 'running' || (state.value === 'paused' && autoPaused.value)) {
      acceptPoint(p)
    }
  }

  function onGeoError(e: GeolocationPositionError) {
    error.value = geoErrorText(e)
    if (e.code === 1 && (state.value === 'locating' || state.value === 'countdown')) {
      state.value = 'idle'
    }
  }

  function startWatch() {
    if (watchId !== null || isDemo.value || !isSupported) return
    watchId = navigator.geolocation.watchPosition(onPosition, onGeoError, {
      enableHighAccuracy: true,
      maximumAge: 0,
      timeout: 15000,
    })
  }

  function stopWatch() {
    if (watchId !== null) {
      navigator.geolocation.clearWatch(watchId)
      watchId = null
    }
  }

  /* ---------------- 开始 / 倒计时 ---------------- */

  function reset() {
    stopTimer()
    stopCountdown()
    stopWatch()
    stopDemo()
    releaseWakeLock()
    state.value = 'idle'
    points.value = []
    distanceM.value = 0
    elapsedSec.value = 0
    accuracy.value = null
    error.value = null
    speedMps.value = 0
    countdown.value = 0
    pauseReason.value = null
    autoPaused.value = false
    gapNotice.value = ''
    isDemo.value = false
    draft.value = null
    clientId.value = ''
    startedAt.value = null
    anchor = null
    lastSmooth = null
    skipNext = false
    lastAcceptAt = 0
    demoTick = 0
  }

  function firstFix(): Promise<GeolocationPosition> {
    return new Promise((resolve, reject) => {
      navigator.geolocation.getCurrentPosition(resolve, reject, {
        enableHighAccuracy: true,
        timeout: 12000,
        maximumAge: 0,
      })
    })
  }

  /** 等精度够好（或超时）再开始，避免用第一个粗点 */
  async function waitWarmup(timeoutMs = 6000, target = 22) {
    const t0 = Date.now()
    while (Date.now() - t0 < timeoutMs) {
      if (accuracy.value != null && accuracy.value <= target) return
      await sleep(200)
    }
  }

  function beginCountdown() {
    stopCountdown()
    state.value = 'countdown'
    countdown.value = 3
    countdownTimer = window.setInterval(() => {
      countdown.value -= 1
      if (countdown.value <= 0) {
        stopCountdown()
        startRunning()
      }
    }, 800)
  }

  function startRunning() {
    state.value = 'running'
    startedAt.value = new Date()
    lastTick = performance.now()
    lastAcceptAt = Date.now()
    anchor = null
    lastSmooth = null
    skipNext = false
    startTimer()
    requestWakeLock()
  }

  async function prepare(): Promise<boolean> {
    reset()
    error.value = null

    if (!isSupported) {
      error.value = '当前浏览器不支持定位功能'
      return false
    }
    if (!isSecure) {
      error.value = '浏览器规定：只有 HTTPS 或 localhost 才能定位（当前是 http 局域网地址）'
      return false
    }

    state.value = 'locating'
    clientId.value = uuid()

    try {
      const first = await firstFix()
      accuracy.value = first.coords.accuracy
      startWatch()
      await waitWarmup()
      if (state.value !== 'locating') return false
      beginCountdown()
      return true
    } catch (e: any) {
      state.value = 'idle'
      error.value = geoErrorText(e)
      return false
    }
  }

  /* ---------------- 演示轨迹 ---------------- */

  function demoStep() {
    // 人民广场附近画一个约 1.5km 的环，带轻微抖动，模拟真实 GPS
    const a = demoTick * 0.02
    demoTick += 1
    const lat = 31.230416 + 0.0016 * Math.sin(a) + (Math.random() - 0.5) * 0.00002
    const lng = 121.473701 + 0.0022 * Math.cos(a) + (Math.random() - 0.5) * 0.00002
    acceptPoint({
      lat,
      lng,
      t: elapsedSec.value,
      acc: 5 + Math.random() * 6,
    })
  }

  function startDemo() {
    reset()
    isDemo.value = true
    clientId.value = uuid()
    state.value = 'countdown'
    countdown.value = 3
    countdownTimer = window.setInterval(() => {
      countdown.value -= 1
      if (countdown.value <= 0) {
        stopCountdown()
        state.value = 'running'
        startedAt.value = new Date()
        lastTick = performance.now()
        lastAcceptAt = Date.now()
        anchor = null
        lastSmooth = null
        skipNext = false
        startTimer()
        demoTimer = window.setInterval(demoStep, 1000)
        demoTick = 0
        demoStep()
      }
    }, 800)
  }

  /* ---------------- 暂停 / 恢复 / 结束 ---------------- */

  function pause(reason: Exclude<PauseReason, null> = 'manual') {
    if (state.value !== 'running') return
    state.value = 'paused'
    pauseReason.value = reason
    autoPaused.value = reason === 'auto'
    stopTimer()
    releaseWakeLock()
    if (reason === 'background') gapNotice.value = '切到后台，已自动暂停（后台期间不记录轨迹）'
    if (reason === 'auto') gapNotice.value = '检测到停止移动，已自动暂停'
    saveDraft()
  }

  function resume() {
    if (state.value !== 'paused') return
    state.value = 'running'
    pauseReason.value = null
    autoPaused.value = false
    gapNotice.value = ''
    skipNext = true
    lastTick = performance.now()
    lastAcceptAt = Date.now()
    startTimer()
    startWatch()
    requestWakeLock()
  }

  function stop() {
    state.value = 'stopped'
    stopTimer()
    stopWatch()
    stopDemo()
    releaseWakeLock()
    return {
      points: points.value.slice(),
      distanceM: Math.round(distanceM.value),
      durationSec: Math.round(elapsedSec.value),
    }
  }

  /** 结束确认“继续记录”时用：不清空轨迹，回到 running */
  function reopen() {
    if (state.value !== 'stopped') return
    state.value = 'running'
    skipNext = true
    pauseReason.value = null
    autoPaused.value = false
    lastTick = performance.now()
    lastAcceptAt = Date.now()
    startTimer()
    startWatch()
    requestWakeLock()
  }

  /* ---------------- 草稿 ---------------- */

  function saveDraft() {
    if (typeof localStorage === 'undefined') return
    if (!points.value.length && state.value !== 'running' && state.value !== 'paused') return
    const now = Date.now()
    if (now - lastDraftAt < 2500) return
    lastDraftAt = now
    try {
      const d: RunDraft = {
        v: 1,
        sport: '',
        startedAt: (startedAt.value || new Date()).toISOString(),
        clientId: clientId.value,
        points: points.value,
        distanceM: distanceM.value,
        elapsedSec: elapsedSec.value,
        updatedAt: now,
      }
      localStorage.setItem(DRAFT_KEY, JSON.stringify(d))
    } catch {
      /* 容量满/隐私模式，忽略 */
    }
  }

  function loadDraft(): RunDraft | null {
    if (typeof localStorage === 'undefined') return null
    try {
      const raw = localStorage.getItem(DRAFT_KEY)
      if (!raw) return null
      const d = JSON.parse(raw) as RunDraft
      if (d?.v !== 1 || !Array.isArray(d.points) || !d.points.length) return null
      // 超过 24 小时的草稿不恢复
      if (Date.now() - (d.updatedAt || 0) > 24 * 3600 * 1000) {
        localStorage.removeItem(DRAFT_KEY)
        return null
      }
      return d
    } catch {
      return null
    }
  }

  function restoreDraft(d: RunDraft) {
    reset()
    clientId.value = d.clientId || uuid()
    points.value = d.points
    distanceM.value = d.distanceM || 0
    elapsedSec.value = d.elapsedSec || 0
    startedAt.value = d.startedAt ? new Date(d.startedAt) : new Date()
    state.value = 'paused'
    pauseReason.value = 'manual'
    gapNotice.value = '已恢复上次未完成的记录，点击继续'
    const last = points.value[points.value.length - 1]
    anchor = last || null
    lastSmooth = last || null
    skipNext = true
    draft.value = null
    // 先不申请定位；用户点「继续」时再 startWatch，避免一恢复就弹权限/报错
  }

  function clearDraft() {
    if (typeof localStorage === 'undefined') return
    try { localStorage.removeItem(DRAFT_KEY) } catch { /* ignore */ }
    draft.value = null
  }

  /* ---------------- 页面可见性 ---------------- */

  function handleVisibility() {
    if (typeof document === 'undefined') return
    if (document.hidden) {
      if (state.value === 'running') pause('background')
      saveDraft()
    } else if (state.value === 'paused' && pauseReason.value === 'background') {
      // 保持暂停，等用户确认；不偷偷继续跑
      gapNotice.value = '后台期间未记录轨迹，点击「继续」恢复'
    }
  }

  function attachLifecycle() {
    if (typeof document === 'undefined') return
    document.addEventListener('visibilitychange', handleVisibility)
    window.addEventListener('pagehide', saveDraft)
  }

  function detachLifecycle() {
    if (typeof document === 'undefined') return
    document.removeEventListener('visibilitychange', handleVisibility)
    window.removeEventListener('pagehide', saveDraft)
  }

  return {
    state, points, distanceM, elapsedSec, accuracy, error, speedMps,
    countdown, pauseReason, autoPaused, gapNotice, isDemo, autoPauseEnabled,
    draft, clientId, startedAt,
    avgPaceSecKm, currentPaceSecKm, accuracyLabel, isSupported, isSecure,
    prepare, startDemo, pause, resume, stop, reopen, reset,
    saveDraft, loadDraft, restoreDraft, clearDraft,
    attachLifecycle, detachLifecycle,
  }
}
