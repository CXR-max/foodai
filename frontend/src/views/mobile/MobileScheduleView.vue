<script setup lang="ts">
// 移动端「日程」：今日主卡（完成度圆环 + 5 项指标）+ 指标区间 + 打卡计划
// + 饮食/饮水/睡眠 + 打卡日历 + 系统自查
// 视觉按《移动端五页演示》日程页落地；业务口径、接口调用与改造前完全一致
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import * as checkinApi from '@/api/checkin'
import type { CheckInItem, DayStat } from '@/api/checkin'
import * as dayApi from '@/api/day'
import * as planApi from '@/api/plan'
import * as wellnessApi from '@/api/wellness'
import type { DaySummary, SleepRecord } from '@/api/wellness'
import { useAuthStore } from '@/stores/auth'

const router = useRouter()
const auth = useAuthStore()

function fmt(d: Date) {
  const m = `${d.getMonth() + 1}`.padStart(2, '0')
  const day = `${d.getDate()}`.padStart(2, '0')
  return `${d.getFullYear()}-${m}-${day}`
}

const today = fmt(new Date())
const selected = ref(today)
const items = ref<CheckInItem[]>([])
const summary = ref<DaySummary | null>(null)
const water = ref<{ total_ml: number; target_ml: number; achieved: boolean; records?: { id: number; ml: number }[] } | null>(null)
const sleep = ref<SleepRecord | null>(null)
const loading = ref(true)
const busyId = ref<number | null>(null)

// ---- 日期 / 进度 ----
const dateLabel = computed(() => {
  const d = new Date(`${selected.value}T00:00:00`)
  return `${d.getMonth() + 1}月${d.getDate()}日`
})
const weekLabel = computed(() => {
  const d = new Date(`${selected.value}T00:00:00`)
  const week = ['周日', '周一', '周二', '周三', '周四', '周五', '周六'][d.getDay()]
  return selected.value === today ? `${week} · 今天` : week
})
const doneCount = computed(() => summary.value?.tasks.done || 0)
const totalCount = computed(() => summary.value?.tasks.total || items.value.length || 0)
const pct = computed(() => (totalCount.value ? Math.round((doneCount.value / totalCount.value) * 100) : 0))

// 圆环：r=34，周长 213.6
const RING_C = 2 * Math.PI * 34
const ringDash = computed(() => `${((RING_C * pct.value) / 100).toFixed(1)} ${RING_C.toFixed(1)}`)

// 待打卡项文案
const pendingText = computed(() => {
  const left = items.value.filter((i) => i.status !== 'done').map((i) => i.label)
  if (!items.value.length) return '这一天还没有任务'
  if (!left.length) return '今天的打卡全部完成'
  return `${left.join('、')} ${left.length} 项待打卡`
})

// ---- 指标区间 ----
const bands = computed(() => summary.value?.bands || [])
const heroBands = computed(() =>
  ['calorie', 'protein', 'water', 'sleep', 'exercise']
    .map((k) => bands.value.find((b) => b.key === k))
    .filter(Boolean) as NonNullable<DaySummary['bands']>,
)
const bandOkCount = computed(
  () => bands.value.filter((b) => b.status === 'in_range' || b.status === 'low' || b.status === 'high').length,
)

// 蛋白不足不算「错误」：菜已经在方案里排好，按方案吃完就够，只在汇总里灰字提示，不标红
const bandCls = (status?: string, key?: string) => {
  if (status === 'in_progress') return 'muted'
  if (key === 'protein' && status === 'under') return 'muted'
  return status === 'in_range' ? 'ok'
    : status === 'low' || status === 'high' ? 'warn'
      : status === 'under' || status === 'over' ? 'bad' : ''
}

// 方案排错不是用户的错：用中性提示色，别跟着一起标红
const dietCls = computed(() => {
  const d = summary.value?.diet
  if (!d) return ''
  if (d.status === 'plan_over') return 'notice'
  if (d.ok) return 'ok'
  if (d.status === 'in_progress') return 'progress'
  return 'warn'
})

// 结构项折叠一行：脂肪/碳水/纤维/钠/糖，只展示不卡打卡
const structureText = computed(() =>
  (summary.value?.diet?.structure || [])
    .map((i) => `${i.label} ${i.value ?? '—'}${i.unit}${i.status === 'in_range' ? ' ✓' : ` ${i.status_text}`}`)
    .join(' · '),
)
const waterBand = computed(() => bands.value.find((b) => b.key === 'water'))
const sleepBand = computed(() => bands.value.find((b) => b.key === 'sleep'))

/** 指标条的区间段与当前位置：按演示稿口径，域上限取 max*1.35 */
function trackOf(b: any) {
  const min = Number(b?.min) || 0
  const max = Number(b?.max) || 0
  const actual = Number(b?.actual)
  const domainMax = max * 1.35 || 1
  const zoneL = Math.max(0, Math.min(100, (min / domainMax) * 100))
  const zoneW = Math.max(2, Math.min(100 - zoneL, ((max - min) / domainMax) * 100))
  const dot = Number.isFinite(actual) ? Math.max(2, Math.min(98, (actual / domainMax) * 100)) : null
  return {
    zoneL: `${zoneL.toFixed(1)}%`,
    zoneW: `${zoneW.toFixed(1)}%`,
    dot: dot == null ? '' : `${dot.toFixed(1)}%`,
    hasActual: dot != null && b?.actual != null,
  }
}

/** 数值展示：小时保留一位，其余取整 */
function numText(v: any, unit?: string) {
  if (v == null || v === '') return '—'
  const n = Number(v)
  if (!Number.isFinite(n)) return String(v)
  return unit === 'h' ? n.toFixed(1) : `${Math.round(n)}`
}
function rangeText(b: any) {
  if (!b) return ''
  return `${numText(b.min, b.unit)}~${numText(b.max, b.unit)}`
}

// ---- 偏离处置 / 当日模式 ----
// 结论跟时间走：上午不判（还没吃完）、21:30 后只调明天，所以选项必须由后端按当前时刻算
const dayState = ref<dayApi.DayState | null>(null)
const applyingKind = ref<string | null>(null)
const modePickerOpen = ref(false)
const selfReview = ref<dayApi.SelfReview | null>(null)
const selfReviewOpen = ref(false)
const recalcBusy = ref(false)

const settlementInfo = computed(() => dayState.value?.point)
const openDeviations = computed(() => dayState.value?.deviations || [])
const currentMode = computed(() =>
  dayState.value?.mode && dayState.value.mode.mode !== 'normal' ? dayState.value.mode : null,
)

async function loadDayState() {
  try {
    dayState.value = await dayApi.getDayState(selected.value)
  } catch {
    dayState.value = null
  }
}

async function chooseMode(mode: string) {
  try {
    const r = await dayApi.setDayMode(mode, { date: selected.value })
    ElMessage.success(r.message)
    modePickerOpen.value = false
    await Promise.all([load(), loadDayState()])
  } catch {
    /* 拦截器已提示 */
  }
}

async function runOption(devId: number, opt: dayApi.DispositionOption) {
  applyingKind.value = `${devId}:${opt.kind}`
  try {
    const r = await dayApi.applyDisposition(devId, opt.kind, opt.payload)
    ElMessage.success(r.detail.join('；') || '已处理')
    await Promise.all([load(), loadDayState()])
  } catch {
    /* 拦截器已提示 */
  } finally {
    applyingKind.value = null
  }
}

async function toggleSelfReview() {
  selfReviewOpen.value = !selfReviewOpen.value
  if (selfReviewOpen.value && !selfReview.value) {
    try {
      selfReview.value = await dayApi.getSelfReview()
    } catch {
      /* 拦截器已提示 */
    }
  }
}

async function doRecalc() {
  recalcBusy.value = true
  try {
    const r = await dayApi.applyRecalc()
    ElMessage.success(r.message)
    selfReview.value = null
    await Promise.all([load(), loadDayState()])
  } catch {
    /* 拦截器已提示 */
  } finally {
    recalcBusy.value = false
  }
}

// ---- 动态调整卡片：系统自动改了方案什么（改了什么/为什么 + 一键撤销）----
// 与「超限应对」分开：那是用户选的处置，这是系统自己做的调整，要透明可回滚
const adaptApplied = computed(() => summary.value?.adapt?.applied || [])
const revertingDay = ref<string | null>(null)
async function doRevert(day: string) {
  revertingDay.value = day
  try {
    await wellnessApi.revertAdapt(day)
    ElMessage.success('已撤销该天的动态调整，方案恢复原样')
    await load()
  } catch {
    /* 拦截器已提示 */
  } finally {
    revertingDay.value = null
  }
}

async function load() {
  loading.value = true
  try {
    const [day, sum, w, s] = await Promise.all([
      checkinApi.getDay(selected.value),
      wellnessApi.getDaySummary(selected.value),
      wellnessApi.getWater(selected.value),
      wellnessApi.getSleep(selected.value),
    ])
    items.value = day.items
    summary.value = sum
    water.value = w
    sleep.value = s.sleep
    await Promise.all([loadMonth(), loadDayState(), loadStreak()])
  } catch {
    // 拦截器已统一提示
  } finally {
    loading.value = false
  }
}

// ---- 打卡日历 ----
const month = ref(selected.value.slice(0, 7))
const monthDays = ref<DayStat[]>([])

// 日历折叠：记住用户上次的选择，首次进来默认展开（紧贴主卡，一眼能看到热力图）
const CAL_KEY = 'foodai:schedCalendar'
const calendarOpen = ref(localStorage.getItem(CAL_KEY) !== '0')
function toggleCalendar() {
  calendarOpen.value = !calendarOpen.value
  try {
    localStorage.setItem(CAL_KEY, calendarOpen.value ? '1' : '0')
  } catch {
    /* 隐私模式下写不了也不影响使用 */
  }
}

