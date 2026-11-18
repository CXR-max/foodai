<script setup lang="ts">
// 移动端「运动」：多项目记录 + 自动算消耗（复用后端 MET 个性化模型）
// 「实时记录」已接入 GPS 跑步页（/workout/run），数据落到 workouts 表
import { computed, onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import * as checkinApi from '@/api/checkin'
import * as planApi from '@/api/plan'
import type { PlanDay, PlanTargets } from '@/api/plan'

const router = useRouter()

const SPORTS = ['快走', '慢跑', '跑步', '跳绳', '骑行', '游泳', '瑜伽', '力量训练', '球类', '其他']
const INTENSITIES = [
  { value: 'light', label: '轻松' },
  { value: 'moderate', label: '适中' },
  { value: 'intense', label: '吃力' },
] as const

const form = ref({
  exercise_type: '慢跑',
  duration_min: 30,
  intensity: 'moderate' as 'light' | 'moderate' | 'intense',
})
const records = ref<any[]>([])
const preview = ref<any>(null)
const saving = ref(false)
const loading = ref(true)

const todayBurn = computed(() =>
  Math.round(records.value.reduce((s, r) => s + (r.calories_burned || 0), 0)),
)

async function loadRecords() {
  loading.value = true
  try {
    const day = await checkinApi.getDay()
    records.value = day.items.filter((i) => i.check_type === 'exercise')
  } catch {
    // 拦截器已提示
  } finally {
    loading.value = false
  }
}

async function loadPreview() {
  try {
    preview.value = await checkinApi.getBurnPreview(
      form.value.exercise_type,
      form.value.duration_min,
      form.value.intensity,
    )
  } catch {
    preview.value = null
  }
}

watch(form, loadPreview, { deep: true })

async function submit() {
  if (!form.value.duration_min) {
    ElMessage.warning('请填写运动时长')
    return
  }
  saving.value = true
  try {
    const r: any = await checkinApi.manual({
      check_type: 'exercise',
      exercise_type: form.value.exercise_type,
      duration_min: form.value.duration_min,
      intensity: form.value.intensity,
    })
    ElMessage.success(
      `已记录 ${form.value.exercise_type} ${form.value.duration_min} 分钟，消耗 ${Math.round(r.calories_burned || 0)} kcal`,
    )
    await loadRecords()
  } finally {
    saving.value = false
  }
}

function intensityLabel(v: string) {
  return INTENSITIES.find((i) => i.value === v)?.label || v
}

// ---- 今日运动处方（读计划模块，日程同源）----
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

async function loadPlan() {
  try {
    active.value = await planApi.getActivePlan()
  } catch {
    active.value = null
  }
}

async function checkPrescription() {
  const plan = prescriptions.value[0]
  if (!plan) return
  checking.value = true
  try {
    const day = await checkinApi.getDay()
    const task = day.items.find((i) => i.task_key === 'exercise' && i.status === 'pending')
    if (task) {
      const r = await checkinApi.complete(task.id)
      ElMessage.success(r.message)
    } else {
      await checkinApi.manual({
        check_type: 'exercise',
        exercise_type: plan.type,
        duration_min: plan.duration_min,
        intensity: plan.intensity || 'moderate',
      })
      ElMessage.success('已按今日处方打卡')
    }
    await loadRecords()
  } finally {
    checking.value = false
  }
}

onMounted(async () => {
  await Promise.all([loadRecords(), loadPlan()])
  await loadPreview()
})
</script>

<template>
  <div class="m-page">
    <div class="hero">
      <div class="hero-label">今日运动消耗</div>
      <div class="hero-value">{{ todayBurn }} <span>kcal</span></div>
      <div class="hero-sub">
        <template v-if="burnMin">计划要求 ≥ {{ burnMin }} kcal · 已完成 {{ burnPct }}%</template>
        <template v-else>共 {{ records.length }} 条运动记录</template>
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

    <!-- 今日运动处方（来自计划模块） -->
    <section v-if="prescriptions.length" class="block">
      <h3 class="block-title">
        今日运动处方
        <el-tag v-if="active?.day?.targets?.is_training_day === false" size="small" type="info" effect="plain">恢复日</el-tag>
      </h3>
      <div class="card rx">
        <div v-for="(ex, i) in prescriptions" :key="i" class="rx-row">
          <div class="rx-main">
            <b>{{ ex.type }}</b>
            <span>{{ ex.duration_min }} 分钟 · 约 {{ ex.calories_burned }} kcal · {{ intensityLabel(ex.intensity || 'moderate') }}</span>
            <em v-if="ex.tips">{{ ex.tips }}</em>
          </div>
        </div>
        <el-button type="success" style="width: 100%; margin-top: 10px" :loading="checking" @click="checkPrescription">
          按处方打卡
        </el-button>
      </div>
    </section>
    <div v-else class="no-plan">今天没有运动处方，导入计划后会出现在这里。</div>

    <!-- 记录表单 -->
    <section class="block">
      <h3 class="block-title">记一次运动</h3>
      <div class="card">
        <div class="field">
          <label>运动项目</label>
          <div class="sports">
            <span
              v-for="s in SPORTS"
              :key="s"
              class="sport"
              :class="{ active: form.exercise_type === s }"
              @click="form.exercise_type = s"
            >{{ s }}</span>
          </div>
        </div>

        <div class="field">
          <label>时长</label>
          <el-slider v-model="form.duration_min" :min="5" :max="180" :step="5" show-stops :marks="{ 30: '30', 60: '60', 120: '120' }" />
        </div>

        <div class="field">
          <label>强度</label>
          <el-radio-group v-model="form.intensity" size="small">
            <el-radio-button v-for="i in INTENSITIES" :key="i.value" :value="i.value">{{ i.label }}</el-radio-button>
          </el-radio-group>
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

        <el-button type="success" style="width: 100%; margin-top: 12px" :loading="saving" @click="submit">
          记录这次运动
        </el-button>
      </div>
    </section>

    <!-- 今日记录 -->
    <section class="block">
      <h3 class="block-title">今日记录</h3>
      <div v-if="loading" class="skeleton">加载中…</div>
      <el-empty v-else-if="!records.length" description="今天还没有运动记录" :image-size="70" />
      <div v-for="r in records" v-else :key="r.id" class="rec">
        <div class="rec-main">
          <div class="rec-title">{{ r.exercise_type || '运动' }}<span v-if="r.duration_min"> · {{ r.duration_min }} 分钟</span></div>
          <div class="rec-sub">
            {{ intensityLabel(r.intensity || 'moderate') }}
            <span v-if="r.calories_burned"> · {{ Math.round(r.calories_burned) }} kcal</span>
            <span v-if="r.met_used"> · MET {{ r.met_used }}</span>
          </div>
        </div>
        <el-tag size="small" type="success" effect="plain">{{ r.points_awarded || 0 }} 分</el-tag>
      </div>
    </section>

    <!-- 实时记录：GPS 跑步 / 快走 / 骑行 -->
    <section class="block">
      <h3 class="block-title">实时记录</h3>
      <div class="card">
        <p class="hint">打开定位边走边记，实时看配速、距离和消耗，跑完自动存轨迹。</p>
        <div style="display:flex;gap:8px;margin-top:10px">
          <el-button type="success" style="flex:1" @click="router.push('/workout/run')">
            开始记录
          </el-button>
          <el-button style="flex:1" @click="router.push('/workout')">
            运动记录
          </el-button>
        </div>
      </div>
    </section>
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
.rx-row { padding: 8px 0; border-bottom: 1px dashed #f0f1f3; }
.rx-row:last-of-type { border-bottom: none; }
.rx-main { display: flex; flex-direction: column; gap: 2px; }
.rx-main b { font-size: 13.5px; color: #303133; }
.rx-main span { font-size: 12px; color: #606266; }
.rx-main em { font-style: normal; font-size: 11.5px; color: #9aa8a0; line-height: 1.5; }
.no-plan { font-size: 12px; color: #a8abb2; background: #fafbfc; border-radius: 10px; padding: 10px 12px; }
.block-title { font-size: 14px; font-weight: 600; color: #303133; margin: 4px 2px 8px; display: flex; align-items: center; gap: 6px; }
.card { background: #fff; border-radius: 12px; padding: 14px; box-shadow: 0 1px 3px rgba(0, 0, 0, 0.05); }
.card.muted { background: #fafafa; }
.field { margin-bottom: 16px; }
.field > label { display: block; font-size: 13px; color: #606266; margin-bottom: 8px; }
.sports { display: flex; flex-wrap: wrap; gap: 8px; }
.sport {
  font-size: 13px;
  padding: 6px 12px;
  border-radius: 16px;
  background: #f4f4f5;
  color: #606266;
  cursor: pointer;
}
.sport.active { background: #f2f8ec; color: #4f9d2f; font-weight: 600; box-shadow: inset 0 0 0 1px #b7dd9a; }
.preview { background: #f7faf4; border-radius: 10px; padding: 10px 12px; }
.preview-main { display: flex; justify-content: space-between; font-size: 13px; color: #606266; }
.preview-main b { color: #4f9d2f; font-size: 16px; }
.preview-sub { font-size: 12px; color: #909399; margin-top: 2px; }
.rec {
  display: flex;
  align-items: center;
  justify-content: space-between;
  background: #fff;
  border-radius: 12px;
  padding: 12px 14px;
  margin-bottom: 8px;
  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.05);
}
.rec-title { font-size: 14px; color: #303133; font-weight: 500; }
.rec-sub { font-size: 12px; color: #909399; margin-top: 2px; }
.hint { font-size: 12px; color: #a8abb2; margin: 0; line-height: 1.7; }
.skeleton { font-size: 13px; color: #909399; padding: 16px; text-align: center; }
</style>
