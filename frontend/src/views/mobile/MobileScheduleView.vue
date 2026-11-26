<script setup lang="ts">
// 移动端「日程」：今日主卡（完成度圆环 + 5 项指标）+ 打卡日历 + 今日任务时间轴
// + 快捷记录（喝水/拍照记餐/记体重/记睡眠）+ 个人分析 + 睡眠与自查
// 视觉按《移动端五页演示》日程页落地；业务口径、接口调用与改造前完全一致
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import * as checkinApi from '@/api/checkin'
import type { CheckInItem, DayStat } from '@/api/checkin'
import * as dayApi from '@/api/day'
import * as planApi from '@/api/plan'
import * as wellnessApi from '@/api/wellness'
import * as profileApi from '@/api/profile'
import * as checkupApi from '@/api/checkup'
import type { CheckupAnalysis } from '@/api/checkup'
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

// 目标依据：基础代谢是算出来的（公式）还是体检测出来的，用户有权知道
const prescription = computed(() => {
  const t = summary.value?.targets as Record<string, any> | undefined
  return (t?.prescription || null) as
    | { bmr?: number | null; bmr_source?: string | null; tdee?: number | null }
    | null
})
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

// ---- 今日体检：整块可收起，跟日历一条栏就能收掉 ----
// 默认展开：这是这一页的正文；收起留给「今天只想过日子、不想看诊断」的时候
const checkOpen = ref(true)
const audit = computed(() => summary.value?.audit || null)
const auditFailed = computed(() => audit.value?.failed || [])
const auditWarnings = computed(() => audit.value?.warnings || [])
const auditPending = computed(() => audit.value?.pending || [])
/** 栏上的结论只给数量，名单就在下面一行一项，不用再复述一遍 */
const auditBrief = computed(() => {
  if (!audit.value) return ''
  if (auditFailed.value.length) return `${auditFailed.value.length} 项没达标`
  return audit.value.headline
})

// ---- 全部指标：数字与区间收在一个折叠区里，默认不展开 ----
const metricsOpen = ref(false)
const METRIC_ORDER = ['calorie', 'protein', 'fat', 'carb', 'fiber', 'sodium', 'sugar',
  'water', 'sleep', 'exercise', 'species']
const STATUS_TEXT: Record<string, string> = {
  in_range: '达标', under: '不足', over: '超标', in_progress: '进行中',
  no_data: '待记录', low: '略低', high: '略高',
}
const metricCls = (status?: string) =>
  status === 'in_range' ? 'ok'
    : status === 'low' || status === 'high' ? 'warn'
      : status === 'under' || status === 'over' ? 'bad' : ''
/** 三位数以上取整，两位数留一位小数：热量 1203 比 1202.8 好读，蛋白 66.4 又不能丢 */
function fmtNum(v: any) {
  if (v == null || v === '') return '—'
  const n = Number(v)
  if (!Number.isFinite(n)) return String(v)
  return Math.abs(n) >= 100 ? `${Math.round(n)}` : `${Math.round(n * 10) / 10}`
}
/** 体检表里的区间文案：min~max 带单位，只有单边就分号写 */
function metricRange(min?: number | null, max?: number | null, unit = '') {
  const u = unit ? ` ${unit}` : ''
  if (min != null && max != null) return `${fmtNum(min)}~${fmtNum(max)}${u}`
  if (max != null) return `≤${fmtNum(max)}${u}`
  if (min != null) return `≥${fmtNum(min)}${u}`
  return '—'
}
const allMetrics = computed(() => {
  const rows = new Map<string, any>()
  // 体检项是权威口径（in_progress 等状态只有它有），先放进来
  for (const it of audit.value?.items || []) {
    rows.set(it.key, {
      key: it.key, label: it.label, value: fmtNum(it.actual), unit: it.actual == null ? '' : it.unit,
      range: metricRange(it.min, it.max, it.unit), text: STATUS_TEXT[it.status] || '', cls: metricCls(it.status),
    })
  }
  // 脂肪/碳水只在结构项里（不进九项），补在这张表里，别让「脂肪超标」无处可查
  for (const st of summary.value?.diet?.structure || []) {
    if (rows.has(st.key)) continue
    rows.set(st.key, {
      key: st.key, label: st.label, value: fmtNum(st.value), unit: st.unit,
      range: st.range_text || '', text: STATUS_TEXT[st.status] || st.status_text || '',
      cls: metricCls(st.status),
    })
  }
  // 睡眠/运动这类「还没记录」的项在体检清单里没有条目，从区间补进来，做到九项齐全
  for (const b of bands.value) {
    if (rows.has(b.key)) continue
    rows.set(b.key, {
      key: b.key, label: b.label, value: fmtNum(b.actual), unit: b.actual == null ? '' : b.unit,
      range: b.range_text || '', text: b.status_text || STATUS_TEXT[b.status] || '',
      cls: metricCls(b.status),
    })
  }
  return METRIC_ORDER.filter((k) => rows.has(k)).map((k) => rows.get(k))
})