// 今日指标详情：点主卡上的指标才弹出，不占页面
const bandsOpen = ref(false)

const monthCells = computed(() => {
  const [y, m] = month.value.split('-').map(Number)
  const first = new Date(y, m - 1, 1)
  const daysInMonth = new Date(y, m, 0).getDate()
  // 热力图按周一起排（一 二 三 四 五 六 日）
  const lead = (first.getDay() + 6) % 7
  const statMap = new Map(monthDays.value.map((d) => [d.date, d]))
  const cells: { date: string; day: number; stat?: DayStat }[] = []
  for (let i = 0; i < lead; i += 1) cells.push({ date: '', day: 0 })
  for (let d = 1; d <= daysInMonth; d += 1) {
    const key = `${y}-${`${m}`.padStart(2, '0')}-${`${d}`.padStart(2, '0')}`
    cells.push({ date: key, day: d, stat: statMap.get(key) })
  }
  return cells
})

/** 热力等级：按当天完成率分 4 档（0 无记录 / 1 少量 / 2 过半 / 3 打满） */
function heatOf(stat?: DayStat) {
  if (!stat?.total) return 0
  const p = stat.pct ?? Math.round(((stat.done || 0) / stat.total) * 100)
  if (p >= 100) return 3
  if (p >= 60) return 2
  if (p > 0) return 1
  return 0
}

/** 本月达标天数 / 已过天数 */
const monthStat = computed(() => {
  const [y, m] = month.value.split('-').map(Number)
  const last = new Date(y, m, 0).getDate()
  const isCur = month.value === today.slice(0, 7)
  const upto = isCur ? Number(today.slice(8, 10)) : last
  const ok = monthDays.value.filter((d) => d.date.slice(8, 10) <= `${upto}`.padStart(2, '0') && heatOf(d) === 3).length
  return { ok, upto }
})

const monthLabel = computed(() => {
  const [y, m] = month.value.split('-').map(Number)
  return `${y} 年 ${m} 月`
})

async function loadMonth() {
  const r = await checkinApi.getMonth(month.value)
  monthDays.value = r.days
}

async function shiftMonth(delta: number) {
  const [y, m] = month.value.split('-').map(Number)
  const d = new Date(y, m - 1 + delta, 1)
  month.value = `${d.getFullYear()}-${`${d.getMonth() + 1}`.padStart(2, '0')}`
  await loadMonth()
}

async function pickDate(date: string) {
  if (!date) return
  selected.value = date
  await load()
}

// ---- 连续天数：本月 + 上月的月历打点自己算，不额外加后端接口 ----
const streakMap = ref<Record<string, DayStat>>({})
const streak = ref(0)

async function loadStreak() {
  try {
    const [y, m] = selected.value.split('-').map(Number)
    const curKey = `${y}-${`${m}`.padStart(2, '0')}`
    const pm = new Date(y, m - 2, 1)
    const prevKey = `${pm.getFullYear()}-${`${pm.getMonth() + 1}`.padStart(2, '0')}`
    const [a, b] = await Promise.all([checkinApi.getMonth(curKey), checkinApi.getMonth(prevKey)])
    const map: Record<string, DayStat> = {}
    for (const d of [...b.days, ...a.days]) map[d.date] = d
    streakMap.value = map
    let n = 0
    const cursor = new Date(`${selected.value}T00:00:00`)
    for (let i = 0; i < 90; i += 1) {
      const key = fmt(cursor)
      const st = map[key]
      if (!st) break
      const p = st.pct || 0
      // 今天还没打完不算断，继续往前数
      if (key === selected.value && p < 100) {
        cursor.setDate(cursor.getDate() - 1)
        continue
      }
      if (p >= 100) n += 1
      else break
      cursor.setDate(cursor.getDate() - 1)
    }
    streak.value = n
  } catch {
    streak.value = 0
  }
}

/** 当天没有任务时的兜底：把进行中的方案一键导入（过期会自动顺延到今天） */
const quickImporting = ref(false)
async function quickImport() {
  quickImporting.value = true
  try {
    const plans = await planApi.getPlans()
    const target = plans.find((p) => p.status === 'active') || plans[0]
    if (!target) {
      ElMessage.info('还没有方案，先去「计划」生成一份')
      router.push('/m/plan')
      return
    }
    const r = await planApi.importPlanToSchedule(target.id, { mode: 'week' })
    ElMessage.success(r.message)
    await load()
  } finally {
    quickImporting.value = false
  }
}

// ---- 时间轴任务流 ----
// 后端任务不带时间字段，按任务类型给常规时段；方案里若自带 time 优先用
const TASK_TIME: Record<string, string> = {
  meal_breakfast: '07:30',
  meal_snack: '10:20',
  meal_lunch: '12:30',
  water: '15:00',
  exercise: '18:30',
  manual_exercise: '18:30',
  meal_dinner: '19:10',
  sleep: '23:00',
}
const TASK_ORDER: Record<string, number> = {
  meal_breakfast: 1,
  meal_snack: 2,
  meal_lunch: 3,
  water: 4,
  exercise: 5,
  manual_exercise: 6,
  meal_dinner: 7,
  sleep: 8,
}
const IN_LABEL: Record<string, string> = { light: '低强度', moderate: '中强度', intense: '高强度' }

/** 按时间先后排，时间轴才是顺的 */
const flowItems = computed(() =>
  [...items.value].sort((a, b) => (TASK_ORDER[a.task_key] ?? 5) - (TASK_ORDER[b.task_key] ?? 5)),
)

function taskTime(it: any) {
  return it?.detail?.time || it?.detail?.planned_time || TASK_TIME[it.task_key] || ''
}

/** 尾部热量：饮食取方案给的 calories，运动完成取实际消耗 */
function taskKcal(it: any): number | null {
  if (it.task_key?.startsWith('meal_')) {
    const c = Number(it.detail?.calories)
    if (Number.isFinite(c) && c > 0) return Math.round(c)
    const list = it.detail?.items || it.detail?.dishes || []
    const sum = Array.isArray(list) ? list.reduce((s: number, d: any) => s + (Number(d.calories) || 0), 0) : 0
    return sum > 0 ? Math.round(sum) : null
  }
  if (it.check_type === 'exercise' && it.status === 'done') {
    const v = Number(it.calories_burned ?? it.detail?.calories_burned)
    return Number.isFinite(v) && v > 0 ? Math.round(v) : null
  }
  return null
}

interface TkChip { text: string; kind: 'pos' | 'warn' | 'ghost' }

function taskChips(it: any): TkChip[] {
  const out: TkChip[] = []
  if (it.status === 'done' && it.points_awarded) out.push({ text: `+${it.points_awarded} 分`, kind: 'pos' })
  if (it.task_key?.startsWith('meal_')) {
    out.push({ text: it.plan_day_id ? '方案餐' : '手动记', kind: 'ghost' })
  } else if (it.task_key === 'water') {
    const cup = 250
    const had = Math.round((water.value?.total_ml || 0) / cup)
    const goal = Math.max(1, Math.round((water.value?.target_ml || 2000) / cup))
    out.push({ text: `${had} / ${goal} 杯`, kind: had >= goal ? 'pos' : 'warn' })
  } else if (it.task_key === 'sleep') {
    const h = summary.value?.targets?.sleep_min_h
    out.push({ text: h ? `目标 ${h} 小时` : '新增', kind: 'ghost' })
  } else if (it.check_type === 'exercise') {
    if (it.task_key === 'manual_exercise') out.push({ text: '手动记录', kind: 'ghost' })
    else if (it.exercise_type) out.push({ text: `${it.exercise_type}${it.intensity ? ` · ${IN_LABEL[it.intensity] || ''}` : ''}`, kind: 'ghost' })
    else out.push({ text: '方案下发', kind: 'ghost' })
  }
  return out
}

/** 副标题拆成分段，模板用圆点分隔 */
function taskSubParts(it: any): string[] {
  if (it.check_type === 'exercise') {
    const list = Array.isArray(it.detail) ? it.detail : it.detail?.type ? [it.detail] : []
    if (!it.exercise_type && list.length) return list.map((e: any) => `${e.type} ${e.duration_min} 分钟`)
    const parts: string[] = []
    if (it.exercise_type) parts.push(it.exercise_type)
    const dur = it.duration_min
    if (dur) parts.push(`${dur} 分钟`)
    const burn = it.calories_burned ?? it.detail?.calories_burned
    if (burn) parts.push(`预计消耗 ${Math.round(Number(burn))} kcal`)
    if (!burn && !dur) parts.push('完成后自动算 MET')
    return parts.length ? parts : ['按计划运动']
  }
  if (it.task_key === 'sleep') {
    if (sleep.value?.duration_min) {
      const p = [`已睡 ${(sleep.value.duration_min / 60).toFixed(1)} 小时`]
      if (sleep.value.bedtime) p.push(`${sleep.value.bedtime} 到 ${sleep.value.wake_at}`)
      return p
    }
    const s = it.detail || {}
    return s.bedtime ? [`计划 ${s.bedtime} 入睡 · ${s.wake_at} 起床`] : ['记入睡 / 起床时间']
  }
  if (it.task_key === 'water') {
    return [`目标 ${water.value?.target_ml || it.detail?.water_ml || 2000} ml`]
  }
  const dishes = it.detail?.dishes || []
  const meals = it.detail?.items || []
  const names = (dishes.length ? dishes : meals).map((d: any) => d.name).filter(Boolean).join(' · ')
  if (names) return [names, it.status === 'done' ? '已完成' : '还没记录']
  return [it.detail?.meal_type ? '方案餐食' : '还没记录']
}

