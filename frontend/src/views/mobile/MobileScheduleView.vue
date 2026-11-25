<script setup lang="ts">
// 移动端「日程」：当天目标条 + 任务打卡 + 食物/饮水/睡眠三个模块 + 打卡日历
// 食物与饮水合成一个独立模块，拍照识别是食物模块里的一个功能（计划页另有快捷入口）
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import * as checkinApi from '@/api/checkin'
import type { CheckInItem, DayStat } from '@/api/checkin'
import * as dayApi from '@/api/day'
import * as foodApi from '@/api/food'
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

const selected = ref(fmt(new Date()))
const items = ref<CheckInItem[]>([])
const summary = ref<DaySummary | null>(null)
const water = ref<{ total_ml: number; target_ml: number; achieved: boolean; records?: { id: number; ml: number }[] } | null>(null)
const sleep = ref<SleepRecord | null>(null)
const records = ref<any[]>([])
const foodSummary = ref<{ total_calories: number }>({ total_calories: 0 })
const loading = ref(true)
const busyId = ref<number | null>(null)

const dateLabel = computed(() => {
  const d = new Date(`${selected.value}T00:00:00`)
  const week = ['周日', '周一', '周二', '周三', '周四', '周五', '周六'][d.getDay()]
  return `${d.getMonth() + 1}月${d.getDate()}日 ${week}`
})
const pct = computed(() => summary.value?.tasks.pct || 0)

// 每日指标区间：热量/蛋白/饮水/睡眠/运动，都由后端 metric_bands 算好（区间 + 浮动容忍）
const bands = computed(() => summary.value?.bands || [])
const heroBands = computed(() =>
  ['calorie', 'protein', 'water', 'sleep', 'exercise']
    .map((k) => bands.value.find((b) => b.key === k))
    .filter(Boolean) as NonNullable<DaySummary['bands']>,
)
// 蛋白不足不算「错误」：菜已经在方案里排好，按方案吃完就够，只在汇总里灰字提示，不标红
const bandCls = (status?: string, key?: string) => {
  // 还有餐没打卡：只是没吃完，用中性灰，不用红色
  if (status === 'in_progress') return 'muted'
  if (key === 'protein' && status === 'under') return 'muted'
  return status === 'in_range' ? 'ok'
    : status === 'low' || status === 'high' ? 'warn'
      : status === 'under' || status === 'over' ? 'bad' : ''
}
const waterBand = computed(() => bands.value.find((b) => b.key === 'water'))
const sleepBand = computed(() => bands.value.find((b) => b.key === 'sleep'))

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

// ---- 今日体检：整块可收起，跟日历栏一样一条栏就能收掉 ----
// 默认展开：这是这一页的正文；收起留给「今天只想过日子、不想看诊断」的时候
const checkOpen = ref(true)
const audit = computed(() => summary.value?.audit || null)
const auditFailed = computed(() => audit.value?.failed || [])
const auditWarnings = computed(() => audit.value?.warnings || [])
const auditPending = computed(() => audit.value?.pending || [])
/** 标题右侧的结论：只给数量，名单就在下面一行一项，不用再复述一遍 */
const auditBrief = computed(() => {
  if (!audit.value) return ''
  if (auditFailed.value.length) return `${auditFailed.value.length} 项没达标`
  return audit.value.headline
})