/** 不在九项里的超限（脂肪/碳水）：一行说清，不再单开一张卡 */
const portionText = computed(() => {
  const d = summary.value?.diet
  if (!d) return ''
  const bits: string[] = []
  const fat = (d.structure || []).find((i) => i.key === 'fat' && i.status === 'over')
  if (fat) {
    const over = fat.max != null ? (fat.value || 0) - fat.max : 0
    bits.push(`脂肪多 ${fmtNum(over)} g`)
  }
  const p = d.portion
  if (p?.significant && p.delta_kcal > 0) bits.push(`多吃 ${Math.round(p.delta_kcal)} kcal`)
  if (!bits.length) return ''
  const tail = p?.significant ? '下餐主食减半碗就拉平' : '明天照常吃，不用补偿'
  return `${bits.join('、')}——${tail}`
})

// ---- 后续调整：系统这轮把后面几天改成了什么样（已生效就能撤销）----
const nextPlan = computed(() => summary.value?.next_plan || null)
const nextActions = computed(() => nextPlan.value?.actions || [])
const nextDays = computed(() => Array.from(new Set(nextActions.value.map((a) => a.day))))
const revertingAll = ref(false)

/** 同一条动作会落在好几天上（「明后两天各下调」），按动作合并成一行，别重复三遍 */
const groupedActions = computed(() => {
  const merged = new Map<string, { days: string[]; action: string; reason: string }>()
  for (const a of nextActions.value) {
    const hit = merged.get(a.action)
    if (hit) {
      if (!hit.days.includes(a.day)) hit.days.push(a.day)
    } else {
      merged.set(a.action, { days: [a.day], action: a.action, reason: a.reason })
    }
  }
  return Array.from(merged.values())
})

function dayShort(d: string) {
  return (d || '').slice(5).replace('-', '/')
}

function dayRange(days: string[]) {
  if (!days.length) return ''
  if (days.length === 1) return dayShort(days[0])
  return `${dayShort(days[0])}–${dayShort(days[days.length - 1])}`
}

const nextOpen = ref(false)
const nextSummary = computed(() => groupedActions.value.map((a) => a.action).join(' · '))
/** 调整有时从今天就开始（饮水/运动当天生效），说「后 3 天」会让人以为今天没改 */
const nextDayLabel = computed(() => {
  const days = nextDays.value
  if (!days.length) return ''
  return days[0] === selected.value ? `今起 ${days.length} 天` : `后 ${days.length} 天`
})

// ---- 待处置偏离：只有「系统真能替你做点什么」才占位置 ----
// 九项体检已经说了缺多少、现在做什么；这里只补可点的动作，和「关掉提醒」的出口。
type DevGroup = dayApi.DayState['deviations'][number]
const NON_ACTION_KINDS = new Set(['no_action', 'fix_plan', 'ask_reason'])
const actionGroups = computed(() =>
  openDeviations.value.filter((g) => g.options.some((o) => !NON_ACTION_KINDS.has(o.kind))))
/** 已经在体检清单 / 结构行里讲过的指标，不许再冒一句「这次不用处理」——同一件事说两遍还互相打脸 */
const coveredKeys = computed(() => {
  const rows = new Set<string>()
  for (const it of audit.value?.items || []) rows.add(it.key)
  for (const st of summary.value?.diet?.structure || []) rows.add(st.key)
  if (portionText.value) {
    rows.add('fat')
    rows.add('calorie')
  }
  return rows
})
const minorGroups = computed(() =>
  openDeviations.value.filter(
    (g) =>
      !g.options.some((o) => !NON_ACTION_KINDS.has(o.kind)) &&
      !coveredKeys.value.has(g.deviation.nutrient || ''),
  ),
)
const minorNames = computed(() =>
  minorGroups.value.map((g) => g.deviation.label).filter(Boolean).join('、'))
/** 一行最多三个动作，推荐项排前面；其余收进「其他做法」 */
function pickOptions(g: DevGroup) {
  return [...g.options]
    .sort((a, b) => Number(b.recommended) - Number(a.recommended))
    .slice(0, 3)
}
const detailDev = ref<number | null>(null)
const dismissing = ref(false)
/** 低优先偏离一次关掉：留着一整排「这次不处置」，比不提醒还吵 */
async function dismissMinor() {
  dismissing.value = true
  try {
    for (const g of minorGroups.value) {
      const opt = g.options.find((o) => o.kind === 'no_action') || g.options[0]
      if (opt) await dayApi.applyDisposition(g.deviation.id, opt.kind, opt.payload || {})
    }
    ElMessage.success('已记下，不再提醒')
    await Promise.all([load(), loadDayState()])
  } catch {
    /* 拦截器已提示 */
  } finally {
    dismissing.value = false
  }
}

