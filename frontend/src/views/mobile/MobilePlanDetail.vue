<script setup lang="ts">
// 移动端「计划详情」：按天查看四餐 / 运动处方 / 睡眠方案 / 当日目标；
// 可改菜（按营养库重算热量与三大营养素）、改运动、改睡眠饮水；
// 可导入单天或整周日程（导入即生效），可沿用这一份排下一周。
import { computed, onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import * as planApi from '@/api/plan'
import type { Plan, PlanDay, PlanExercise, PlanMeal, PlanNutrition, PlanReview } from '@/api/plan'
import * as foodApi from '@/api/food'
import type { FoodAlternative } from '@/api/food'
import * as profileApi from '@/api/profile'
import type { OverLimitPolicy } from '@/api/profile'
import { notifyReactivated } from '@/utils/planFeedback'

const props = defineProps<{ planId: number }>()
const emit = defineEmits<{ (e: 'close'): void; (e: 'changed'): void }>()

const router = useRouter()
const plan = ref<Plan | null>(null)
const loading = ref(true)
const review = ref<PlanReview | null>(null)
const openDays = ref<Set<number>>(new Set([1]))
const busy = ref(false)

const targets = computed(() => plan.value?.targets || {})
const days = computed(() => plan.value?.days || [])
const todayStr = fmt(new Date())
// 只有整份方案都过期（最后一天也过了）才叫"日期已过"；
// 按第一天判断会把"昨天开始、今天还在进行"的方案误报成过期
const expired = computed(() => {
  const list = days.value
  if (!list.length) return false
  const last = list[list.length - 1]?.date
  return !!last && last < todayStr
})

const MEAL_LABELS: Record<string, string> = {
  breakfast: '🌅 早餐', lunch: '☀️ 午餐', dinner: '🌙 晚餐', snack: '🍎 加餐',
}
const MEAL_ORDER = ['breakfast', 'lunch', 'dinner', 'snack']
const EX_TYPES = ['跑步', '快走', '散步', '骑行', '游泳', '跳绳', '力量训练', '球类', '瑜伽', '拉伸']
const INTENSITY: Record<string, string> = { light: '轻松', moderate: '适中', intense: '吃力' }
const PROVIDER_LABEL: Record<string, string> = {
  openai_compat: 'AI 生成', solver: '本地求解', mock: '离线模板', reuse: '沿用',
}

function fmt(d: Date) {
  const m = `${d.getMonth() + 1}`.padStart(2, '0')
  const day = `${d.getDate()}`.padStart(2, '0')
  return `${d.getFullYear()}-${m}-${day}`
}

function exercisesOf(day: PlanDay): PlanExercise[] {
  if (day.exercises && day.exercises.length) return day.exercises
  return day.exercise ? [day.exercise] : []
}

/** 处置/动态调整的留痕：必须说清「变了什么」和「为什么」，只给一个数字用户看不懂 */
function adjustText(day: PlanDay): string {
  const replan = day.day_flags?.replanned
  const info = replan || day.day_flags?.adjusted
  if (!info) return ''
  const moves = (info.changed || [])
    .filter((c) => c.to)
    .slice(0, 3)
    .map((c) => `${c.meal}${c.from ? `${c.from}→${c.to}` : `加了${c.to}`}`)
  // 理由跟事实走：处置重排和动态调整各有自己的原因，别写死「因前面几天超了」
  const head = replan
    ? (replan.reason || '这天已按新目标重排')
    : `这天已按用户选择的处置下调${info.trim_kcal ? ` ${info.trim_kcal} kcal` : ''}`
  return moves.length ? `${head}：${moves.join('、')}` : head
}

function shortDate(d?: string) {
  return d ? d.slice(5).replace('-', '/') : ''
}

async function load() {
  loading.value = true
  try {
    plan.value = await planApi.getPlan(props.planId)
    // 评价单独拉：进行中只算进度，归档后是最终分
    planApi.getPlanReview(props.planId).then((r) => { review.value = r }).catch(() => { review.value = null })
  } finally {
    loading.value = false
  }
}

function toggle(index: number) {
  const next = new Set(openDays.value)
  next.has(index) ? next.delete(index) : next.add(index)
  openDays.value = next
}

// ---- 编辑：餐食 / 运动 / 睡眠 / 饮水 ----
const editing = ref<PlanDay | null>(null)
const editOpen = ref(false)
const form = ref<{
  meals: PlanMeal[]
  exercises: PlanExercise[]
  bedtime: string
  wakeAt: string
  targetH: number
  water: number
}>({ meals: [], exercises: [], bedtime: '23:00', wakeAt: '07:00', targetH: 7, water: 2000 })
const nutri = ref<PlanNutrition | null>(null)
const saving = ref(false)
const warnOpen = ref(false)
// 热量超限默认处理（读档案里的用户偏好）：ask 手动选择 / portion 缩份量 / exercise 加运动
const overLimitPolicy = ref<OverLimitPolicy>('ask')
const policySaving = ref(false)
const warnAction = ref<'portion' | 'exercise' | 'save'>('save')

const POLICY_LABEL: Record<OverLimitPolicy, string> = {
  ask: '手动选择', portion: '按比例缩份量', exercise: '加运动抵消',
}

let previewTimer: ReturnType<typeof setTimeout> | null = null
let previewSeq = 0

function cloneMeals(meals: PlanMeal[]): PlanMeal[] {
  return meals.map((m) => ({ ...m, items: (m.items || []).map((i) => ({ ...i })) }))
}

/** 只提交菜名/克重/类别，营养由后端统一算，避免两端口径不一致 */
function cleanMeals(): PlanMeal[] {
  return form.value.meals
    .filter((m) => (m.items || []).length)
    .map((m) => ({
      meal_type: m.meal_type,
      name: m.name,
      calories: 0,
      items: m.items
        .filter((i) => (i.name || '').trim())
        .map((i) => ({ name: i.name.trim(), portion_g: i.portion_g, category: i.category || undefined })),
    })) as PlanMeal[]
}

function openEdit(day: PlanDay) {
  editing.value = day
  editOpen.value = true
  warnOpen.value = false
  nutri.value = null
  form.value = {
    meals: cloneMeals((day.meals || []) as PlanMeal[]).sort(
      (a, b) => MEAL_ORDER.indexOf(a.meal_type) - MEAL_ORDER.indexOf(b.meal_type),
    ),
    exercises: exercisesOf(day).map((e) => ({ ...e })),
    bedtime: day.sleep?.bedtime || '23:00',
    wakeAt: day.sleep?.wake_at || '07:00',
    targetH: day.sleep?.target_h || 7,
    water: day.water_ml || 2000,
  }
  runPreview()
}

function schedulePreview() {
  if (!editOpen.value) return
  if (previewTimer) clearTimeout(previewTimer)
  previewTimer = setTimeout(runPreview, 400)
}

async function runPreview() {
  if (!editOpen.value || !editing.value || !plan.value) return
  const meals = cleanMeals()
  if (!meals.length) {
    nutri.value = null
    return
  }
  const seq = ++previewSeq
  try {
    const r = await planApi.previewPlanDay(plan.value.id, editing.value.day_index, { meals })
    if (seq === previewSeq) nutri.value = r
  } catch {
    // 拦截器已提示
  }
}

watch(() => form.value.meals, schedulePreview, { deep: true })

function mealCalories(mi: number) {
  return nutri.value?.meals?.[mi]?.calories ?? 0
}

function dishCalories(mi: number, ii: number) {
  return nutri.value?.meals?.[mi]?.items?.[ii]?.calories
}

function dishCalText(mi: number, ii: number) {
  const value = dishCalories(mi, ii)
  return value == null ? '—' : String(Math.round(value))
}

function addMeal() {
  const used = new Set(form.value.meals.map((m) => m.meal_type))
  const next = MEAL_ORDER.find((t) => !used.has(t))
  if (!next) {
    ElMessage.info('四餐都齐了，可以直接加菜')
    return
  }
  form.value.meals.push({ meal_type: next, name: MEAL_LABELS[next].slice(2), calories: 0, items: [] })
  form.value.meals.sort((a, b) => MEAL_ORDER.indexOf(a.meal_type) - MEAL_ORDER.indexOf(b.meal_type))
}

function removeMeal(mi: number) {
  form.value.meals.splice(mi, 1)
}

function addDish(meal: PlanMeal) {
  meal.items.push({ name: '', portion_g: 150 })
}

function removeDish(mi: number, ii: number) {
  form.value.meals[mi].items.splice(ii, 1)
}

// ---- 换一道 ----
const swapOpen = ref(false)
const swapLoading = ref(false)
const swapTarget = ref<{ mi: number; ii: number; mealType: string; name: string } | null>(null)
const alternatives = ref<FoodAlternative[]>([])

async function openSwap(mi: number, ii: number) {
  const meal = form.value.meals[mi]
  const item = meal.items[ii]
  if (!(item.name || '').trim()) {
    ElMessage.info('先填个菜名，再找替换')
    return
  }
  swapTarget.value = { mi, ii, mealType: meal.meal_type, name: item.name }
  swapOpen.value = true
  swapLoading.value = true
  alternatives.value = []
  try {
    alternatives.value = await foodApi.getFoodAlternatives(item.name, meal.meal_type, 8)
    if (!alternatives.value.length) ElMessage.info('营养库里没找到热量相近的替换，可以直接改菜名')
  } finally {
    swapLoading.value = false
  }
}

function applySwap(alt: FoodAlternative) {
  if (!swapTarget.value) return
  const { mi, ii } = swapTarget.value
  const item = form.value.meals[mi].items[ii]
  item.name = alt.name
  item.portion_g = alt.common_portion_g || item.portion_g
  item.category = alt.category
  swapOpen.value = false
}

// ---- 运动 / 睡眠 ----
function addExercise() {
  form.value.exercises.push({ type: '快走', duration_min: 30, calories_burned: 0, tips: '', intensity: 'moderate' })
}

function removeExercise(index: number) {
  form.value.exercises.splice(index, 1)
}

async function doSave(rebalance?: 'portion' | 'exercise') {
  if (!editing.value || !plan.value) return
  saving.value = true
  try {
    const items = form.value.exercises.filter((e) => e.type)
    const resp = await planApi.updatePlanDay(plan.value.id, editing.value.day_index, {
      meals: cleanMeals(),
      exercises: items,
      sleep: {
        ...(editing.value.sleep || {}),
        bedtime: form.value.bedtime,
        wake_at: form.value.wakeAt,
        target_h: form.value.targetH,
      },
      water_ml: form.value.water,
      ...(rebalance ? { rebalance } : {}),
    })
    const added = resp.nutrition?.exercise_added
    const msg = added?.added_min
      ? `已保存，并追加 ${added.added_min} 分钟运动抵消超出的热量`
      : '已保存，营养已按库重算，改动会同步到日程待办'
    ElMessage.success(msg)
    warnOpen.value = false
    editOpen.value = false
    await load()
    emit('changed')
  } finally {
    saving.value = false
  }
}

function saveEdit() {
  if (!cleanMeals().length) {
    ElMessage.warning('至少留一餐')
    return
  }
  if (nutri.value?.status === 'high') {
    // 弹窗照旧提示超限，但按用户偏好预选默认处理方式
    warnAction.value = overLimitPolicy.value === 'portion'
      ? 'portion'
      : overLimitPolicy.value === 'exercise' ? 'exercise' : 'save'
    warnOpen.value = true
    return
  }
  doSave()
}

/** 超限弹窗里点「按此处理」：按当前选项决定是否重排 / 加运动 */
function confirmWarn() {
  doSave(warnAction.value === 'save' ? undefined : warnAction.value)
}

async function loadPolicy() {
  try {
    const p = await profileApi.getProfile()
    overLimitPolicy.value = p.over_limit_policy || 'ask'
  } catch {
    overLimitPolicy.value = 'ask'
  }
}

/** 改偏好：存进健康档案，所有方案共用一个默认值 */
async function savePolicy(value: string | number | boolean | undefined) {
  const policy = String(value) as OverLimitPolicy
  policySaving.value = true
  try {
    await profileApi.saveProfile({ over_limit_policy: policy })
    ElMessage.success(
      policy === 'ask'
        ? '以后超限先弹窗，由你手动选择'
        : `以后超限默认${POLICY_LABEL[policy]}，弹窗里仍可临时改`,
    )
  } catch {
    overLimitPolicy.value = 'ask'
  } finally {
    policySaving.value = false
  }
}

// ---- 导入日程 / 沿用 ----
async function importDay(day: PlanDay) {
  if (!plan.value) return
  busy.value = true
  try {
    const r = await planApi.importPlanToSchedule(plan.value.id, { mode: 'day', day_index: day.day_index })
    if (!notifyReactivated(r)) ElMessage.success(r.message)
    await load()
    emit('changed')
  } finally {
    busy.value = false
  }
}

async function importWeek() {
  if (!plan.value) return
  busy.value = true
  try {
    const r = await planApi.importPlanToSchedule(plan.value.id, { mode: 'week' })
    if (!notifyReactivated(r)) ElMessage.success(r.message)
    await load()
    emit('changed')
    router.push('/m/schedule')
  } finally {
    busy.value = false
  }
}

const reuseDialog = ref(false)
const reuseMode = ref<'reframe' | 'copy'>('reframe')

async function doReuse() {
  if (!plan.value) return
  busy.value = true
  try {
    const created = await planApi.reusePlan(plan.value.id, { mode: reuseMode.value })
    ElMessage.success(`${created.days_count || created.days?.length || 0} 天方案已排到 ${created.start_date} 起`)
    reuseDialog.value = false
    emit('changed')
    emit('close')
  } finally {
    busy.value = false
  }
}

onMounted(async () => {
  await Promise.all([load(), loadPolicy()])
})
</script>

<template>
  <div class="detail">
    <div class="topbar">
      <el-button text @click="emit('close')">‹ 返回</el-button>
      <span class="topbar-title">计划详情</span>
      <span class="topbar-version">v{{ plan?.plan_version || 1 }}</span>
    </div>

    <div v-if="loading" class="skeleton">加载中…</div>

    <template v-else-if="plan">
      <div class="hero">
        <div class="hero-title">{{ plan.title }}</div>
        <div class="hero-sub">
          {{ plan.start_date }} 起 · {{ days.length }} 天 · {{ PROVIDER_LABEL[plan.provider] || plan.provider }}
          <el-tag v-if="plan.status === 'active' && plan.phase === 'scheduled'" size="small"
                  effect="plain" type="warning" round style="margin-left: 6px">待开始</el-tag>
          <el-tag v-else-if="plan.status === 'active'" size="small" effect="dark" round style="margin-left: 6px">进行中</el-tag>
          <el-tag v-else size="small" effect="plain" type="info" round style="margin-left: 6px">历史方案</el-tag>
        </div>
        <div v-if="plan.summary" class="hero-summary">{{ plan.summary }}</div>
        <div v-if="plan.reactivated_at" class="reactivate-bar">
          ↻ 已重新启用 · {{ plan.reactivated_at.slice(5, 16).replace('T', ' ') }}
          <em v-if="plan.original_start_date">原 {{ plan.original_start_date }} 起，已排到今天</em>
        </div>
      </div>

      <div v-if="expired" class="expired-bar">
        这份方案的日期已过（{{ days[0]?.date }} 起），导入时会自动顺延到今天，任务才不会落空。
      </div>

      <!-- 档案变了：判定区间已按最新体重/诉求重算，但餐食仍是旧目标排的 -->
      <div v-if="plan.targets_drift" class="drift-bar">
        <div class="drift-text">📐 {{ plan.targets_drift.message }}</div>
        <button class="drift-btn" @click="router.push('/m/plan')">去生成新方案</button>
      </div>

      <!-- 本期评价：停用（归档）后是最终分，进行中给实时进度 -->
      <section v-if="review" class="block">
        <h3 class="block-title">
          本期评价
          <el-tag v-if="review.status === 'not_started'" size="small" type="info" effect="plain">未执行</el-tag>
          <el-tag v-else-if="plan?.phase === 'scheduled'" size="small" type="warning" effect="plain" round>未开始</el-tag>
          <el-tag v-else-if="review.final" size="small" effect="dark" round>{{ review.grade }} · {{ review.score }} 分</el-tag>
          <el-tag v-else size="small" type="info" effect="plain" round>进行中 · 计分中</el-tag>
        </h3>

        <div v-if="review.status === 'not_started'" class="review-empty">
          {{ review.headline }}
        </div>

        <template v-else>
          <div class="review-card">
            <template v-if="review.final">
              <div class="review-score">
                <b>{{ review.score }}</b><em>/100</em>
                <span class="rs-grade">{{ review.grade }}</span>
              </div>
              <div class="review-main">
                <p class="review-headline">{{ review.headline }}</p>
                <p class="review-meta">
                  {{ (review.period.start || '').slice(5) }} → {{ (review.period.end || '').slice(5) }} ·
                  {{ review.period.days }} 天 · 完成 {{ review.stats.tasks_done }}/{{ review.stats.tasks_total }} 项
                </p>
              </div>
            </template>
            <template v-else>
              <div class="review-progress">
                <b>{{ review.stats.tasks_done }}<em>/{{ review.stats.tasks_total }} 项</em></b>
                <span>本次共 {{ review.stats.days_with_tasks || 0 }} 天任务 · 连续 {{ review.stats.longest_streak || 0 }} 天全清</span>
              </div>
            </template>
          </div>
          <p v-if="!review.final" class="review-tip">
            <template v-if="plan?.phase === 'scheduled'">
              这份方案 {{ plan.start_date }} 才开始，还没进入执行期；到点后自动接手，期间仍按「进行中」那份执行。
            </template>
            <template v-else>
              方案还在进行中，这里只显示进度；停用（归档）后会给这一期算最终评价，并留在「历史方案」里。
            </template>
          </p>

          <!-- 分项得分与评语只在停用后出现，避免刚开头就下结论 -->
          <template v-if="review.final">
            <div class="review-dims">
              <div v-for="d in review.dimensions" :key="d.key" class="rd">
                <div class="rd-head"><span>{{ d.label }}</span><b>{{ d.score }}<i>/{{ d.max }}</i></b></div>
                <div class="rd-bar"><span :style="{ width: `${Math.round((d.score / d.max) * 100)}%` }"></span></div>
                <p class="rd-detail">{{ d.detail }}</p>
              </div>
            </div>

            <div v-if="review.highlights.length" class="review-notes good">
              <b>做得好的</b>
              <p v-for="(h, i) in review.highlights" :key="i">· {{ h }}</p>
            </div>
            <div v-if="review.suggestions.length" class="review-notes todo">
              <b>下一期建议</b>
              <p v-for="(s, i) in review.suggestions" :key="i">· {{ s }}</p>
            </div>
          </template>
        </template>
      </section>

      <section class="block">
        <h3 class="block-title">
          每日目标
          <em v-if="plan.targets_drift" class="title-note">生成时口径</em>
        </h3>
        <div class="targets">
          <div class="target">
            <span class="t-label">热量</span>
            <b>{{ targets.calorie_min || '—' }}~{{ targets.calorie_max || '—' }}</b>
            <span class="t-unit">kcal</span>
          </div>
          <div class="target">
            <span class="t-label">蛋白</span>
            <b>{{ targets.protein_min_g || '—' }}~{{ targets.protein_max_g || '—' }}</b>
            <span class="t-unit">g</span>
          </div>
          <div class="target">
            <span class="t-label">饮水</span>
            <b>{{ targets.water_min_ml || '—' }}~{{ targets.water_max_ml || '—' }}</b>
            <span class="t-unit">ml</span>
          </div>
          <div class="target">
            <span class="t-label">睡眠</span>
            <b>{{ targets.sleep_min_h || '—' }}~{{ targets.sleep_max_h || '—' }}</b>
            <span class="t-unit">小时</span>
          </div>
          <div class="target">
            <span class="t-label">运动消耗</span>
            <b>{{ targets.exercise_burn_min || '—' }}~{{ targets.exercise_burn_max || '—' }}</b>
            <span class="t-unit">kcal</span>
          </div>
          <div class="target">
            <span class="t-label">每周训练</span>
            <b>{{ targets.exercise_days_per_week || '—' }}</b>
            <span class="t-unit">天</span>
          </div>
        </div>
        <p class="target-note">
          区间口径：热量 -10%~+5%（训练日上限按额外消耗的 40% 回补）；蛋白按下限~上限；
          饮水体重×30~×40 ml；睡眠 7~9 h；运动消耗下限取热量缺口的 50%，上限为 1.8 倍。
          打卡判定用区间 + 浮动容忍（热量 ±5%、饮水 -10%、睡眠 -0.5h、运动 -15%），
          落在容忍内也算达标，不会因为差一点判失败。
        </p>

        <!-- 超限处理偏好：改菜后热量超上限时，弹窗默认选中哪一项 -->
        <div class="policy-card">
          <div class="policy-head">
            <span class="policy-title">超限处理</span>
            <span class="policy-sub">改菜超过热量上限时，弹窗默认按这种方式</span>
          </div>
          <el-radio-group
            v-model="overLimitPolicy"
            size="small"
            :disabled="policySaving"
            class="policy-group"
            @change="savePolicy"
          >
            <el-radio-button value="ask">手动选择</el-radio-button>
            <el-radio-button value="portion">缩份量</el-radio-button>
            <el-radio-button value="exercise">加运动</el-radio-button>
          </el-radio-group>
          <p class="policy-hint">
            {{
              overLimitPolicy === 'ask'
                ? '每次超限都弹窗问你，不自动改动。'
                : overLimitPolicy === 'portion'
                  ? '弹窗默认「按比例缩份量」，主食与菜等比减量。'
                  : '弹窗默认「加运动抵消」，把多出的热量折算成当天运动时长。'
            }}弹窗仍会出现，可以临时改别的处理方式。
          </p>
        </div>
      </section>

      <section class="block">
        <h3 class="block-title">逐天安排</h3>
        <div v-for="day in days" :key="day.id" class="day-card">
          <div class="day-head" @click="toggle(day.day_index)">
            <div class="day-info">
              <b>第 {{ day.day_index }} 天 · {{ day.theme || '—' }}</b>
              <span>{{ shortDate(day.date) }} · {{ Math.round(day.estimated_calories || 0) }} kcal</span>
            </div>
            <div class="day-right">
              <el-tag v-if="day.targets?.is_training_day === false" size="small" type="info" effect="plain">恢复日</el-tag>
              <span class="chev">{{ openDays.has(day.day_index) ? '收起' : '展开' }}</span>
            </div>
          </div>

          <div v-if="openDays.has(day.day_index)" class="day-body">
            <!-- 这天的七项实排值：按这份菜吃完，就是这几组数（生成时已校验落在区间内） -->
            <div v-if="day.nutrition" class="day-nutrition">
              <span><em>蛋白</em> {{ Math.round(day.nutrition.protein_g) }} g</span>
              <span><em>脂肪</em> {{ Math.round(day.nutrition.fat_g) }} g</span>
              <span><em>碳水</em> {{ Math.round(day.nutrition.carb_g) }} g</span>
              <span><em>纤维</em> {{ Math.round(day.nutrition.fiber_g) }} g</span>
              <span><em>钠</em> {{ Math.round(day.nutrition.sodium_mg) }} mg</span>
            </div>
            <p v-if="adjustText(day)" class="day-flag adjust">{{ adjustText(day) }}</p>
            <p v-if="day.day_flags?.relaxed?.length" class="day-flag">
              这天{{ day.day_flags.relaxed.map((r) => `${r.label}放宽到 ${r.value}${r.label === '钠' ? ' mg' : ' g'}（目标 ${r.range_text}）`).join('、') }}
              ——菜库数据不全，这里的值是估算，不是你没吃对
            </p>
            <p v-else-if="day.day_flags?.variety_relaxed" class="day-flag">
              为保证这天菜品能排出七项都达标的组合，没有强制换菜
            </p>
            <div class="meal" v-for="(meal, mi) in day.meals" :key="mi">
              <div class="meal-head">
                <span>{{ MEAL_LABELS[meal.meal_type] || meal.meal_type }}</span>
                <b>{{ Math.round(meal.calories) }} kcal</b>
              </div>
              <div class="meal-items">
                <span v-for="(item, ii) in meal.items" :key="ii" class="dish">
                  {{ item.name }} <em>{{ Math.round(item.portion_g) }}g</em>
                  <i v-if="item.calories">{{ Math.round(item.calories) }}</i>
                </span>
              </div>
            </div>

            <div v-if="exercisesOf(day).length" class="ex-block">
              <div class="ex-head">🏃 运动处方</div>
              <div v-for="(ex, ei) in exercisesOf(day)" :key="ei" class="ex-row">
                <b>{{ ex.type }}</b>
                <span>{{ ex.duration_min }} 分钟 · {{ ex.calories_burned }} kcal · {{ INTENSITY[ex.intensity || 'moderate'] }}</span>
                <div v-if="ex.tips" class="ex-tips">{{ ex.tips }}</div>
              </div>
            </div>

            <div v-if="day.sleep" class="mini-block">
              <div class="mini-head">😴 睡眠方案</div>
              <div class="mini-row">
                {{ day.sleep.bedtime }} 入睡 · {{ day.sleep.wake_at }} 起床 · 目标 {{ day.sleep.target_h }} 小时
              </div>
              <div v-if="day.sleep.tips" class="mini-tips">{{ day.sleep.tips }}</div>
            </div>

            <div v-if="day.water_ml" class="mini-block">
              <div class="mini-head">💧 饮水目标</div>
              <div class="mini-row">{{ day.water_ml }} ml，分 6~8 次喝完</div>
            </div>

            <div class="day-actions">
              <el-button size="small" plain @click="openEdit(day)">改菜 / 改运动</el-button>
              <el-button size="small" type="success" :loading="busy" @click="importDay(day)">导入这一天</el-button>
            </div>
          </div>
        </div>
      </section>

      <div class="bottom-bar">
        <el-button :loading="busy" plain @click="reuseDialog = true">沿用这周</el-button>
        <el-button type="success" :loading="busy" @click="importWeek">导入整周日程</el-button>
      </div>
    </template>

    <!-- 编辑抽屉：餐食 / 运动 / 睡眠 / 饮水 -->
    <el-drawer v-model="editOpen" title="编辑这一天" direction="btt" size="88%" :with-header="false">
      <div v-if="editing" class="edit">
        <div class="edit-head">
          <b>第 {{ editing.day_index }} 天</b>
          <span>改完按营养库重算</span>
        </div>

        <!-- 实时对比条 -->
        <div v-if="nutri" class="nutri" :class="nutri.status">
          <div class="nutri-row">
            <div class="nutri-item">
              <span>热量</span>
              <b>{{ Math.round(nutri.total.calories) }}</b>
              <em>/ {{ nutri.target.calorie_min }}~{{ nutri.target.calorie_max }}</em>
            </div>
            <div class="nutri-item">
              <span>蛋白</span>
              <b>{{ Math.round(nutri.total.protein_g) }}</b>
              <em>/ ≥{{ nutri.target.protein_min_g }}</em>
            </div>
            <div class="nutri-item">
              <span>碳水</span>
              <b>{{ Math.round(nutri.total.carb_g) }}</b>
              <em>g</em>
            </div>
          </div>
          <div v-if="nutri.coverage.total_items > nutri.coverage.matched_items" class="nutri-note">
            有 {{ nutri.coverage.total_items - nutri.coverage.matched_items }} 道菜未收录，按同类均值估算
          </div>
          <div v-for="(w, wi) in nutri.warnings" :key="wi" class="nutri-warn">{{ w.message }}</div>
        </div>
        <div v-else class="nutri neutral">改完菜会自动算热量</div>

        <h4 class="edit-title">
          <span>餐食</span>
          <el-button text type="primary" size="small" @click="addMeal">+ 加一餐</el-button>
        </h4>
        <div v-for="(meal, mi) in form.meals" :key="mi" class="meal-edit">
          <div class="me-head">
            <span>{{ MEAL_LABELS[meal.meal_type] }}</span>
            <b>{{ Math.round(mealCalories(mi)) }} kcal</b>
            <el-button text type="danger" size="small" @click="removeMeal(mi)">删这餐</el-button>
          </div>
          <div v-for="(item, ii) in meal.items" :key="ii" class="dish-edit">
            <el-input v-model="item.name" size="small" placeholder="菜名" class="dish-name" />
            <el-input-number
              v-model="item.portion_g"
              :min="10"
              :max="800"
              :step="10"
              size="small"
              controls-position="right"
              class="dish-g"
            />
            <span class="dish-cal">{{ dishCalText(mi, ii) }}</span>
            <el-button text size="small" @click="openSwap(mi, ii)">换</el-button>
            <el-button text size="small" type="danger" @click="removeDish(mi, ii)">×</el-button>
          </div>
          <el-button text type="primary" size="small" @click="addDish(meal)">+ 加一道菜</el-button>
        </div>

        <h4 class="edit-title"><span>运动处方</span></h4>
        <div v-for="(ex, ei) in form.exercises" :key="ei" class="ex-edit">
          <el-select v-model="ex.type" size="small" style="width: 34%">
            <el-option v-for="t in EX_TYPES" :key="t" :value="t" :label="t" />
          </el-select>
          <el-input-number v-model="ex.duration_min" :min="5" :max="180" :step="5" size="small" controls-position="right" style="width: 100px" />
          <span class="unit">分钟</span>
          <el-select v-model="ex.intensity" size="small" style="width: 76px">
            <el-option value="light" label="轻松" />
            <el-option value="moderate" label="适中" />
            <el-option value="intense" label="吃力" />
          </el-select>
          <el-button text type="danger" size="small" @click="removeExercise(ei)">删</el-button>
        </div>
        <el-button text type="primary" size="small" @click="addExercise">+ 加一项运动</el-button>

        <h4 class="edit-title"><span>睡眠</span></h4>
        <div class="sleep-edit">
          <el-input v-model="form.bedtime" size="small" placeholder="23:00" style="width: 80px" />
          <span>入睡 →</span>
          <el-input v-model="form.wakeAt" size="small" placeholder="07:00" style="width: 80px" />
          <span>起床，目标</span>
          <el-input-number v-model="form.targetH" :min="5" :max="10" :step="0.5" size="small" style="width: 96px" />
          <span>小时</span>
        </div>

        <h4 class="edit-title"><span>饮水目标</span></h4>
        <div class="sleep-edit">
          <el-input-number v-model="form.water" :min="500" :max="4000" :step="100" size="small" style="width: 120px" />
          <span>ml</span>
        </div>

        <div class="edit-actions">
          <el-button @click="editOpen = false">取消</el-button>
          <el-button type="success" :loading="saving" @click="saveEdit">保存</el-button>
        </div>
      </div>
    </el-drawer>

    <!-- 换一道 -->
    <el-dialog v-model="swapOpen" title="换一道" width="88%" align-center>
      <p v-if="swapTarget" class="swap-tip">
        「{{ swapTarget.name }}」热量相近的替换（同餐段，±20%）
      </p>
      <div v-if="swapLoading" class="skeleton">查找中…</div>
      <div v-else class="swap-list">
        <div v-for="alt in alternatives" :key="alt.name" class="swap-item" @click="applySwap(alt)">
          <div class="swap-main">
            <b>{{ alt.name }}</b>
            <span>{{ alt.category }} · 蛋白 {{ alt.protein_g }} g</span>
          </div>
          <div class="swap-cal">
            <b>{{ alt.calories }}</b>
            <span>kcal/100g</span>
          </div>
        </div>
        <el-empty v-if="!alternatives.length" description="没有合适的替换，直接改菜名也行" :image-size="60" />
      </div>
    </el-dialog>

    <!-- 超上限处置：弹窗照旧提示，默认项按档案里的偏好预选 -->
    <el-dialog v-model="warnOpen" title="热量超上限了" width="88%" align-center>
      <p class="warn-text">
        这一天的餐食比上限多了约 {{ Math.round(nutri?.total.calories || 0) - (nutri?.target.calorie_max || 0) }} kcal，
        选一种处理方式，或者原样保存。
      </p>
      <el-radio-group v-model="warnAction" class="warn-group">
        <el-radio value="portion">按比例缩份量<em>让总量回到区间</em></el-radio>
        <el-radio value="exercise">
          加运动抵消<em v-if="nutri?.exercise_suggestion?.added_min">追加 {{ nutri.exercise_suggestion.added_min }} 分钟{{ nutri.exercise_suggestion.type || '' }}</em>
          <em v-else>折算成当天运动时长</em>
        </el-radio>
        <el-radio value="save">就这样保存<em>只记录，不改餐也不加运动</em></el-radio>
      </el-radio-group>
      <p class="warn-policy">
        默认选中「{{ POLICY_LABEL[overLimitPolicy] }}」，可在下方「每日目标 → 超限处理」修改。
      </p>
      <template #footer>
        <el-button text @click="warnOpen = false">继续改</el-button>
        <el-button type="success" :loading="saving" @click="confirmWarn">按此处理</el-button>
      </template>
    </el-dialog>

    <!-- 沿用这周 -->
    <el-dialog v-model="reuseDialog" title="沿用这周" width="86%" align-center>
      <p class="reuse-tip">会新建一份方案并置为进行中，当前方案自动归档到历史，原有打卡记录不受影响。</p>
      <el-radio-group v-model="reuseMode" class="reuse-group">
        <el-radio value="reframe">保留框架，重算餐食（推荐）</el-radio>
        <el-radio value="copy">原样复制，菜名不变</el-radio>
      </el-radio-group>
      <template #footer>
        <el-button @click="reuseDialog = false">取消</el-button>
        <el-button type="success" :loading="busy" @click="doReuse">确定沿用</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
/* ============ 设计 token（与日程 / 运动 / 我的页同一套，仅本页作用域） ============ */
.detail {
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
  --shadow: 0 10px 28px rgba(28, 36, 22, 0.07);
  --shadow-lime: 0 18px 38px rgba(122, 152, 40, 0.2);

  display: flex;
  flex-direction: column;
  gap: 12px;
  padding-bottom: 96px;
  color: var(--ink);
  font-family: 'Manrope', -apple-system, BlinkMacSystemFont, 'PingFang SC', 'HarmonyOS Sans SC',
    'Microsoft YaHei', sans-serif;
  font-size: 13.5px;
  line-height: 1.55;
  -webkit-font-smoothing: antialiased;
}
.detail *,
.detail *::before,
.detail *::after { box-sizing: border-box; }
.detail h3,
.detail h4,
.detail p { margin: 0; }

/* ============ 顶栏 ============ */
.topbar { display: flex; align-items: center; justify-content: space-between; }
.topbar-title { font-size: 15px; font-weight: 800; color: var(--ink); letter-spacing: -0.01em; }
.topbar-version { font-size: 11px; font-weight: 800; color: var(--muted); background: var(--mint); border-radius: 999px; padding: 2px 8px; }

/* ============ 主卡：方案概要 ============ */
.hero {
  background: var(--teal);
  border: 1px solid rgba(23, 26, 20, 0.06);
  color: var(--teal-tx);
  border-radius: 24px;
  padding: 16px 18px;
  box-shadow: var(--shadow-lime);
}
.hero-title { font-size: 16.5px; font-weight: 800; letter-spacing: -0.02em; }
.hero-sub { font-size: 11.5px; color: var(--ink-2); margin-top: 4px; }
.hero-summary { font-size: 11.5px; line-height: 1.6; margin-top: 8px; color: var(--ink-2); }
.hero :deep(.el-tag) {
  height: 21px;
  padding: 0 8px;
  border: none;
  border-radius: 999px;
  font-size: 10px;
  font-weight: 800;
  background: rgba(255, 255, 255, 0.6);
  color: var(--teal-tx);
}
.hero :deep(.el-tag--dark) { background: var(--teal-tx); color: var(--teal); }
.hero :deep(.el-tag--warning) { background: #fbf4e7; color: var(--amber); }
.hero :deep(.el-tag--info) { background: rgba(255, 255, 255, 0.6); color: var(--ink-2); }

/* 提示条 */
.expired-bar {
  font-size: 11.5px;
  line-height: 1.6;
  color: var(--amber);
  background: #fbf4e7;
  border: 1px solid #f0e2c8;
  border-radius: 16px;
  padding: 10px 12px;
}
/* 档案偏离提示：只提示不自动改餐食 */
.drift-bar {
  display: flex;
  align-items: center;
  gap: 10px;
  font-size: 11.5px;
  line-height: 1.55;
  color: var(--ink-2);
  background: var(--mint);
  border: 1px solid var(--line);
  border-radius: 16px;
  padding: 10px 12px;
}
.drift-text { flex: 1; }
.drift-btn {
  flex: none;
  border: none;
  background: var(--teal-tx);
  color: var(--teal);
  font-size: 11px;
  font-weight: 800;
  padding: 6px 11px;
  border-radius: 999px;
  cursor: pointer;
}
.drift-btn:active { background: #000; }
.title-note { font-style: normal; font-size: 10.5px; font-weight: 500; color: var(--muted); margin-left: 6px; }
.reactivate-bar {
  margin-top: 8px;
  font-size: 11px;
  line-height: 1.5;
  color: var(--ink-2);
  background: rgba(255, 255, 255, 0.55);
  border: 1px solid rgba(255, 255, 255, 0.7);
  border-radius: 12px;
  padding: 5px 9px;
  display: flex;
  flex-wrap: wrap;
  gap: 4px 8px;
  align-items: center;
}
.reactivate-bar em { font-style: normal; color: var(--muted); }

/* ============ 本期评价 ============ */
.review-card {
  display: flex;
  align-items: center;
  gap: 14px;
  background: var(--surface);
  border: 1px solid var(--line);
  border-radius: 18px;
  padding: 13px 14px;
  box-shadow: var(--shadow);
}
.review-score { display: flex; align-items: baseline; gap: 2px; position: relative; padding-top: 7px; }
.review-score b { font-size: 32px; line-height: 1; color: var(--ink); font-weight: 800; letter-spacing: -0.03em; }
.review-score em { font-size: 10.5px; color: var(--muted); font-style: normal; }
.rs-grade {
  position: absolute;
  top: -6px;
  right: -12px;
  background: var(--teal-tx);
  color: var(--teal);
  font-size: 9.5px;
  font-weight: 800;
  line-height: 16px;
  padding: 0 6px;
  border-radius: 7px;
}
.review-main { min-width: 0; }
.review-headline { font-size: 12.5px; color: var(--ink); margin: 0; line-height: 1.5; font-weight: 700; }
.review-meta { font-size: 11px; color: var(--muted); margin: 4px 0 0; }
.review-dims { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; margin-top: 8px; }
.rd {
  background: var(--surface);
  border: 1px solid var(--line);
  border-radius: 14px;
  padding: 9px 10px;
  box-shadow: var(--shadow);
}
.rd-head { display: flex; justify-content: space-between; align-items: baseline; font-size: 11.5px; color: var(--muted); }
.rd-head b { font-size: 13px; font-weight: 800; color: var(--ink); font-variant-numeric: tabular-nums; }
.rd-head i { font-size: 9.5px; color: var(--muted); font-style: normal; }
.rd-bar { height: 5px; background: var(--mint); border-radius: 3px; margin: 6px 0 5px; overflow: hidden; }
.rd-bar span { display: block; height: 100%; background: var(--teal-dk); border-radius: 3px; }
.rd-detail { font-size: 10.5px; color: var(--muted); margin: 0; line-height: 1.5; }
.review-notes {
  margin-top: 8px;
  background: var(--surface);
  border: 1px solid var(--line);
  border-radius: 14px;
  padding: 10px 12px;
  box-shadow: var(--shadow);
}
.review-notes b { font-size: 11.5px; font-weight: 800; color: var(--ink); }
.review-notes p { font-size: 11.5px; color: var(--ink-2); margin: 4px 0 0; line-height: 1.55; }
.review-notes.good b { color: #5f7d22; }
.review-notes.todo b { color: var(--amber); }
.review-empty {
  background: var(--surface);
  border: 1px solid var(--line);
  border-radius: 16px;
  padding: 14px;
  font-size: 12px;
  color: var(--muted);
  line-height: 1.65;
  box-shadow: var(--shadow);
}
.review-progress { display: flex; flex-direction: column; gap: 4px; }
.review-progress b { font-size: 22px; color: var(--ink); line-height: 1.2; font-weight: 800; font-variant-numeric: tabular-nums; }
.review-progress b em { font-size: 10.5px; color: var(--muted); font-style: normal; margin-left: 2px; }
.review-progress span { font-size: 11px; color: var(--muted); }
.review-tip { font-size: 11px; color: var(--muted); line-height: 1.6; margin: 6px 2px 0; }

/* ============ 板块标题 ============ */
.block-title { font-size: 13px; font-weight: 800; color: var(--ink); margin: 2px 2px 8px; display: flex; align-items: center; }
.block-title :deep(.el-tag) {
  margin-left: 6px;
  height: 20px;
  padding: 0 7px;
  border: none;
  border-radius: 7px;
  font-size: 9.5px;
  font-weight: 800;
  background: var(--mint);
  color: var(--ink-2);
}
.block-title :deep(.el-tag--dark) { background: var(--teal-tx); color: var(--teal); }
.block-title :deep(.el-tag--warning) { background: #fbf4e7; color: var(--amber); }
.block-title :deep(.el-tag--info) { background: #f4f6ee; color: var(--muted); }

/* ============ 每日目标 ============ */
.targets { display: grid; grid-template-columns: repeat(3, 1fr); gap: 8px; }
.target {
  background: var(--mint);
  border-radius: 14px;
  padding: 10px 6px;
  display: flex;
  flex-direction: column;
  align-items: center;
}
.t-label { font-size: 10px; color: var(--muted); }
.target b {
  font-size: 13.5px;
  font-weight: 800;
  color: var(--ink);
  margin-top: 2px;
  white-space: nowrap;
  font-variant-numeric: tabular-nums;
  letter-spacing: -0.01em;
}
.target-note { font-size: 10.5px; line-height: 1.7; color: var(--muted); margin: 8px 0 0; }
.t-unit { font-size: 9.5px; color: var(--muted); }

/* ============ 逐天安排 ============ */
.day-card {
  background: var(--surface);
  border: 1px solid var(--line);
  border-radius: 18px;
  margin-bottom: 8px;
  box-shadow: var(--shadow);
  overflow: hidden;
}
.day-head { display: flex; justify-content: space-between; align-items: center; padding: 13px 14px; gap: 8px; cursor: pointer; }
.day-info { display: flex; flex-direction: column; min-width: 0; }
.day-info b { font-size: 13px; font-weight: 800; color: var(--ink); }
.day-info span { font-size: 11px; color: var(--muted); margin-top: 2px; }
.day-right { display: flex; align-items: center; gap: 6px; }
.chev { font-size: 11px; font-weight: 800; color: var(--ink-2); background: var(--mint); border-radius: 999px; padding: 3px 9px; }
.day-body { padding: 0 14px 14px; border-top: 1px solid var(--line); }
/* 七项实排值：一眼看到"按这份菜吃完是几克"，不用自己加 */
.day-nutrition {
  display: flex;
  flex-wrap: wrap;
  gap: 4px 12px;
  margin: 10px 0 2px;
  padding: 8px 10px;
  border-radius: 12px;
  background: var(--mint);
  font-size: 11px;
  color: var(--ink-2);
  font-variant-numeric: tabular-nums;
}
.day-nutrition em { font-style: normal; color: var(--muted); margin-right: 2px; }
.day-flag { margin: 6px 0; font-size: 10.5px; line-height: 1.55; color: var(--amber); }
/* 处置调整是「做过的事」，不是「问题」：用中性色，别和降级警告混成一片橙 */
.day-flag.adjust { color: var(--ink-2); background: #f4f6ee; border-radius: 8px; padding: 5px 8px; }

.meal { padding: 9px 0; border-bottom: 1px dashed var(--line); }
.meal-head { display: flex; justify-content: space-between; font-size: 12px; color: var(--ink-2); font-weight: 700; }
.meal-head b { color: var(--ink); font-size: 12px; font-weight: 800; font-variant-numeric: tabular-nums; }
.meal-items { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 7px; }
.dish {
  font-size: 11.5px;
  color: var(--ink);
  background: var(--mint);
  border-radius: 8px;
  padding: 3px 8px;
  font-weight: 700;
}
.dish em { font-style: normal; color: var(--muted); margin-left: 2px; font-weight: 500; }
.dish i { font-style: normal; color: var(--muted); margin-left: 4px; font-size: 10px; font-weight: 500; }

.ex-block {
  margin-top: 10px;
  background: var(--mint);
  border: 1px solid transparent;
  border-radius: 14px;
  padding: 11px 12px;
}
.ex-head { font-size: 12px; font-weight: 800; color: var(--ink); margin-bottom: 7px; }
.ex-row { font-size: 11.5px; color: var(--ink-2); margin-bottom: 8px; }
.ex-row b { color: var(--ink); font-weight: 800; margin-right: 8px; }
.ex-tips { color: var(--muted); margin-top: 2px; }

.mini-block {
  margin-top: 10px;
  background: #f4f6ee;
  border: 1px solid var(--line);
  border-radius: 14px;
  padding: 11px 12px;
}
.mini-head { font-size: 12px; font-weight: 800; color: var(--ink); }
.mini-row { font-size: 11.5px; color: var(--ink-2); margin-top: 4px; }
.mini-tips { font-size: 11.5px; color: var(--muted); margin-top: 4px; line-height: 1.5; }

.day-actions { display: flex; gap: 8px; margin-top: 12px; }
.day-actions :deep(.el-button) { flex: 1; margin-left: 0; }

.bottom-bar {
  position: sticky;
  bottom: 0;
  display: flex;
  gap: 8px;
  padding: 10px 0;
  background: linear-gradient(to top, var(--paper) 70%, rgba(240, 247, 222, 0));
}
.bottom-bar :deep(.el-button) { flex: 1; margin-left: 0; }

/* ============ 编辑抽屉内容 ============ */
.reuse-tip { font-size: 12.5px; color: var(--ink-2); line-height: 1.6; margin-top: 0; }
.reuse-group { display: flex; flex-direction: column; gap: 12px; }
.edit { padding: 4px 2px 20px; color: var(--ink); font-size: 13px; }
.edit-head { display: flex; justify-content: space-between; align-items: baseline; margin-bottom: 6px; }
.edit-head b { font-size: 15px; font-weight: 800; }
.edit-head span { font-size: 11px; color: var(--muted); }
.edit-title {
  font-size: 12px;
  color: var(--ink);
  margin: 16px 0 8px;
  display: flex;
  align-items: center;
  justify-content: space-between;
}
.edit-title span { font-weight: 800; }
.nutri {
  border-radius: 14px;
  padding: 10px 12px;
  background: var(--mint);
  border: 1px solid transparent;
  margin-top: 8px;
}
.nutri.high { background: #fdeeec; border-color: #f6d8d8; }
.nutri.low { background: #fbf4e7; border-color: #f0e2c8; }
.nutri.neutral { color: var(--muted); font-size: 11.5px; background: #f4f6ee; border-color: var(--line); }
.nutri-row { display: flex; gap: 10px; }
.nutri-item { flex: 1; display: flex; flex-direction: column; align-items: center; }
.nutri-item span { font-size: 10px; color: var(--muted); }
.nutri-item b { font-size: 16px; font-weight: 800; color: var(--ink); font-variant-numeric: tabular-nums; }
.nutri.high .nutri-item b { color: var(--red); }
.nutri-item em { font-style: normal; font-size: 9.5px; color: var(--muted); }
.nutri-note { font-size: 10.5px; color: var(--amber); margin-top: 6px; }
.nutri-warn { font-size: 11px; color: var(--red); margin-top: 4px; line-height: 1.5; }
.meal-edit {
  background: #f8faef;
  border: 1px solid var(--line);
  border-radius: 14px;
  padding: 9px 10px;
  margin-bottom: 8px;
}
.me-head { display: flex; align-items: center; gap: 8px; font-size: 12.5px; color: var(--ink); }
.me-head span { font-weight: 800; }
.me-head b { margin-left: auto; font-size: 11.5px; font-weight: 800; color: var(--ink-2); font-variant-numeric: tabular-nums; }
.dish-edit { display: flex; align-items: center; gap: 4px; margin-top: 6px; }
.dish-name { flex: 1; min-width: 0; }
.dish-g { width: 96px; flex: none; }
.dish-cal { width: 34px; text-align: right; font-size: 10.5px; color: var(--muted); }
.ex-edit { display: flex; align-items: center; gap: 6px; margin-bottom: 8px; }
.unit { font-size: 11.5px; color: var(--muted); }
.sleep-edit { display: flex; align-items: center; gap: 6px; flex-wrap: wrap; font-size: 11.5px; color: var(--muted); }
.edit-actions { display: flex; gap: 10px; margin-top: 24px; }
.edit-actions :deep(.el-button) { flex: 1; margin-left: 0; }

/* 换一道 */
.swap-tip { font-size: 12px; color: var(--ink-2); margin-top: 0; }
.swap-list { max-height: 52vh; overflow-y: auto; }
.swap-item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 11px 12px;
  border-radius: 14px;
  background: #f8faef;
  border: 1px solid var(--line);
  margin-bottom: 6px;
  cursor: pointer;
}
.swap-main { display: flex; flex-direction: column; min-width: 0; }
.swap-main b { font-size: 13px; font-weight: 800; color: var(--ink); }
.swap-main span { font-size: 10.5px; color: var(--muted); margin-top: 2px; }
.swap-cal { display: flex; flex-direction: column; align-items: flex-end; }
.swap-cal b { font-size: 15px; font-weight: 800; color: var(--ink); font-variant-numeric: tabular-nums; }
.swap-cal span { font-size: 9.5px; color: var(--muted); }

/* 超上限处置 */
.warn-text { font-size: 12.5px; color: var(--ink-2); line-height: 1.7; margin-top: 0; }
.warn-group { display: flex; flex-direction: column; gap: 10px; align-items: stretch; width: 100%; }
.warn-group :deep(.el-radio) {
  margin-right: 0;
  height: auto;
  padding: 10px 12px;
  border: 1px solid var(--line);
  border-radius: 14px;
  background: #f8faef;
  align-items: flex-start;
}
.warn-group :deep(.el-radio.is-checked) { border-color: var(--teal-dk); background: var(--mint); }
.warn-group :deep(.el-radio__label) { display: flex; flex-direction: column; line-height: 1.45; font-size: 12.5px; color: var(--ink); font-weight: 700; }
.warn-group em { font-style: normal; font-size: 11px; font-weight: 500; color: var(--muted); margin-top: 2px; }
.warn-policy { font-size: 11px; color: var(--muted); margin: 10px 0 0; line-height: 1.6; }

/* 超限处理偏好卡片 */
.policy-card {
  margin-top: 10px;
  background: var(--surface);
  border: 1px solid var(--line);
  border-radius: 16px;
  padding: 12px;
  box-shadow: var(--shadow);
}
.policy-head { display: flex; flex-direction: column; gap: 2px; margin-bottom: 9px; }
.policy-title { font-size: 12.5px; font-weight: 800; color: var(--ink); }
.policy-sub { font-size: 11px; color: var(--muted); }
.policy-group :deep(.el-radio-button__inner) {
  padding: 7px 10px;
  font-size: 11.5px;
  font-weight: 700;
  border-color: var(--line);
  background: var(--surface);
  color: var(--ink-2);
}
.policy-group :deep(.el-radio-button.is-active .el-radio-button__inner) {
  background: var(--teal);
  border-color: var(--teal);
  color: var(--teal-tx);
  box-shadow: none;
}
.policy-hint { font-size: 11px; color: var(--muted); line-height: 1.6; margin: 9px 0 0; }

.skeleton { text-align: center; color: var(--muted); font-size: 12.5px; padding: 30px 0; }

/* ============ Element Plus 本地换肤（只在本组件作用域内） ============ */
.detail :deep(.el-button) {
  border-radius: 999px;
  font-weight: 700;
  border-color: var(--line);
  background: var(--surface);
  color: var(--ink-2);
}
.detail :deep(.el-button:hover) { border-color: var(--teal-dk); color: var(--ink); background: #fbfdf4; }
.detail :deep(.el-button--success) { background: var(--teal); border-color: var(--teal); color: var(--teal-tx); }
.detail :deep(.el-button--success:hover) { background: var(--teal-dk); border-color: var(--teal-dk); color: var(--teal-tx); }
.detail :deep(.el-button--primary.is-text),
.detail :deep(.el-button--danger.is-text) { background: transparent; border-color: transparent; }
.detail :deep(.el-button--primary.is-text) { color: #5f7d22; }
.detail :deep(.el-button--danger.is-text) { color: var(--red); }
.detail :deep(.el-empty__description p) { font-size: 12px; color: var(--muted); }
</style>
