<script setup lang="ts">
// 食物识别页：拖拽上传 → AI 识别 → 结果卡片 → 手动校准 → 保存为饮食记录
import { computed, onMounted, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import type { UploadRequestOptions } from 'element-plus'
import { UploadFilled } from '@element-plus/icons-vue'
import * as foodApi from '@/api/food'
import type { FoodOption, MatchCandidate, Recognition } from '@/api/food'
import { getOverview } from '@/api/stats'
import { getProfile } from '@/api/profile'
import { useAuthStore } from '@/stores/auth'
import { isMobile } from '@/composables/useIsMobile'
import { assetUrl } from '@/api/http'

const auth = useAuthStore()
const current = ref<Recognition | null>(null)   // 当前识别结果
const history = ref<Recognition[]>([])          // 识别历史
const recognizing = ref(false)
const rerunningId = ref<number | null>(null)   // 正在重新识别的记录 id（按钮转圈用）
const saving = ref(false)
const saveDialog = ref(false)
const mealType = ref('lunch')
const MEAL_LABELS: Record<string, string> = { breakfast: '早餐', lunch: '午餐', dinner: '晚餐', snack: '加餐' }
// 三用途：方案内打卡（会自动打卡）/ 额外加餐 / 只看热量（不落记录）
const usage = ref<foodApi.PhotoUsage>('extra')
const USAGE_OPTIONS: { value: foodApi.PhotoUsage; label: string; desc: string }[] = [
  { value: 'plan', label: '方案内打卡', desc: '按方案吃的那餐：先比对菜品，对上才打卡' },
  { value: 'extra', label: '额外加餐', desc: '方案之外自己加的：记下来但不打卡，超了也不算你的错' },
  { value: 'inspect', label: '只看热量', desc: '不保存为饮食记录，不影响当天摄入' },
]

// ---- 方案内打卡的判定（菜品对不对）----
// 判定必须由后端做：菜名归一化、方案基线都在这边，前端算不了。
// 切餐次、切用途都要重新请求——用午餐的结论去打晚餐的卡，结果一定是错的。
const planCheck = ref<foodApi.PlanCheck | null>(null)
const checkLoading = ref(false)
// 只对上一部分时：缺的那几道算不算吃过。默认「没吃」更保守——
// 按方案补上等于替用户多吃了几道，除非他自己说吃了
const missing = ref<'skip' | 'plan'>('skip')
const replaceManual = ref(false)     // 这餐已有手动记录时是否一起替换
const overrideG = ref<Record<number, number>>({})   // 下标 → 用户改过的克数

const CHECK_TEXT: Record<string, string> = {
  ok: '和方案对得上',
  partial: '只对上一部分',
  fail: '和方案对不上',
}

/** 照片没对上的那几道菜名（partial 的选项要念出来是哪几道） */
const gapText = computed(() =>
  (planCheck.value?.only_plan || []).map((d) => d.dish_name).join('、'))

async function loadPlanCheck() {
  const rec = current.value
  if (!rec || usage.value !== 'plan') {
    planCheck.value = null
    return
  }
  checkLoading.value = true
  try {
    const res = await foodApi.planCheck(rec.id, mealType.value)
    planCheck.value = res
    missing.value = 'skip'
    replaceManual.value = false
    const init: Record<number, number> = {}
    // 用 dish_index（照片里第几道菜）而不是 matches 的顺序：未命中的菜会让两者错位
    res.matches.forEach((m) => { init[m.dish_index] = m.photo_portion_g })
    overrideG.value = init
  } catch {
    planCheck.value = null      // 判定失败不该挡住保存流程，退回「不校验」的旧行为
  } finally {
    checkLoading.value = false
  }
}

/** 菜品对不上就不能打卡，没有绕过——所以 fail 一档永久禁用按钮 */
const planBlocked = computed(() => {
  const c = planCheck.value
  if (usage.value !== 'plan' || !c) return false
  return c.status === 'fail'
})

/** 改过克数的项：只有和识别值不同的才提交，避免无谓重算 */
const overridesPayload = computed(() => {
  const c = planCheck.value
  if (!c) return []
  const out: { index: number; portion_g: number }[] = []
  for (const m of c.matches) {
    const grams = Math.round(overrideG.value[m.dish_index] || 0)
    if (grams > 0 && grams !== Math.round(m.photo_portion_g)) {
      out.push({ index: m.dish_index, portion_g: grams })
    }
  }
  return out
})

/** 按钮文案跟着判定走：不能打的时候要写清楚为什么，别让人点半天没反应 */
const saveButtonText = computed(() => {
  if (usage.value !== 'plan') return '保存为饮食记录（+2 分）'
  const c = planCheck.value
  if (!c) return `保存并打卡${MEAL_LABELS[mealType.value] || ''}（+2 分）`
  if (c.status === 'ok') return `打卡${c.meal_label}（+2 分）`
  if (c.status === 'partial') {
    return missing.value === 'plan' ? '补上缺的几道并打卡（+2 分）' : '按拍到的打卡（+2 分）'
  }
  return '和方案对不上，不能打卡'
})

/** 点失败出口：每个都直接改变弹窗状态，不做二次确认 */
function pickCheckOption(opt: foodApi.PlanCheckOption) {
  if (opt.key === 'extra') usage.value = 'extra'
  else if (opt.key === 'inspect') usage.value = 'inspect'
  else if (opt.key === 'missing_skip') missing.value = 'skip'
  else if (opt.key === 'missing_plan') missing.value = 'plan'
  else if (opt.key === 'change_meal') {
    const suggest = planCheck.value?.suggest_meal
    if (suggest) mealType.value = suggest.meal_type   // 触发 watch 重新判定
    else ElMessage.info('换一下上面的餐次，会自动重新比对')
  }
}

/** 按当前时间猜这餐属于哪一顿（用户可改，只是别让他每次手选） */
function guessMeal(): string {
  const h = new Date().getHours()
  return h < 10 ? 'breakfast' : h < 15 ? 'lunch' : h < 21 ? 'dinner' : 'snack'
}

// ---- 识别历史分页 ----
const page = ref(1)
const pageSize = ref(10)
const total = ref(0)

// ---- 手动校准（只允许改菜名+克数，营养由后端按本地营养库重算）----
const correctDialog = ref(false)
const correctSaving = ref(false)
const foodLibrary = ref<FoodOption[]>([])
const correctForm = ref({ dish_name: '', portion_g: 200 })
const correctIndex = ref<number | null>(null)   // 多菜时校准的是第几道
const dishCount = computed(() => current.value?.result?.dishes?.length || 0)
const saveLabel = computed(() => {
  if (current.value?.food_record_id) return '✓ 已保存为饮食记录'
  return dishCount.value > 1 ? `保存为饮食记录（${dishCount.value} 道菜）` : '保存为饮食记录'
})

// 当前选中库内菜（提供标准份量与每100g营养，用于快捷份量和实时预览）
const selectedOption = computed(() =>
  foodLibrary.value.find((f) => f.name === correctForm.value.dish_name) || null,
)

// 只读营养预览：库内菜按 per_100g 实时推导，口径与后端一致
const previewNut = computed(() => {
  const opt = selectedOption.value
  if (!opt) return null
  const factor = Math.max(correctForm.value.portion_g, 0) / 100
  const r1 = (n: number) => Math.round(n * factor * 10) / 10
  return {
    calories: r1(opt.per_100g.calories),
    protein_g: r1(opt.per_100g.protein_g),
    fat_g: r1(opt.per_100g.fat_g),
    carb_g: r1(opt.per_100g.carb_g),
  }
})

// ---- 低置信度 / 易混淆：候选确认（后端给出 match.needs_confirm 时才出现）----
const confirmName = ref('')
const confirmPortion = ref(200)
const confirming = ref(false)
const matchInfo = computed(() => current.value?.result?.match || null)
const METHOD_LABELS: Record<string, string> = {
  exact: '精确命中', synonym: '同义归一', alias: '别名命中',
  contains: '包含匹配', fuzzy: '相似匹配', confusable: '易混淆',
}

function methodLabel(method: string) {
  return METHOD_LABELS[method] || method
}

// 匹配标签颜色：需确认=橙，否则=绿
function matchTagType(match: { needs_confirm?: boolean } | null | undefined) {
  return match?.needs_confirm ? 'warning' : 'success'
}

// 时间显示：手机上省掉年份，节省一行空间
function fmtTime(s: string) {
  const d = new Date(s)
  if (Number.isNaN(d.getTime())) return s
  return isMobile.value
    ? d.toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' })
    : d.toLocaleString('zh-CN')
}

// 用后端候选初始化确认表单：默认选第一个（通常是模型原判）
function initConfirm() {
  const info = current.value?.result?.match
  if (!info?.needs_confirm) return
  const first = info.candidates?.[0]
  confirmName.value = first?.name || current.value?.dish_name || ''
  confirmPortion.value = Math.round(first?.common_portion_g || current.value?.result?.vision?.portion_g || 200)
}

function pickCandidate(c: MatchCandidate) {
  confirmName.value = c.name
  confirmPortion.value = Math.round(c.common_portion_g || confirmPortion.value)
}

async function confirmMatch() {
  if (!current.value || !confirmName.value) return
  confirming.value = true
  try {
    current.value = await foodApi.correctRecognition(current.value.id, {
      dish_name: confirmName.value,
      portion_g: confirmPortion.value,
      dish_index: dishCount.value > 1 ? 0 : undefined,
    })
    ElMessage.success('已按你选择的菜品重算营养')
    await loadHistory()
  } finally {
    confirming.value = false
  }
}

// ---- 今日热量预算 + 禁忌/过敏提示（只做提示，不阻断保存）----
const budget = ref<{ today: number; target: number | null } | null>(null)
const restrictions = ref<string[]>([])
const budgetPct = computed(() => {
  if (!budget.value?.target) return 0
  return Math.min(100, Math.round((budget.value.today / budget.value.target) * 100))
})

async function loadMeta() {
  try {
    const o = await getOverview()
    budget.value = { today: o.today_calories, target: o.daily_target }
  } catch { /* 未登录/接口异常时静默 */ }
  try {
    const p = await getProfile()
    restrictions.value = p.dietary_restrictions || []
  } catch { /* 同上 */ }
}

const ALLERGY_RULES: { key: string; words: string[] }[] = [
  { key: '海鲜过敏', words: ['虾', '蟹', '鱼', '贝', '蛤', '鱿', '海参'] },
  { key: '花生过敏', words: ['花生'] },
  { key: '坚果过敏', words: ['花生', '核桃', '杏仁', '腰果', '榛子'] },
  { key: '乳糖不耐', words: ['牛奶', '奶油', '黄油', '芝士', '酸奶'] },
  { key: '麸质过敏', words: ['面粉', '面包', '面条', '馒头', '饺子', '面筋'] },
  { key: '大豆过敏', words: ['豆腐', '豆干', '腐竹', '黄豆', '豆芽', '豆浆', '豆瓣酱'] },
  { key: '不吃猪肉', words: ['猪肉', '五花', '排骨', '里脊', '火腿', '腊肉', '叉烧'] },
  { key: '不吃牛肉', words: ['牛肉', '牛腩'] },
  { key: '清真', words: ['猪肉', '五花', '排骨', '火腿', '腊肉', '料酒'] },
]
const MEAT_WORDS = ['肉', '鸡', '鸭', '鱼', '虾', '蟹', '排骨', '火腿', '腊肉', '牛', '羊']

// 命中档案里的禁忌/过敏项时给红条提示（数据来自营养库标准食材 + 标签）
const dietWarnings = computed(() => {
  // 后端已按档案算过一份（口径统一），有结果就直接用
  const fromServer = current.value?.result?.dietary_warnings
  if (fromServer && fromServer.length) return fromServer
  const info = matchInfo.value
  if (!info || !current.value?.is_food) return []
  const text = [current.value.dish_name, ...(info.ingredients || [])].join(' ')
  const tags = [...(info.dietary_tags || []), ...(info.taste_tags || [])]
  const hits: string[] = []
  const portion = current.value.result.vision.portion_g || 1
  const fatPer100 = ((current.value.result.nutrition.fat_g || 0) * 100) / portion

  for (const rule of restrictions.value) {
    if (rule === '素食' || rule === '蛋奶素') {
      const hit = MEAT_WORDS.find((w) => text.includes(w))
      if (hit) hits.push(`${rule}：这道菜含「${hit}」`)
      continue
    }
    if (rule === '忌辣') {
      if (tags.some((t) => t.includes('辣')) || ['辣椒', '花椒', '豆瓣酱'].some((w) => text.includes(w))) {
        hits.push('忌辣：含辣椒/花椒/豆瓣酱')
      }
      continue
    }
    if (rule === '少油') {
      if (fatPer100 > 15) hits.push(`少油：脂肪约 ${Math.round(fatPer100)}g/100g`)
      continue
    }
    if (rule === '低糖') {
      if (['白糖', '冰糖'].some((w) => text.includes(w))) hits.push('低糖：含糖调味')
      continue
    }
    if (rule === '低盐') continue // 营养结果里暂无钠，留给后续
    const map = ALLERGY_RULES.find((x) => x.key === rule)
    if (!map) continue
    const hit = map!.words.find((w) => text.includes(w) || tags.some((t) => t.includes(w)))
    if (hit) hits.push(`${rule}：含「${hit}」`)
  }
  return [...new Set(hits)]
})

// 结果卡上的份量快捷：小/标准/大份 = 标准份 × 0.7 / 1 / 1.3
async function quickPortion(factor: number) {
  if (!current.value) return
  const base = matchInfo.value?.candidates?.[0]?.common_portion_g
    || current.value.result?.vision?.portion_g
    || 200
  confirming.value = true
  try {
    current.value = await foodApi.correctRecognition(current.value.id, {
      dish_name: current.value.dish_name,
      portion_g: Math.max(10, Math.round(base * factor)),
      dish_index: dishCount.value > 1 ? 0 : undefined,
    })
    ElMessage.success(`已按${factor < 1 ? '小份' : factor > 1 ? '大份' : '标准份'}重算营养`)
    await loadHistory()
  } finally {
    confirming.value = false
  }
}

// 自定义上传：拿到文件后调识别接口（120s 超时已封装在 api 层）
async function doUpload(options: UploadRequestOptions) {
  recognizing.value = true
  current.value = null
  try {
    current.value = await foodApi.recognize(options.file)
    initConfirm()
    if (!current.value.is_food) {
      ElMessage.warning('图片中未识别到食物，换一张试试？')
    } else if (current.value.provider === 'mock') {
      ElMessage.warning('AI 识别失败，当前为本地模拟结果，建议手动校准')
    } else {
      ElMessage.success('识别完成！')
    }
    await loadHistory()
  } catch {
    ElMessage.error('识别失败：AI 处理超时或服务繁忙，请稍后再试')
  } finally {
    recognizing.value = false
  }
}

async function loadHistory() {
  const r = await foodApi.getRecognitions(page.value, pageSize.value)
  history.value = r.items
  total.value = r.total
}

function onPageChange(p: number) {
  page.value = p
  loadHistory()
}

// 删除识别记录（仅"未保存为饮食记录"的草稿可删）
async function deleteRec(row: Recognition) {
  await ElMessageBox.confirm(
    '确定删除这条识别记录吗？删除后不可恢复。',
    '删除识别记录',
    { confirmButtonText: '删除', cancelButtonText: '取消', type: 'warning' },
  )
  await foodApi.deleteRecognition(row.id)
  ElMessage.success('已删除')
  await loadHistory()
}

// 重新识别：复用原图再跑一次模型（已保存/未保存的记录都允许；
// 已保存的会由后端同步更新对应饮食记录，保证统计一致）
async function rerunRec(row: Recognition) {
  rerunningId.value = row.id
  try {
    const r = await foodApi.rerunRecognition(row.id)
    current.value = r        // ★ 右侧结果卡片直接展示重跑后的完整明细（克重/食材/营养）
    initConfirm()
    if (!r.is_food) {
      ElMessage.warning('重新识别完成：图片中未识别到食物')
    } else if (r.provider === 'mock') {
      ElMessage.warning('AI 视觉模型暂时不可用（可能限流），当前为本地模拟结果，建议稍后再试')
    } else {
      ElMessage.success(`已重新识别：${r.dish_name}`)
    }
    await loadHistory()
  } catch {
    ElMessage.error('重新识别失败：AI 处理超时或服务繁忙，请稍后再试')
  } finally {
    rerunningId.value = null
  }
}

function openSave() {
  // 默认值由后端按「照片里的菜 vs 方案那餐」算好：命中方案就是方案内打卡，
  // 否则按额外加餐。默认错了归因就错（方案错 vs 自己多吃，处置方向完全不同）
  const r = current.value
  mealType.value = r?.suggested_meal_type || r?.plan_compare?.meal_type || guessMeal()
  usage.value = (r?.suggested_usage as foodApi.PhotoUsage) || 'extra'
  saveDialog.value = true
  loadPlanCheck()
}

// 切用途、切餐次都要重新判定：比对结果和餐次绑死，换一餐结论就变了
watch([usage, mealType], () => {
  if (saveDialog.value) loadPlanCheck()
})

/** 用途改了要重新判断：方案里没有这道菜却选「方案内」，等于把额外吃的算成方案，归因会错 */
const usageHint = computed(() => {
  const cmp = current.value?.plan_compare
  if (!cmp) return ''
  if (usage.value === 'plan') return ''      // 方案内由判定卡片接管，不再重复提示
  if (usage.value === 'extra' && cmp.matches.length) {
    return '这道菜方案里有：选「额外加餐」会按方案外加一份算，总量会偏高'
  }
  return cmp.hint
})

async function confirmSave() {
  if (!current.value) return
  // 只看热量：不落饮食记录，也就不会影响当天摄入和判定
  if (usage.value === 'inspect') {
    saveDialog.value = false
    ElMessage.info('仅查看成分，没有保存为饮食记录')
    return
  }
  if (planBlocked.value) {
    ElMessage.warning('照片里的菜和方案这餐对不上，不能按方案打卡——改成「额外加餐」或换一餐再试')
    return
  }
  saving.value = true
  try {
    const r = await foodApi.saveRecognition(current.value.id, {
      meal_type: mealType.value,
      usage: usage.value,
      missing: missing.value,
      replace_manual: replaceManual.value,
      overrides: overridesPayload.value,
    })
    // 后端拦下来（理论上前端已挡，这里是兜底）：原因和出口要原样给用户看
    if (!r.saved) {
      ElMessage.warning(r.reason || r.message || '没能打卡')
      await loadPlanCheck()
      return
    }
    auth.fetchMe().catch(() => {})   // 顶栏积分刷新
    ElMessage.success(r.message || `已保存为饮食记录 +${r.points_awarded || 0} 分`)
    saveDialog.value = false
    await loadHistory()
    loadMeta()   // 预算卡刷新
    if (current.value) current.value.food_record_id = r.food_record_id ?? null
  } finally {
    saving.value = false
  }
}

// ---- 校准交互 ----
async function openCorrect(index?: number | Event) {
  // 模板里 @click="openCorrect" 会把 MouseEvent 传进来，这里只认数字下标
  if (typeof index !== "number") index = undefined
  if (!current.value) return
  correctIndex.value = index ?? null
  const dish = index != null ? current.value.result?.dishes?.[index] : null
  const g = Math.round(dish?.portion_g || current.value.result?.vision?.portion_g || 0)
  correctForm.value = {
    dish_name: dish?.dish_name || (current.value.is_food ? current.value.dish_name : ''),
    portion_g: g > 0 ? g : 200,
  }
  correctDialog.value = true
  if (!foodLibrary.value.length) {
    try {
      foodLibrary.value = await foodApi.getFoodLibrary()
    } catch {
      foodLibrary.value = []   // 拉取失败不阻塞：仍可自由输入菜名
    }
  }
}

function onDishChange(name: string) {
  const opt = foodLibrary.value.find((f) => f.name === name)
  if (opt) correctForm.value.portion_g = opt.common_portion_g
}

function applySize(factor: number) {
  const opt = selectedOption.value
  if (opt) correctForm.value.portion_g = Math.round(opt.common_portion_g * factor)
}

async function confirmCorrect() {
  if (!current.value) return
  const name = correctForm.value.dish_name.trim()
  if (!name) {
    ElMessage.warning('请选择或输入菜名')
    return
  }
  correctSaving.value = true
  try {
    current.value = await foodApi.correctRecognition(current.value.id, {
      dish_name: name,
      portion_g: correctForm.value.portion_g,
      dish_index: correctIndex.value ?? undefined,
    })
    ElMessage.success('已校准，营养值已按本地营养库重算')
    correctDialog.value = false
    await loadHistory()
  } finally {
    correctSaving.value = false
  }
}

onMounted(() => {
  loadHistory()
  loadMeta()
})
</script>

<template>
  <div class="page">
    <el-row :gutter="16">
      <!-- 左：上传 + 图片 -->
      <el-col :span="10" :xs="24">
        <div class="page-card">
          <h3 class="page-title">📷 拍一拍，识别食物</h3>
          <el-upload
            drag
            accept="image/jpeg,image/png,image/webp"
            :show-file-list="false"
            :http-request="doUpload"
          >
            <div class="upload-inner" :class="{ 'upload-inner-mobile': isMobile }">
              <template v-if="current?.image_url">
                <img :src="assetUrl(current.image_url)" class="preview" />
              </template>
              <template v-else-if="recognizing">
                <el-icon class="is-loading" :size="40" color="#67c23a"><i class="el-icon-loading" /></el-icon>
                <p>AI 正在识别，请稍候（约几秒到几十秒）…</p>
              </template>
              <template v-else>
                <el-icon :size="48" color="#c0c4cc"><UploadFilled /></el-icon>
                <p>{{ isMobile ? '点这里拍照或选图' : '拖一张食物照片到这里，或' }} <em>点击上传</em></p>
                <p class="tip">支持 jpg / png / webp，最大 10MB</p>
              </template>
            </div>
          </el-upload>
          <div v-if="recognizing" class="loading-bar"><el-progress :percentage="90" :indeterminate="true" :duration="3" status="success" /></div>
        </div>
      </el-col>

      <!-- 右：识别结果 -->
      <el-col :span="14" :xs="24">
        <div class="page-card">
          <h3 class="page-title">🔍 识别结果</h3>
          <el-empty v-if="!current && !recognizing" description="上传图片后这里显示识别结果" :image-size="80" />
          <template v-else-if="current">
            <template v-if="current.is_food">
              <div v-if="budget?.target" class="budget">
                <span class="budget-text">
                  今日 {{ Math.round(budget.today) }} / {{ budget.target }} kcal
                  <b v-if="budget.today <= budget.target" class="muted">还可摄入 {{ Math.round(budget.target - budget.today) }}</b>
                  <b v-else class="over">已超出 {{ Math.round(budget.today - budget.target) }}</b>
                </span>
                <el-progress
                  :percentage="budgetPct" :stroke-width="8" :show-text="false"
                  :status="budgetPct >= 100 ? 'exception' : 'success'" style="flex:1"
                />
              </div>
              <el-alert
                v-for="w in dietWarnings" :key="w"
                :title="w" type="error" :closable="false" show-icon
                style="margin-bottom:8px"
              />
              <div class="result-head">
                <span class="dish">{{ current.dish_name }}</span>
                <el-tag v-if="current.result.vision.corrected" type="warning" size="small">已手动校准</el-tag>
                <el-tag type="info" size="small">识别来源：{{ current.provider === 'mock' ? '本地模拟' : 'AI 视觉模型' }}</el-tag>
                <el-tag
                  v-if="matchInfo"
                  size="small" effect="plain"
                  :type="matchInfo.needs_confirm ? 'warning' : 'success'"
                >
                  匹配：{{ matchInfo.method_label }} · {{ Math.round(matchInfo.score * 100) }}%
                </el-tag>
              </div>
              <div v-if="!current.result.vision.corrected" class="conf">
                置信度
                <el-progress :percentage="Math.round(current.confidence * 100)" :stroke-width="12" status="success" style="flex:1" />
              </div>
              <p v-if="current.result.vision.basis && !current.result.vision.corrected" class="basis">📏 {{ current.result.vision.basis }}</p>

              <div v-if="dishCount > 1" class="dishes-box">
                <div class="dishes-title">这张图里识别到 {{ dishCount }} 道菜，合计 {{ current.result.nutrition.calories }} kcal</div>

                <!-- 手机：卡片列表 -->
                <div v-if="isMobile" class="dish-cards">
                  <div v-for="(row, idx) in current.result.dishes" :key="idx" class="dish-card">
                    <div class="dc-row1">
                      <b class="dc-name">{{ row.dish_name }}</b>
                      <span class="dc-kcal">{{ Math.round(row.nutrition.calories) }} kcal</span>
                    </div>
                    <div class="dc-row2">
                      <span class="dc-portion">{{ row.portion_g }}g</span>
                      <el-tag v-if="row.match" size="small" :type="matchTagType(row.match)">
                        {{ row.match.method_label }}
                      </el-tag>
                      <el-tag v-if="row.match?.needs_confirm" size="small" type="danger" effect="plain">需确认</el-tag>
                      <el-tag v-if="row.warnings && row.warnings.length" size="small" type="danger" effect="plain">禁忌</el-tag>
                    </div>
                    <div v-if="row.warnings && row.warnings.length" class="dc-warn">{{ row.warnings.join('；') }}</div>
                    <el-button
                      size="small" type="warning" plain class="dc-btn"
                      @click="openCorrect(idx)"
                    >{{ row.match?.needs_confirm ? '确认' : '校准' }}</el-button>
                  </div>
                </div>

                <!-- 桌面：原表格 -->
                <el-table v-else :data="current.result.dishes" size="small">
                  <el-table-column prop="dish_name" label="菜品" min-width="110" />
                  <el-table-column prop="portion_g" label="克重(g)" width="90" />
                  <el-table-column prop="nutrition.calories" label="热量(kcal)" width="100" />
                  <el-table-column label="匹配" width="150">
                    <template #default="{ row }">
                      <template v-if="row.match">
                        <el-tag size="small" :type="row.match.needs_confirm ? 'warning' : 'success'">
                          {{ row.match.method_label }}
                        </el-tag>
                        <el-tag
                          v-if="row.match.needs_confirm" size="small" type="danger" effect="plain"
                          style="margin-left:4px"
                        >需确认</el-tag>
                      </template>
                    </template>
                  </el-table-column>
                  <el-table-column label="提示" width="80">
                    <template #default="{ row }">
                      <el-tooltip v-if="row.warnings && row.warnings.length" :content="row.warnings.join('；')">
                        <el-tag size="small" type="danger" effect="plain">禁忌</el-tag>
                      </el-tooltip>
                    </template>
                  </el-table-column>
                  <el-table-column label="操作" width="90">
                    <template #default="{ row, $index }">
                      <el-button link type="warning" size="small" @click="openCorrect($index)">
                        {{ row.match?.needs_confirm ? '确认' : '校准' }}
                      </el-button>
                    </template>
                  </el-table-column>
                </el-table>
              </div>
              <el-alert
                v-if="current.provider === 'mock' && !current.result.vision.corrected"
                title="AI 视觉模型不可用，当前为本地模拟结果，请手动校准或稍后重试"
                type="error" :closable="false" style="margin-top:12px"
              />

              <el-descriptions :column="isMobile ? 2 : 4" border style="margin:12px 0">
                <el-descriptions-item label="热量" class="hl">{{ current.calories }} kcal</el-descriptions-item>
                <el-descriptions-item label="蛋白质">{{ current.result.nutrition.protein_g }} g</el-descriptions-item>
                <el-descriptions-item label="脂肪">{{ current.result.nutrition.fat_g }} g</el-descriptions-item>
                <el-descriptions-item label="碳水">{{ current.result.nutrition.carb_g }} g</el-descriptions-item>
              </el-descriptions>
              <el-alert v-if="!current.result.nutrition.matched" :title="current.result.nutrition.note" type="warning" :closable="false" style="margin-bottom:12px" />
              <el-alert v-else title="营养值来自本地营养数据库，口径精确一致" type="success" :closable="false" style="margin-bottom:12px" />

              <!-- 手机：食材用标签流；桌面：保留表格 -->
              <div v-if="current.result.vision.corrected" class="ing-tags">
                <span class="ing-label">主要食材：</span>
                <el-tag v-for="ing in current.result.vision.ingredients" :key="ing.name" size="small" type="info">{{ ing.name }}</el-tag>
                <span v-if="!current.result.vision.ingredients.length" class="muted">营养库暂无食材明细</span>
              </div>
              <div v-else-if="isMobile" class="ing-list">
                <div v-for="ing in current.result.vision.ingredients" :key="ing.name" class="ing-row">
                  <span>{{ ing.name }}</span>
                  <span class="muted">{{ ing.portion_g }} g</span>
                </div>
                <div v-if="!current.result.vision.ingredients.length" class="muted">暂无食材明细</div>
              </div>
              <el-table v-else :data="current.result.vision.ingredients" size="small" max-height="200">
                <el-table-column prop="name" label="食材" />
                <el-table-column prop="portion_g" label="估算克重(g)" width="140" />
              </el-table>

              <div v-if="matchInfo?.needs_confirm && !current.result.vision.corrected" class="confirm-box">
                <el-alert
                  type="warning" :closable="false" show-icon
                  :title="`需要你确认：${matchInfo.reasons.join('；')}`"
                  description="从候选里选一个，或改克重后点确认，营养值会按营养库重算。"
                />
                <div class="cand-list">
                  <div
                    v-for="c in matchInfo.candidates" :key="c.name"
                    class="cand" :class="{ active: confirmName === c.name }"
                    @click="pickCandidate(c)"
                  >
                    <div class="cand-name">
                      {{ c.name }}
                      <el-tag size="small" type="info">{{ methodLabel(c.match_method) }} {{ Math.round(c.score * 100) }}%</el-tag>
                    </div>
                    <div class="cand-meta">
                      {{ c.category }} · 标准 {{ c.common_portion_g }}g
                      <span v-if="c.calories_per_100g"> · {{ Math.round(c.calories_per_100g) }} kcal/100g</span>
                      <span v-if="c.distinguish_tip"> · {{ c.distinguish_tip }}</span>
                    </div>
                  </div>
                </div>
                <div class="confirm-actions">
                  <span class="muted">克重</span>
                  <el-input-number v-model="confirmPortion" :min="10" :max="3000" :step="50" style="width:140px" />
                  <span class="muted">g</span>
                  <el-button type="warning" :loading="confirming" @click="confirmMatch">确认并重算营养</el-button>
                </div>
              </div>

              <div class="portion-quick">
                <span class="muted">份量：</span>
                <el-button size="small" :loading="confirming" @click="quickPortion(0.7)">小份</el-button>
                <el-button size="small" :loading="confirming" @click="quickPortion(1)">标准份</el-button>
                <el-button size="small" :loading="confirming" @click="quickPortion(1.3)">大份</el-button>
              </div>

              <div class="actions">
                <el-button type="success" :disabled="!!current.food_record_id" @click="openSave">
                  {{ saveLabel }}
                </el-button>
                <el-button type="warning" plain @click="openCorrect()">识别有误？手动校准</el-button>
              </div>
            </template>
            <template v-else>
              <el-alert title="未识别到食物，请换一张图片" type="warning" :closable="false" />
              <el-button type="warning" plain style="margin-top:12px" @click="openCorrect()">其实是食物？手动校准</el-button>
            </template>
          </template>
        </div>
      </el-col>
    </el-row>

    <!-- 识别历史 -->
    <div class="page-card">
      <h3 class="page-title">🕘 识别历史</h3>

      <!-- 手机：卡片列表 -->
      <div v-if="isMobile" class="hist-cards">
        <div v-for="row in history" :key="row.id" class="hist-card">
          <el-image
            v-if="row.image_url"
            :src="assetUrl(row.image_url)" fit="cover" class="hist-thumb"
            :preview-src-list="[assetUrl(row.image_url)]" preview-teleported
          />
          <div v-else class="hist-thumb hist-thumb-empty">无图</div>
          <div class="hist-body">
            <div class="hist-row1">
              <b class="hist-name">{{ row.dish_name || '未识别' }}</b>
              <span class="hist-kcal">{{ row.calories }} kcal</span>
            </div>
            <div class="hist-row2">
              <el-tag v-if="row.food_record_id" type="success" size="small">已保存</el-tag>
              <el-tag v-else-if="!row.is_food" type="info" size="small">非食物</el-tag>
              <el-tag v-else type="warning" size="small">未保存</el-tag>
              <el-tag v-if="row.provider === 'mock'" type="danger" size="small" effect="plain">模拟</el-tag>
              <span class="hist-time">{{ fmtTime(row.created_at) }}</span>
            </div>
            <div class="hist-row3">
              <el-button
                size="small" type="primary" plain
                :loading="rerunningId === row.id"
                @click="rerunRec(row)"
              >重新识别</el-button>
              <el-button
                size="small" type="danger" plain
                :disabled="!!row.food_record_id"
                @click="deleteRec(row)"
              >删除</el-button>
            </div>
          </div>
        </div>
        <el-empty v-if="!history.length" description="还没有识别记录" :image-size="70" />
      </div>

      <!-- 桌面：原表格 -->
      <el-table v-else :data="history" size="small">
        <el-table-column label="图片" width="90">
          <template #default="{ row }">
            <el-image v-if="row.image_url" :src="assetUrl(row.image_url)" fit="cover" style="width:56px;height:56px;border-radius:6px" :preview-src-list="[assetUrl(row.image_url)]" preview-teleported />
          </template>
        </el-table-column>
        <el-table-column prop="dish_name" label="菜品" min-width="120" />
        <el-table-column prop="calories" label="热量(kcal)" width="110" />
        <el-table-column label="状态" width="130">
          <template #default="{ row }">
            <el-tag v-if="row.food_record_id" type="success" size="small">已保存记录</el-tag>
            <el-tag v-else-if="!row.is_food" type="info" size="small">非食物</el-tag>
            <el-tag v-else type="warning" size="small">未保存</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="来源" width="100">
          <template #default="{ row }">
            <el-tag v-if="row.provider === 'mock'" type="danger" size="small" effect="plain">本地模拟</el-tag>
            <el-tag v-else type="primary" size="small" effect="plain">AI 视觉</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="created_at" label="时间" min-width="150">
          <template #default="{ row }">{{ new Date(row.created_at).toLocaleString('zh-CN') }}</template>
        </el-table-column>
        <el-table-column label="操作" width="170" fixed="right">
          <template #default="{ row }">
            <el-button
              link type="primary" size="small"
              :loading="rerunningId === row.id"
              :title="row.food_record_id ? '重新识别会同步更新已保存的饮食记录' : '复用原图再识别一次'"
              @click="rerunRec(row)"
            >重新识别</el-button>
            <el-button
              link type="danger" size="small"
              :disabled="!!row.food_record_id"
              :title="row.food_record_id ? '已生成饮食记录，禁止删除（保护溯源）' : ''"
              @click="deleteRec(row)"
            >删除</el-button>
          </template>
        </el-table-column>
      </el-table>
      <div class="pager">
        <el-pagination
          background
          :layout="isMobile ? 'prev, pager, next' : 'total, prev, pager, next'"
          :total="total"
          :page-size="pageSize"
          :current-page="page"
          :pager-count="isMobile ? 5 : 7"
          @current-change="onPageChange"
        />
      </div>
    </div>

    <!-- 保存：先定用途，再定餐次，方案内还要先过「菜品对不对」这一关 -->
    <el-dialog v-model="saveDialog" title="这张照片算什么？" :width="isMobile ? '100%' : '440px'" :fullscreen="isMobile">
      <div class="usage-list">
        <div v-for="opt in USAGE_OPTIONS" :key="opt.value"
             class="usage-item" :class="{ active: usage === opt.value }"
             @click="usage = opt.value">
          <div class="usage-head">
            <span class="usage-label">{{ opt.label }}</span>
            <span v-if="opt.value === (current?.suggested_usage || 'extra')" class="usage-badge">推荐</span>
          </div>
          <p class="usage-desc">{{ opt.desc }}</p>
        </div>
      </div>

      <template v-if="usage !== 'inspect'">
        <p class="dlg-label">这是哪一餐？</p>
        <el-radio-group v-model="mealType">
          <el-radio-button v-for="(label, key) in MEAL_LABELS" :key="key" :value="key">{{ label }}</el-radio-button>
        </el-radio-group>
      </template>

      <!-- 方案内：菜品判定结果。对不上就不让打卡，并把原因和出口摆出来 -->
      <div v-if="usage === 'plan'" v-loading="checkLoading"
           class="check-card" :class="planCheck ? `check-${planCheck.status}` : ''">
        <template v-if="planCheck">
          <div class="check-head">
            <span class="check-badge">{{ CHECK_TEXT[planCheck.status] }}</span>
            <span class="check-reason">{{ planCheck.reason }}</span>
          </div>

          <div v-if="planCheck.matches.length" class="check-rows">
            <div v-for="m in planCheck.matches" :key="m.dish_index" class="check-row">
              <span class="check-dish">{{ m.plan_dish }}</span>
              <span class="check-plan">方案 {{ m.plan_portion_g }}g</span>
              <el-input-number v-model="overrideG[m.dish_index]" :min="5" :max="3000" :step="10"
                               size="small" controls-position="right" style="width:104px" />
              <span class="check-unit">g</span>
            </div>
            <p class="check-tip">克数按你实际吃的改，热量和判定会跟着重算</p>
          </div>

          <p v-if="planCheck.only_photo.length" class="check-line">
            方案里没有：{{ planCheck.only_photo.map((d) => d.dish_name).join('、') }}
            <span class="muted">— 按「额外加餐」另记更准</span>
          </p>
          <p v-if="planCheck.only_plan.length && planCheck.status !== 'fail'" class="check-line muted">
            没拍到：{{ planCheck.only_plan.map((d) => d.dish_name).join('、') }}
          </p>
          <p v-if="planCheck.portion.significant" class="check-line">
            {{ planCheck.portion.detail }}
            <span v-if="planCheck.portion.needs_confirm" class="muted"> — 识别可能不准，不对就在上面改克数</span>
          </p>
          <p v-if="planCheck.existing.note" class="check-line muted">{{ planCheck.existing.note }}</p>
          <el-checkbox v-if="planCheck.existing.manual_records" v-model="replaceManual" size="small">
            替换这餐已有的 {{ planCheck.existing.manual_records }} 条手动记录
          </el-checkbox>

          <!-- 只对上一部分：缺的那几道算不算吃过。两个都是正常路径，数据各自自洽 -->
          <div v-if="planCheck.status === 'partial'" class="check-actions">
            <p class="check-ask">「{{ gapText }}」这几道吃了吗？</p>
            <el-radio-group v-model="missing" class="missing-group">
              <el-radio value="skip">没吃，只记拍到的</el-radio>
              <el-radio value="plan">都吃了，只是没拍到（按方案补上）</el-radio>
            </el-radio-group>
            <div class="check-btns">
              <el-button v-for="o in planCheck.options.filter((x) => !x.key.startsWith('missing_'))"
                         :key="o.key" size="small" text @click="pickCheckOption(o)">
                {{ o.label }}
              </el-button>
            </div>
          </div>

          <!-- 一道都对不上：这不算这餐，只能换个方式记（没有"仍按方案打卡"这条路） -->
          <div v-if="planCheck.status === 'fail'" class="check-actions">
            <p class="check-ask">这不算{{ planCheck.meal_label }}的打卡，换个方式记：</p>
            <div class="check-btns">
              <el-button v-for="o in planCheck.options" :key="o.key" size="small" @click="pickCheckOption(o)">
                {{ o.label }}
              </el-button>
            </div>
            <p class="check-foot">如果确实按方案吃了这餐，点「取消」后手动打卡就行，方案那份会照常记账</p>
          </div>
        </template>
        <p v-else-if="!checkLoading" class="check-line muted">
          没能比对方案，直接保存会按你选的方式记
        </p>
      </div>

      <p v-if="usageHint" class="usage-hint">{{ usageHint }}</p>

      <!-- 额外加餐：同种菜分量不同，仍要说清差多少 -->
      <div v-if="usage === 'extra' && current?.plan_compare?.matches?.length" class="compare-box">
        <p class="compare-title">和方案比</p>
        <div v-for="m in current.plan_compare.matches" :key="m.dish_name" class="compare-row">
          <span>{{ m.plan_dish }}</span>
          <span class="compare-nums">
            方案 {{ m.plan_portion_g }}g / 照片 {{ m.photo_portion_g }}g
            <b :class="m.kcal_delta > 0 ? 'up' : 'down'">
              {{ m.kcal_delta > 0 ? '+' : '' }}{{ m.kcal_delta }} kcal
            </b>
          </span>
        </div>
        <p class="compare-note">按你实际吃的算，方案那份不会重复计</p>
      </div>

      <template #footer>
        <el-button @click="saveDialog = false">取消</el-button>
        <el-button v-if="usage === 'inspect'" @click="confirmSave">只看，不保存</el-button>
        <el-button v-else type="success" :loading="saving" :disabled="planBlocked" @click="confirmSave">
          {{ saveButtonText }}
        </el-button>
      </template>
    </el-dialog>

    <!-- 手动校准：只改菜名+克数，热量/营养由本地营养库自动重算 -->
    <el-dialog v-model="correctDialog" title="手动校准识别结果" :width="isMobile ? '100%' : '440px'" :fullscreen="isMobile">
      <p class="dlg-tip">热量与营养由本地营养库按「每100g × 克数」自动计算，不可手动修改</p>
      <div class="form-row">
        <span class="form-label">菜名</span>
        <el-select
          v-model="correctForm.dish_name"
          filterable allow-create default-first-option
          placeholder="搜索营养库菜品，或直接输入菜名"
          style="flex:1"
          @change="onDishChange"
        >
          <el-option v-for="f in foodLibrary" :key="f.name" :label="f.name" :value="f.name">
            <span>{{ f.name }}</span>
            <span class="opt-extra">{{ f.category }} · 标准{{ f.common_portion_g }}g</span>
          </el-option>
        </el-select>
      </div>
      <div class="form-row">
        <span class="form-label">克数</span>
        <el-input-number v-model="correctForm.portion_g" :min="10" :max="3000" :step="50" style="width:150px" />
        <template v-if="selectedOption">
          <el-button size="small" @click="applySize(0.7)">小份</el-button>
          <el-button size="small" @click="applySize(1)">标准份</el-button>
          <el-button size="small" @click="applySize(1.3)">大份</el-button>
        </template>
      </div>
      <div class="preview-box">
        <template v-if="previewNut">
          <span>校准后：</span>
          <b class="hl">{{ previewNut.calories }} kcal</b>
          <span class="muted">蛋白 {{ previewNut.protein_g }}g · 脂肪 {{ previewNut.fat_g }}g · 碳水 {{ previewNut.carb_g }}g</span>
        </template>
        <span v-else class="muted">该菜不在营养库中，将按通用估算值计算热量</span>
      </div>
      <template #footer>
        <el-button @click="correctDialog = false">取消</el-button>
        <el-button type="warning" :loading="correctSaving" @click="confirmCorrect">保存校准</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
.upload-inner { padding: 28px 0; }
.upload-inner .tip { font-size: 12px; color: #c0c4cc; }
.preview { max-height: 260px; border-radius: 8px; }
.loading-bar { margin-top: 10px; }
.result-head { display: flex; align-items: center; gap: 10px; }
.result-head .dish { font-size: 22px; font-weight: 700; }
.conf { display: flex; align-items: center; gap: 10px; color: #909399; font-size: 13px; }
.basis { margin: 6px 0 0; font-size: 12px; color: #909399; }
.hl { color: #f56c6c; font-weight: 700; }
.actions { display: flex; gap: 10px; margin-top: 14px; }
.confirm-box { margin: 4px 0 12px; }
.cand-list { margin: 10px 0; display: flex; flex-direction: column; gap: 8px; }
.cand { border: 1px solid #ebeef5; border-radius: 8px; padding: 8px 12px; cursor: pointer; transition: all .15s; }
.cand:hover { border-color: #e6a23c; background: #fdf6ec; }
.cand.active { border-color: #e6a23c; background: #fdf6ec; box-shadow: 0 0 0 1px #e6a23c inset; }
.cand-name { font-size: 14px; font-weight: 600; display: flex; align-items: center; gap: 8px; }
.cand-meta { font-size: 12px; color: #909399; margin-top: 4px; }
.confirm-actions { display: flex; align-items: center; gap: 8px; }
.budget { display: flex; align-items: center; gap: 10px; margin-bottom: 10px; padding: 8px 10px; background: #f5f7fa; border-radius: 8px; }
.budget-text { font-size: 13px; color: #606266; white-space: nowrap; }
.budget-text .over { color: #f56c6c; }
.portion-quick { display: flex; align-items: center; gap: 8px; margin-top: 10px; }
.dishes-box { margin: 10px 0 4px; }
.dishes-title { font-size: 13px; color: #606266; margin-bottom: 6px; }
.ing-tags { display: flex; align-items: center; flex-wrap: wrap; gap: 6px; }
.ing-label { font-size: 13px; color: #606266; }
.muted { font-size: 12px; color: #909399; }
.dlg-tip { margin: 0 0 14px; font-size: 12px; color: #909399; }

/* 三用途选择：三块可点的卡片，一眼看出差别（不是三个看不出区别的 radio） */
.usage-list { display: flex; flex-direction: column; gap: 8px; }
.usage-item {
  border: 1px solid #e4e7ed; border-radius: 10px; padding: 10px 12px;
  cursor: pointer; transition: border-color .15s, background .15s;
}
.usage-item.active { border-color: #67c23a; background: #f0f9eb; }
.usage-head { display: flex; align-items: center; gap: 6px; }
.usage-label { font-size: 14px; font-weight: 600; color: #303133; }
.usage-badge {
  font-size: 10px; padding: 0 5px; border-radius: 8px;
  background: #67c23a; color: #fff; line-height: 16px;
}
.usage-desc { margin: 4px 0 0; font-size: 12px; line-height: 1.5; color: #909399; }
.usage-hint { margin: 10px 0 0; font-size: 12px; line-height: 1.5; color: #909399; }
.usage-hint.warn { color: #e6a23c; }
/* 方案内判定卡片：绿=对上、黄=部分对上要确认、红=不能打卡 */
.check-card { margin-top: 12px; padding: 10px 12px; border: 1px solid #dcdfe6; border-radius: 8px; background: #fafafa; }
.check-card.check-ok { border-color: #b3e19d; background: #f0f9eb; }
.check-card.check-partial { border-color: #f0c78a; background: #fdf6ec; }
.check-card.check-fail { border-color: #f3b3b3; background: #fef0f0; }
.check-head { display: flex; align-items: flex-start; gap: 8px; }
.check-badge {
  flex: none; padding: 1px 7px; border: 1px solid currentColor; border-radius: 10px;
  font-size: 12px; font-weight: 600; color: #606266; background: #fff;
}
.check-ok .check-badge { color: #529b2e; }
.check-partial .check-badge { color: #b88230; }
.check-fail .check-badge { color: #c45656; }
.check-reason { font-size: 12px; line-height: 1.6; color: #303133; }
.check-rows { margin-top: 8px; display: flex; flex-direction: column; gap: 6px; }
.check-row { display: flex; align-items: center; gap: 8px; font-size: 12px; }
.check-dish { flex: 1; min-width: 0; color: #303133; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.check-plan, .check-unit { color: #909399; }
.check-tip { margin: 2px 0 0; font-size: 11px; color: #a0a6b3; }
.check-line { margin: 6px 0 0; font-size: 12px; line-height: 1.6; color: #303133; }
.check-line.muted, .muted { color: #909399; }
.check-actions { margin-top: 10px; }
.check-ask { margin: 0 0 6px; font-size: 12px; color: #606266; }
.check-btns { display: flex; flex-wrap: wrap; gap: 6px; }
.check-btns :deep(.el-button) { margin-left: 0; }
.check-force { margin-top: 8px; }
.check-force :deep(.el-checkbox__label) { font-size: 12px; color: #b88230; }
.dlg-label { margin: 14px 0 8px; font-size: 13px; color: #606266; }
.compare-box { margin-top: 10px; padding: 10px 12px; background: #fafafa; border-radius: 8px; }
.compare-title { margin: 0 0 6px; font-size: 12px; font-weight: 600; color: #606266; }
.compare-row {
  display: flex; justify-content: space-between; gap: 8px;
  font-size: 12px; color: #606266; padding: 2px 0;
}
.compare-nums { color: #909399; white-space: nowrap; }
.compare-nums .up { color: #f56c6c; }
.compare-nums .down { color: #67c23a; }
.compare-note { margin: 6px 0 0; font-size: 11.5px; color: #b0b3b8; }
.form-row { display: flex; align-items: center; gap: 10px; margin-bottom: 14px; }
.form-label { width: 40px; font-size: 14px; color: #606266; flex-shrink: 0; }
.preview-box { display: flex; align-items: center; gap: 8px; padding: 10px 12px; background: #f5f7fa; border-radius: 6px; font-size: 13px; }
.opt-extra { float: right; margin-left: 16px; font-size: 12px; color: #909399; }
.pager { margin-top: 12px; display: flex; justify-content: flex-end; }

/* ============ 手机端样式 ============ */
.upload-inner-mobile { padding: 16px 0; }
.upload-inner-mobile .preview { max-height: 200px; width: 100%; object-fit: contain; }

/* 多菜卡片 */
.dish-cards { display: flex; flex-direction: column; gap: 10px; }
.dish-card {
  border: 1px solid #ebeef5;
  border-radius: 10px;
  padding: 10px 12px;
  background: #fcfcfd;
}
.dc-row1 { display: flex; align-items: baseline; justify-content: space-between; gap: 8px; }
.dc-name { font-size: 15px; color: #303133; }
.dc-kcal { font-size: 13px; color: #f56c6c; font-weight: 600; white-space: nowrap; }
.dc-row2 { display: flex; align-items: center; flex-wrap: wrap; gap: 6px; margin-top: 7px; }
.dc-portion { font-size: 12px; color: #909399; }
.dc-warn { margin-top: 6px; font-size: 12px; color: #f56c6c; line-height: 1.5; }
.dc-btn { margin-top: 9px; width: 100%; }

/* 食材竖排 */
.ing-list { display: flex; flex-direction: column; gap: 1px; background: #f5f7fa; border-radius: 8px; padding: 4px 10px; }
.ing-row { display: flex; justify-content: space-between; padding: 7px 0; font-size: 13px; border-bottom: 1px solid #ebeef5; }
.ing-row:last-child { border-bottom: none; }

/* 历史卡片 */
.hist-cards { display: flex; flex-direction: column; gap: 10px; }
.hist-card {
  display: flex;
  gap: 10px;
  border: 1px solid #ebeef5;
  border-radius: 10px;
  padding: 10px;
  background: #fcfcfd;
}
.hist-thumb {
  width: 64px; height: 64px; border-radius: 8px; flex-shrink: 0; display: block;
}
.hist-thumb-empty {
  display: flex; align-items: center; justify-content: center;
  background: #f5f7fa; color: #c0c4cc; font-size: 11px;
}
.hist-body { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 5px; }
.hist-row1 { display: flex; align-items: baseline; justify-content: space-between; gap: 8px; }
.hist-name {
  font-size: 14px; color: #303133; overflow: hidden;
  text-overflow: ellipsis; white-space: nowrap;
}
.hist-kcal { font-size: 12px; color: #f56c6c; font-weight: 600; white-space: nowrap; }
.hist-row2 { display: flex; align-items: center; flex-wrap: wrap; gap: 5px; }
.hist-time { font-size: 11px; color: #a8abb2; }
.hist-row3 { display: flex; gap: 8px; margin-top: 2px; }

/* 手机端：几处横排改成可换行，避免挤成一条 */
@media (max-width: 768px) {
  .result-head { flex-wrap: wrap; gap: 6px; }
  .result-head .dish { font-size: 19px; }
  .conf { font-size: 12px; }
  .budget { flex-wrap: wrap; }
  .budget-text { white-space: normal; font-size: 12px; }
  .actions { flex-direction: column; }
  .actions .el-button { width: 100%; margin-left: 0; }
  .portion-quick { flex-wrap: wrap; }
  .confirm-actions { flex-wrap: wrap; }
  .confirm-actions .el-button { width: 100%; margin-left: 0; }
  .form-row { flex-wrap: wrap; }
  .pager { justify-content: center; }
  .preview-box { flex-wrap: wrap; }
}
</style>

