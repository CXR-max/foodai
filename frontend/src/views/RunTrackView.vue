<script setup lang="ts">
// 跑步中（全屏）：预热定位 → 3-2-1 → 实时轨迹/配速 → 长按结束 → 保存
// 只做手机端；定位实现走 @/platform/location，将来打 App 包只换那一层
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { onBeforeRouteLeave, useRoute, useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import * as workoutApi from '@/api/workout'
import { useRunTracker } from '@/platform/location'
import type { RunDraft, TrackPoint } from '@/platform/location'
import TrackMap from '@/components/TrackMap.vue'
import { fmtDistance, fmtDuration, fmtPace, fmtSpeed, haversineM, isRide, paceToSpeed } from '@/utils/workout'
import { refreshTodayCheckin } from '@/stores/todayCheckin'

const route = useRoute()
const router = useRouter()
const sport = (route.query.sport as string) || '跑步'
const revengeName = String(route.query.revenge_name || '')
const demoMode = route.query.demo === '1'

const tracker = useRunTracker()
const {
  state, points, distanceM, elapsedSec, accuracy, error, avgPaceSecKm, currentPaceSecKm,
  accuracyLabel, countdown, pauseReason, gapNotice, isDemo,
  autoPauseEnabled, clientId, startedAt, isSecure,
} = tracker

const saving = ref(false)
const saveFailed = ref(false)
const lastResult = ref<{ points: TrackPoint[]; distanceM: number; durationSec: number } | null>(null)
const pressing = ref(false)
const pressProgress = ref(0)
const voiceOn = ref(true)
const showDraftDialog = ref(false)
const draftToRestore = ref<RunDraft | null>(null)
let allowLeave = false
let lastKm = 0
let pressTimer: number | null = null
let progressTimer: number | null = null

const running = computed(() => state.value === 'running')
const paused = computed(() => state.value === 'paused')
const active = computed(() => running.value || paused.value)
const locating = computed(() => state.value === 'locating')
const counting = computed(() => state.value === 'countdown')
const stopped = computed(() => state.value === 'stopped')

function loopAreaM2(pts: TrackPoint[]): number {
  if (pts.length < 3) return 0
  const R = 6371000
  const lat0 = (pts[0].lat * Math.PI) / 180
  const cos0 = Math.cos(lat0)
  const xy = pts.map((p) => [
    (R * (p.lng * Math.PI)) / 180 * cos0,
    (R * (p.lat * Math.PI)) / 180,
  ])
  let sum = 0
  for (let i = 0; i < xy.length; i++) {
    const [x1, y1] = xy[i]
    const [x2, y2] = xy[(i + 1) % xy.length]
    sum += x1 * y2 - x2 * y1
  }
  return Math.abs(sum) / 2
}

const loopPreview = computed(() => {
  const arr = points.value
  if (arr.length < 10 || distanceM.value < 100) return null
  const first = arr[0]
  const last = arr[arr.length - 1]
  const gap = haversineM(first, last)
  if (gap > 50) return { closed: false, gap, area: 0 }
  return { closed: true, gap, area: loopAreaM2(arr) }
})

function fmtPreviewArea(m2: number): string {
  if (m2 >= 10000) return `${(m2 / 10000).toFixed(2)} 万㎡`
  return `${Math.round(m2)} ㎡`
}

const dist = computed(() => fmtDistance(distanceM.value))
const curPace = computed(() => currentPaceSecKm.value || avgPaceSecKm.value)
const ride = computed(() => isRide(sport))
const curText = computed(() => ride.value
  ? fmtSpeed(paceToSpeed(curPace.value))
  : fmtPace(curPace.value))
const avgText = computed(() => ride.value
  ? fmtSpeed(paceToSpeed(avgPaceSecKm.value))
  : fmtPace(avgPaceSecKm.value))
const pauseText = computed(() => {
  if (pauseReason.value === 'auto') return '自动暂停 · 走两步自动继续'
  if (pauseReason.value === 'background') return '后台期间已暂停'
  return '已暂停'
})

const headSub = computed(() => {
  if (isDemo.value) return '演示轨迹'
  if (paused.value) return '已暂停'
  return accuracyLabel.value
})

function speak(text: string) {
  if (!voiceOn.value || typeof speechSynthesis === 'undefined') return
  try {
    speechSynthesis.cancel()
    const u = new SpeechSynthesisUtterance(text)
    u.lang = 'zh-CN'
    u.rate = 1
    speechSynthesis.speak(u)
  } catch {
    /* 忽略 */
  }
}

async function start() {
  if (locating.value || counting.value) return
  saveFailed.value = false
  lastKm = 0
  const ok = await tracker.prepare()
  if (!ok && error.value) ElMessage.warning(error.value)
}

function startDemo() {
  saveFailed.value = false
  lastKm = 0
  tracker.startDemo()
}

function togglePause() {
  if (running.value) tracker.pause('manual')
  else if (paused.value) tracker.resume()
}

// ---- 长按结束（800ms，带进度条，防误触）----
function clearPress() {
  if (pressTimer !== null) { clearTimeout(pressTimer); pressTimer = null }
  if (progressTimer !== null) { clearInterval(progressTimer); progressTimer = null }
  pressing.value = false
  pressProgress.value = 0
}

function pressStart() {
  if (!active.value || saving.value || pressTimer !== null) return
  pressing.value = true
  pressProgress.value = 0
  const t0 = performance.now()
  progressTimer = window.setInterval(() => {
    pressProgress.value = Math.min(100, ((performance.now() - t0) / 800) * 100)
  }, 30)
  pressTimer = window.setTimeout(() => {
    clearPress()
    finish()
  }, 800)
}

function pressCancel() {
  if (!pressing.value) return
  clearPress()
}

async function finish() {
  if (!active.value || saving.value) return
  const r = tracker.stop()
  if (r.points.length < 2) {
    ElMessage.warning('没有采集到有效轨迹，再走两步试试')
    tracker.reopen()
    return
  }
  if (r.durationSec < 60 || r.distanceM < 50) {
    try {
      await ElMessageBox.confirm(
        `本次只记录到 ${fmtDistance(r.distanceM).value}${fmtDistance(r.distanceM).unit} / ${fmtDuration(r.durationSec)}，仍然保存吗？`,
        '运动太短',
        { confirmButtonText: '保存', cancelButtonText: '继续记录', type: 'warning' },
      )
    } catch {
      tracker.reopen()
      return
    }
  }
  await save(r)
}

async function save(r: { points: TrackPoint[]; distanceM: number; durationSec: number }) {
  if (saving.value) return
  saving.value = true
  saveFailed.value = false
  lastResult.value = r
  try {
    const res = await workoutApi.createWorkout({
      sport_type: sport,
      start_at: (startedAt.value || new Date()).toISOString(),
      end_at: new Date().toISOString(),
      duration_sec: r.durationSec,
      track: r.points,
      distance_m: r.distanceM,
      source: 'gps',
      client_id: clientId.value || undefined,
    })
    tracker.clearDraft()
    refreshTodayCheckin()
    if (res.points_awarded > 0) ElMessage.success(res.message)
    else ElMessage.info(res.message || '已保存')
    allowLeave = true
    // 领地结算：只有真的记到地了才进整屏结算，否则直接回运动报告
    if (res.territory?.valid) router.replace(`/territory/settle/${res.id}`)
    else router.replace(`/workout/${res.id}`)
  } catch {
    saveFailed.value = true
    tracker.saveDraft()
    ElMessage.error('保存失败，草稿已保留，可点「重新保存」')
  } finally {
    saving.value = false
  }
}

function retrySave() {
  if (lastResult.value) save(lastResult.value)
}

async function back() {
  if (saving.value) return
  if (active.value || counting.value || locating.value) {
    try {
      await ElMessageBox.confirm(
        '正在记录中，离开会保留草稿，下次进入可恢复。确定离开？',
        '提示',
        { confirmButtonText: '离开', cancelButtonText: '继续记录', type: 'warning' },
      )
    } catch {
      return
    }
    tracker.saveDraft()
    tracker.stop()
  }
  allowLeave = true
  router.back()
}

function doRestore() {
  if (!draftToRestore.value) return
  tracker.restoreDraft(draftToRestore.value)
  showDraftDialog.value = false
}

function discardDraft() {
  tracker.clearDraft()
  showDraftDialog.value = false
  if (demoMode) startDemo()
  else start()
}

function onBeforeUnload(e: BeforeUnloadEvent) {
  if (active.value || counting.value || locating.value) {
    e.preventDefault()
    e.returnValue = ''
  }
}

// 每公里语音播报
watch(distanceM, (m) => {
  if (!running.value) return
  const km = Math.floor(m / 1000)
  if (km > lastKm && km > 0) {
    lastKm = km
    speak(`已完成 ${km} 公里，用时 ${fmtDuration(elapsedSec.value)}`)
  }
})

watch(state, (s, prev) => {
  if (s === 'running' && prev === 'countdown') {
    lastKm = 0
    speak('开始记录')
  }
})

watch(error, (e) => {
  if (e && active.value) ElMessage.warning(e)
})

onMounted(() => {
  tracker.attachLifecycle()
  window.addEventListener('beforeunload', onBeforeUnload)

  const d = tracker.loadDraft()
  if (d) {
    draftToRestore.value = d
    showDraftDialog.value = true
    return
  }
  if (demoMode) {
    startDemo()
    return
  }
  start()
})

onBeforeRouteLeave(async () => {
  if (allowLeave) return true
  if (!(active.value || counting.value || locating.value)) return true
  if (saving.value) return false
  try {
    await ElMessageBox.confirm(
      '正在记录中，离开会保留草稿，下次进入可恢复。确定离开？',
      '提示',
      { confirmButtonText: '离开', cancelButtonText: '继续记录', type: 'warning' },
    )
  } catch {
    return false
  }
  tracker.saveDraft()
  tracker.stop()
  allowLeave = true
  return true
})

onBeforeUnmount(() => {
  tracker.detachLifecycle()
  window.removeEventListener('beforeunload', onBeforeUnload)
  if (!allowLeave) tracker.saveDraft()
  tracker.stop()
})
</script>

<template>
  <div class="run">
    <header class="run-head">
      <button class="head-back" @click="back">‹</button>
      <div class="head-center">
        <span class="head-sport">
          {{ sport }}
          <em v-if="isDemo">演示</em>
        </span>
        <span class="head-acc" :class="{ weak: !isDemo && !paused && (accuracy || 0) > 25 }">
          {{ headSub }}
        </span>
      </div>
      <div class="head-right">{{ points.length }} 点</div>
    </header>

    <div v-if="revengeName" class="revenge-tip">
      ⚔️ 跑一个闭环，把 <b>{{ revengeName }}</b> 的地切回来
    </div>

    <div v-if="active && loopPreview" class="loop-tip" :class="{ ready: loopPreview.closed }">
      <template v-if="loopPreview.closed">
        ✅ 闭环已形成 · 预计圈下 {{ fmtPreviewArea(loopPreview.area) }}
      </template>
      <template v-else>
        还差 {{ Math.ceil(loopPreview.gap) }}m 回到起点附近就能圈地
      </template>
    </div>

    <!-- 不安全来源：提前说清楚原因和办法，避免"一直转圈以为坏了" -->
    <div v-if="!isSecure && !isDemo" class="blocker">
      <div class="blocker-icon">🔒</div>
      <h3>当前环境不允许定位</h3>
      <p>浏览器规定：只有 <b>HTTPS</b> 或 <b>localhost</b> 才能获取位置。你现在用的是 <code>http://局域网IP</code>，定位会被直接拒绝。</p>
      <p class="blocker-tip">
        解决办法（任选其一）：<br>
        1. 用内网穿透工具把首页映射成 https 域名，手机访问该域名；<br>
        2. 把前端部署到任意支持 HTTPS 的地址；<br>
        3. 用电脑浏览器打开 localhost 演示（看得到定位与轨迹逻辑）。
      </p>
      <div class="blocker-actions">
        <el-button size="large" @click="back">返回</el-button>
        <el-button type="success" size="large" @click="startDemo">先看演示轨迹</el-button>
      </div>
    </div>

    <template v-else>
      <!-- 地图 / 轨迹 -->
      <div class="map-wrap">
        <TrackMap
          :points="points"
          :live="running || paused"
          :sport="sport"
          height="100%"
        />

        <div v-if="locating" class="map-mask">
          <div class="pulse" />
          <span>正在定位…</span>
          <small>{{ accuracyLabel }} · 等信号稳定后会自动倒计时</small>
        </div>

        <div v-else-if="counting" class="countdown">
          <b>{{ countdown }}</b>
          <span>准备开始</span>
          <small>把手机放进口袋，别盯着屏幕</small>
        </div>

        <div v-else-if="error && !points.length" class="map-mask">
          <span class="err-text">{{ error }}</span>
          <div class="mask-actions">
            <el-button size="small" type="success" @click="start">重试</el-button>
            <el-button size="small" @click="startDemo">用演示轨迹</el-button>
          </div>
        </div>

        <div v-if="paused && points.length" class="pause-badge">
          <b>{{ pauseText }}</b>
          <span>点击下方「继续」恢复</span>
        </div>

        <div v-if="saveFailed" class="map-mask">
          <span class="err-text">保存失败了，但草稿还在</span>
          <el-button size="small" type="success" :loading="saving" @click="retrySave">重新保存</el-button>
        </div>
      </div>

      <!-- 实时数据 -->
      <div class="metrics">
        <div class="metric-main">
          <b>{{ dist.value }}</b>
          <span>{{ dist.unit }}</span>
        </div>
        <div class="metric-row">
          <div class="metric">
            <b>{{ fmtDuration(elapsedSec) }}</b>
            <span>时长</span>
          </div>
          <div class="metric">
            <b>{{ curText }}</b>
            <span>{{ ride ? '当前速度' : '当前配速' }}</span>
          </div>
          <div class="metric">
            <b>{{ avgText }}</b>
            <span>{{ ride ? '平均速度' : '平均配速' }}</span>
          </div>
        </div>
      </div>

      <!-- 操作 -->
      <div class="actions">
        <button class="act-pause" :disabled="locating || counting || stopped" @click="togglePause">
          {{ running ? '暂停' : '继续' }}
        </button>
        <button
          class="act-stop"
          :class="{ pressing }"
          :disabled="locating || counting || saving || stopped"
          @pointerdown.prevent="pressStart"
          @pointerup.prevent="pressCancel"
          @pointercancel.prevent="pressCancel"
          @pointerleave="pressCancel"
        >
          <span class="stop-bar" :style="{ width: pressProgress + '%' }" />
          <span class="stop-label">{{ saving ? '保存中…' : (pressing ? '松开取消' : '长按结束') }}</span>
        </button>
      </div>

      <div class="chips">
        <button class="chip" :class="{ on: autoPauseEnabled }" @click="autoPauseEnabled = !autoPauseEnabled">
          自动暂停 {{ autoPauseEnabled ? '开' : '关' }}
        </button>
        <button class="chip" :class="{ on: voiceOn }" @click="voiceOn = !voiceOn">
          语音 {{ voiceOn ? '开' : '关' }}
        </button>
        <span v-if="gapNotice" class="gap">{{ gapNotice }}</span>
      </div>
    </template>

    <!-- 草稿恢复 -->
    <el-dialog
      v-model="showDraftDialog"
      title="发现未完成的运动"
      width="86%"
      :show-close="false"
      :close-on-click-modal="false"
      :close-on-press-escape="false"
    >
      <div v-if="draftToRestore" class="draft">
        <p>上次有一条没有保存的记录：</p>
        <div class="draft-nums">
          <div><b>{{ fmtDistance(draftToRestore.distanceM).value }}</b><span>{{ fmtDistance(draftToRestore.distanceM).unit }}</span></div>
          <div><b>{{ fmtDuration(draftToRestore.elapsedSec) }}</b><span>时长</span></div>
          <div><b>{{ draftToRestore.points.length }}</b><span>轨迹点</span></div>
        </div>
        <p class="draft-tip">恢复后可以直接继续跑，也可以长按结束直接保存。</p>
      </div>
      <template #footer>
        <el-button @click="discardDraft">放弃并重新开始</el-button>
        <el-button type="success" @click="doRestore">恢复</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
.run {
  position: fixed;
  inset: 0;
  z-index: 2000;
  background: #14181d;
  color: #fff;
  display: flex;
  flex-direction: column;
  padding: 0 14px calc(14px + env(safe-area-inset-bottom, 0));
}

/* 顶部 */
.run-head {
  height: 52px;
  display: flex;
  align-items: center;
  gap: 10px;
  flex: 0 0 52px;
}
.head-back {
  width: 34px; height: 34px;
  border-radius: 50%;
  border: none;
  background: rgba(255, 255, 255, 0.1);
  color: #fff;
  font-size: 22px;
  line-height: 1;
  padding-bottom: 4px;
}
.head-center { display: flex; flex-direction: column; align-items: center; flex: 1; }
.head-sport { font-size: 16px; font-weight: 600; letter-spacing: 1px; }
.revenge-tip {
  margin: 8px 12px 0; padding: 8px 12px; border-radius: 10px;
  background: rgba(249, 115, 22, 0.12); color: #c2410c;
  font-size: 11px; line-height: 1.5;
}
.revenge-tip b { color: #ea580c; }
.loop-tip {
  margin: 8px 12px 0; padding: 8px 12px; border-radius: 10px;
  background: rgba(148, 163, 184, 0.12); color: #64748b;
  font-size: 11px; line-height: 1.5;
}
.loop-tip.ready {
  background: rgba(34, 197, 94, 0.14); color: #15803d;
  font-weight: 600;
}
.head-sport em {
  font-style: normal; font-size: 9px; font-weight: 400;
  margin-left: 5px; padding: 1px 4px; border-radius: 4px;
  background: rgba(255, 255, 255, 0.16); vertical-align: 2px;
}
.head-acc { font-size: 10px; color: #8ee06a; margin-top: 1px; }
.head-acc.weak { color: #e6a23c; }
.head-right { font-size: 11px; color: rgba(255, 255, 255, 0.4); width: 40px; text-align: right; }

/* 地图 */
.map-wrap {
  position: relative;
  flex: 1 1 auto;
  min-height: 0;
  border-radius: 18px;
  overflow: hidden;
  background: #f2f5f8;
}
.map-mask {
  position: absolute;
  inset: 0;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 8px;
  background: rgba(20, 24, 29, 0.72);
  color: #fff;
  font-size: 14px;
  padding: 20px;
  text-align: center;
  z-index: 700;
}
.map-mask small { font-size: 11px; color: rgba(255, 255, 255, 0.55); }
.err-text { font-size: 13px; line-height: 1.6; color: #ffd9a0; }
.mask-actions { display: flex; gap: 8px; }
.pulse {
  width: 40px; height: 40px;
  border-radius: 50%;
  border: 3px solid rgba(103, 194, 58, 0.3);
  border-top-color: #67c23a;
  animation: spin 0.9s linear infinite;
}
@keyframes spin { to { transform: rotate(360deg); } }

.countdown {
  position: absolute;
  inset: 0;
  z-index: 700;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 4px;
  background: rgba(20, 24, 29, 0.45);
  backdrop-filter: blur(1px);
}
.countdown b {
  font-size: 104px;
  line-height: 1;
  color: #8ee06a;
  text-shadow: 0 4px 24px rgba(103, 194, 58, 0.45);
  animation: pop 0.7s ease-out;
}
.countdown span { font-size: 16px; font-weight: 600; }
.countdown small { font-size: 11px; color: rgba(255, 255, 255, 0.55); }
@keyframes pop {
  0% { transform: scale(0.6); opacity: 0; }
  60% { transform: scale(1.08); opacity: 1; }
  100% { transform: scale(1); }
}

.pause-badge {
  position: absolute;
  left: 50%; top: 50%; transform: translate(-50%, -50%);
  z-index: 650;
  display: flex; flex-direction: column; align-items: center; gap: 4px;
  padding: 14px 20px; border-radius: 14px;
  background: rgba(20, 24, 29, 0.76);
  backdrop-filter: blur(4px);
}
.pause-badge b { font-size: 16px; }
.pause-badge span { font-size: 11px; color: rgba(255, 255, 255, 0.6); }

/* 数据 */
.metrics { flex: 0 0 auto; padding: 14px 4px 6px; }
.metric-main { display: flex; align-items: baseline; justify-content: center; gap: 6px; }
.metric-main b { font-size: 62px; font-weight: 700; line-height: 1; letter-spacing: -2px; }
.metric-main span { font-size: 16px; color: rgba(255, 255, 255, 0.55); }
.metric-row { display: flex; justify-content: space-around; margin-top: 12px; }
.metric { display: flex; flex-direction: column; align-items: center; }
.metric b { font-size: 20px; font-weight: 600; }
.metric span { font-size: 11px; color: rgba(255, 255, 255, 0.45); margin-top: 2px; }

/* 操作 */
.actions { display: flex; gap: 12px; flex: 0 0 auto; margin-top: 8px; }
.act-pause, .act-stop {
  height: 54px;
  border-radius: 27px;
  border: none;
  font-size: 16px;
  font-weight: 600;
  letter-spacing: 1px;
  transition: transform 0.12s, background 0.2s;
  position: relative;
  overflow: hidden;
}
.act-pause {
  flex: 0 0 38%;
  background: rgba(255, 255, 255, 0.12);
  color: #fff;
}
.act-stop {
  flex: 1;
  background: #f56c6c;
  color: #fff;
  touch-action: none;
  user-select: none;
}
.act-stop.pressing { background: #d94848; transform: scale(0.97); }
.act-pause:disabled, .act-stop:disabled { opacity: 0.45; }
.stop-bar {
  position: absolute; left: 0; bottom: 0; height: 3px;
  background: rgba(255, 255, 255, 0.85);
  transition: width 0.03s linear;
}
.stop-label { position: relative; z-index: 1; }

.chips {
  display: flex; align-items: center; gap: 8px;
  margin-top: 10px; min-height: 22px; flex-wrap: wrap;
}
.chip {
  border: 1px solid rgba(255, 255, 255, 0.16);
  background: transparent; color: rgba(255, 255, 255, 0.6);
  border-radius: 12px; padding: 3px 9px; font-size: 11px;
  cursor: pointer;
}
.chip.on { color: #8ee06a; border-color: rgba(142, 224, 106, 0.45); background: rgba(142, 224, 106, 0.08); }
.gap { font-size: 10px; color: #e6a23c; }

/* 拦截页 */
.blocker {
  flex: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 10px;
  text-align: center;
  padding: 0 8px;
}
.blocker-icon { font-size: 40px; }
.blocker h3 { margin: 0; font-size: 17px; }
.blocker p { margin: 0; font-size: 13px; line-height: 1.7; color: rgba(255, 255, 255, 0.7); }
.blocker code { background: rgba(255, 255, 255, 0.12); padding: 1px 5px; border-radius: 4px; }
.blocker-tip { font-size: 12px !important; text-align: left; color: rgba(255, 255, 255, 0.55) !important; }
.blocker-actions { display: flex; gap: 10px; margin-top: 6px; }

/* 草稿弹窗 */
.draft p { margin: 0 0 10px; font-size: 14px; color: #303133; }
.draft-nums { display: flex; gap: 10px; margin: 12px 0; }
.draft-nums div {
  flex: 1; background: #f7f8fa; border-radius: 10px;
  padding: 10px 4px; text-align: center;
}
.draft-nums b { display: block; font-size: 20px; color: #303133; }
.draft-nums span { font-size: 11px; color: #909399; }
.draft-tip { font-size: 12px !important; color: #909399 !important; margin: 0 !important; }
</style>