/** 偏离量的单位：钠是 mg，别写成 g（734 g 钠看着像能要人命） */
function nutrientUnit(nutrient?: string) {
  if (nutrient === 'calorie') return ' kcal'
  if (nutrient === 'sodium') return ' mg'
  return ' g'
}

/** 一次撤销这轮调整涉及的所有天：只撤一天会让后面几天的目标对不上 */
async function doRevertAll() {
  revertingAll.value = true
  try {
    for (const d of nextDays.value) {
      await wellnessApi.revertAdapt(d)
    }
    ElMessage.success('已撤销这次自动调整，方案恢复原样')
    await load()
  } catch {
    /* 拦截器已提示 */
  } finally {
    revertingAll.value = false
  }
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

/** 当天状态：full 全满 / ok 达标 / low 偏少 / miss 缺卡
 *
 * 四档而不是五档：月历接口只有 total（当天该打几项）与 done（打了几项），
 * 没有「热量超标」这一维，硬造一个红档就是假数据。等后端按月给出热量判定再补。
 */
function stateOf(stat?: DayStat): 'full' | 'ok' | 'low' | 'miss' {
  if (!stat?.total) return 'miss'
  const p = stat.pct ?? Math.round(((stat.done || 0) / stat.total) * 100)
  if (p >= 100) return 'full'
  if (p >= 70) return 'ok'
  if (p > 0) return 'low'
  return 'miss'
}

/** 本月达标天数 / 已过天数 */
const monthStat = computed(() => {
  const [y, m] = month.value.split('-').map(Number)
  const last = new Date(y, m, 0).getDate()
  const isCur = month.value === today.slice(0, 7)
  const upto = isCur ? Number(today.slice(8, 10)) : last
  const ok = monthDays.value.filter((d) => d.date.slice(8, 10) <= `${upto}`.padStart(2, '0') && stateOf(d) === 'full').length
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

// ---- 饮水（快捷行只保留 +200，详细进度在时间轴的饮水任务里看） ----
async function drink(ml: number) {
  if (ml <= 0) return
  const r = await wellnessApi.addWater(ml, selected.value)
  ElMessage.success(r.achieved ? `今日饮水达标 ${r.total_ml} ml 🎉` : `已喝 ${r.total_ml} / ${r.target_ml} ml`)
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
// ---- 个人分析（体检报告 → 一行结论，详情在 /m/analysis）----
const analysis = ref<CheckupAnalysis | null>(null)

async function loadAnalysis() {
  try {
    analysis.value = await checkupApi.getAnalysis()
  } catch {
    analysis.value = null
  }
}

function goAnalysis() {
  router.push('/m/analysis')
}

// ---- 记体重（回写档案，方案目标会跟着变）----
const weightOpen = ref(false)
const weightForm = ref<number | null>(null)
const weightSaving = ref(false)

function openWeight() {
  weightForm.value = analysis.value?.profile.weight_kg ?? null
  weightOpen.value = true
}

async function submitWeight() {
  const w = Number(weightForm.value)
  if (!Number.isFinite(w) || w < 20 || w > 300) {
    ElMessage.warning('请填写正确的体重')
    return
  }
  weightSaving.value = true
  try {
    const r = await profileApi.addWeight({ weight_kg: w })
    ElMessage.success(r.points_awarded ? `已记录 ${w} kg，+${r.points_awarded} 积分` : `已记录 ${w} kg`)
    weightOpen.value = false
    weightForm.value = w
    await load()
    await loadAnalysis()
  } finally {
    weightSaving.value = false
  }
}

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
  scale: [
    'M5 4h14a2 2 0 0 1 2 2v12a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2z',
    'M8 16a4 4 0 0 1 8 0',
    'M12 16l2.2-2.4',
  ],
  chevron: ['M6 9.5l6 6 6-6'],
}

onMounted(() => {
  load()
  loadAnalysis()
})
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

      <!-- ===== 今日体检：一条栏即可收起；收起时仍留着「几项没达标」的结论 ===== -->
      <div v-if="audit || bands.length" class="fold" :class="{ on: checkOpen }">
        <button class="fold-bar" :aria-expanded="checkOpen" @click="checkOpen = !checkOpen">
          <span class="fold-l">
            <svg class="ico" viewBox="0 0 24 24" aria-hidden="true">
              <path d="M12 3.8a8.2 8.2 0 1 0 0 16.4 8.2 8.2 0 0 0 0-16.4z" />
              <path d="M12 7.7v4.8l3.2 1.9" />
            </svg>
            今日体检
            <i class="fold-num" :class="{ bad: auditFailed.length }">{{ auditBrief }}</i>
          </span>
          <span class="fold-r">
            {{ checkOpen ? '收起' : '展开' }}
            <svg class="ico" viewBox="0 0 24 24" aria-hidden="true"><path d="M6 9.5l6 6 6-6" /></svg>
          </span>
        </button>
        <div v-show="checkOpen" class="panel on">
          <div class="card check-card">
            <!-- 当日模式：聚餐/生病/出差…只改今天的判定口径，第二天自动恢复 -->
            <div class="ck-top">
              <span v-if="currentMode" class="ck-mode-note">
                今天按「{{ currentMode.label }}」判<em v-if="currentMode.note_text">：{{ currentMode.note_text }}</em>
              </span>
              <span v-else class="ck-mode-idle">今天按常规口径判</span>
              <button class="ck-mode-btn" @click="modePickerOpen = true">
                {{ currentMode ? '换一个' : '有特殊情况？' }}
              </button>
            </div>

            <!-- 没达标：一项两行——差多少、现在做什么 -->
            <div v-for="item in auditFailed" :key="item.key" class="ck-item" :class="item.severity">
              <div class="ck-row">
                <i class="ck-dot"></i>
                <b class="ck-name">{{ item.label }}</b>
                <span class="ck-gap">{{ item.gap_text }}</span>
              </div>
              <p v-if="item.now" class="ck-tip">{{ item.now }}</p>
            </div>

            <!-- 还没吃完的项：只解释为什么不判，不给建议 -->
            <p v-if="auditPending.length" class="ck-note">
              待吃完：{{ auditPending.map((i) => i.label).join('、') }}，打卡后再判，现在不算没达标
            </p>
            <p v-else-if="audit?.ok" class="ck-note ok">{{ audit?.headline }}</p>
            <p v-if="!audit" class="ck-note">今天还没有可判定的记录</p>

            <!-- 系统能替你做的那几个动作，点一下才落地；不做也是合法选项 -->
            <div v-for="g in actionGroups" :key="g.deviation.id" class="ck-item ck-item-act">
              <div class="ck-row">
                <i class="ck-dot"></i>
                <b class="ck-name">{{ g.deviation.label }}</b>
                <span class="ck-gap">
                  {{ g.deviation.kind === 'over' ? '超' : '少' }}
                  {{ Math.abs(g.deviation.delta || 0) }}{{ nutrientUnit(g.deviation.nutrient) }}
                </span>
              </div>
              <div class="ck-dev">
                <div class="ck-chips">
                  <button
                    v-for="opt in pickOptions(g)"
                    :key="opt.kind"
                    class="ck-chip"
                    :class="{ rec: opt.recommended, unsafe: !opt.safe }"
                    :disabled="applyingKind === `${g.deviation.id}:${opt.kind}`"
                    @click="runOption(g.deviation.id, opt)"
                  >{{ opt.label }}</button>
                  <button
                    class="ck-chip more"
                    @click="detailDev = detailDev === g.deviation.id ? null : g.deviation.id"
                  >{{ detailDev === g.deviation.id ? '收起' : '其他做法' }}</button>
                </div>
                <div v-if="detailDev === g.deviation.id" class="ck-dev-detail">
                  <div v-for="opt in g.options" :key="opt.kind" class="ck-dd">
                    <b>{{ opt.label }}</b>
                    <p>{{ opt.detail }}</p>
                    <em v-for="(sf, i) in opt.safety" :key="i">{{ sf }}</em>
                    <button @click="runOption(g.deviation.id, opt)">用这个</button>
                  </div>
                </div>
              </div>
            </div>

            <!-- 脂肪/碳水这类不进九项的偏差：一行说完 -->
            <p v-if="portionText" class="ck-note">{{ portionText }}</p>
            <p v-if="auditWarnings.length" class="ck-note">
              略偏：{{ auditWarnings.map((i) => i.label).join('、') }}，问题不大
            </p>

            <!-- 低优先偏离：留一整排「这次不处置」比不提醒还吵，收成一行 -->
            <p v-if="minorGroups.length" class="ck-note">
              {{ minorNames }}这次不用处理
              <button class="ck-link" :disabled="dismissing" @click="dismissMinor">知道了</button>
            </p>

            <!-- 后续调整：一句话摘要就够，原因点开再看；已生效才给撤销 -->
            <div v-if="nextActions.length" class="ck-next">
              <div class="ck-next-head" @click="nextOpen = !nextOpen">
                <span class="ck-check">✓</span>
                <b class="ck-next-label">{{ nextPlan?.applied ? `${nextDayLabel}已调整` : `准备调整${nextDayLabel}` }}</b>
                <span class="ck-next-tools">
                  <span class="ck-caret">{{ nextOpen ? '收起' : '为什么' }}</span>
                  <span v-if="nextPlan?.applied" class="ck-caret danger" @click.stop="doRevertAll">撤销</span>
                </span>
              </div>
              <p class="ck-next-text">{{ nextSummary }}</p>
              <div v-if="nextOpen" class="ck-next-why">
                <p v-for="(a, i) in groupedActions" :key="i">
                  <b>{{ dayRange(a.days) }} · {{ a.action }}</b>——{{ a.reason }}
                </p>
                <p v-for="(n, i) in nextPlan?.notes || []" :key="'n' + i">{{ n }}</p>
              </div>
            </div>

            <!-- 全部指标：数字和区间都在这里，默认收起，不跟上面的结论抢版面 -->
            <div class="mx-toggle" @click="metricsOpen = !metricsOpen">
              <span>全部指标明细</span>
              <span class="ck-caret">{{ metricsOpen ? '收起' : '展开' }}</span>
            </div>
            <div v-if="metricsOpen" class="mx-list">
              <div v-for="m in allMetrics" :key="m.key" class="mx-row" :class="m.cls">
                <span class="mx-name">{{ m.label }}</span>
                <span class="mx-val">{{ m.value }}<em>{{ m.unit }}</em></span>
                <span class="mx-range">{{ m.range }}</span>
                <span class="mx-st">{{ m.text }}</span>
              </div>
              <p v-if="summary?.intake?.estimated" class="mx-foot">
                {{ summary.intake.note }}，记下实际吃了什么会更准。
              </p>
              <p class="mx-foot">
                区间为推荐值；黄色=在浮动容忍内也算达标，红色=明显偏离。口径见「计划 → 每日目标」。
              </p>
            </div>
          </div>
        </div>
      </div>

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
                    `s-${stateOf(c.stat)}`,
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
                  <span class="cc-day">{{ c.day || '' }}</span>
                  <span v-if="c.day && c.stat?.total" class="cc-num">{{ c.stat.done }}/{{ c.stat.total }}</span>
                </button>
              </div>
              <div class="cal-legend">
                <span class="cl-item"><i class="s-full"></i>全满</span>
                <span class="cl-item"><i class="s-ok"></i>达标</span>
                <span class="cl-item"><i class="s-low"></i>偏少</span>
                <span class="cl-item"><i class="s-miss"></i>缺卡</span>
              </div>
              <p class="cal-hint">点日期可切到当天</p>
            </div>
          </div>
        </div>
      </div>

      <!-- ===== 快捷记录：四个入口一行，不占整块卡片 ===== -->
      <div class="quick-acts">
        <button class="qa" @click="drink(200)">
          <svg class="ico" viewBox="0 0 24 24" aria-hidden="true">
            <path v-for="(d, i) in ICON_PATHS.drop" :key="i" :d="d" />
          </svg>
          <span>喝水 +200</span>
        </button>
        <button class="qa" @click="router.push('/recognition')">
          <svg class="ico" viewBox="0 0 24 24" aria-hidden="true">
            <path v-for="(d, i) in ICON_PATHS.cam" :key="i" :d="d" />
          </svg>
          <span>拍照记餐</span>
        </button>
        <button class="qa" @click="openWeight">
          <svg class="ico" viewBox="0 0 24 24" aria-hidden="true">
            <path v-for="(d, i) in ICON_PATHS.scale" :key="i" :d="d" />
          </svg>
          <span>记体重</span>
        </button>
        <button class="qa" @click="openSleep">
          <svg class="ico" viewBox="0 0 24 24" aria-hidden="true">
            <path v-for="(d, i) in ICON_PATHS.moon" :key="i" :d="d" />
          </svg>
          <span>记睡眠</span>
        </button>
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

      <!-- ===== 个人分析：只放一行结论，详情另开页 ===== -->
      <section class="card ana" role="button" tabindex="0" @click="goAnalysis" @keydown.enter.prevent="goAnalysis">
        <div class="card-head">
          <h2>个人分析</h2>
          <span class="head-tag">
            {{ analysis?.has_report ? '基于体检报告与近 30 天打卡' : '导入体检后按你的基线算' }}
          </span>
        </div>

        <template v-if="analysis?.has_report">
          <div class="ana-type">{{ analysis.type }}</div>
          <p class="ana-line">{{ analysis.headline }}</p>
          <div v-if="analysis.chips.length" class="ana-chips">
            <span v-for="c in analysis.chips" :key="c">{{ c }}</span>
          </div>
        </template>
        <p v-else class="ana-line">
          {{ analysis?.headline || '还没有体检报告。' }}
        </p>

        <div class="ana-foot">
          <span>
            {{ analysis?.has_report ? `最近体检 ${analysis.report?.check_date}` : '拍照 / PDF / 手动录入' }}
          </span>
          <b>{{ analysis?.has_report ? '查看分析' : '去导入' }} ›</b>
        </div>
      </section>

      <!-- ===== 睡眠与自查（折叠，原睡眠整卡收进来） ===== -->
      <div class="fold" :class="{ on: selfReviewOpen }">
        <button class="fold-bar" :aria-expanded="selfReviewOpen" @click="toggleSelfReview">
          <span class="fold-l">
            <svg class="ico" viewBox="0 0 24 24" aria-hidden="true">
              <path v-for="(d, i) in ICON_PATHS.moon" :key="i" :d="d" />
            </svg>
            睡眠与自查
            <i class="fold-num">{{ sleep?.duration_h ? `${sleep.duration_h.toFixed(1)} h` : '未记录' }}</i>
          </span>
          <span class="fold-r">
            {{ selfReviewOpen ? '收起' : '展开' }}
            <svg class="ico" viewBox="0 0 24 24" aria-hidden="true"><path d="M6 9.5l6 6 6-6" /></svg>
          </span>
        </button>
        <div v-show="selfReviewOpen" class="panel on">
          <!-- 睡眠一行：原来单独一张卡，现在收进折叠里 -->
          <div class="sleep-mini">
            <div class="sm-l">
              <b>{{ sleep?.duration_h ? sleep.duration_h.toFixed(1) : '—' }}</b><i>小时</i>
              <em v-if="sleepBand">推荐 {{ sleepBand.range_text }} h</em>
            </div>
            <div class="sm-r">
              <span v-if="sleep">
                {{ sleep.bedtime }} 入睡 · {{ sleep.wake_at }} 起床
                <template v-if="sleep.quality_score"> · 质量 {{ sleep.quality_score }}/5</template>
                <template v-if="sleep.source"> · {{ sleep.source === 'manual' ? '手动' : '手环' }}</template>
              </span>
              <span v-else>还没记录昨晚睡眠</span>
            </div>
            <button class="sm-btn" @click="openSleep">{{ sleep ? '修改' : '记录' }}</button>
          </div>
          <p v-if="sleepTip" class="sleep-tip">{{ sleepTip }}</p>
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
      <!-- 目标从哪来的：体检测过的基础代谢优先，否则是公式估算 -->
      <div v-if="prescription?.bmr" class="basis-line">
        <span class="bl-label">基础代谢</span>
        <b>{{ prescription.bmr }} kcal</b>
        <em class="bl-src" :class="{ on: prescription.bmr_source === 'measured' }">
          {{ prescription.bmr_source === 'measured' ? '体检实测' : '公式估算' }}
        </em>
      </div>
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

    <!-- ===== 弹层：记体重 ===== -->
    <div class="mask" :class="{ on: weightOpen }" @click="weightOpen = false"></div>
    <div class="sheet" :class="{ on: weightOpen }" role="dialog" aria-modal="true" aria-label="记体重">
      <div class="sheet-grab"></div>
      <h3>记体重</h3>
      <p class="sheet-tip">每天第一次记录 +2 积分。体重会同时更新方案的热量与蛋白目标。</p>
      <div class="sheet-body">
        <div class="field">
          <span>体重（kg）</span>
          <input
            v-model.number="weightForm"
            type="number"
            inputmode="decimal"
            step="0.1"
            min="20"
            max="300"
            placeholder="例如 45.0"
          />
        </div>
      </div>
      <div class="sheet-acts">
        <button class="btn" @click="weightOpen = false">取消</button>
        <button class="btn btn-solid" :disabled="weightSaving" @click="submitWeight">
          {{ weightSaving ? '保存中…' : '保存' }}
        </button>
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

/* 目标依据：基础代谢来自体检还是公式 */
.basis-line {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 8px;
  padding: 10px 14px;
  border-radius: 14px;
  background: var(--mint);
  font-size: 13px;
  color: var(--ink);
}
.basis-line b { font-weight: 500; }
.bl-label { color: #5f6b4e; }
.bl-src {
  margin-left: auto;
  padding: 2px 9px;
  border-radius: 999px;
  background: rgba(255, 255, 255, 0.75);
  color: #5f6b4e;
  font-style: normal;
  font-size: 11px;
}
.bl-src.on { background: var(--teal); color: #2f3a20; }

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

/* ============ 快捷记录（喝水 / 拍照记餐 / 记体重 / 记睡眠） ============ */
.quick-acts {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 8px;
  margin-top: 12px;
}
.qa {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 6px;
  min-height: 62px;
  padding: 10px 4px;
  border: 1px solid var(--glass-line);
  border-radius: 18px;
  background: var(--mint);
  color: var(--ink);
  font-size: 11px;
  font-weight: 700;
  line-height: 1.2;
  cursor: pointer;
  transition: transform 0.15s ease, background 0.15s ease;
}
.qa .ico { width: 20px; height: 20px; color: var(--ink-2); flex: 0 0 20px; }
.qa:active { transform: scale(0.95); background: var(--mint-2); }

/* ============ 个人分析 ============ */
.ana { cursor: pointer; }
.ana-type {
  margin-top: 8px;
  font-size: 20px;
  font-weight: 800;
  letter-spacing: -0.03em;
}
.ana-line {
  margin-top: 6px;
  font-size: 12px;
  color: var(--ink-2);
  line-height: 1.62;
}
.ana-chips { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 10px; }
.ana-chips span {
  font-size: 10.5px;
  font-weight: 700;
  padding: 3px 9px;
  border-radius: 999px;
  background: var(--mint);
  color: var(--ink-2);
}
.ana-foot {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  margin-top: 12px;
  padding-top: 10px;
  border-top: 1px dashed var(--line);
  font-size: 11px;
  color: var(--muted);
}
.ana-foot b { color: var(--teal-tx); font-weight: 800; flex: 0 0 auto; }

/* ============ 睡眠（收进折叠里的一行） ============ */
.sleep-mini {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-top: 10px;
  padding: 12px 14px;
  border-radius: var(--r-md);
  background: var(--mint);
}
.sm-l { display: flex; align-items: baseline; gap: 3px; font-variant-numeric: tabular-nums; flex: 0 0 auto; }
.sm-l b { font-size: 22px; font-weight: 800; letter-spacing: -0.03em; }
.sm-l i { font-style: normal; font-size: 10.5px; color: var(--muted); }
.sm-l em { font-style: normal; margin-left: 4px; font-size: 9.5px; color: var(--muted); }
.sm-r { flex: 1; min-width: 0; font-size: 10.5px; color: var(--ink-2); line-height: 1.5; }
.sm-btn {
  flex: 0 0 auto;
  padding: 6px 12px;
  border: none;
  border-radius: 999px;
  background: var(--teal);
  color: var(--ink);
  font-size: 11px;
  font-weight: 800;
  cursor: pointer;
}
.sm-btn:active { background: var(--teal-dk); }
.sleep-tip {
  margin-top: 8px;
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
  border-radius: 11px;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 1px;
  padding: 0;
  background: #fff;
  color: var(--ink-2);
  font-variant-numeric: tabular-nums;
  transition: transform 0.12s ease;
}
.cal-cell:active { transform: scale(0.93); }
.cal-cell.empty { visibility: hidden; }
.cc-day { font-size: 15px; font-weight: 600; line-height: 1.1; }
.cc-num { font-size: 9.5px; line-height: 1; opacity: 0.7; letter-spacing: -0.2px; }

/* 状态色：整格填实色，颜色对比拉开，扫一眼能挑出没打满的日子 */
.cal-cell.s-full { background: #7fa83c; color: #1f2a10; }
.cal-cell.s-full .cc-num { opacity: 0.8; }
.cal-cell.s-ok { background: #c5de8f; color: #2f3a20; }
.cal-cell.s-low { background: #f0b563; color: #4a3113; }
.cal-cell.s-miss { background: #ecebe4; color: #a3a096; }

.cal-cell.future { opacity: 0.4; }
.cal-cell.today { outline: 2px solid rgba(23, 26, 20, 0.45); outline-offset: 1px; }
.cal-cell.sel { outline: 2px solid rgba(122, 152, 40, 0.6); outline-offset: 1px; }
.cal-legend {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-top: 11px;
  font-size: 10px;
  color: var(--muted);
}
.cl-item { display: inline-flex; align-items: center; gap: 4px; }
.cal-legend i { width: 10px; height: 10px; border-radius: 3px; display: block; }
.cal-legend i.s-full { background: #7fa83c; }
.cal-legend i.s-ok { background: #c5de8f; }
.cal-legend i.s-low { background: #f0b563; }
.cal-legend i.s-miss { background: #ecebe4; }
.cal-hint { margin-top: 8px; font-size: 10.5px; color: var(--muted); line-height: 1.6; text-align: center; }

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

/* ============ 今日体检（沿用本页 token） ============ */
.check-card { padding: 14px 16px; }
.ck-top {
  display: flex; align-items: center; gap: 8px;
  padding-bottom: 9px; margin-bottom: 2px; border-bottom: 1px solid var(--line);
}
.ck-mode-idle { font-size: 11.5px; color: var(--muted); }
.ck-mode-note { font-size: 11.5px; color: var(--ink-2); }
.ck-mode-note em { font-style: normal; color: var(--muted); }
.ck-mode-btn {
  margin-left: auto; border: none; background: none; padding: 0;
  font-size: 11.5px; color: var(--muted); cursor: pointer; text-decoration: underline;
}
.fold-num.bad { color: var(--red); }

.ck-item { padding: 9px 0 8px; border-bottom: 1px dashed var(--line); }
.ck-item:last-of-type { border-bottom: none; }
.ck-row { display: flex; align-items: baseline; gap: 6px; }
.ck-dot { width: 5px; height: 5px; border-radius: 50%; background: var(--amber); flex: none; align-self: center; }
.ck-item.high .ck-dot { background: var(--red); }
.ck-item.low .ck-dot { background: var(--muted); opacity: 0.5; }
.ck-name { font-size: 13px; font-weight: 600; color: var(--ink); }
.ck-gap { margin-left: auto; font-size: 11.5px; color: var(--amber); font-variant-numeric: tabular-nums; }
.ck-item.low .ck-gap { color: var(--muted); }
.ck-tip { margin: 3px 0 0 11px; font-size: 12px; line-height: 1.5; color: var(--ink-2); }
.ck-note { margin: 7px 0 0; font-size: 11.5px; line-height: 1.5; color: var(--muted); }
.ck-note.ok { color: #4f8a2b; }
.ck-link {
  border: none; background: none; padding: 0 0 0 4px;
  font-size: 11.5px; color: var(--ink-2); cursor: pointer; text-decoration: underline;
}
.ck-caret { font-size: 11px; color: var(--muted); flex: none; cursor: pointer; }
.ck-caret.danger { color: var(--red); }

/* 可点的处置：一排小按钮，点了才落地 */
.ck-item-act { padding-top: 8px; }
.ck-dev { margin: 6px 0 0 11px; }
.ck-chips { display: flex; flex-wrap: wrap; gap: 6px; }
.ck-chip {
  border: 1px solid var(--line); background: var(--mint); color: var(--ink);
  border-radius: 999px; padding: 3px 10px; font-size: 11.5px; cursor: pointer;
}
.ck-chip.rec { border-color: var(--mint-2); background: var(--mint-2); font-weight: 500; }
.ck-chip.unsafe { opacity: 0.62; }
.ck-chip.more { border-style: dashed; background: transparent; color: var(--muted); }
.ck-chip:disabled { opacity: 0.5; }
.ck-dev-detail {
  margin-top: 6px; padding-top: 6px; border-top: 1px dashed var(--line);
  display: flex; flex-direction: column; gap: 7px;
}
.ck-dd b { font-size: 11.5px; color: var(--ink); }
.ck-dd p { margin: 2px 0 0; font-size: 11px; line-height: 1.55; color: var(--muted); }
.ck-dd em { display: block; font-style: normal; font-size: 10.5px; color: var(--amber); line-height: 1.5; }
.ck-dd button {
  margin-top: 4px; border: 1px solid var(--line); background: var(--surface);
  border-radius: 999px; padding: 1px 10px; font-size: 11px; color: var(--ink-2); cursor: pointer;
}

/* 后续调整：一行摘要，原因点开再看 */
.ck-next { margin-top: 9px; padding-top: 8px; border-top: 1px solid var(--line); }
.ck-next-head { display: flex; align-items: center; gap: 5px; cursor: pointer; }
.ck-check { flex: none; font-size: 11.5px; color: #4f8a2b; }
.ck-next-label { font-size: 11.5px; font-weight: 500; color: var(--ink-2); }
.ck-next-tools { margin-left: auto; display: flex; gap: 8px; }
.ck-next-text { margin: 3px 0 0 12px; font-size: 11.5px; line-height: 1.5; color: var(--muted); }
.ck-next-why { margin: 5px 0 0 12px; }
.ck-next-why p { margin: 3px 0 0; font-size: 11px; line-height: 1.55; color: var(--muted); }
.ck-next-why b { font-weight: 500; color: var(--ink-2); }

/* 全部指标：折叠的明细表 */
.mx-toggle {
  margin-top: 9px; padding-top: 8px; border-top: 1px solid var(--line);
  display: flex; align-items: center; justify-content: space-between;
  font-size: 11.5px; color: var(--muted); cursor: pointer;
}
.mx-row { display: flex; align-items: baseline; gap: 6px; padding: 3px 0; font-size: 11.5px; }
.mx-name { width: 58px; flex: none; color: var(--ink-2); }
.mx-val { width: 72px; flex: none; color: var(--ink); font-variant-numeric: tabular-nums; }
.mx-val em { font-style: normal; font-size: 9.5px; color: var(--muted); margin-left: 3px; }
.mx-range { flex: 1; font-size: 10.5px; color: var(--muted); }
.mx-st { flex: none; font-size: 10.5px; color: var(--muted); }
.mx-row.ok .mx-st { color: #4f8a2b; }
.mx-row.warn .mx-st { color: var(--amber); }
.mx-row.bad .mx-st { color: var(--red); }
.mx-foot { margin: 5px 0 0; font-size: 10.5px; line-height: 1.55; color: var(--muted); }

</style>
