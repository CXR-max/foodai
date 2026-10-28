<script setup lang="ts">
// 食物识别页：拖拽上传 → AI 识别 → 结果卡片 → 手动校准 → 保存为饮食记录
import { computed, onMounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import type { UploadRequestOptions } from 'element-plus'
import { UploadFilled } from '@element-plus/icons-vue'
import * as foodApi from '@/api/food'
import type { FoodOption, MatchCandidate, Recognition } from '@/api/food'
import { getOverview } from '@/api/stats'
import { getProfile } from '@/api/profile'
import { useAuthStore } from '@/stores/auth'

const auth = useAuthStore()
const current = ref<Recognition | null>(null)   // 当前识别结果
const history = ref<Recognition[]>([])          // 识别历史
const recognizing = ref(false)
const rerunningId = ref<number | null>(null)   // 正在重新识别的记录 id（按钮转圈用）
const saving = ref(false)
const saveDialog = ref(false)
const mealType = ref('lunch')
const MEAL_LABELS: Record<string, string> = { breakfast: '早餐', lunch: '午餐', dinner: '晚餐', snack: '加餐' }

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
  mealType.value = 'lunch'
  saveDialog.value = true
}

async function confirmSave() {
  if (!current.value) return
  saving.value = true
  try {
    const r = await foodApi.saveRecognition(current.value.id, mealType.value)
    auth.fetchMe().catch(() => {})   // 顶栏积分刷新
    ElMessage.success(`已保存为饮食记录 +${r.points_awarded} 分`)
    saveDialog.value = false
    await loadHistory()
    loadMeta()   // 预算卡刷新
    if (current.value) current.value.food_record_id = r.food_record_id
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
      <el-col :span="10">
        <div class="page-card">
          <h3 class="page-title">📷 拍一拍，识别食物</h3>
          <el-upload
            drag
            accept="image/jpeg,image/png,image/webp"
            :show-file-list="false"
            :http-request="doUpload"
          >
            <div class="upload-inner">
              <template v-if="current?.image_url">
                <img :src="current.image_url" class="preview" />
              </template>
              <template v-else-if="recognizing">
                <el-icon class="is-loading" :size="40" color="#67c23a"><i class="el-icon-loading" /></el-icon>
                <p>AI 正在识别，请稍候（约几秒到几十秒）…</p>
              </template>
              <template v-else>
                <el-icon :size="48" color="#c0c4cc"><UploadFilled /></el-icon>
                <p>拖一张食物照片到这里，或 <em>点击上传</em></p>
                <p class="tip">支持 jpg / png / webp，最大 10MB</p>
              </template>
            </div>
          </el-upload>
          <div v-if="recognizing" class="loading-bar"><el-progress :percentage="90" :indeterminate="true" :duration="3" status="success" /></div>
        </div>
      </el-col>

      <!-- 右：识别结果 -->
      <el-col :span="14">
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
                <el-table :data="current.result.dishes" size="small">
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

              <el-descriptions :column="4" border style="margin:12px 0">
                <el-descriptions-item label="热量" class="hl">{{ current.calories }} kcal</el-descriptions-item>
                <el-descriptions-item label="蛋白质">{{ current.result.nutrition.protein_g }} g</el-descriptions-item>
                <el-descriptions-item label="脂肪">{{ current.result.nutrition.fat_g }} g</el-descriptions-item>
                <el-descriptions-item label="碳水">{{ current.result.nutrition.carb_g }} g</el-descriptions-item>
              </el-descriptions>
              <el-alert v-if="!current.result.nutrition.matched" :title="current.result.nutrition.note" type="warning" :closable="false" style="margin-bottom:12px" />
              <el-alert v-else title="营养值来自本地营养数据库，口径精确一致" type="success" :closable="false" style="margin-bottom:12px" />

              <div v-if="current.result.vision.corrected" class="ing-tags">
                <span class="ing-label">主要食材：</span>
                <el-tag v-for="ing in current.result.vision.ingredients" :key="ing.name" size="small" type="info">{{ ing.name }}</el-tag>
                <span v-if="!current.result.vision.ingredients.length" class="muted">营养库暂无食材明细</span>
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
      <el-table :data="history" size="small">
        <el-table-column label="图片" width="90">
          <template #default="{ row }">
            <el-image v-if="row.image_url" :src="row.image_url" fit="cover" style="width:56px;height:56px;border-radius:6px" :preview-src-list="[row.image_url]" preview-teleported />
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
          layout="total, prev, pager, next"
          :total="total"
          :page-size="pageSize"
          :current-page="page"
          @current-change="onPageChange"
        />
      </div>
    </div>

    <!-- 保存餐次选择 -->
    <el-dialog v-model="saveDialog" title="保存为饮食记录" width="360px">
      <p style="margin-top:0">这是哪一餐？</p>
      <el-radio-group v-model="mealType">
        <el-radio-button v-for="(label, key) in MEAL_LABELS" :key="key" :value="key">{{ label }}</el-radio-button>
      </el-radio-group>
      <template #footer>
        <el-button @click="saveDialog = false">取消</el-button>
        <el-button type="success" :loading="saving" @click="confirmSave">保存（+2 分）</el-button>
      </template>
    </el-dialog>

    <!-- 手动校准：只改菜名+克数，热量/营养由本地营养库自动重算 -->
    <el-dialog v-model="correctDialog" title="手动校准识别结果" width="440px">
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
.form-row { display: flex; align-items: center; gap: 10px; margin-bottom: 14px; }
.form-label { width: 40px; font-size: 14px; color: #606266; flex-shrink: 0; }
.preview-box { display: flex; align-items: center; gap: 8px; padding: 10px 12px; background: #f5f7fa; border-radius: 6px; font-size: 13px; }
.opt-extra { float: right; margin-left: 16px; font-size: 12px; color: #909399; }
.pager { margin-top: 12px; display: flex; justify-content: flex-end; }
</style>

