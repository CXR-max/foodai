<script setup lang="ts">
// 移动端「运动」：唯一入口
// 结构：今日消耗 → 今日运动处方 → 开始运动（GPS 轨迹）→ 补录 → 运动记录
// 数据只有一套：全部读写 workouts 表（旧的手动打卡 checkins 已并入 workouts）
// 说明：/workout 页只留给桌面端，手机端不再有第二个「运动首页」
import { computed, onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import * as workoutApi from '@/api/workout'
import type { WorkoutItem, WorkoutStats } from '@/api/workout'
import * as planApi from '@/api/plan'
import type { PlanDay, PlanTargets } from '@/api/plan'
import * as checkinApi from '@/api/checkin'
import { fmtDistance, fmtDuration, fmtWhen, sportIcon } from '@/utils/workout'
import { refreshTodayCheckin } from '@/stores/todayCheckin'

const router = useRouter()

// ---------------- GPS 类型：进全屏跑步页 ----------------
const GPS_TYPES = [
  { name: '跑步', icon: '🏃', desc: '轨迹 · 配速' },
  { name: '快走', icon: '🚶', desc: '轻量有氧' },
  { name: '骑行', icon: '🚴', desc: '速度 · 里程' },
]
const sport = ref('跑步')

// ---------------- 补录类型：没有轨迹的运动 ----------------
const MANUAL_TYPES = ['跑步机', '跳绳', '瑜伽', '拉伸', '八段锦', '慢跑', '游泳', '力量训练', '球类', '其他']
const NO_DISTANCE = ['跳绳', '瑜伽', '拉伸', '八段锦', '力量训练']
const INTENSITIES = [
  { value: 'light', label: '轻松' },
  { value: 'moderate', label: '适中' },
  { value: 'intense', label: '吃力' },
] as const

const stats = ref<WorkoutStats | null>(null)
const items = ref<WorkoutItem[]>([])
const total = ref(0)
const loading = ref(true)
/** 今天的方案运动任务是否已完成（避免“按处方完成”重复落记录） */
const rxDone = ref(false)

// ---------------- 今日消耗（统一口径：workouts 表）----------------
function dayKey(iso: string | null | undefined) {
  return (iso || '').slice(0, 10)
}
function todayKey() {
  const d = new Date()
  return `${d.getFullYear()}-${`${d.getMonth() + 1}`.padStart(2, '0')}-${`${d.getDate()}`.padStart(2, '0')}`
}
const todayItems = computed(() => items.value.filter((i) => dayKey(i.start_at) === todayKey()))
const todayBurn = computed(() =>
  Math.round(todayItems.value.reduce((s, i) => s + (i.calories || 0), 0)),
)

// ---------------- 今日运动处方（来自计划模块）----------------
const active = ref<{ has_plan: boolean; day?: PlanDay; targets: PlanTargets } | null>(null)
const checking = ref(false)
const prescriptions = computed(() => {
  const day = active.value?.day
  if (!day) return []
  if (day.exercises?.length) return day.exercises
  return day.exercise ? [day.exercise] : []
})
const burnMin = computed(() => active.value?.targets?.exercise_burn_min || 0)
const burnPct = computed(() => {
  if (!burnMin.value) return 0
  return Math.min(100, Math.round((todayBurn.value / burnMin.value) * 100))
})

// ---------------- 记录列表（今天 / 更早）----------------
const grouped = computed(() => {
  const t: WorkoutItem[] = []
  const e: WorkoutItem[] = []
  for (const it of items.value) (dayKey(it.start_at) === todayKey() ? t : e).push(it)
  return { today: t, earlier: e.slice(0, 8) }
})

function distText(it: WorkoutItem) {
  if (!it.distance_m) return ''
  const d = fmtDistance(it.distance_m)
  return `${d.value}${d.unit}`
}
function intensityLabel(v?: string | null) {
  return INTENSITIES.find((i) => i.value === v)?.label || ''
}

// ---------------- 加载 ----------------
async function loadAll() {
  loading.value = true
  try {
    const [s, list, plan, dayTasks] = await Promise.all([
      workoutApi.getWorkoutStats().catch(() => null),
      workoutApi.listWorkouts({ range: 'all', limit: 30 }).catch(() => ({ total: 0, items: [] })),
      planApi.getActivePlan().catch(() => null),
      checkinApi.getDay().catch(() => null),
    ])
    stats.value = s
    items.value = list.items
    total.value = list.total
    active.value = plan as any
    const taskItems = (dayTasks as any)?.items || []
    rxDone.value = taskItems.some((it: any) => it.task_key === 'exercise' && it.status === 'done')
  } finally {
    loading.value = false
  }
}

function goRun() {
  router.push({ path: '/workout/run', query: { sport: sport.value } })
}

function openDetail(it: WorkoutItem) {
  // 手动补录没有轨迹，但详情页支持无轨迹展示和删除，不能只让轨迹记录可点
  router.push(`/workout/${it.id}`)
}

// ---------------- 按处方打卡 ----------------
async function checkPrescription() {
  const list = prescriptions.value
  if (!list.length || rxDone.value) return
  const dayId = active.value?.day?.id
  checking.value = true
  try {
    // 处方可能是「主项 + 拉伸」两项，必须逐项落记录；
    // client_id 用 plan-day-index 固定，重复点/刷新后再点不会落第二条。
    for (let i = 0; i < list.length; i += 1) {
      const p = list[i]
      await workoutApi.createManualWorkout({
        sport_type: p.type,
        duration_sec: Math.max(60, (p.duration_min || 30) * 60),
        intensity: (p.intensity as any) || undefined,
        note: '按今日处方完成',
        client_id: `plan-${dayId || 'x'}-${i}`,
        plan_day_id: dayId,
      })
    }
    rxDone.value = true
    ElMessage.success(`已按处方记录 ${list.length} 项`)
    refreshTodayCheckin()
    await loadAll()
  } finally {
    checking.value = false
  }
}

// ---------------- 补录 ----------------
const manualOpen = ref(false)
const mType = ref('跑步机')
const mMin = ref(30)
const mKm = ref<number | null>(3)
const mWhen = ref<Date | null>(new Date())
const mIntensity = ref<'light' | 'moderate' | 'intense'>('moderate')
const preview = ref<any>(null)
const saving = ref(false)

function newClientId(): string {
  const c = (globalThis as any).crypto
  if (c?.randomUUID) return c.randomUUID()
  return `m_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`
}
const clientId = ref('')

function openManual(type = '跑步机') {
  mType.value = type
  mMin.value = 30
  mKm.value = NO_DISTANCE.includes(type) ? null : 3
  mWhen.value = new Date()
  mIntensity.value = 'moderate'
  clientId.value = newClientId()
  manualOpen.value = true
  loadPreview()
}

// 换类型时，本来不需要距离的自动清空
watch(mType, (t) => {
  if (NO_DISTANCE.includes(t)) mKm.value = null
  else if (!mKm.value) mKm.value = 3
  loadPreview()
})
watch([mMin, mIntensity], loadPreview)

async function loadPreview() {
  try {
    preview.value = await checkinApi.getBurnPreview(mType.value, mMin.value, mIntensity.value)
  } catch {
    preview.value = null
  }
}

async function saveManual() {
  if (saving.value) return
  saving.value = true
  try {
    const r = await workoutApi.createManualWorkout({
      sport_type: mType.value,
      start_at: (mWhen.value || new Date()).toISOString(),
      duration_sec: Math.max(60, Math.round(mMin.value * 60)),
      distance_m: mKm.value ? Math.round(mKm.value * 1000) : null,
      intensity: mIntensity.value,
      client_id: clientId.value,
    })
    manualOpen.value = false
    ElMessage.success(r.message || '已记录')
    refreshTodayCheckin()
    await loadAll()
  } finally {
    saving.value = false
  }
}

onMounted(loadAll)
</script>

<template>
  <div class="m-page">
    <!-- 今日消耗 -->
    <div class="hero">
      <div class="hero-label">今日运动消耗</div>
      <div class="hero-value">{{ todayBurn }} <span>kcal</span></div>
      <div class="hero-sub">
        <template v-if="burnMin">计划要求 ≥ {{ burnMin }} kcal · 已完成 {{ burnPct }}%</template>
        <template v-else-if="stats">
          本周 {{ fmtDistance(stats.week.distance_m).value }}{{ fmtDistance(stats.week.distance_m).unit }}
          · {{ stats.week.count }} 次 · {{ Math.round(stats.week.calories) }} kcal
        </template>
        <template v-else>今天还没有运动记录</template>
      </div>
      <el-progress
        v-if="burnMin"
        :percentage="burnPct"
        :stroke-width="6"
        :show-text="false"
        color="#fff"
        style="margin-top: 8px"
      />
    </div>

    <!-- 今日运动处方 -->
    <section v-if="prescriptions.length" class="block">
      <h3 class="block-title">
        今日运动处方
        <el-tag v-if="active?.day?.targets?.is_training_day === false" size="small" type="info" effect="plain">恢复日</el-tag>
      </h3>
      <div class="card">
        <div v-for="(ex, i) in prescriptions" :key="i" class="rx-row">
          <div class="rx-main">
            <b>{{ ex.type }}</b>
            <span>{{ ex.duration_min }} 分钟 · 约 {{ ex.calories_burned }} kcal · {{ intensityLabel(ex.intensity) || '适中' }}</span>
            <em v-if="ex.tips">{{ ex.tips }}</em>
          </div>
        </div>
        <el-button
          type="success"
          style="width: 100%; margin-top: 10px"
          :loading="checking"
          :disabled="rxDone"
          @click="checkPrescription"
        >
          {{ rxDone ? '今日处方已完成' : '按处方完成' }}
        </el-button>
      </div>
    </section>
    <div v-else class="no-plan">今天没有运动处方，导入计划后会出现在这里。</div>

    <!-- 开始运动 -->
    <section class="block">
      <h3 class="block-title">开始运动</h3>
      <div class="starters">
        <button
          v-for="s in GPS_TYPES"
          :key="s.name"
          class="starter"
          :class="{ on: sport === s.name }"
          @click="sport = s.name"
        >
          <span class="ico">{{ s.icon }}</span>
          <b>{{ s.name }}</b>
          <em>{{ s.desc }}</em>
        </button>
      </div>
      <el-button type="success" class="run-btn" @click="goRun">▶ 开始{{ sport }}</el-button>
      <div class="quick-manual">
        <span>没有轨迹？</span>
        <a v-for="t in ['跑步机', '跳绳', '瑜伽', '拉伸']" :key="t" @click="openManual(t)">{{ t }}补录</a>
      </div>
    </section>

    <!-- 运动记录 -->
    <section class="block">
      <h3 class="block-title">
        运动记录
        <em v-if="total">{{ total }} 次</em>
      </h3>

      <div v-if="loading" class="skeleton">加载中…</div>
      <el-empty v-else-if="!items.length" description="还没有运动记录" :image-size="70">
        <p class="empty-tip">选一个运动开始，跑完自动同步到打卡</p>
      </el-empty>

      <template v-else>
        <div v-if="grouped.today.length" class="group-label">今天</div>
        <div
          v-for="it in grouped.today"
          :key="it.id"
          class="rec clickable"
          @click="openDetail(it)"
        >
          <span class="rec-ico">{{ sportIcon(it.sport_type) }}</span>
          <div class="rec-main">
            <div class="rec-title">
              {{ it.sport_type }}
              <span v-if="distText(it)" class="rec-dist">{{ distText(it) }}</span>
              <span v-if="it.has_track" class="rec-track">轨迹</span>
            </div>
            <div class="rec-sub">
              {{ fmtDuration(it.duration_sec) }}
              <span v-if="it.calories"> · {{ Math.round(it.calories) }} kcal</span>
              <span v-if="it.intensity"> · {{ intensityLabel(it.intensity) }}</span>
            </div>
          </div>
          <span class="rec-arrow">›</span>
        </div>

        <div v-if="grouped.earlier.length" class="group-label">更早</div>
        <div
          v-for="it in grouped.earlier"
          :key="it.id"
          class="rec clickable"
          @click="openDetail(it)"
        >
          <span class="rec-ico">{{ sportIcon(it.sport_type) }}</span>
          <div class="rec-main">
            <div class="rec-title">
              {{ it.sport_type }}
              <span v-if="distText(it)" class="rec-dist">{{ distText(it) }}</span>
              <span v-if="it.has_track" class="rec-track">轨迹</span>
            </div>
            <div class="rec-sub">
              {{ fmtWhen(it.start_at) }} · {{ fmtDuration(it.duration_sec) }}
              <span v-if="it.calories"> · {{ Math.round(it.calories) }} kcal</span>
            </div>
          </div>
          <span class="rec-arrow">›</span>
        </div>
      </template>
    </section>

    <!-- 补录抽屉 -->
    <el-drawer v-model="manualOpen" direction="btt" size="auto" :with-header="false">
      <div class="sheet">
        <div class="sheet-head">
          <b>补录一次运动</b>
          <span @click="manualOpen = false">✕</span>
        </div>

        <div class="field">
          <label>运动项目</label>
          <div class="chips">
            <span
              v-for="t in MANUAL_TYPES"
              :key="t"
              class="chip"
              :class="{ on: mType === t }"
              @click="mType = t"
            >{{ t }}</span>
          </div>
        </div>

        <div class="field">
          <label>时间</label>
          <el-date-picker
            v-model="mWhen"
            type="datetime"
            format="MM-DD HH:mm"
            placeholder="选择时间"
            style="width: 100%"
          />
        </div>

        <div class="field">
          <label>时长 <b>{{ mMin }} 分钟</b></label>
          <el-slider v-model="mMin" :min="5" :max="180" :step="5" show-stops />
        </div>

        <div class="field">
          <label>强度</label>
          <div class="chips">
            <span
              v-for="i in INTENSITIES"
              :key="i.value"
              class="chip"
              :class="{ on: mIntensity === i.value }"
              @click="mIntensity = i.value"
            >{{ i.label }}</span>
          </div>
        </div>

        <div v-if="!NO_DISTANCE.includes(mType)" class="field">
          <label>距离（可不填）</label>
          <el-input v-model.number="mKm" type="number" placeholder="公里">
            <template #append>km</template>
          </el-input>
        </div>

        <div v-if="preview" class="preview">
          <div class="preview-main">
            <span>预计消耗</span>
            <b>{{ Math.round(preview.calories_burned) }} kcal</b>
          </div>
          <div class="preview-sub">
            区间 {{ Math.round(preview.low) }}–{{ Math.round(preview.high) }} kcal · MET {{ preview.met }}
          </div>
        </div>

        <el-button type="success" class="save-btn" :loading="saving" @click="saveManual">
          保存记录
        </el-button>
      </div>
    </el-drawer>
  </div>
</template>

<style scoped>
.m-page { display: flex; flex-direction: column; gap: 12px; }
.hero {
  background: linear-gradient(135deg, #4f9d2f, #7bc043);
  border-radius: 14px;
  padding: 16px;
  color: #fff;
}
.hero-label { font-size: 12px; opacity: 0.9; }
.hero-value { font-size: 30px; font-weight: 600; line-height: 1.2; }
.hero-value span { font-size: 13px; font-weight: 400; opacity: 0.9; }
.hero-sub { font-size: 12px; opacity: 0.9; margin-top: 2px; }

.block-title { font-size: 14px; font-weight: 600; color: #303133; margin: 4px 2px 8px; display: flex; align-items: center; gap: 6px; }
.block-title em { font-style: normal; font-size: 11px; font-weight: 400; color: #a8abb2; }
.card { background: #fff; border-radius: 12px; padding: 14px; box-shadow: 0 1px 3px rgba(0, 0, 0, 0.05); }

.rx-row { padding: 8px 0; border-bottom: 1px dashed #f0f1f3; }
.rx-row:last-of-type { border-bottom: none; }
.rx-main { display: flex; flex-direction: column; gap: 2px; }
.rx-main b { font-size: 13.5px; color: #303133; }
.rx-main span { font-size: 12px; color: #606266; }
.rx-main em { font-style: normal; font-size: 11.5px; color: #9aa8a0; line-height: 1.5; }
.no-plan { font-size: 12px; color: #a8abb2; background: #fafbfc; border-radius: 10px; padding: 10px 12px; }

.starters { display: grid; grid-template-columns: repeat(3, 1fr); gap: 8px; }
.starter {
  display: flex; flex-direction: column; align-items: center; gap: 2px;
  background: #fff; border: 1px solid #eef0f2; border-radius: 12px;
  padding: 12px 6px; cursor: pointer; transition: all 0.15s;
}
.starter.on { border-color: #8ec96a; background: #f6fbf2; box-shadow: 0 2px 8px rgba(103, 194, 58, 0.12); }
.starter .ico { font-size: 24px; line-height: 1; }
.starter b { font-size: 13px; color: #303133; }
.starter em { font-style: normal; font-size: 10.5px; color: #a8abb2; }
.run-btn { width: 100%; height: 46px; margin-top: 10px; border-radius: 12px; font-size: 15px; font-weight: 600; }
.quick-manual { display: flex; flex-wrap: wrap; gap: 10px; align-items: center; margin-top: 10px; font-size: 12px; color: #a8abb2; }
.quick-manual a { color: #4f9d2f; }

.group-label { font-size: 12px; color: #909399; margin: 8px 2px 6px; }
.rec {
  display: flex; align-items: center; gap: 10px;
  background: #fff; border-radius: 12px; padding: 12px 14px;
  margin-bottom: 8px; box-shadow: 0 1px 3px rgba(0, 0, 0, 0.05);
}
.rec.clickable:active { background: #f7f9f5; }
.rec-ico { font-size: 20px; }
.rec-main { flex: 1; min-width: 0; }
.rec-title { font-size: 14px; color: #303133; font-weight: 500; display: flex; align-items: center; gap: 6px; }
.rec-dist { font-size: 12px; color: #4f9d2f; font-weight: 600; }
.rec-track { font-size: 10px; color: #8aa87a; background: #f2f8ec; border-radius: 4px; padding: 1px 4px; }
.rec-sub { font-size: 12px; color: #909399; margin-top: 2px; }
.rec-arrow { color: #c8c9cc; font-size: 18px; }
.empty-tip { font-size: 12px; color: #a8abb2; margin: 0; }
.skeleton { text-align: center; font-size: 13px; color: #909399; padding: 20px 0; }

/* 底部抽屉 */
.sheet { padding: 4px 2px 10px; }
.sheet-head { display: flex; align-items: center; justify-content: space-between; margin-bottom: 14px; }
.sheet-head b { font-size: 16px; color: #303133; }
.sheet-head span { color: #a8abb2; font-size: 16px; padding: 4px 8px; }
.field { margin-bottom: 16px; }
.field > label { display: flex; justify-content: space-between; font-size: 13px; color: #606266; margin-bottom: 8px; }
.field > label b { color: #4f9d2f; }
.chips { display: flex; flex-wrap: wrap; gap: 8px; }
.chip {
  font-size: 13px; padding: 6px 12px; border-radius: 16px;
  background: #f4f4f5; color: #606266; cursor: pointer;
}
.chip.on { background: #f2f8ec; color: #4f9d2f; font-weight: 600; box-shadow: inset 0 0 0 1px #b7dd9a; }
.preview { background: #f7faf4; border-radius: 10px; padding: 10px 12px; }
.preview-main { display: flex; justify-content: space-between; font-size: 13px; color: #606266; }
.preview-main b { color: #4f9d2f; font-size: 16px; }
.preview-sub { font-size: 12px; color: #909399; margin-top: 2px; }
.save-btn { width: 100%; height: 44px; border-radius: 12px; margin-top: 14px; }
</style>