type CtaAct = 'water' | 'sleep' | 'shot' | 'done'

/** 尾部操作：有 CTA 就顶掉热量，避免窄屏挤成两行 */
function taskCta(it: any): { label: string; act: CtaAct } | null {
  if (it.task_key === 'water') return { label: '+1 杯', act: 'water' }
  if (it.task_key === 'sleep') return { label: sleep.value ? '改一下' : '记录', act: 'sleep' }
  if (it.status !== 'done' && it.task_key?.startsWith('meal_')) return { label: '拍照', act: 'shot' }
  if (it.status !== 'done' && it.check_type === 'exercise') return { label: '去打卡', act: 'done' }
  return null
}

async function runCta(it: any) {
  const c = taskCta(it)
  if (!c) return
  if (c.act === 'water') return void (await drink(200))
  if (c.act === 'sleep') return openSleep()
  if (c.act === 'shot') return void router.push('/recognition')
  if (c.act === 'done') return markDone(it)
}

async function markDone(it: CheckInItem) {
  if (it.status === 'done' || busyId.value) return
  if (it.task_key === 'sleep') {
    openSleep()
    return
  }
  if (it.task_key === 'water') {
    await drink(Math.max(200, (water.value?.target_ml || 2000) - (water.value?.total_ml || 0)))
    return
  }
  busyId.value = it.id
  try {
    const r = await checkinApi.complete(it.id)
    ElMessage.success(r.message)
    await load()
    auth.fetchMe().catch(() => {})
  } finally {
    busyId.value = null
  }
}

// ---- 饮水 ----
const waterPct = computed(() => {
  const t = water.value?.target_ml || 2000
  return Math.min(100, Math.round(((water.value?.total_ml || 0) / t) * 100))
})
const waterTarget = computed(() => water.value?.target_ml || waterBand.value?.max || 2000)

async function drink(ml: number) {
  if (ml <= 0) return
  const r = await wellnessApi.addWater(ml, selected.value)
  ElMessage.success(r.achieved ? `今日饮水达标 ${r.total_ml} ml 🎉` : `已喝 ${r.total_ml} / ${r.target_ml} ml`)
  await load()
}

async function undoWater() {
  const last = water.value?.records?.[water.value.records.length - 1]
  if (!last) return
  await wellnessApi.deleteWater(last.id)
  await load()
}

// ---- 睡眠 ----
const sleepOpen = ref(false)
const sleepForm = ref({ bedtime: '23:00', wakeAt: '07:00', quality: 4 })
const sleepSaving = ref(false)

const sleepTip = computed(() => {
  const s = summary.value?.plan?.sleep
  if (s?.tips) return s.tips
  if (!sleep.value?.duration_min) {
    return summary.value?.plan?.sleep
      ? `计划 ${s?.bedtime || '23:00'} 入睡 · ${s?.wake_at || '07:00'} 起床，至少 ${summary.value?.targets?.sleep_min_h || 7} 小时。`
      : '还没有昨晚的记录，填一下入睡和起床时间，按计划时长自动判达标。'
  }
  return ''
})

// 方案里的作息建议（没有计划时为 undefined），模板里直接读，避免在模板写类型断言
const planSleep = computed(() => (summary.value?.plan?.sleep || {}) as { bedtime?: string; wake_at?: string })

function openSleep() {
  sleepForm.value = {
    bedtime: sleep.value?.bedtime || planSleep.value.bedtime || '23:00',
    wakeAt: sleep.value?.wake_at || planSleep.value.wake_at || '07:00',
    quality: sleep.value?.quality_score || 4,
  }
  sleepOpen.value = true
}

async function saveSleep() {
  sleepSaving.value = true
  try {
    const r = await wellnessApi.saveSleep({
      sleep_date: selected.value,
      bedtime: sleepForm.value.bedtime,
      wake_at: sleepForm.value.wakeAt,
      quality_score: sleepForm.value.quality,
    })
    ElMessage.success(
      r.checkin.achieved
        ? `睡眠 ${(r.sleep.duration_h || 0).toFixed(1)} 小时，已达标 🎉`
        : `睡眠 ${(r.sleep.duration_h || 0).toFixed(1)} 小时，低于计划 ${r.target_min_h} 小时`,
    )
    sleepOpen.value = false
    await load()
    auth.fetchMe().catch(() => {})
  } finally {
    sleepSaving.value = false
  }
}

// ---- 图标 ----
const ICON_PATHS: Record<string, string[]> = {
  bowl: [
    'M4 10.5h16',
    'M5.5 10.5c.5 5.2 3.1 8.2 6.5 8.2s6-3 6.5-8.2',
    'M9 7.4c0-1.4 1-1.6 1-3M14 7.4c0-1.4 1-1.6 1-3',
  ],
  drop: ['M12 3.5c3.2 4.2 5.5 6.9 5.5 9.8a5.5 5.5 0 0 1-11 0c0-2.9 2.3-5.6 5.5-9.8z'],
  moon: ['M20 13.6A8 8 0 0 1 9.6 3.8a7.6 7.6 0 1 0 10.4 9.8z'],
  run: ['M3.5 12h4l2.5-6 4.5 12 2.5-6h4'],
  check: ['M5 12.5l4.5 4.5L19 7.5'],
  cam: [
    'M3 10a4 4 0 0 1 4-4h1.5l1.2-2h4.6l1.2 2H17a4 4 0 0 1 4 4v6a4 4 0 0 1-4 4H7a4 4 0 0 1-4-4z',
    'M12 15.5a3 3 0 1 0 0-6 3 3 0 0 0 0 6z',
  ],
  pen: ['M4 20h4l10-10-4-4-10 10v4z', 'M14 6l4 4'],
  cal: [
    'M4 6.5a3 3 0 0 1 3-3h10a3 3 0 0 1 3 3v11a3 3 0 0 1-3 3H7a3 3 0 0 1-3-3z',
    'M8 2.5v4M16 2.5v4M4 10h16',
  ],
  pulse: [
    'M12 20s-7-4.4-7-9.3A4 4 0 0 1 12 7a4 4 0 0 1 7 3.7C19 15.6 12 20 12 20z',
    'M7 12.5h3l1.2-2.4 1.6 4 1.2-1.6h3',
  ],
  star: ['M12 3.8l2.6 5.4 5.9.8-4.3 4.1 1 5.9-5.2-2.8-5.2 2.8 1-5.9-4.3-4.1 5.9-.8z'],
  chevron: ['M6 9.5l6 6 6-6'],
}

onMounted(load)
</script>

