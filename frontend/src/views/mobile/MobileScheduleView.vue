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
      <p v-if="summary?.band_note" class="hero-note">{{ summary.band_note }}</p>
    </div>

    <!-- 指标区间：每项给确切上下限 + 实际值 + 判定，超限才提示怎么补 -->
    <section v-if="bands.length" class="block">
      <h3 class="block-title">
        今日指标区间
        <em>{{ bands.filter((b) => b.status === 'in_range' || b.status === 'low' || b.status === 'high').length }}/{{ bands.length }} 达标</em>
      </h3>
      <div class="card bands-card">
        <!-- 饮食一行汇总：七项判完给一条结论。方案排错不怪用户，额外加餐给出口不指责 -->
        <div v-if="summary?.diet?.line" class="diet-line" :class="dietCls">
          <span class="diet-text">{{ summary.diet.line }}</span>
          <span class="diet-tag">{{ summary.diet.status_text }}</span>
        </div>
        <p v-if="summary?.diet?.hint" class="diet-hint">{{ summary.diet.hint }}</p>

        <!-- 当日模式：聚餐/生病/出差…只改今天的判定口径，第二天自动恢复 -->
        <div class="mode-bar">
          <span v-if="currentMode" class="mode-tag">
            {{ currentMode.label }}
            <em v-if="currentMode.note_text">{{ currentMode.note_text }}</em>
          </span>
          <span v-else class="mode-idle">今天一切正常</span>
          <span class="mode-action" @click="modePickerOpen = true">
            {{ currentMode ? '换' : '今天有特殊情况？' }}
          </span>
        </div>

        <!-- 结算时点：为什么现在不判/还能做什么，先讲清楚再看数字 -->
        <p v-if="settlementInfo" class="settle-hint" :class="settlementInfo.point">
          {{ settlementInfo.message }}
        </p>

        <!-- 动态调整：系统自动改了方案什么（每条带为什么，可单独撤销） -->
        <div v-if="adaptApplied.length" class="adapt-wrap">
          <div v-for="item in adaptApplied" :key="item.day" class="adapt-card">
            <div class="adapt-head">
              <span class="adapt-tag">方案已自动调整</span>
              <span class="adapt-day">{{ item.day }}</span>
              <button
                class="adapt-revert"
                :disabled="revertingDay === item.day"
                @click="doRevert(item.day)"
              >
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
        <div v-for="b in bands" :key="b.key" class="band-row" :class="bandCls(b.status, b.key)">
          <div class="band-head">
            <span class="band-name">{{ b.label }}</span>
            <span class="band-status">{{ b.status_text }}</span>
          </div>
          <div class="band-mid">
            <span class="band-range">推荐 {{ b.range_text }}</span>
            <span class="band-value">
              <b>{{ b.actual != null ? b.actual : '—' }}</b>
              <em v-if="b.actual != null">{{ b.unit }}</em>
            </span>
          </div>
          <p class="band-hint">{{ b.hint }}</p>
        </div>
        <p class="band-foot">
          区间为推荐值，绿色=区间内，黄色=在浮动容忍内也算达标，红色=明显偏离。
          口径见「计划 → 每日目标」，超限可在计划详情里改餐或加运动。
        </p>
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
/* 指标区间卡片 */
.bands-card { padding: 2px 12px 10px; }
/* 饮食一行汇总 */
.diet-line {
  display: flex; align-items: center; justify-content: space-between; gap: 8px;
  margin: 9px 0 3px; padding: 8px 10px; border-radius: 8px;
  background: #f7f9fc; font-size: 12px; color: #606266;
}
.diet-line.ok { background: #f2fbef; }
.diet-line.warn { background: #fdf6ec; }
/* 还有餐没打卡：只是没吃完，用中性蓝灰，不用警告色 */
.diet-line.progress { background: #f2f6fd; }
.diet-line.progress .diet-tag { background: #e6eefc; color: #4a72b8; }
/* 方案排错：是系统的问题，用中性提示色，不能跟着标红让用户自责 */
.diet-line.notice { background: #f4f4f7; }
.diet-line.notice .diet-tag { background: #e8e8ef; color: #6b6b7d; }
.diet-hint { margin: 2px 2px 4px; font-size: 11.5px; line-height: 1.5; color: #909399; }
/* 结构项一行：脂肪/碳水/纤维，只展示不卡打卡 */
.diet-struct { margin: 0 2px 6px; font-size: 11px; color: #a8abb2; line-height: 1.5; }
.diet-est { margin: 0 0 3px; padding: 0 2px; font-size: 11px; color: #a0a6b3; }

/* ---- 当日模式 ---- */
.mode-bar {
  display: flex; align-items: center; justify-content: space-between; gap: 8px;
  margin: 6px 0 4px; padding: 6px 8px; border-radius: 8px; background: #f7f8fa;
}
.mode-tag { font-size: 11.5px; color: #409eff; font-weight: 600; }
.mode-tag em { font-style: normal; font-weight: 400; color: #909399; margin-left: 4px; }
.mode-idle { font-size: 11.5px; color: #b0b3b8; }
.mode-action { font-size: 11.5px; color: #409eff; white-space: nowrap; }
.mode-tip { margin: 0 0 10px; font-size: 12px; color: #909399; line-height: 1.6; }
.mode-list { display: flex; flex-direction: column; gap: 8px; }
.mode-item {
  text-align: left; border: 1px solid #e4e7ed; background: #fff; border-radius: 10px;
  padding: 9px 12px; display: flex; flex-direction: column; gap: 2px; cursor: pointer;
}
.mode-item b { font-size: 13.5px; color: #303133; }
.mode-item span { font-size: 11.5px; color: #909399; line-height: 1.5; }
.mode-item.normal { border-style: dashed; }

/* ---- 结算时点 ---- */
.settle-hint { margin: 0 0 6px; padding: 5px 8px; border-radius: 6px; font-size: 11px; line-height: 1.5; }
.settle-hint.open { background: #f4f4f5; color: #909399; }
.settle-hint.midday { background: #fdf6ec; color: #b88230; }
.settle-hint.evening { background: #f0f9eb; color: #529b2e; }
.settle-hint.final { background: #f4f4f5; color: #909399; }

/* ---- 超限应对 ---- */
.disp-wrap { display: flex; flex-direction: column; gap: 8px; margin: 8px 0 4px; }
.disp-card { border: 1px solid #f3d19e; background: #fffbf5; border-radius: 10px; padding: 9px 10px; }
.disp-head { display: flex; align-items: center; justify-content: space-between; margin-bottom: 6px; }
.disp-name { font-size: 12.5px; font-weight: 600; color: #b88230; }
.disp-tag { font-size: 10px; padding: 1px 6px; border-radius: 8px; background: #f4f4f5; color: #909399; }
.disp-tag.plan { background: #f4f4f5; color: #909399; }
.disp-tag.extra { background: #ecf5ff; color: #409eff; }
.disp-tag.intake { background: #fdf6ec; color: #b88230; }
.disp-opts { display: flex; flex-direction: column; gap: 6px; }
.disp-opt {
  text-align: left; border: 1px solid #e4e7ed; background: #fff; border-radius: 8px;
  padding: 7px 9px; display: flex; flex-direction: column; gap: 3px; cursor: pointer;
}
.disp-opt b { font-size: 12.5px; color: #303133; display: flex; align-items: center; gap: 4px; }
.disp-opt b i {
  font-style: normal; font-size: 9.5px; background: #67c23a; color: #fff;
  padding: 0 4px; border-radius: 6px; line-height: 14px;
}
.disp-opt span { font-size: 11.5px; color: #606266; line-height: 1.55; }
.disp-opt em.safety { font-style: normal; font-size: 10.5px; color: #e6a23c; line-height: 1.5; }
.disp-opt.rec { border-color: #b3e19d; background: #fbfef9; }
.disp-opt.unsafe { opacity: .62; }
.disp-opt:disabled { opacity: .5; }

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
.diet-text { font-variant-numeric: tabular-nums; }
.diet-tag {
  flex: none; padding: 1px 7px; border-radius: 999px;
  background: #eef1f6; color: #8a94a6; font-size: 11px;
}
.diet-line.ok .diet-tag { background: #e2f5da; color: #4a9b23; }
.diet-line.warn .diet-tag { background: #f8e6c8; color: #a06000; }
.band-row { padding: 10px 0 9px; border-bottom: 1px solid #f2f3f5; }
.band-row:last-of-type { border-bottom: none; }
.band-head { display: flex; align-items: center; justify-content: space-between; }
.band-name { font-size: 13.5px; font-weight: 600; color: #303133; }
.band-mid { display: flex; align-items: baseline; justify-content: space-between; margin-top: 2px; }
.band-range { font-size: 11.5px; color: #909399; }
.band-value b { font-size: 17px; color: #303133; }
.band-value em { font-style: normal; font-size: 10.5px; color: #a8abb2; margin-left: 2px; }
.band-status { font-size: 11px; padding: 1px 7px; border-radius: 4px; background: #f2f3f5; color: #909399; }
.band-row.ok .band-status { background: #f0f9eb; color: #4f9d2f; }
.band-row.ok .band-value b { color: #4f9d2f; }
.band-row.warn .band-status { background: #fdf6ec; color: #c98a1b; }
.band-row.warn .band-value b { color: #c98a1b; }
.band-row.bad .band-status { background: #fef0f0; color: #d94a4a; }
.band-row.bad .band-value b { color: #d94a4a; }
/* 蛋白不足：灰字即可，不标红（方案里已排好菜） */
.band-row.muted .band-status { background: #f4f4f6; color: #8a94a6; }
.band-row.muted .band-value b { color: #8a94a6; }
.ht.muted b { color: rgba(255, 255, 255, 0.82); }
.band-hint { font-size: 11.5px; line-height: 1.6; color: #909399; margin: 3px 0 0; }
.band-foot { font-size: 11px; line-height: 1.7; color: #a8abb2; margin: 6px 0 0; }
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
/* 动态调整：是「系统做过的事」，中性蓝；撤销按钮低调但可点 */
.adapt-wrap { margin-top: 10px; display: flex; flex-direction: column; gap: 8px; }
.adapt-card { background: #f2f7fc; border: 1px solid #d8e6f3; border-radius: 10px; padding: 9px 11px; }
.adapt-head { display: flex; align-items: center; gap: 8px; }
.adapt-tag { font-size: 11px; padding: 1px 7px; border-radius: 999px; background: #dcebfa; color: #2b6cb0; flex: none; }
.adapt-day { font-size: 12px; color: #606266; flex: 1; }
.adapt-revert { font-size: 11.5px; color: #2b6cb0; background: transparent; border: 1px solid #b9d3ec; border-radius: 999px; padding: 1px 10px; cursor: pointer; }
.adapt-revert:disabled { opacity: 0.5; cursor: default; }
.adapt-reason { font-size: 12px; line-height: 1.6; color: #4a5568; margin: 6px 0 0; }
.adapt-reason b { font-weight: 500; color: #2b6cb0; }
</style>
