<script setup lang="ts">
// 移动端「日程」：当天目标条 + 任务打卡 + 食物/饮水/睡眠三个模块 + 打卡日历
// 食物与饮水合成一个独立模块，拍照识别是食物模块里的一个功能（计划页另有快捷入口）
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import * as checkinApi from '@/api/checkin'
import type { CheckInItem, DayStat } from '@/api/checkin'
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
const bandCls = (status?: string) =>
  status === 'in_range' ? 'ok' : status === 'low' || status === 'high' ? 'warn' : status === 'under' || status === 'over' ? 'bad' : ''
const waterBand = computed(() => bands.value.find((b) => b.key === 'water'))
const sleepBand = computed(() => bands.value.find((b) => b.key === 'sleep'))

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
    await loadMonth()
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
        <div v-for="b in heroBands" :key="b.key" class="ht" :class="bandCls(b.status)">
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
        <div v-for="b in bands" :key="b.key" class="band-row" :class="bandCls(b.status)">
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
</style>