<template>
  <div class="sched">
    <div class="app">
      <!-- ===== 主卡：今日打卡 ===== -->
      <section class="hero">
        <div class="hero-top">
          <div class="hero-date">
            <b>{{ dateLabel }}</b>
            <span>{{ weekLabel }}</span>
          </div>
          <span v-if="streak > 0" class="hero-chip">连续 {{ streak }} 天</span>
        </div>

        <div class="hero-mid">
          <div>
            <div class="hero-k">今日打卡</div>
            <div class="hero-num">
              <b>{{ doneCount }}</b><i>/{{ totalCount }}</i><u>项</u>
            </div>
            <p class="hero-sub">{{ pendingText }}</p>
          </div>
          <div class="ring">
            <svg viewBox="0 0 84 84" aria-hidden="true">
              <circle cx="42" cy="42" r="34" fill="none" stroke="rgba(23,26,20,0.16)" stroke-width="7" />
              <circle
                cx="42" cy="42" r="34" fill="none" stroke="#171A14" stroke-width="7"
                stroke-linecap="round" :stroke-dasharray="ringDash" transform="rotate(-90 42 42)"
              />
            </svg>
            <div class="ring-txt">
              <b>{{ pct }}%</b><span>完成度</span>
            </div>
          </div>
        </div>

        <div class="hero-metrics">
          <div
            v-for="b in heroBands"
            :key="b.key"
            class="hm"
            :class="bandCls(b.status, b.key)"
            role="button"
            tabindex="0"
            aria-label="查看今日指标明细"
            @click="bandsOpen = true"
            @keydown.enter.prevent="bandsOpen = true"
          >
            <span>{{ b.label }}</span>
            <b>{{ numText(b.actual, b.unit) }}<i>{{ b.actual != null ? b.unit : '' }}</i></b>
          </div>
        </div>
      </section>

      <!-- ===== 打卡日历（紧贴主卡，可折叠） ===== -->
      <div class="fold cal-fold" :class="{ on: calendarOpen }">
        <button class="fold-bar" :aria-expanded="calendarOpen" @click="toggleCalendar">
          <span class="fold-l">
            <svg class="ico" viewBox="0 0 24 24" aria-hidden="true">
              <path v-for="(d, i) in ICON_PATHS.cal" :key="i" :d="d" />
            </svg>
            {{ month.split('-')[1] }} 月打卡日历
            <i class="fold-num">{{ monthStat.ok }}/{{ monthStat.upto }} 天打满</i>
          </span>
          <span class="fold-r">
            {{ calendarOpen ? '收起' : '展开' }}
            <svg class="ico" viewBox="0 0 24 24" aria-hidden="true"><path d="M6 9.5l6 6 6-6" /></svg>
          </span>
        </button>
        <div v-show="calendarOpen" class="panel on">
          <div class="card cal-card">
            <div class="cal-panel">
              <div class="cal-head">
                <button class="cal-nav" aria-label="上个月" @click="shiftMonth(-1)">‹</button>
                <b>{{ monthLabel }}</b>
                <button class="cal-nav" aria-label="下个月" @click="shiftMonth(1)">›</button>
              </div>
              <div class="cal-week">
                <span v-for="w in ['一', '二', '三', '四', '五', '六', '日']" :key="w">{{ w }}</span>
              </div>
              <div class="cal-grid">
                <button
                  v-for="(c, i) in monthCells"
                  :key="i"
                  class="cal-cell"
                  :class="[
                    `l${heatOf(c.stat)}`,
                    {
                      empty: !c.day,
                      today: c.date === today,
                      future: !!c.date && c.date > today,
                      sel: c.date === selected && c.date !== today,
                    },
                  ]"
                  :disabled="!c.day"
                  @click="pickDate(c.date)"
                >
                  <span>{{ c.day || '' }}</span>
                </button>
              </div>
              <div class="cal-legend">
                <span>少</span>
                <i class="l0"></i><i class="l1"></i><i class="l2"></i><i class="l3"></i>
                <span>多</span>
                <span class="cal-legend-num">颜色越深完成度越高 · 点日期切当天</span>
              </div>
            </div>
          </div>
        </div>
      </div>

      <!-- ===== 今日任务（时间轴） ===== -->
      <section class="card">
        <div class="card-head">
          <h2>今日任务</h2>
          <span v-if="items.length" class="head-tag">{{ doneCount }}/{{ totalCount }} · 点圆圈打卡</span>
        </div>

        <div v-if="loading" class="skeleton">
          <span v-for="i in 4" :key="i" class="sk-row"></span>
        </div>

        <div v-else-if="!items.length" class="empty">
          <p class="empty-t">这一天还没有任务</p>
          <p class="empty-s">导入一份进行中的方案，就能按天打卡</p>
          <div class="btn-row">
            <button class="btn btn-solid" :disabled="quickImporting" @click="quickImport">
              {{ quickImporting ? '导入中…' : '一键导入方案' }}
            </button>
            <button class="btn" @click="router.push('/m/plan')">去计划页</button>
          </div>
        </div>

        <div v-else class="flow">
          <div v-for="it in flowItems" :key="it.id" class="tk" :class="{ done: it.status === 'done' }">
            <div class="tk-time">{{ taskTime(it) }}</div>
            <button
              class="tk-node"
              :aria-label="it.status === 'done' ? `取消打卡：${it.label}` : `打卡：${it.label}`"
              :disabled="busyId === it.id"
              @click="markDone(it)"
            >
              <svg class="ico" viewBox="0 0 24 24" aria-hidden="true">
                <path d="M5 12.5l4.5 4.5L19 7.5" />
              </svg>
            </button>
            <div class="tk-body">
              <div class="tk-title">
                <span class="t">{{ it.label }}</span>
                <span v-for="c in taskChips(it)" :key="c.text" class="tchip" :class="c.kind">{{ c.text }}</span>
              </div>
              <div class="tk-sub">
                <template v-for="(p, pi) in taskSubParts(it)" :key="pi">
                  <span v-if="pi" class="sep"></span>
                  <span>{{ p }}</span>
                </template>
              </div>
            </div>
            <div class="tk-tail">
              <button v-if="taskCta(it)" class="tk-cta" @click="runCta(it)">{{ taskCta(it)?.label }}</button>
              <span v-else-if="taskKcal(it)" class="tk-kcal">{{ taskKcal(it) }}<small>kcal</small></span>
            </div>
          </div>
        </div>
      </section>

      <!-- ===== 饮水 ===== -->
      <section class="card">
        <div class="w-top">
          <div>
            <div class="w-k">饮水</div>
            <div class="w-num"><b>{{ water?.total_ml || 0 }}</b><i>/ {{ waterTarget }} ml</i></div>
          </div>
          <span class="w-pct">{{ waterPct }}%</span>
        </div>
        <div class="w-bar"><span :style="{ width: `${waterPct}%` }"></span></div>
        <div class="w-acts">
          <button class="btn" @click="drink(200)">+200</button>
          <button class="btn" @click="drink(300)">+300</button>
          <button class="btn" @click="drink(500)">+500</button>
          <button class="btn ghost" :disabled="!water?.records?.length" @click="undoWater">撤销</button>
        </div>
      </section>

      <!-- ===== 睡眠 ===== -->
      <section class="card">
        <div class="card-head">
          <h2>睡眠打卡</h2>
          <span v-if="sleep" class="src-tag">{{ sleep.source === 'manual' ? '手动记录' : '手环同步' }}</span>
        </div>
        <div class="sleep-num">
          <b>{{ sleep?.duration_h ? sleep.duration_h.toFixed(1) : '—' }}</b><i>小时</i>
        </div>
        <div v-if="sleep" class="sleep-line">
          {{ sleep.bedtime }} 入睡 · {{ sleep.wake_at }} 起床
          <template v-if="sleep.quality_score"> · 质量 {{ sleep.quality_score }}/5</template>
          <template v-if="sleepBand"> · 推荐 {{ sleepBand.range_text }} h</template>
        </div>
        <div v-else class="sleep-line">
          还没记录昨晚睡眠<template v-if="sleepBand">，推荐 {{ sleepBand.range_text }} 小时</template>
        </div>
        <p v-if="sleepTip" class="sleep-tip">{{ sleepTip }}</p>
        <div class="btn-row">
          <button class="btn" @click="openSleep">{{ sleep ? '修改睡眠' : '记录睡眠' }}</button>
        </div>
      </section>

      <!-- ===== 系统自查（折叠） ===== -->
      <div class="fold" :class="{ on: selfReviewOpen }">
        <button class="fold-bar" :aria-expanded="selfReviewOpen" @click="toggleSelfReview">
          <span class="fold-l">
            <svg class="ico" viewBox="0 0 24 24" aria-hidden="true">
              <path v-for="(d, i) in ICON_PATHS.pulse" :key="i" :d="d" />
            </svg>
            系统自查
          </span>
          <span class="fold-r">
            {{ selfReviewOpen ? '收起' : '展开' }}
            <svg class="ico" viewBox="0 0 24 24" aria-hidden="true"><path d="M6 9.5l6 6 6-6" /></svg>
          </span>
        </button>
        <div v-show="selfReviewOpen" class="panel on">
          <div v-if="selfReview" class="cal-panel">
            <p v-for="(line, i) in selfReview.lines" :key="i" class="review-line">{{ line }}</p>
            <p v-if="!selfReview.lines.length" class="review-line">
              最近没有连续偏离，也没有挂例外，口径和你的实际吃得对得上。
            </p>
            <div v-if="selfReview.proposal" class="recalc-box">
              <p class="recalc-target">
                每日目标 {{ selfReview.proposal.old_target }} → {{ selfReview.proposal.new_target }} kcal
                <i v-if="selfReview.proposal.bmr">（不低于基础代谢 {{ selfReview.proposal.bmr }}）</i>
              </p>
              <p class="recalc-why">{{ selfReview.proposal.reason }}</p>
              <button class="btn btn-solid" :disabled="recalcBusy" @click="doRecalc">
                {{ recalcBusy ? '重算中…' : '按实际数据重算' }}
              </button>
            </div>
            <p class="review-foot">
              这是系统的自查，不是你的评分。建议不实用会换成「改餐食」，而不是「加练」。
            </p>
          </div>
          <div v-else class="cal-panel"><p class="review-line">加载中…</p></div>
        </div>
      </div>
    </div>

    <!-- ===== 弹层：今日指标明细 ===== -->
    <div class="mask" :class="{ on: bandsOpen }" @click="bandsOpen = false"></div>
    <div
      class="sheet bands-sheet"
      :class="{ on: bandsOpen }"
      role="dialog"
      aria-modal="true"
      aria-label="今日指标明细"
    >
      <div class="sheet-grab"></div>
      <div class="card-head">
        <h2>今日指标明细</h2>
        <span class="head-tag">{{ bandOkCount }}/{{ bands.length }} 达标</span>
      </div>
      <p class="sheet-tip">区间为推荐值，落在浅色段内即达标；口径见「计划 → 每日目标」，超限可在计划详情里改餐或加运动。点空白处关闭。</p>
      <div class="sheet-body">
        <!-- 饮食一行汇总：七项判完给一条结论。方案排错不怪用户，额外加餐给出口不指责 -->
        <div v-if="summary?.diet?.line" class="diet-line" :class="dietCls">
          <span class="dl-text">{{ summary.diet.line }}</span>
          <span class="dl-tag">{{ summary.diet.status_text }}</span>
        </div>
        <p v-if="summary?.diet?.hint" class="diet-hint">{{ summary.diet.hint }}</p>

        <!-- 当日模式：聚餐/生病/出差…只改今天的判定口径，第二天自动恢复 -->
        <div class="mode-bar">
          <span v-if="currentMode" class="mode-tag">
            {{ currentMode.label }}<i v-if="currentMode.note_text">{{ currentMode.note_text }}</i>
          </span>
          <span v-else class="mode-idle">今天一切正常</span>
          <button class="mode-btn" @click="modePickerOpen = true">
            {{ currentMode ? '换一个' : '有特殊情况？' }}
          </button>
        </div>

        <!-- 结算时点：为什么现在不判/还能做什么，先讲清楚再看数字 -->
        <p v-if="settlementInfo" class="settle">{{ settlementInfo.message }}</p>

        <!-- 动态调整：系统自动改了方案什么（每条带为什么，可单独撤销） -->
        <div v-if="adaptApplied.length" class="adapt-wrap">
          <div v-for="item in adaptApplied" :key="item.day" class="adapt-card">
            <div class="adapt-head">
              <span class="adapt-tag">方案已自动调整</span>
              <span class="adapt-day">{{ item.day }}</span>
              <button class="adapt-revert" :disabled="revertingDay === item.day" @click="doRevert(item.day)">
                撤销
              </button>
            </div>
            <p v-for="(r, i) in item.reasons" :key="i" class="adapt-reason">
              <b>{{ r.action }}</b>——{{ r.reason }}
            </p>
          </div>
        </div>

        <!-- 超限应对：每个选项都是算好的动作，点了才落地；「不处置」永远可选 -->
        <div v-if="openDeviations.length" class="disp-wrap">
          <div v-for="group in openDeviations" :key="group.deviation.id" class="disp-card">
            <div class="disp-head">
              <span class="disp-name">
                {{ group.deviation.label }}
                {{ group.deviation.kind === 'over' ? '超' : '少' }}
                {{ Math.abs(group.deviation.delta || 0) }}{{ group.deviation.nutrient === 'calorie' ? ' kcal' : ' g' }}
              </span>
              <span class="disp-tag" :class="group.deviation.attribution">
                {{
                  group.deviation.attribution === 'plan' ? '方案排错'
                  : group.deviation.attribution === 'extra' ? '额外摄入'
                  : group.deviation.attribution === 'intake' ? '没吃完'
                  : '偏差'
                }}
              </span>
            </div>
            <div class="disp-opts">
              <button
                v-for="opt in group.options"
                :key="opt.kind"
                class="disp-opt"
                :class="{ rec: opt.recommended, unsafe: !opt.safe }"
                :disabled="applyingKind === `${group.deviation.id}:${opt.kind}`"
                @click="runOption(group.deviation.id, opt)"
              >
                <b>{{ opt.label }}<i v-if="opt.recommended">推荐</i></b>
                <span>{{ opt.detail }}</span>
                <em v-for="(s, i) in opt.safety" :key="i" class="safety">{{ s }}</em>
              </button>
            </div>
          </div>
        </div>

        <div v-if="structureText" class="diet-struct">结构：{{ structureText }}</div>
        <!-- 打卡即按方案折算，得说清楚这是估算，别让人以为系统知道他吃没吃 -->
        <div v-if="summary?.intake?.estimated" class="diet-est">
          {{ summary.intake.note }}，记下实际吃了什么会更准
        </div>

        <div class="metrics">
          <div v-for="b in bands" :key="b.key" class="m-row" :class="bandCls(b.status, b.key)">
            <div class="m-top">
              <span class="m-name">{{ b.label }}</span>
              <span class="m-status">{{ b.status_text }}</span>
            </div>
            <div class="m-mid">
              <div class="m-val">
                <b>{{ numText(b.actual, b.unit) }}</b><i v-if="b.actual != null">{{ b.unit }}</i>
              </div>
              <span class="m-range">{{ rangeText(b) }} {{ b.unit }}</span>
            </div>
            <div class="m-track">
              <span
                class="m-zone"
                :style="{ left: trackOf(b).zoneL, width: trackOf(b).zoneW }"
              ></span>
              <span v-if="trackOf(b).hasActual" class="m-dot" :style="{ left: trackOf(b).dot }"></span>
            </div>
            <p class="m-hint">{{ b.hint }}</p>
          </div>
        </div>

      </div>
    </div>

    <!-- ===== 弹层：记录睡眠 ===== -->
    <div class="mask" :class="{ on: sleepOpen }" @click="sleepOpen = false"></div>
    <div class="sheet" :class="{ on: sleepOpen }" role="dialog" aria-modal="true" aria-label="记录睡眠">
      <div class="sheet-grab"></div>
      <h3>记录昨晚睡眠</h3>
      <p class="sheet-tip">
        <template v-if="summary?.plan?.sleep">
          计划 {{ planSleep.bedtime }} 入睡 · {{ planSleep.wake_at }} 起床，
          至少 {{ summary.targets?.sleep_min_h || 7 }} 小时。
        </template>
        <template v-else>填入睡和起床时间，系统按时长判达标。</template>
      </p>
      <div class="sheet-body">
        <div class="field">
          <span>入睡 / 起床</span>
          <div class="time-row">
            <input v-model="sleepForm.bedtime" type="time" />
            <span class="time-sep">到</span>
            <input v-model="sleepForm.wakeAt" type="time" />
          </div>
        </div>
        <div class="field">
          <span>睡眠质量</span>
          <div class="stars">
            <button
              v-for="n in 5"
              :key="n"
              :class="{ on: sleepForm.quality >= n }"
              :aria-label="`${n} 星`"
              @click="sleepForm.quality = n"
            >
              <svg class="ico" viewBox="0 0 24 24" aria-hidden="true">
                <path v-for="(d, i) in ICON_PATHS.star" :key="i" :d="d" />
              </svg>
            </button>
          </div>
        </div>
      </div>
      <div class="sheet-acts">
        <button class="btn" @click="sleepOpen = false">取消</button>
        <button class="btn btn-solid" :disabled="sleepSaving" @click="saveSleep">
          {{ sleepSaving ? '保存中…' : '保存并打卡' }}
        </button>
      </div>
    </div>

    <!-- ===== 弹层：今日特殊情况 ===== -->
    <div class="mask" :class="{ on: modePickerOpen }" @click="modePickerOpen = false"></div>
    <div class="sheet" :class="{ on: modePickerOpen }" role="dialog" aria-modal="true" aria-label="今日特殊情况">
      <div class="sheet-grab"></div>
      <h3>今天有什么特殊情况？</h3>
      <p class="sheet-tip">只影响今天的判定口径，明天自动恢复。不是找借口，是让标准符合现实。</p>
      <div class="sheet-body">
        <div class="mode-list">
          <button v-for="m in dayApi.MODE_OPTIONS" :key="m.value" class="mode-item" @click="chooseMode(m.value)">
            <b>{{ m.label }}</b><span>{{ m.desc }}</span>
          </button>
          <button class="mode-item normal" @click="chooseMode('normal')">
            <b>恢复正常</b><span>按平常口径判，不做任何放宽</span>
          </button>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