// ---- 全部指标：数字与区间都收在一个折叠区里，默认不展开 ----
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
function rangeText(min?: number | null, max?: number | null, unit = '') {
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
      range: rangeText(it.min, it.max, it.unit), text: STATUS_TEXT[it.status] || '', cls: metricCls(it.status),
    })
  }
  // 脂肪/碳水只在结构项里（不进九项），补在这张表里，别让「脂肪超标」无处可查
  for (const s of summary.value?.diet?.structure || []) {
    if (rows.has(s.key)) continue
    rows.set(s.key, {
      key: s.key, label: s.label, value: fmtNum(s.value), unit: s.unit,
      range: s.range_text || '', text: STATUS_TEXT[s.status] || s.status_text || '',
      cls: metricCls(s.status),
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

/** 顶部只留「这天的判定口径被改过」这类真信息；普通结算口径在卡片里已经讲了，不重复 */
const heroNote = computed(() =>
  summary.value?.band_note || (audit.value ? '' : settlementInfo.value?.message || ''),
)

// ---- 后续调整：系统这轮把后面几天改成了什么样（已生效就能撤销）----
const nextPlan = computed(() => summary.value?.next_plan || null)
const nextActions = computed(() => nextPlan.value?.actions || [])
const nextDays = computed(() => Array.from(new Set(nextActions.value.map((a) => a.day))))
const revertingAll = ref(false)

/** 同一条动作会落在好几天上（"明后两天各下调"），按动作合并成一行，别重复三遍 */
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

// ---- 后续调整的一句话摘要 ----
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
  const s = new Set<string>()
  for (const it of audit.value?.items || []) s.add(it.key)
  for (const st of summary.value?.diet?.structure || []) s.add(st.key)
  if (portionText.value) {
    s.add('fat')
    s.add('calorie')
  }
  return s
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

async function load() {
  loading.value = true
  try {
    const [day, foods, sum, w, s] = await Promise.all([
      checkinApi.getDay(selected.value),
      foodApi.getFoodRecords(selected.value),
      wellnessApi.getDaySummary(selected.value),
      wellnessApi.getWater(selected.value),
      wellnessApi.getSleep(selected.value),
    ])
    items.value = day.items
    records.value = foods.records
    foodSummary.value = foods.summary
    summary.value = sum
    water.value = w
    sleep.value = s.sleep
    await Promise.all([loadMonth(), loadDayState()])
  } catch {
    // 拦截器已统一提示
  } finally {
    loading.value = false
  }
}

// ---- 打卡日历 ----
const calendarOpen = ref(false)
const month = ref(selected.value.slice(0, 7))
const monthDays = ref<DayStat[]>([])

const monthCells = computed(() => {
  const [y, m] = month.value.split('-').map(Number)
  const first = new Date(y, m - 1, 1)
  const daysInMonth = new Date(y, m, 0).getDate()
  const lead = first.getDay()
  const statMap = new Map(monthDays.value.map((d) => [d.date, d]))
  const cells: { date: string; day: number; stat?: DayStat }[] = []
  for (let i = 0; i < lead; i += 1) cells.push({ date: '', day: 0 })
  for (let d = 1; d <= daysInMonth; d += 1) {
    const key = `${y}-${`${m}`.padStart(2, '0')}-${`${d}`.padStart(2, '0')}`
    cells.push({ date: key, day: d, stat: statMap.get(key) })
  }
  return cells
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

/** 日历格子里的小点：取前 4 项任务看完成态（早/午/晚/加餐优先） */
function cellDots(stat?: DayStat) {
  return (stat?.tasks || []).slice(0, 4)
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

// ---- 任务 ----
function taskSub(it: any) {
  if (it.check_type === 'exercise') {
    const list = Array.isArray(it.detail) ? it.detail : it.detail ? [it.detail] : []
    if (list.length) return list.map((e: any) => `${e.type} ${e.duration_min} 分钟`).join(' + ')
    return [it.exercise_type, it.duration_min ? `${it.duration_min} 分钟` : ''].filter(Boolean).join(' · ') || '按计划运动'
  }
  if (it.task_key === 'sleep') {
    if (sleep.value?.duration_min) return `已睡 ${(sleep.value.duration_min / 60).toFixed(1)} 小时`
    const s = it.detail || {}
    return s.bedtime ? `计划 ${s.bedtime} 入睡 · ${s.wake_at} 起床` : '记录昨晚睡眠'
  }
  if (it.task_key === 'water') {
    return `已喝 ${water.value?.total_ml || 0} / ${water.value?.target_ml || it.detail?.water_ml || 2000} ml`
  }
  const dishes = it.detail?.dishes || []
  if (dishes.length) return dishes.map((d: any) => d.name).filter(Boolean).join('、')
  const meals = it.detail?.items
  if (Array.isArray(meals) && meals.length) return meals.map((d: any) => d.name).filter(Boolean).join('、')
  return it.detail?.meal_type ? '方案餐食' : ''
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

function openSleep() {
  const s = summary.value?.plan?.sleep || {}
  sleepForm.value = {
    bedtime: sleep.value?.bedtime || s.bedtime || '23:00',
    wakeAt: sleep.value?.wake_at || s.wake_at || '07:00',
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

// ---- 手动记一餐（食物打卡模块内）----
const showManual = ref(false)
const saving = ref(false)
const manual = ref({ dish_name: '', portion_g: 200, meal_type: 'lunch' })

async function submitManual() {
  if (!manual.value.dish_name.trim()) {
    ElMessage.warning('请先填写菜名')
    return
  }
  saving.value = true
  try {
    await foodApi.createFoodRecord({
      dish_name: manual.value.dish_name.trim(),
      portion_g: manual.value.portion_g,
      meal_type: manual.value.meal_type,
    })
    ElMessage.success('已记入当日饮食')
    showManual.value = false
    manual.value = { dish_name: '', portion_g: 200, meal_type: 'lunch' }
    await load()
  } finally {
    saving.value = false
  }
}

onMounted(load)
</script>

<template>
  <div class="m-page">
    <!-- 今日进度 + 目标条 -->
    <div class="hero">
      <div class="hero-top">
        <span class="date">{{ dateLabel }}</span>
        <span class="ratio">{{ summary?.tasks.done || 0 }}/{{ summary?.tasks.total || 0 }} 项</span>
      </div>
      <el-progress :percentage="pct" :stroke-width="8" :show-text="false" color="#fff" />
      <div class="hero-targets">
        <div v-for="b in heroBands" :key="b.key" class="ht" :class="bandCls(b.status, b.key)">
          <span>{{ b.label }}</span>
          <b>{{ b.actual != null ? b.actual : '—' }}</b>
          <em>{{ b.min }}~{{ b.max }}{{ b.unit === 'h' ? 'h' : '' }}</em>
        </div>
      </div>
      <p v-if="heroNote" class="hero-note">{{ heroNote }}</p>
    </div>

    <!-- 今日体检：一条栏就能收起，跟日历一致；收起时也留着「几项没达标」的结论 -->
    <section v-if="audit || bands.length" class="block">
      <div class="cal-bar ck-bar" @click="checkOpen = !checkOpen">
        <span class="ck-bar-title">
          今日体检
          <em class="ck-count" :class="{ bad: auditFailed.length }">{{ auditBrief }}</em>
        </span>
        <span class="ck-bar-tools">
          <span class="ck-mode" @click.stop="modePickerOpen = true">
            {{ currentMode ? currentMode.label : '有特殊情况？' }}
          </span>
          <span class="cal-toggle">{{ checkOpen ? '收起' : '展开' }}</span>
        </span>
      </div>
      <div v-if="checkOpen" class="card check-card">
        <!-- 当日模式：聚餐/生病/出差…只改今天的判定口径，第二天自动恢复 -->
        <p v-if="currentMode" class="ck-mode-note">
          今天按「{{ currentMode.label }}」判<em v-if="currentMode.note_text">：{{ currentMode.note_text }}</em>
        </p>

        <!-- 没达标：一项两行——差多少、现在做什么。后续统一在下面那一行讲，不再逐项重复 -->
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
                <em v-for="(s, i) in opt.safety" :key="i">{{ s }}</em>
                <button @click="runOption(g.deviation.id, opt)">用这个</button>
              </div>
            </div>
          </div>
        </div>

        <!-- 脂肪/碳水这类不进九项的偏差：一行说完，不再单开卡 -->
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
    </section>

    <!-- 日历入口 -->
    <div class="cal-bar" @click="calendarOpen = !calendarOpen">
      <span>📅 {{ month }} 打卡日历</span>
      <span class="cal-toggle">{{ calendarOpen ? '收起' : '展开' }}</span>
    </div>

    <div v-if="calendarOpen" class="calendar">
      <div class="cal-head">
        <el-button text size="small" @click="shiftMonth(-1)">‹</el-button>
        <b>{{ month }}</b>
        <el-button text size="small" @click="shiftMonth(1)">›</el-button>
      </div>
      <div class="cal-week">
        <span v-for="w in ['日', '一', '二', '三', '四', '五', '六']" :key="w">{{ w }}</span>
      </div>
      <div class="cal-grid">
        <div
          v-for="(c, i) in monthCells"
          :key="i"
          class="cal-cell"
          :class="{
            empty: !c.day,
            active: c.date === selected,
            plan: c.stat?.has_plan,
            part: (c.stat?.pct || 0) > 0 && (c.stat?.pct || 0) < 100,
            full: (c.stat?.pct || 0) >= 100,
          }"
          @click="pickDate(c.date)"
        >
          <span class="cal-day">{{ c.day || '' }}</span>
          <span v-if="cellDots(c.stat).length" class="cal-dots">
            <i
              v-for="(t, ti) in cellDots(c.stat)"
              :key="ti"
              :class="['dot', t.status === 'done' ? 'on' : 'off']"
            ></i>
          </span>
          <span v-else-if="c.stat?.has_plan" class="cal-plan-dot"></span>
        </div>
      </div>
      <p class="cal-hint">点某天看当日简约打卡计划；点可直接打卡</p>

      <!-- 当日简约打卡计划（在日历里看，也能直接打卡） -->
      <div v-if="items.length" class="day-plan">
        <div class="dp-head">
          <b>{{ dateLabel }} · 简约计划</b>
          <span>{{ summary?.tasks.done || 0 }}/{{ summary?.tasks.total || 0 }}</span>
        </div>
        <div class="dp-chips">
          <button
            v-for="it in items"
            :key="it.id"
            :class="['dp-chip', { on: it.status === 'done' }]"
            @click="markDone(it)"
          >
            <span class="dpc-label">{{ it.label }}</span>
            <span class="dpc-sub">{{ taskSub(it) || (it.task_key === 'exercise' ? '按计划运动' : '按计划完成') }}</span>
          </button>
        </div>
      </div>
      <p v-else class="cal-hint">这一天还没有任务</p>
    </div>

    <!-- 今日任务 -->
    <section class="block">
      <h3 class="block-title">
        打卡计划
        <em v-if="items.length">{{ summary?.tasks.done || 0 }}/{{ items.length }}</em>
      </h3>
      <div v-if="loading" class="skeleton">加载中…</div>
      <el-empty v-else-if="!items.length" description="这一天还没有任务，导入一份计划就能打卡" :image-size="70">
        <el-button type="success" size="small" :loading="quickImporting" @click="quickImport">
          一键导入进行中的方案
        </el-button>
        <el-button size="small" plain @click="router.push('/m/plan')">去计划页</el-button>
      </el-empty>
      <div v-for="it in items" v-else :key="it.id" class="task" :class="{ done: it.status === 'done' }">
        <div class="task-main">
          <div class="task-label">{{ it.label }}</div>
          <div v-if="taskSub(it)" class="task-sub">{{ taskSub(it) }}</div>
        </div>
        <el-button
          size="small"
          :type="it.status === 'done' ? 'success' : 'primary'"
          :plain="it.status === 'done'"
          :loading="busyId === it.id"
          @click="markDone(it)"
        >
          {{ it.status === 'done' ? '已完成' : '打卡' }}
        </el-button>
      </div>
    </section>

    <!-- 食物 + 饮水 打卡模块 -->
    <section class="block">
      <h3 class="block-title">饮食打卡</h3>
      <div class="card">
        <div class="row">
          <span>今日已记 {{ records.length }} 餐</span>
          <b>{{ Math.round(foodSummary.total_calories) }} kcal</b>
        </div>
        <div v-if="records.length" class="chips">
          <span v-for="r in records.slice(0, 4)" :key="r.id" class="chip">{{ r.dish_name }}</span>
          <span v-if="records.length > 4" class="chip muted">+{{ records.length - 4 }}</span>
        </div>
        <div class="actions">
          <el-button type="success" @click="router.push('/recognition')">📷 拍照识别热量</el-button>
          <el-button plain @click="showManual = true">手动记一餐</el-button>
        </div>
      </div>

      <!-- 饮水：与食物同属一个模块 -->
      <div class="card water-card">
        <div class="row">
          <span>💧 饮水</span>
          <b>{{ water?.total_ml || 0 }} / {{ waterBand?.range_text || `${water?.target_ml || 2000} ml` }}</b>
        </div>
        <el-progress
          :percentage="Math.min(100, Math.round(((water?.total_ml || 0) / (water?.target_ml || 2000)) * 100))"
          :stroke-width="8"
          :show-text="false"
          color="#409eff"
          style="margin-top: 8px"
        />
        <div class="water-actions">
          <el-button size="small" @click="drink(200)">+200</el-button>
          <el-button size="small" @click="drink(300)">+300</el-button>
          <el-button size="small" @click="drink(500)">+500</el-button>
          <el-button size="small" text :disabled="!water?.records?.length" @click="undoWater">撤销</el-button>
        </div>
      </div>
    </section>

    <!-- 睡眠打卡 -->
    <section class="block">
      <h3 class="block-title">睡眠打卡</h3>
      <div class="card">
        <div class="row">
          <span>昨晚睡眠</span>
          <b>{{ sleep?.duration_h ? `${sleep.duration_h.toFixed(1)} 小时` : '未记录' }}</b>
        </div>
        <div v-if="sleep" class="sleep-line">
          {{ sleep.bedtime }} 入睡 · {{ sleep.wake_at }} 起床
          <el-tag size="small" effect="plain" :type="sleep.source === 'manual' ? 'info' : 'success'">
            {{ sleep.source === 'manual' ? '手动' : '手环同步' }}
          </el-tag>
        </div>
        <div v-else-if="summary?.plan?.sleep" class="sleep-line">
          计划 {{ summary.plan.sleep.bedtime }} 入睡 · {{ summary.plan.sleep.wake_at }} 起床
          <span v-if="sleepBand">，推荐 {{ sleepBand.range_text }}</span>
        </div>
        <div v-if="summary?.plan?.sleep?.tips" class="sleep-tips">{{ summary.plan.sleep.tips }}</div>
        <div class="actions">
          <el-button type="success" @click="openSleep">{{ sleep ? '修改睡眠' : '记录睡眠' }}</el-button>
        </div>
      </div>
    </section>

    <!-- 手动记一餐 -->
    <el-dialog v-model="showManual" title="手动记一餐" width="86%" align-center>
      <el-form label-position="top">
        <el-form-item label="菜名">
          <el-input v-model="manual.dish_name" placeholder="例如：番茄炒蛋" />
        </el-form-item>
        <el-form-item label="份量（克）">
          <el-input-number v-model="manual.portion_g" :min="10" :max="1500" :step="50" style="width: 100%" />
        </el-form-item>
        <el-form-item label="餐次">
          <el-radio-group v-model="manual.meal_type">
            <el-radio-button value="breakfast">早餐</el-radio-button>
            <el-radio-button value="lunch">午餐</el-radio-button>
            <el-radio-button value="dinner">晚餐</el-radio-button>
            <el-radio-button value="snack">加餐</el-radio-button>
          </el-radio-group>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="showManual = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="submitManual">保存</el-button>
      </template>
    </el-dialog>

    <!-- 记录睡眠 -->
    <el-drawer v-model="sleepOpen" title="记录昨晚睡眠" direction="btt" size="56%">
      <div class="sleep-form">
        <div class="sf-row">
          <span>入睡</span>
          <el-input v-model="sleepForm.bedtime" placeholder="23:00" style="width: 100px" />
          <span>起床</span>
          <el-input v-model="sleepForm.wakeAt" placeholder="07:00" style="width: 100px" />
        </div>
        <div class="sf-row">
          <span>睡眠质量</span>
          <el-rate v-model="sleepForm.quality" :max="5" />
        </div>
        <div v-if="summary?.plan?.sleep" class="sf-plan">
          计划目标：{{ summary.plan.sleep.bedtime }}–{{ summary.plan.sleep.wake_at }}，
          至少 {{ summary.targets.sleep_min_h }} 小时
        </div>
        <el-button type="success" style="width: 100%; margin-top: 8px" :loading="sleepSaving" @click="saveSleep">
          保存并打卡
        </el-button>
      </div>
    </el-drawer>

    <!-- 系统自省：连续偏离要重算目标（而不是继续压摄入），处置有效性也能看见 -->
    <section class="block">
      <div class="review-bar" @click="toggleSelfReview">
        <span>🩺 系统自查</span>
        <span class="cal-toggle">{{ selfReviewOpen ? '收起' : '展开' }}</span>
      </div>
      <div v-if="selfReviewOpen && selfReview" class="card review-card">
        <p v-for="(line, i) in selfReview.lines" :key="i" class="review-line">{{ line }}</p>
        <p v-if="!selfReview.lines.length" class="review-line muted">
          最近没有连续偏离，也没有挂例外，口径和你的实际吃得对得上。
        </p>
        <div v-if="selfReview.proposal" class="recalc-box">
          <p class="recalc-target">
            每日目标 {{ selfReview.proposal.old_target }} → {{ selfReview.proposal.new_target }} kcal
            <em v-if="selfReview.proposal.bmr">（不低于基础代谢 {{ selfReview.proposal.bmr }}）</em>
          </p>
          <p class="recalc-why">{{ selfReview.proposal.reason }}</p>
          <el-button type="success" size="small" :loading="recalcBusy" @click="doRecalc">
            按实际数据重算
          </el-button>
        </div>
        <p class="review-foot">
          这是系统的自查，不是你的评分。采纳率低说明建议不实用，会换成「改餐食」而不是「加练」。
        </p>
      </div>
    </section>

    <!-- 当日模式选择 -->
    <el-dialog v-model="modePickerOpen" title="今天有什么特殊情况？" width="92%" style="max-width: 420px">
      <p class="mode-tip">只影响今天的判定口径，明天自动恢复；不是给自己找借口，是让标准符合现实。</p>
      <div class="mode-list">
        <button v-for="m in dayApi.MODE_OPTIONS" :key="m.value" class="mode-item" @click="chooseMode(m.value)">
          <b>{{ m.label }}</b>
          <span>{{ m.desc }}</span>
        </button>
        <button class="mode-item normal" @click="chooseMode('normal')">
          <b>恢复正常</b>
          <span>按平常的口径判，不做任何放宽</span>
        </button>
      </div>
    </el-dialog>
  </div>
</template>

<style scoped>
.m-page { display: flex; flex-direction: column; gap: 12px; }
.hero { background: linear-gradient(135deg, #67c23a, #95d475); border-radius: 14px; padding: 14px 16px; color: #fff; }
.hero-top { display: flex; justify-content: space-between; align-items: baseline; margin-bottom: 8px; }
.hero-top .date { font-size: 15px; font-weight: 600; }
.hero-top .ratio { font-size: 12px; opacity: 0.9; }
.hero-targets { display: grid; grid-template-columns: repeat(5, 1fr); gap: 2px; margin-top: 10px; }
.ht { display: flex; flex-direction: column; align-items: center; }
.ht span { font-size: 10.5px; opacity: 0.85; }
.ht b { font-size: 14px; }
.ht em { font-style: normal; font-size: 9px; opacity: 0.8; white-space: nowrap; }
.ht.bad b { color: #ffe0e0; }
.ht.warn b { color: #fff3c4; }
.hero-note { font-size: 11px; line-height: 1.5; margin: 8px 0 0; opacity: 0.9; }
/* 今日体检卡：一项两行，密度高但要能扫 */
.check-card { padding: 2px 12px 8px; }
.ck-count { font-style: normal; font-size: 11.5px; font-weight: 500; color: #8a94a6; margin-left: 0; }
/* 标题里的统计紧跟标题，别被 block-title em 的 margin-left:auto 甩到中间 */
.block-title .ck-count { margin-left: 0; }
.ck-count.bad { color: #c05a4a; }
.ck-mode { margin-left: auto; font-size: 11.5px; color: #409eff; white-space: nowrap; }
.ck-mode-note { margin: 6px 0 0; font-size: 11.5px; color: #409eff; line-height: 1.5; }
.ck-mode-note em { font-style: normal; color: #909399; }
.ck-item { padding: 9px 0 8px; border-bottom: 1px dashed #f0f2f5; }
.ck-item:last-of-type { border-bottom: none; }
.ck-row { display: flex; align-items: baseline; gap: 6px; }
.ck-dot { width: 5px; height: 5px; border-radius: 50%; background: #d9a441; flex: none; align-self: center; }
.ck-item.high .ck-dot { background: #d9534f; }
.ck-item.low .ck-dot { background: #c0bbae; }
.ck-name { font-size: 13px; font-weight: 600; color: #303133; }
.ck-gap { margin-left: auto; font-size: 11.5px; color: #a8702a; font-variant-numeric: tabular-nums; }
.ck-item.low .ck-gap { color: #9a9384; }
.ck-tip { margin: 3px 0 0 11px; font-size: 12px; line-height: 1.5; color: #606266; }
.ck-note { margin: 7px 0 0; font-size: 11.5px; line-height: 1.5; color: #909399; }
.ck-note.ok { color: #4f9d2f; }
.ck-link { border: none; background: none; padding: 0 0 0 4px; font-size: 11.5px; color: #409eff; cursor: pointer; }
.ck-caret { font-size: 11px; color: #2b6cb0; flex: none; cursor: pointer; }
.ck-caret.danger { color: #c05a4a; }
/* 今日体检栏：跟日历用同一套「一条栏 + 展开/收起」，收起时仍留着「几项没达标」 */
.ck-bar { margin-bottom: 6px; }
.ck-bar-title { display: flex; align-items: center; gap: 6px; font-size: 13.5px; font-weight: 600; color: #303133; }
.ck-bar-title .ck-count { font-weight: 500; }
.ck-bar-tools { display: flex; align-items: center; gap: 12px; }
.ck-bar-tools .ck-mode { margin-left: 0; }
.mode-tip { margin: 0 0 10px; font-size: 12px; color: #909399; line-height: 1.6; }
.mode-list { display: flex; flex-direction: column; gap: 8px; }
.mode-item {
  text-align: left; border: 1px solid #e4e7ed; background: #fff; border-radius: 10px;
  padding: 9px 12px; display: flex; flex-direction: column; gap: 2px; cursor: pointer;
}
.mode-item b { font-size: 13.5px; color: #303133; }
.mode-item span { font-size: 11.5px; color: #909399; line-height: 1.5; }
.mode-item.normal { border-style: dashed; }

/* ---- 可点的处置：一排小按钮，点了才落地 ---- */
.ck-item-act { padding-top: 8px; }
.ck-dev { margin: 6px 0 0 11px; }
.ck-chips { display: flex; flex-wrap: wrap; gap: 6px; }
.ck-chip {
  border: 1px solid #e0e6dd; background: #fafcf8; color: #4a6b3c;
  border-radius: 999px; padding: 3px 10px; font-size: 11.5px; cursor: pointer;
}
.ck-chip.rec { border-color: #b8dca2; background: #f0f9e9; color: #3f7c22; }
.ck-chip.unsafe { opacity: 0.62; }
.ck-chip.more { border-style: dashed; background: #fff; color: #909399; }
.ck-chip:disabled { opacity: 0.5; }
.ck-dev-detail {
  margin-top: 6px; padding-top: 6px; border-top: 1px dashed #eef0f2;
  display: flex; flex-direction: column; gap: 7px;
}
.ck-dd b { font-size: 11.5px; color: #303133; }
.ck-dd p { margin: 2px 0 0; font-size: 11px; line-height: 1.55; color: #909399; }
.ck-dd em { display: block; font-style: normal; font-size: 10.5px; color: #d08a1e; line-height: 1.5; }
.ck-dd button {
  margin-top: 4px; border: 1px solid #e4e7ed; background: #fff; border-radius: 999px;
  padding: 1px 10px; font-size: 11px; color: #4f9d2f; cursor: pointer;
}

/* ---- 后续调整：一行摘要，原因点开再看 ---- */
.ck-next { margin-top: 9px; padding-top: 8px; border-top: 1px solid #eef1f5; }
.ck-next-head { display: flex; align-items: center; gap: 5px; cursor: pointer; }
.ck-check { flex: none; font-size: 11.5px; color: #4f9d2f; }
.ck-next-label { font-size: 11.5px; font-weight: 500; color: #4a5568; }
.ck-next-tools { margin-left: auto; display: flex; gap: 8px; }
.ck-next-text { margin: 3px 0 0 12px; font-size: 11.5px; line-height: 1.5; color: #7b8794; }
.ck-next-why { margin: 5px 0 0 12px; }
.ck-next-why p { margin: 3px 0 0; font-size: 11px; line-height: 1.55; color: #909399; }
.ck-next-why b { font-weight: 500; color: #607089; }

/* ---- 全部指标：折叠的明细表 ---- */
.mx-toggle {
  margin-top: 9px; padding-top: 8px; border-top: 1px solid #eef1f5;
  display: flex; align-items: center; justify-content: space-between;
  font-size: 11.5px; color: #909399; cursor: pointer;
}
.mx-row { display: flex; align-items: baseline; gap: 6px; padding: 3px 0; font-size: 11.5px; }
.mx-name { width: 58px; flex: none; color: #606266; }
.mx-val { width: 72px; flex: none; color: #303133; font-variant-numeric: tabular-nums; }
.mx-val em { font-style: normal; font-size: 9.5px; color: #b0b3b8; margin-left: 3px; }
.mx-range { flex: 1; font-size: 10.5px; color: #a8abb2; }
.mx-st { flex: none; font-size: 10.5px; color: #909399; }
.mx-row.ok .mx-st { color: #4f9d2f; }
.mx-row.warn .mx-st { color: #c98a1b; }
.mx-row.bad .mx-st { color: #d94a4a; }
.mx-foot { margin: 5px 0 0; font-size: 10.5px; line-height: 1.55; color: #b0b3b8; }
.ht.muted b { color: rgba(255, 255, 255, 0.82); }

/* ---- 自省卡 ---- */
.review-bar {
  display: flex; align-items: center; justify-content: space-between;
  margin: 14px 2px 6px; font-size: 12.5px; color: #606266;
}
.review-card { padding: 10px 12px; }
.review-line { margin: 0 0 6px; font-size: 11.5px; line-height: 1.6; color: #606266; }
.review-line.muted { color: #a8abb2; }
.recalc-box { margin-top: 8px; padding: 9px 10px; border-radius: 8px; background: #f0f9eb; }
.recalc-target { margin: 0 0 4px; font-size: 12.5px; font-weight: 600; color: #529b2e; }
.recalc-target em { font-style: normal; font-weight: 400; font-size: 11px; color: #909399; }
.recalc-why { margin: 0 0 8px; font-size: 11.5px; color: #606266; line-height: 1.6; }
.review-foot { margin: 8px 0 0; font-size: 11px; color: #b0b3b8; line-height: 1.5; }
/* 蛋白不足：灰字即可，不标红（方案里已排好菜） */
.ht.muted b { color: rgba(255, 255, 255, 0.82); }
.cal-bar { display: flex; justify-content: space-between; align-items: center; background: #fff; border-radius: 10px; padding: 10px 12px; font-size: 13px; color: #606266; box-shadow: 0 1px 3px rgba(0, 0, 0, 0.05); }
.cal-toggle { font-size: 12px; color: #4f9d2f; }
.calendar { background: #fff; border-radius: 12px; padding: 10px 12px 6px; box-shadow: 0 1px 3px rgba(0, 0, 0, 0.05); }
.cal-head { display: flex; justify-content: space-between; align-items: center; font-size: 13px; color: #303133; }
.cal-week { display: grid; grid-template-columns: repeat(7, 1fr); margin-top: 6px; font-size: 11px; color: #a8abb2; text-align: center; }
.cal-grid { display: grid; grid-template-columns: repeat(7, 1fr); gap: 2px; margin-top: 4px; }
.cal-cell { min-height: 40px; border-radius: 8px; display: flex; flex-direction: column; align-items: center; justify-content: center; font-size: 12px; color: #606266; }
.cal-cell.empty { visibility: hidden; }
.cal-cell.plan { background: #f6faf2; }
.cal-cell.active { outline: 2px solid #67c23a; }
.cal-day { font-weight: 500; }
.cal-dot { font-size: 9.5px; color: #909399; }
.cal-cell.full .cal-dot { color: #4f9d2f; }
.cal-plan-dot { width: 4px; height: 4px; border-radius: 50%; background: #95d475; }
.cal-hint { font-size: 11px; color: #a8abb2; margin: 6px 0 2px; }
.cal-dots { display: flex; gap: 2px; margin-top: 2px; }
.dot { width: 4px; height: 4px; border-radius: 50%; display: inline-block; }
.dot.on { background: #67c23a; }
.dot.off { background: #dcdfe6; }
.cal-cell.part { background: #f7fbf3; }
.day-plan { margin-top: 8px; border-top: 1px dashed #eef0f2; padding-top: 8px; }
.dp-head { display: flex; justify-content: space-between; align-items: baseline; font-size: 12.5px; color: #303133; }
.dp-head span { font-size: 11px; color: #909399; }
.dp-chips { display: grid; grid-template-columns: repeat(2, 1fr); gap: 6px; margin-top: 8px; }
.dp-chip {
  text-align: left;
  border: 1px solid #eef0f2;
  background: #fafbfc;
  border-radius: 8px;
  padding: 6px 8px;
  display: flex;
  flex-direction: column;
  gap: 2px;
  cursor: pointer;
  min-width: 0;
}
.dp-chip.on { background: #f2f8ec; border-color: #dceecd; }
.dpc-label { font-size: 12px; color: #303133; font-weight: 500; }
.dp-chip.on .dpc-label { color: #4f9d2f; }
.dpc-sub { font-size: 10.5px; color: #a0a8b3; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.block-title em { font-style: normal; font-size: 11.5px; font-weight: 400; color: #909399; margin-left: auto; }
.block-title { font-size: 14px; font-weight: 600; color: #303133; margin: 4px 2px 8px; display: flex; align-items: center; gap: 6px; }
.card { background: #fff; border-radius: 12px; padding: 12px 14px; box-shadow: 0 1px 3px rgba(0, 0, 0, 0.05); }
.water-card { margin-top: 10px; }
.row { display: flex; justify-content: space-between; align-items: center; font-size: 13px; color: #606266; }
.row b { color: #303133; }
.task { display: flex; align-items: center; justify-content: space-between; gap: 10px; background: #fff; border-radius: 12px; padding: 12px 14px; margin-bottom: 8px; box-shadow: 0 1px 3px rgba(0, 0, 0, 0.05); }
.task.done { background: #f6faf2; }
.task-main { min-width: 0; }
.task-label { font-size: 14px; color: #303133; font-weight: 500; }
.task-sub { font-size: 12px; color: #909399; margin-top: 2px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.chips { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 8px; }
.chip { font-size: 12px; color: #4f9d2f; background: #f2f8ec; border-radius: 4px; padding: 2px 6px; }
.chip.muted { color: #909399; background: #f4f4f5; }
.actions { display: flex; gap: 8px; margin-top: 12px; flex-wrap: wrap; }
.actions :deep(.el-button) { margin-left: 0; flex: 1; }
.water-actions { display: flex; gap: 6px; margin-top: 10px; }
.water-actions :deep(.el-button) { margin-left: 0; flex: 1; }
.sleep-line { font-size: 12px; color: #606266; margin-top: 8px; display: flex; align-items: center; gap: 6px; }
.sleep-tips { font-size: 12px; color: #a0a8b3; margin-top: 6px; line-height: 1.5; }
.sleep-form { display: flex; flex-direction: column; gap: 14px; }
.sf-row { display: flex; align-items: center; gap: 10px; font-size: 13px; color: #606266; }
.sf-plan { font-size: 12px; color: #909399; background: #f7f8fa; border-radius: 8px; padding: 8px 10px; line-height: 1.6; }
.skeleton { font-size: 13px; color: #909399; padding: 16px; text-align: center; }
</style>