/* ============ 设计 token（仅本页作用域，不动全局样式） ============ */
.sched {
  --paper: #f0f7de;
  --surface: #ffffff;
  --teal: #c2e07c;
  --teal-dk: #b4d46a;
  --teal-tx: #171a14;
  --mint: #e9f3cc;
  --mint-2: #d3e79f;
  --ink: #171a14;
  --ink-2: #3f4639;
  --muted: #6d7565;
  --line: #e6edd6;
  --amber: #b8791a;
  --red: #be4a3c;
  --r-xl: 26px;
  --r-lg: 22px;
  --r-md: 16px;
  --shadow: 0 10px 28px rgba(28, 36, 22, 0.07);
  --shadow-lime: 0 18px 38px rgba(122, 152, 40, 0.2);
  --glass: rgba(255, 255, 255, 0.66);
  --glass-mint: rgba(233, 243, 204, 0.78);
  --glass-line: rgba(255, 255, 255, 0.7);

  /* 吃掉 el-main 的 10px 内边距与底部 tabBar 预留；左右用阴影补齐，避免负 margin 撑出横向滚动 */
  margin: -10px 0 calc(-70px - env(safe-area-inset-bottom, 0px));
  padding: 0 0 calc(72px + env(safe-area-inset-bottom, 0px));
  min-height: calc(100vh - 52px);
  background: var(--paper);
  box-shadow: 0 0 0 10px var(--paper);
  color: var(--ink);
  font-family: 'Manrope', -apple-system, BlinkMacSystemFont, 'PingFang SC', 'HarmonyOS Sans SC',
    'Microsoft YaHei', sans-serif;
  font-size: 14px;
  line-height: 1.55;
  -webkit-font-smoothing: antialiased;
}

.sched *,
.sched *::before,
.sched *::after { box-sizing: border-box; }

.sched h2,
.sched h3,
.sched p { margin: 0; }
.sched button { font: inherit; color: inherit; cursor: pointer; }

.ico {
  width: 1em;
  height: 1em;
  display: block;
  fill: none;
  stroke: currentColor;
  stroke-width: 1.6;
  stroke-linecap: round;
  stroke-linejoin: round;
}

.app {
  max-width: 460px;
  margin: 0 auto;
  padding: 12px 16px 0;
}

/* ============ 主卡 ============ */
.hero {
  position: relative;
  overflow: hidden;
  background: var(--teal);
  border-radius: var(--r-xl);
  padding: 18px;
  color: var(--ink);
  box-shadow: var(--shadow-lime);
}
.hero-top { display: flex; align-items: flex-start; justify-content: space-between; gap: 10px; }
.hero-date b {
  display: block;
  font-size: 19px;
  font-weight: 800;
  letter-spacing: -0.02em;
  line-height: 1.2;
}
.hero-date span { display: block; margin-top: 2px; font-size: 11.5px; color: var(--ink-2); }
.hero-chip {
  flex: 0 0 auto;
  font-size: 11px;
  font-weight: 700;
  padding: 5px 10px;
  border-radius: 999px;
  background: rgba(23, 26, 20, 0.1);
  border: 1px solid rgba(23, 26, 20, 0.12);
  white-space: nowrap;
}
.hero-mid { display: flex; align-items: center; justify-content: space-between; gap: 14px; margin-top: 14px; }
.hero-k { font-size: 11.5px; color: var(--ink-2); }
.hero-num {
  display: flex;
  align-items: baseline;
  gap: 3px;
  margin-top: 2px;
  font-variant-numeric: tabular-nums;
}
.hero-num b {
  font-size: clamp(34px, 9.5vw, 44px);
  font-weight: 800;
  line-height: 1;
  letter-spacing: -0.04em;
}
.hero-num i { font-style: normal; font-size: 16px; font-weight: 700; color: var(--ink-2); }
.hero-num u { text-decoration: none; font-size: 11px; color: var(--muted); margin-left: 3px; }
.hero-sub { margin-top: 7px; font-size: 11.5px; line-height: 1.5; color: var(--ink-2); }

.ring { position: relative; width: 84px; height: 84px; flex: 0 0 84px; }
.ring svg { width: 100%; height: 100%; display: block; }
.ring-txt { position: absolute; inset: 0; display: grid; place-content: center; text-align: center; }
.ring-txt b { font-size: 20px; font-weight: 800; letter-spacing: -0.03em; }
.ring-txt span { display: block; font-size: 9.5px; color: var(--ink-2); margin-top: 1px; }

.hero-metrics {
  display: grid;
  grid-template-columns: repeat(5, minmax(0, 1fr));
  margin-top: 15px;
  padding-top: 12px;
  border-top: 1px solid rgba(23, 26, 20, 0.16);
}
.hm { min-width: 0; padding: 0 4px; }
.hm:first-child { padding-left: 0; }
.hm + .hm { border-left: 1px solid rgba(23, 26, 20, 0.16); }
.hm span { display: block; font-size: 10px; color: var(--ink-2); white-space: nowrap; }
.hm b {
  display: block;
  margin-top: 2px;
  font-size: clamp(12px, 3.5vw, 14px);
  font-weight: 800;
  font-variant-numeric: tabular-nums;
  white-space: nowrap;
  letter-spacing: -0.02em;
}
.hm b i {
  font-style: normal;
  font-size: 9.5px;
  font-weight: 600;
  color: var(--muted);
  margin-left: 1px;
  letter-spacing: 0;
}
.hm.bad b { color: #8f2f24; }
.hm.warn b { color: #7c5210; }
.hm.muted b { color: var(--ink-2); }

/* ============ 通用卡片 ============ */
.card {
  margin-top: 14px;
  background: var(--surface);
  border: 1px solid var(--line);
  border-radius: var(--r-xl);
  padding: 16px;
  box-shadow: var(--shadow);
}
.card-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  margin-bottom: 12px;
}
.card-head h2 { font-size: 15px; font-weight: 800; letter-spacing: -0.01em; }
.head-tag { font-size: 11.5px; color: var(--muted); white-space: nowrap; }

/* 主卡 5 项指标可点：点开「今日指标明细」抽屉 */
.hm { cursor: pointer; transition: opacity 0.15s ease; }
.hm:active { opacity: 0.55; }
.hm:focus-visible { outline: 2px solid rgba(23, 26, 20, 0.45); outline-offset: 2px; border-radius: 6px; }

/* 日历紧贴主卡：折叠条 + 极简面板，去掉内层 panel 的二次边框与留白 */
.cal-fold { margin-top: 14px; }
.cal-fold .panel .card { margin-top: 10px; }
.fold-num {
  font-style: normal;
  font-size: 10.5px;
  font-weight: 600;
  color: var(--muted);
  margin-left: 2px;
  white-space: nowrap;
}
.cal-card { padding: 14px 16px 12px; }
.cal-card .cal-panel {
  margin-top: 0;
  padding: 0;
  border: none;
  background: transparent;
  box-shadow: none;
}
.cal-card .cal-legend { margin-top: 10px; }

/* 指标明细抽屉：标题与正文更紧凑 */
.bands-sheet .card-head { margin-bottom: 4px; }
.bands-sheet .sheet-tip { margin-bottom: 6px; }
.bands-sheet .sheet-body { margin-top: 8px; gap: 10px; }

/* ============ 指标区间 ============ */
.diet-line {
  display: flex;
  align-items: flex-start;
  gap: 10px;
  padding: 10px 12px;
  border-radius: var(--r-md);
  background: var(--mint);
  font-size: 12px;
  line-height: 1.5;
  color: var(--ink-2);
}
.dl-text { min-width: 0; flex: 1; }
.dl-tag {
  flex: 0 0 auto;
  font-size: 10.5px;
  font-weight: 700;
  padding: 2px 8px;
  border-radius: 999px;
  background: rgba(23, 26, 20, 0.1);
  color: var(--teal-tx);
}
.diet-line.warn { background: #fbf4e7; }
.diet-line.warn .dl-tag { background: rgba(184, 121, 26, 0.14); color: var(--amber); }
.diet-line.progress { background: #f3f7fb; }
.diet-line.progress .dl-tag { background: rgba(74, 114, 184, 0.14); color: #4a72b8; }
/* 方案排错是系统的问题，用中性灰，别让用户自责 */
.diet-line.notice { background: #f4f4f6; }
.diet-line.notice .dl-tag { background: rgba(109, 117, 101, 0.12); color: var(--muted); }
.diet-hint { margin: 8px 2px 0; font-size: 11.5px; color: var(--muted); line-height: 1.55; }
.diet-struct { margin: 8px 2px 0; font-size: 10.5px; color: #9aa392; line-height: 1.6; }
.diet-est { margin: 6px 2px 0; font-size: 10.5px; color: #9aa392; line-height: 1.6; }

.mode-bar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  margin-top: 10px;
  padding: 9px 12px;
  border-radius: var(--r-md);
  background: #f8faef;
  border: 1px solid var(--line);
}
.mode-idle { font-size: 11.5px; color: var(--muted); }
.mode-tag { font-size: 11.5px; font-weight: 700; color: var(--teal-tx); }
.mode-tag i { font-style: normal; font-weight: 400; color: var(--muted); margin-left: 4px; }
.mode-btn {
  flex: 0 0 auto;
  border: 1px solid var(--glass-line);
  background: var(--glass-mint);
  -webkit-backdrop-filter: blur(12px) saturate(160%);
  backdrop-filter: blur(12px) saturate(160%);
  color: var(--teal-tx);
  font-size: 11.5px;
  font-weight: 700;
  padding: 5px 11px;
  border-radius: 999px;
  white-space: nowrap;
}

.settle {
  margin-top: 10px;
  padding: 8px 11px;
  border-radius: 12px;
  background: #f8faef;
  font-size: 11px;
  line-height: 1.55;
  color: var(--muted);
}

.metrics { margin-top: 4px; }
.m-row { padding: 12px 0 4px; border-top: 1px solid var(--line); }
.m-row:first-child { border-top: none; padding-top: 8px; }
.m-top { display: flex; align-items: center; justify-content: space-between; gap: 8px; }
.m-name { font-size: 13.5px; font-weight: 700; }
.m-status {
  font-size: 10.5px;
  font-weight: 700;
  padding: 2px 8px;
  border-radius: 999px;
  background: var(--mint);
  color: var(--teal-tx);
  white-space: nowrap;
}
.m-row.warn .m-status { background: #fbf4e7; color: var(--amber); }
.m-row.bad .m-status { background: #fbeeec; color: var(--red); }
.m-row.muted .m-status { background: #f2f6f4; color: var(--muted); }

.m-mid { display: flex; align-items: baseline; justify-content: space-between; gap: 8px; margin-top: 3px; }
.m-val { min-width: 0; }
.m-val b {
  font-size: 22px;
  font-weight: 800;
  letter-spacing: -0.03em;
  font-variant-numeric: tabular-nums;
}
.m-val i { font-style: normal; font-size: 11px; color: var(--muted); margin-left: 3px; }
.m-range { font-size: 11px; color: var(--muted); white-space: nowrap; font-variant-numeric: tabular-nums; }

.m-track { position: relative; height: 6px; margin-top: 8px; border-radius: 999px; background: #eef3dc; }
.m-zone { position: absolute; top: 0; bottom: 0; border-radius: 999px; background: var(--mint-2); }
.m-dot {
  position: absolute;
  top: 50%;
  width: 10px;
  height: 10px;
  border-radius: 50%;
  background: var(--teal-tx);
  transform: translate(-50%, -50%);
  box-shadow: 0 0 0 3px rgba(23, 26, 20, 0.12);
}
.m-row.warn .m-dot { background: var(--amber); box-shadow: 0 0 0 3px rgba(184, 121, 26, 0.14); }
.m-row.bad .m-dot { background: var(--red); box-shadow: 0 0 0 3px rgba(190, 74, 60, 0.14); }
.m-row.muted .m-dot { background: #a9b39f; box-shadow: 0 0 0 3px rgba(109, 117, 101, 0.12); }
.m-hint { margin-top: 7px; padding-bottom: 8px; font-size: 11.5px; color: var(--muted); line-height: 1.55; }

.card-foot {
  margin-top: 6px;
  padding-top: 10px;
  border-top: 1px solid var(--line);
  font-size: 10.5px;
  color: var(--muted);
  line-height: 1.6;
}

/* ============ 动态调整 / 超限应对 ============ */
.adapt-wrap { margin-top: 10px; display: flex; flex-direction: column; gap: 8px; }
.adapt-card { background: #f4f8ff; border: 1px solid #dce8f7; border-radius: var(--r-md); padding: 10px 12px; }
.adapt-head { display: flex; align-items: center; gap: 8px; }
.adapt-tag {
  font-size: 10.5px;
  padding: 2px 8px;
  border-radius: 999px;
  background: #dcebfa;
  color: #2b6cb0;
  flex: none;
  font-weight: 700;
}
.adapt-day { font-size: 11.5px; color: var(--muted); flex: 1; }
.adapt-revert {
  font-size: 11px;
  color: #2b6cb0;
  background: transparent;
  border: 1px solid #b9d3ec;
  border-radius: 999px;
  padding: 2px 10px;
}
.adapt-revert:disabled { opacity: 0.5; }
.adapt-reason { font-size: 11.5px; line-height: 1.6; color: #4a5568; margin: 6px 0 0; }
.adapt-reason b { font-weight: 700; color: #2b6cb0; }

.disp-wrap { display: flex; flex-direction: column; gap: 8px; margin-top: 10px; }
.disp-card { border: 1px solid #f0dcb8; background: #fffdf7; border-radius: var(--r-md); padding: 10px 11px; }
.disp-head { display: flex; align-items: center; justify-content: space-between; gap: 8px; margin-bottom: 7px; }
.disp-name { font-size: 12px; font-weight: 700; color: var(--amber); }
.disp-tag { font-size: 10px; padding: 1px 7px; border-radius: 999px; background: #f4f4f5; color: var(--muted); }
.disp-tag.extra { background: #ecf5ff; color: #409eff; }
.disp-tag.intake { background: #fdf6ec; color: var(--amber); }
.disp-opts { display: flex; flex-direction: column; gap: 6px; }
.disp-opt {
  text-align: left;
  border: 1px solid var(--line);
  background: #fff;
  border-radius: 12px;
  padding: 8px 10px;
  display: flex;
  flex-direction: column;
  gap: 3px;
}
.disp-opt b { font-size: 12px; display: flex; align-items: center; gap: 5px; }
.disp-opt b i {
  font-style: normal;
  font-size: 9.5px;
  background: var(--teal);
  color: var(--ink);
  padding: 0 5px;
  border-radius: 6px;
  line-height: 15px;
  font-weight: 700;
}
.disp-opt span { font-size: 11px; color: var(--ink-2); line-height: 1.55; }
.disp-opt em.safety { font-style: normal; font-size: 10.5px; color: var(--amber); line-height: 1.5; }
.disp-opt.rec { border-color: var(--mint-2); background: #fbfef5; }
.disp-opt.unsafe { opacity: 0.62; }
.disp-opt:disabled { opacity: 0.5; }

/* ============ 打卡计划（时间轴任务流） ============ */
.flow { position: relative; margin-top: 2px; }
.flow::before {
  content: '';
  position: absolute;
  left: 57px;
  top: 13px;
  bottom: 20px;
  width: 1.5px;
  border-radius: 2px;
  background: linear-gradient(180deg, rgba(150, 190, 60, 0.5), rgba(150, 190, 60, 0.05));
}
.tk {
  position: relative;
  display: grid;
  grid-template-columns: 38px 22px minmax(0, 1fr) auto;
  gap: 0 8px;
  align-items: start;
  padding: 12px 0;
}
.tk:first-child { padding-top: 4px; }
.tk-time {
  padding-top: 3px;
  text-align: right;
  font-size: 11px;
  color: var(--muted);
  font-variant-numeric: tabular-nums;
}
.tk-node {
  position: relative;
  z-index: 1;
  justify-self: center;
  width: 17px;
  height: 17px;
  margin-top: 1px;
  padding: 0;
  border: 0;
  border-radius: 50%;
  background: #fff;
  box-shadow: inset 0 0 0 1.6px rgba(126, 143, 96, 0.42);
  display: grid;
  place-items: center;
  color: var(--ink);
  transition: background 0.16s ease, box-shadow 0.16s ease, transform 0.12s ease;
}
.tk-node .ico { width: 10px; height: 10px; stroke-width: 3.4; opacity: 0; }
.tk-node:active { transform: scale(0.9); }
.tk-node:disabled { opacity: 0.55; }
.tk.done .tk-node {
  background: linear-gradient(160deg, var(--teal), var(--teal-dk));
  box-shadow: 0 3px 9px -4px rgba(122, 152, 40, 0.9);
}
.tk.done .tk-node .ico { opacity: 1; }
.tk-body { min-width: 0; }
.tk-title { display: flex; align-items: center; gap: 6px; flex-wrap: wrap; }
.tk-title .t { font-size: 14px; font-weight: 800; letter-spacing: -0.01em; }
.tk.done .tk-title .t {
  color: var(--muted);
  font-weight: 700;
  text-decoration: line-through;
  text-decoration-thickness: 1px;
}
.tchip {
  padding: 3px 7px;
  border-radius: 999px;
  font-size: 10px;
  font-weight: 700;
  white-space: nowrap;
}
.tchip.pos { background: rgba(194, 224, 124, 0.55); color: var(--ink); }
.tchip.warn { background: rgba(240, 214, 140, 0.6); color: #6b5a29; }
.tchip.ghost { background: transparent; box-shadow: inset 0 0 0 1px var(--line); color: var(--muted); font-weight: 600; }
.tk-sub {
  margin-top: 3px;
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
  font-size: 11.5px;
  line-height: 1.5;
  color: var(--muted);
}
.tk-sub .sep { width: 3px; height: 3px; border-radius: 50%; background: rgba(126, 143, 96, 0.55); }
.tk-tail { display: flex; align-items: center; padding-top: 2px; }
.tk-kcal {
  font-size: 12.5px;
  font-weight: 800;
  color: var(--ink);
  white-space: nowrap;
  font-variant-numeric: tabular-nums;
}
.tk-kcal small { margin-left: 2px; font-size: 9.5px; font-weight: 500; color: var(--muted); }
.tk-cta {
  height: 28px;
  padding: 0 10px;
  border: 0;
  border-radius: 9px;
  background: var(--teal);
  color: var(--ink);
  font-size: 11.5px;
  font-weight: 700;
  white-space: nowrap;
  box-shadow: 0 6px 14px -8px rgba(122, 152, 40, 0.9);
}
.tk-cta:active { transform: translateY(1px); }

/* ============ 按钮 ============ */
.btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
  min-height: 42px;
  padding: 0 16px;
  border-radius: 999px;
  border: 1px solid var(--glass-line);
  background: var(--glass-mint);
  -webkit-backdrop-filter: blur(16px) saturate(170%);
  backdrop-filter: blur(16px) saturate(170%);
  box-shadow: 0 6px 16px rgba(28, 36, 22, 0.08), inset 0 1px 0 rgba(255, 255, 255, 0.9);
  color: var(--teal-tx);
  font-size: 13px;
  font-weight: 700;
  transition: transform 0.16s ease, box-shadow 0.16s ease;
}
.btn .ico { width: 16px; height: 16px; }
.btn:active { transform: translateY(1px); }
.btn:disabled { opacity: 0.55; }
.btn-solid {
  background: var(--teal);
  border-color: var(--teal);
  color: #171a14;
  box-shadow: 0 10px 24px rgba(23, 26, 20, 0.22), inset 0 1px 0 rgba(255, 255, 255, 0.34);
}
.btn-row { display: flex; gap: 8px; margin-top: 14px; flex-wrap: wrap; }
.btn-row .btn { flex: 1; min-width: 0; }

/* ============ 饮水 ============ */
.w-top { display: flex; align-items: flex-start; justify-content: space-between; gap: 10px; }
.w-k { font-size: 11.5px; color: var(--muted); }
.w-num { display: flex; align-items: baseline; gap: 4px; margin-top: 2px; font-variant-numeric: tabular-nums; }
.w-num b { font-size: 28px; font-weight: 800; letter-spacing: -0.035em; }
.w-num i { font-style: normal; font-size: 11.5px; color: var(--muted); }
.w-pct {
  font-size: 11.5px;
  font-weight: 700;
  color: var(--teal-tx);
  background: var(--mint);
  padding: 3px 9px;
  border-radius: 999px;
}
.w-bar { height: 8px; margin-top: 12px; border-radius: 999px; background: #eef3dc; overflow: hidden; }
.w-bar span {
  display: block;
  height: 100%;
  border-radius: 999px;
  background: var(--teal);
  transition: width 0.24s ease;
}
.w-acts { display: flex; gap: 7px; margin-top: 12px; }
.w-acts .btn { flex: 1; min-width: 0; min-height: 38px; padding: 0 8px; font-size: 12.5px; }
.w-acts .btn.ghost {
  background: transparent;
  border-color: var(--line);
  color: var(--muted);
  box-shadow: none;
  flex: 0 0 auto;
  padding: 0 14px;
}

/* ============ 睡眠 ============ */
.sleep-num { display: flex; align-items: baseline; gap: 5px; font-variant-numeric: tabular-nums; }
.sleep-num b { font-size: 30px; font-weight: 800; letter-spacing: -0.035em; }
.sleep-num i { font-style: normal; font-size: 11.5px; color: var(--muted); }
.sleep-line { margin-top: 6px; font-size: 11.5px; color: var(--ink-2); }
.src-tag {
  font-size: 10.5px;
  font-weight: 700;
  color: var(--teal-tx);
  background: var(--mint);
  padding: 3px 9px;
  border-radius: 999px;
}
.sleep-tip {
  margin-top: 10px;
  padding: 9px 11px;
  border-radius: 12px;
  background: #f8faef;
  font-size: 11px;
  color: var(--muted);
  line-height: 1.55;
}

/* ============ 折叠条 ============ */
.fold { margin-top: 14px; }
.fold-bar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  width: 100%;
  padding: 14px 16px;
  border-radius: var(--r-lg);
  border: 1px solid var(--glass-line);
  background: var(--glass);
  -webkit-backdrop-filter: blur(16px) saturate(170%);
  backdrop-filter: blur(16px) saturate(170%);
  box-shadow: 0 6px 18px rgba(28, 36, 22, 0.06);
  font-size: 13.5px;
  font-weight: 700;
  color: var(--ink);
}
.fold-l { display: flex; align-items: center; gap: 9px; min-width: 0; }
.fold-l .ico { width: 18px; height: 18px; color: var(--teal-tx); flex: 0 0 18px; }
.fold-r {
  display: flex;
  align-items: center;
  gap: 4px;
  font-size: 11.5px;
  font-weight: 600;
  color: var(--muted);
  flex: 0 0 auto;
}
.fold-r .ico { width: 14px; height: 14px; transition: transform 0.2s ease; }
.fold.on .fold-r .ico { transform: rotate(180deg); }

.panel.on { animation: fade 0.22s ease both; }
@keyframes fade {
  from { opacity: 0; transform: translateY(-4px); }
  to { opacity: 1; transform: none; }
}

/* ============ 日历 ============ */
/* ============ 打卡日历（热力图） ============ */
.cal-panel {
  margin-top: 10px;
  background: var(--surface);
  border: 1px solid var(--line);
  border-radius: var(--r-lg);
  padding: 14px;
  box-shadow: var(--shadow);
}
.cal-head { display: flex; align-items: center; justify-content: space-between; gap: 10px; }
.cal-head b { font-size: 13.5px; font-weight: 800; font-variant-numeric: tabular-nums; }
.cal-nav {
  width: 30px;
  height: 30px;
  border-radius: 9px;
  border: 1px solid var(--line);
  background: #f9fbf0;
  color: var(--ink-2);
  display: grid;
  place-items: center;
  font-size: 14px;
  line-height: 1;
}
.cal-week {
  display: grid;
  grid-template-columns: repeat(7, minmax(0, 1fr));
  margin-top: 10px;
  gap: 5px;
  font-size: 10px;
  color: var(--muted);
  text-align: center;
}
.cal-grid { display: grid; grid-template-columns: repeat(7, minmax(0, 1fr)); gap: 5px; margin-top: 4px; }
.cal-cell {
  aspect-ratio: 1 / 1;
  border: none;
  border-radius: 9px;
  background: #fff;
  box-shadow: inset 0 0 0 1px var(--line);
  display: grid;
  place-items: center;
  font-size: 11.5px;
  font-weight: 600;
  color: var(--ink-2);
  font-variant-numeric: tabular-nums;
  padding: 0;
  transition: transform 0.12s ease;
}
.cal-cell:active { transform: scale(0.93); }
.cal-cell.empty { visibility: hidden; }
.cal-cell.l1 { background: #e9f3cc; color: var(--ink); box-shadow: none; }
.cal-cell.l2 { background: #d5e9a0; color: var(--ink); box-shadow: none; }
.cal-cell.l3 {
  background: linear-gradient(160deg, var(--teal), var(--teal-dk));
  color: var(--ink);
  font-weight: 800;
  box-shadow: 0 6px 14px -10px rgba(122, 152, 40, 0.9);
}
.cal-cell.future { opacity: 0.42; }
.cal-cell.today { outline: 2px solid rgba(122, 152, 40, 0.55); outline-offset: 1px; }
.cal-cell.sel { outline: 2px solid rgba(23, 26, 20, 0.35); outline-offset: 1px; }
.cal-legend {
  display: flex;
  align-items: center;
  gap: 5px;
  margin-top: 11px;
  font-size: 10px;
  color: var(--muted);
}
.cal-legend i { width: 10px; height: 10px; border-radius: 3px; display: block; box-shadow: inset 0 0 0 1px var(--line); background: #fff; }
.cal-legend i.l1 { background: #e9f3cc; box-shadow: none; }
.cal-legend i.l2 { background: #d5e9a0; box-shadow: none; }
.cal-legend i.l3 { background: linear-gradient(160deg, var(--teal), var(--teal-dk)); box-shadow: none; }
.cal-legend-num { margin-left: auto; font-variant-numeric: tabular-nums; }
.cal-hint { margin-top: 8px; font-size: 10.5px; color: var(--muted); line-height: 1.6; }

/* ============ 系统自查 ============ */
.review-line {
  font-size: 11.5px;
  color: var(--ink-2);
  line-height: 1.6;
  padding: 7px 0;
  border-top: 1px solid var(--line);
}
.review-line:first-child { border-top: none; }
.recalc-box { margin-top: 10px; padding: 12px; border-radius: var(--r-md); background: var(--mint); }
.recalc-target { font-size: 12.5px; font-weight: 700; color: var(--teal-tx); font-variant-numeric: tabular-nums; }
.recalc-target i { font-style: normal; font-weight: 400; font-size: 10.5px; color: var(--muted); }
.recalc-why { margin-top: 4px; font-size: 11px; color: var(--ink-2); line-height: 1.6; }
.recalc-box .btn { margin-top: 10px; min-height: 38px; font-size: 12.5px; }
.review-foot { margin-top: 10px; font-size: 10.5px; color: var(--muted); line-height: 1.6; }

/* ============ 空态 / 骨架 ============ */
.empty { padding: 10px 0 2px; text-align: center; }
.empty-t { font-size: 13.5px; font-weight: 700; color: var(--ink-2); }
.empty-s { font-size: 11.5px; color: var(--muted); margin-top: 4px; line-height: 1.55; }
.skeleton { display: flex; flex-direction: column; gap: 10px; padding: 4px 0; }
.sk-row {
  display: block;
  height: 38px;
  border-radius: 12px;
  background: linear-gradient(90deg, #f4f7ea 25%, #eef3dc 37%, #f4f7ea 63%);
  background-size: 400% 100%;
  animation: shine 1.3s ease infinite;
}
@keyframes shine {
  0% { background-position: 100% 50%; }
  100% { background-position: 0 50%; }
}

/* ============ 弹层 ============ */
.mask {
  position: fixed;
  inset: 0;
  z-index: 1200;
  background: rgba(23, 26, 20, 0.42);
  opacity: 0;
  pointer-events: none;
  transition: opacity 0.22s ease;
}
.mask.on { opacity: 1; pointer-events: auto; }

.sheet {
  position: fixed;
  left: 50%;
  bottom: 0;
  z-index: 1201;
  width: min(100%, 460px);
  max-height: 86vh;
  overflow-y: auto;
  transform: translate(-50%, 102%);
  transition: transform 0.26s cubic-bezier(0.32, 0.72, 0, 1);
  background: var(--paper);
  border-radius: 26px 26px 0 0;
  padding: 10px 18px calc(18px + env(safe-area-inset-bottom, 0px));
  box-shadow: 0 -12px 40px rgba(23, 26, 20, 0.22);
}
.sheet.on { transform: translate(-50%, 0); }
.sheet-grab { width: 38px; height: 4px; border-radius: 999px; background: #dfe6cb; margin: 0 auto 12px; }
.sheet h3 { font-size: 16px; font-weight: 800; letter-spacing: -0.02em; }
.sheet-tip { margin-top: 4px; font-size: 11.5px; color: var(--muted); line-height: 1.6; }
.sheet-body { margin-top: 14px; display: flex; flex-direction: column; gap: 12px; }

.field { display: flex; flex-direction: column; gap: 6px; }
.field > span { font-size: 12px; font-weight: 700; color: var(--ink-2); }
.field input {
  width: 100%;
  height: 44px;
  padding: 0 13px;
  border-radius: 13px;
  border: 1px solid var(--line);
  background: #fff;
  font: inherit;
  font-size: 14px;
  color: var(--ink);
  outline: none;
}
.field input:focus { outline: 2px solid rgba(23, 26, 20, 0.35); outline-offset: -1px; }
.time-row { display: flex; align-items: center; gap: 8px; }
.time-row input { flex: 1; min-width: 0; }
.time-sep { font-size: 12px; color: var(--muted); flex: 0 0 auto; }

.seg { display: flex; gap: 4px; padding: 4px; border-radius: 14px; background: var(--mint); }
.seg button {
  flex: 1;
  min-height: 36px;
  border: none;
  border-radius: 11px;
  background: transparent;
  color: var(--ink-2);
  font-size: 12.5px;
  font-weight: 700;
}
.seg button.on { background: rgba(23, 26, 20, 0.94); color: #fff; box-shadow: 0 4px 12px rgba(23, 26, 20, 0.26); }

.stars { display: flex; gap: 6px; }
.stars button {
  width: 44px;
  height: 44px;
  border-radius: 13px;
  border: 1px solid var(--line);
  background: #fff;
  color: #c3cdb4;
  display: grid;
  place-items: center;
}
.stars button.on { color: var(--teal-tx); border-color: var(--mint-2); background: var(--mint); }
.stars .ico { width: 20px; height: 20px; stroke-width: 1.4; fill: none; }
.stars button.on .ico { fill: currentColor; stroke: currentColor; }

.sheet-acts { display: flex; gap: 8px; margin-top: 16px; }
.sheet-acts .btn { flex: 1; }

.mode-list { display: flex; flex-direction: column; gap: 8px; }
.mode-item {
  text-align: left;
  border: 1px solid var(--line);
  background: #fff;
  border-radius: var(--r-md);
  padding: 11px 13px;
  display: flex;
  flex-direction: column;
  gap: 2px;
}
.mode-item b { font-size: 13.5px; color: var(--ink); }
.mode-item span { font-size: 11.5px; color: var(--muted); line-height: 1.5; }
.mode-item.normal { border-style: dashed; background: transparent; }
</style>
