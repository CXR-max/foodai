<script setup lang="ts">
// 食物识别页：样本托盘上传 → AI 识别 → 营养成分表式结果 → 手动校准 → 保存为饮食记录
import { computed, onMounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import type { UploadRequestOptions } from 'element-plus'
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

// 重新识别：复用原图再跑一次模型
async function rerunRec(row: Recognition) {
  rerunningId.value = row.id
  try {
    const r = await foodApi.rerunRecognition(row.id)
    current.value = r
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
async function openCorrect(index?: number) {
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

// ---- 结果卡的展示派生 ----
const servingG = computed(() => {
  const r = current.value?.result
  if (!r) return 0
  if (r.dishes?.length) return Math.round(r.dishes.reduce((s, d) => s + (d.portion_g || 0), 0))
  return Math.round(r.vision?.portion_g || 0)
})

const confidencePct = computed(() => Math.round((current.value?.confidence || 0) * 100))

// 每日参考摄入（仅用于「营养成分表」右侧占比，标注在卡片底部）
const DAILY_REF = { protein_g: 60, fat_g: 60, carb_g: 300 }
function pctOf(value: number, ref: number) {
  return `${Math.round((value / ref) * 100)}%`
}

onMounted(() => {
  loadHistory()
  loadMeta()
})
</script>

<template>
  <div class="page">
    <div class="recog">
      <!-- 左：样本托盘 -->
      <div>
        <div class="sec" style="margin-top: 0">
          <span class="sec-no">01</span>
          <span class="sec-title">放入照片</span>
          <span class="sec-line"></span>
          <span class="sec-act mono">JPG / PNG / WEBP</span>
        </div>

        <div class="tray">
          <el-upload
            drag
            class="tray-upload"
            accept="image/jpeg,image/png,image/webp"
            :show-file-list="false"
            :http-request="doUpload"
          >
            <div class="upload-inner">
              <template v-if="current?.image_url">
                <img :src="current.image_url" class="preview" />
                <div class="preview-cap mono">已上传 · 点击可换一张</div>
              </template>
              <template v-else-if="recognizing">
                <div class="pulse"><span></span><span></span><span></span></div>
                <p class="up-t">AI 正在识别…</p>
                <p class="up-s">通常几秒到几十秒，请稍候</p>
              </template>
              <template v-else>
                <div class="tray-ico">
                  <svg viewBox="0 0 24 24">
                    <path d="M3.5 8.5h3l1.6-2.5h7.8l1.6 2.5h3v10h-17z" />
                    <path d="M12 9.8a3.2 3.2 0 110 6.4 3.2 3.2 0 010-6.4" />
                  </svg>
                </div>
                <p class="up-t">拖入或点击上传</p>
                <p class="up-s mono">单张 ≤ 10MB · 自动去重</p>
                <span class="up-btn">选择照片</span>
              </template>
            </div>
          </el-upload>
        </div>

        <div class="tray-foot mono">
          <span>GLM-4V-FLASH · 视觉识别</span>
          <span>{{ current ? '已生成结果' : '等待样本' }}</span>
        </div>

        <!-- 今日预算 -->
        <div v-if="budget?.target" class="budget">
          <div class="budget-top">
            <span class="budget-k mono">TODAY BUDGET</span>
            <span class="budget-v mono">{{ Math.round(budget.today) }} / {{ budget.target }} kcal</span>
          </div>
          <div class="budget-bar"><i :style="{ width: budgetPct + '%' }" :class="{ over: budgetPct >= 100 }"></i></div>
          <div class="budget-note">
            <span v-if="budget.today <= budget.target" class="muted mono">还可摄入 {{ Math.round(budget.target - budget.today) }} kcal</span>
            <span v-else class="over mono">已超出 {{ Math.round(budget.today - budget.target) }} kcal</span>
          </div>
        </div>
      </div>

      <!-- 右：结果卡 -->
      <div>
        <div class="sec" style="margin-top: 0">
          <span class="sec-no">02</span>
          <span class="sec-title">识别结果</span>
          <span class="sec-line"></span>
          <span v-if="current" class="sec-act" @click="openCorrect()">校准 →</span>
        </div>

        <div v-if="!current && !recognizing" class="placeholder">
          <span class="ph-k mono">NUTRITION FACTS</span>
          <span class="ph-t">上传图片后，这里会生成一张营养成分表</span>
          <span class="ph-s">热量、蛋白质、脂肪、碳水全部来自本地营养库，可核对、可校准。</span>
        </div>

        <template v-else-if="current">
          <template v-if="current.is_food">
            <!-- 禁忌提示 -->
            <div v-for="w in dietWarnings" :key="w" class="warn">
              <span class="warn-dot"></span>{{ w }}
            </div>

            <!-- 营养成分表 -->
            <div class="label">
              <div class="label-top">
                <div class="label-h">
                  {{ current.dish_name }}
                  <span>NUTRITION FACTS · 每份</span>
                </div>
                <div class="label-serv mono">
                  每份 {{ servingG }} g<br />
                  共 1 份
                </div>
              </div>

              <div class="lrow big">
                <span class="lk">热量 Energy</span>
                <span class="lv">{{ Math.round(current.calories) }}</span>
                <span class="lp">kcal</span>
              </div>
              <div class="lrow p">
                <span class="lk">蛋白质<small>PROTEIN</small></span>
                <span class="lv">{{ current.result.nutrition.protein_g }} g</span>
                <span class="lp">{{ pctOf(current.result.nutrition.protein_g, DAILY_REF.protein_g) }}</span>
              </div>
              <div class="lrow f">
                <span class="lk">脂肪<small>FAT</small></span>
                <span class="lv">{{ current.result.nutrition.fat_g }} g</span>
                <span class="lp">{{ pctOf(current.result.nutrition.fat_g, DAILY_REF.fat_g) }}</span>
              </div>
              <div class="lrow c">
                <span class="lk">碳水化合物<small>CARBS</small></span>
                <span class="lv">{{ current.result.nutrition.carb_g }} g</span>
                <span class="lp">{{ pctOf(current.result.nutrition.carb_g, DAILY_REF.carb_g) }}</span>
              </div>

              <div class="label-foot mono">
                <span>% 为每日参考摄入占比</span>
                <span>{{ current.result.nutrition.matched ? 'LOCAL NUTRITION DB' : '通用估算值' }}</span>
              </div>
            </div>

            <!-- 来源 / 可信度 -->
            <div class="meta-row">
              <span class="tag-pill" :class="{ mock: current.provider === 'mock' }">
                {{ current.provider === 'mock' ? '本地模拟' : 'AI 视觉模型' }}
              </span>
              <span v-if="current.result.vision.corrected" class="tag-pill">已手动校准</span>
              <span v-if="matchInfo" class="tag-pill">
                匹配 {{ matchInfo.method_label }} · {{ Math.round(matchInfo.score * 100) }}%
              </span>
              <span class="conf">
                <i></i>可信度 {{ confidencePct }}%
              </span>
            </div>

            <p v-if="current.result.vision.basis && !current.result.vision.corrected" class="basis">
              {{ current.result.vision.basis }}
            </p>

            <div v-if="!current.result.nutrition.matched" class="note-line">
              {{ current.result.nutrition.note }}
            </div>
            <div v-if="current.provider === 'mock' && !current.result.vision.corrected" class="note-line">
              AI 视觉模型不可用，当前为本地模拟结果，建议手动校准或稍后重试。
            </div>

            <!-- 多菜 -->
            <div v-if="dishCount > 1" class="dishes">
              <div class="dishes-title">
                这张图识别到 {{ dishCount }} 道菜，合计 {{ current.result.nutrition.calories }} kcal
              </div>
              <div class="dish-cards">
                <button
                  v-for="(d, i) in current.result.dishes" :key="i"
                  class="dish-card" @click="openCorrect(i)"
                >
                  <span class="dn">
                    {{ d.dish_name }}
                    <span v-if="d.match?.needs_confirm" class="need">需确认</span>
                  </span>
                  <span class="dv mono">{{ Math.round(d.portion_g) }} g · {{ Math.round(d.nutrition.calories) }} kcal</span>
                  <span class="dc mono">校准 →</span>
                </button>
              </div>
            </div>

            <!-- 食材 -->
            <div v-if="current.result.vision.ingredients?.length" class="ings">
              <span class="ings-k mono">INGREDIENTS</span>
              <span v-for="ing in current.result.vision.ingredients" :key="ing.name" class="ing">
                {{ ing.name }} <b class="mono">{{ Math.round(ing.portion_g) }}g</b>
              </span>
            </div>

            <!-- 低置信度候选确认 -->
            <div v-if="matchInfo?.needs_confirm && !current.result.vision.corrected" class="confirm-box">
              <div class="confirm-head">
                <span class="confirm-k mono">NEEDS CONFIRM</span>
                <span class="confirm-t">{{ matchInfo.reasons.join('；') }}</span>
              </div>
              <div class="cand-list">
                <button
                  v-for="c in matchInfo.candidates" :key="c.name"
                  class="cand" :class="{ active: confirmName === c.name }"
                  @click="pickCandidate(c)"
                >
                  <span class="cand-name">{{ c.name }}<span class="cand-score mono">{{ Math.round(c.score * 100) }}%</span></span>
                  <span class="cand-meta">
                    {{ c.category }} · 标准 {{ c.common_portion_g }}g
                    <template v-if="c.calories_per_100g"> · {{ Math.round(c.calories_per_100g) }} kcal/100g</template>
                  </span>
                </button>
              </div>
              <div class="confirm-actions">
                <span class="muted">克重</span>
                <el-input-number v-model="confirmPortion" :min="10" :max="3000" :step="50" style="width: 130px" />
                <span class="muted">g</span>
                <el-button type="primary" :loading="confirming" @click="confirmMatch">确认并重算营养</el-button>
              </div>
            </div>

            <!-- 份量快捷 + 操作 -->
            <div class="portion-quick">
              <span class="pq-k mono">PORTION</span>
              <button class="qbtn" :disabled="confirming" @click="quickPortion(0.7)">小份</button>
              <button class="qbtn" :disabled="confirming" @click="quickPortion(1)">标准份</button>
              <button class="qbtn" :disabled="confirming" @click="quickPortion(1.3)">大份</button>
            </div>

            <div class="actions">
              <el-button type="primary" :disabled="!!current.food_record_id" @click="openSave">{{ saveLabel }}</el-button>
              <el-button @click="openCorrect()">识别有误？手动校准</el-button>
            </div>
          </template>

          <template v-else>
            <div class="placeholder" style="min-height: 220px">
              <span class="ph-k mono">NOT FOOD</span>
              <span class="ph-t">未识别到食物，请换一张图片</span>
              <button class="btn-ghost" @click="openCorrect()">其实是食物？手动校准</button>
            </div>
          </template>
        </template>
      </div>
    </div>

    <!-- 识别历史 -->
    <div class="history">
      <div class="sec">
        <span class="sec-no">03</span>
        <span class="sec-title">识别历史</span>
        <span class="sec-line"></span>
        <span class="sec-act mono">{{ total }} RECORDS</span>
      </div>
      <el-table :data="history" size="small">
        <el-table-column label="图片" width="86">
          <template #default="{ row }">
            <el-image
              v-if="row.image_url" :src="row.image_url" fit="cover"
              style="width: 48px; height: 48px; border-radius: 3px"
              :preview-src-list="[row.image_url]" preview-teleported
            />
            <span v-else class="muted mono">—</span>
          </template>
        </el-table-column>
        <el-table-column prop="dish_name" label="菜品" min-width="130" />
        <el-table-column prop="calories" label="热量 (kcal)" width="110">
          <template #default="{ row }"><span class="mono">{{ row.calories }}</span></template>
        </el-table-column>
        <el-table-column label="状态" width="120">
          <template #default="{ row }">
            <span v-if="row.food_record_id" class="tag-pill ok">已保存记录</span>
            <span v-else-if="!row.is_food" class="tag-pill">非食物</span>
            <span v-else class="tag-pill warn-pill">未保存</span>
          </template>
        </el-table-column>
        <el-table-column label="来源" width="100">
          <template #default="{ row }">
            <span class="tag-pill" :class="{ mock: row.provider === 'mock' }">
              {{ row.provider === 'mock' ? '本地模拟' : 'AI 视觉' }}
            </span>
          </template>
        </el-table-column>
        <el-table-column prop="created_at" label="时间" min-width="150">
          <template #default="{ row }"><span class="mono muted">{{ new Date(row.created_at).toLocaleString('zh-CN') }}</span></template>
        </el-table-column>
        <el-table-column label="操作" width="150" fixed="right">
          <template #default="{ row }">
            <el-button link type="primary" size="small" :loading="rerunningId === row.id" @click="rerunRec(row)">重新识别</el-button>
            <el-button link type="danger" size="small" :disabled="!!row.food_record_id" @click="deleteRec(row)">删除</el-button>
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
    <el-dialog v-model="saveDialog" title="保存为饮食记录" width="380px">
      <p class="dlg-tip">这是哪一餐？</p>
      <el-radio-group v-model="mealType">
        <el-radio-button v-for="(label, key) in MEAL_LABELS" :key="key" :value="key">{{ label }}</el-radio-button>
      </el-radio-group>
      <template #footer>
        <el-button @click="saveDialog = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="confirmSave">保存（+2 分）</el-button>
      </template>
    </el-dialog>

    <!-- 手动校准 -->
    <el-dialog v-model="correctDialog" title="手动校准识别结果" width="460px">
      <p class="dlg-tip">热量与营养由本地营养库按「每 100g × 克数」自动计算，不可手动修改。</p>
      <div class="form-row">
        <span class="form-label">菜名</span>
        <el-select
          v-model="correctForm.dish_name"
          filterable allow-create default-first-option
          placeholder="搜索营养库菜品，或直接输入菜名"
          style="flex: 1"
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
        <el-input-number v-model="correctForm.portion_g" :min="10" :max="3000" :step="50" style="width: 150px" />
        <template v-if="selectedOption">
          <el-button size="small" @click="applySize(0.7)">小份</el-button>
          <el-button size="small" @click="applySize(1)">标准份</el-button>
          <el-button size="small" @click="applySize(1.3)">大份</el-button>
        </template>
      </div>
      <div class="preview-box">
        <template v-if="previewNut">
          <span>校准后：</span>
          <b class="hl mono">{{ previewNut.calories }} kcal</b>
          <span class="muted mono">蛋白 {{ previewNut.protein_g }}g · 脂肪 {{ previewNut.fat_g }}g · 碳水 {{ previewNut.carb_g }}g</span>
        </template>
        <span v-else class="muted">该菜不在营养库中，将按通用估算值计算热量</span>
      </div>
      <template #footer>
        <el-button @click="correctDialog = false">取消</el-button>
        <el-button type="primary" :loading="correctSaving" @click="confirmCorrect">保存校准</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
/* ---------- 两栏 ---------- */
.recog {
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(0, 1.12fr);
  gap: 32px;
  align-items: start;
}

/* ---------- 样本托盘 ---------- */
.tray {
  position: relative;
  border: 1px solid var(--line-2);
  background: var(--surface);
  border-radius: 4px;
  padding: 16px;
}
.tray::before,
.tray::after {
  content: '';
  position: absolute;
  width: 20px;
  height: 20px;
  border: 1.5px solid var(--ink);
  pointer-events: none;
}
.tray::before { left: 12px; top: 12px; border-right: 0; border-bottom: 0; }
.tray::after { right: 12px; bottom: 12px; border-left: 0; border-top: 0; }

.tray :deep(.el-upload) { display: block; width: 100%; }
.tray :deep(.el-upload-dragger) {
  border: 1px dashed var(--line);
  background: transparent;
  padding: 0;
  border-radius: 2px;
  width: 100%;
  min-height: 356px;
  display: flex;
  align-items: center;
  justify-content: center;
}
.tray :deep(.el-upload-dragger:hover) { border-color: var(--accent); background: var(--accent-soft); }
.tray:has(.preview) :deep(.el-upload-dragger) { min-height: auto; border: 0; }
.tray:has(.preview) .upload-inner { padding: 18px; }

.upload-inner {
  padding: 34px 20px;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 10px;
  width: 100%;
}
.tray-ico {
  width: 54px;
  height: 54px;
  border-radius: 50%;
  background: var(--accent-soft);
  color: var(--accent);
  display: grid;
  place-items: center;
}
.tray-ico svg {
  width: 24px;
  height: 24px;
  fill: none;
  stroke: currentColor;
  stroke-width: 1.5;
  stroke-linecap: round;
  stroke-linejoin: round;
}
.up-t { font-family: var(--serif); font-size: 16px; font-weight: 600; margin: 4px 0 0; color: var(--ink); }
.up-s { font-size: 10.5px; letter-spacing: 0.06em; color: var(--muted); margin: 0; }
.up-btn {
  margin-top: 8px;
  background: var(--ink);
  color: var(--paper);
  border-radius: 999px;
  padding: 9px 22px;
  font-size: 12px;
  letter-spacing: 0.02em;
}
.preview {
  width: 100%;
  max-height: 320px;
  border-radius: 3px;
  object-fit: cover;
}
.preview-cap { font-size: 10px; letter-spacing: 0.08em; color: var(--muted); margin-top: 8px; }

/* 识别中脉冲 */
.pulse { display: flex; gap: 5px; margin-bottom: 6px; }
.pulse span {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: var(--accent);
  animation: pulse 1.1s infinite ease-in-out;
}
.pulse span:nth-child(2) { animation-delay: 0.16s; }
.pulse span:nth-child(3) { animation-delay: 0.32s; }
@keyframes pulse {
  0%, 80%, 100% { opacity: 0.25; transform: translateY(0); }
  40% { opacity: 1; transform: translateY(-4px); }
}

.tray-foot {
  display: flex;
  justify-content: space-between;
  padding: 10px 4px 0;
  font-size: 9.5px;
  letter-spacing: 0.08em;
  color: var(--muted);
}

/* 预算 */
.budget {
  margin-top: 16px;
  border: 1px solid var(--line);
  background: var(--surface);
  border-radius: 4px;
  padding: 13px 15px;
}
.budget-top { display: flex; justify-content: space-between; align-items: baseline; margin-bottom: 9px; }
.budget-k { font-size: 9.5px; letter-spacing: 0.16em; color: var(--muted); }
.budget-v { font-size: 12px; color: var(--ink); }
.budget-bar { height: 4px; background: var(--paper-2); border-radius: 999px; overflow: hidden; }
.budget-bar i { display: block; height: 100%; background: var(--accent); border-radius: 999px; transition: width 0.4s; }
.budget-bar i.over { background: var(--clay); }
.budget-note { margin-top: 8px; font-size: 10px; letter-spacing: 0.04em; }
.budget-note .over { color: var(--clay); }

/* ---------- 结果区 ---------- */
.placeholder {
  border: 1px dashed var(--line-2);
  border-radius: 4px;
  padding: 40px 26px;
  min-height: 300px;
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  justify-content: center;
  gap: 8px;
  background: var(--surface);
}
.ph-k { font-size: 10px; letter-spacing: 0.18em; color: var(--muted); }
.ph-t { font-family: var(--serif); font-size: 16px; font-weight: 600; color: var(--ink); }
.ph-s { font-size: 11.5px; color: var(--muted); line-height: 1.7; max-width: 340px; }

.warn {
  display: flex;
  align-items: center;
  gap: 8px;
  border: 1px solid #e8cdc4;
  background: var(--clay-soft);
  color: var(--clay-deep);
  font-size: 12px;
  padding: 8px 12px;
  border-radius: 4px;
  margin-bottom: 8px;
}
.warn-dot { width: 5px; height: 5px; border-radius: 50%; background: var(--clay); flex: 0 0 5px; }

/* 营养成分表 */
.label {
  border: 1px solid var(--ink);
  background: var(--surface);
  padding: 18px 20px 16px;
  border-radius: 2px;
}
.label-top {
  display: flex;
  justify-content: space-between;
  align-items: flex-end;
  border-bottom: 7px solid var(--ink);
  padding-bottom: 8px;
  margin-bottom: 10px;
  gap: 14px;
}
.label-h {
  font-family: var(--serif);
  font-size: 20px;
  font-weight: 700;
  letter-spacing: 0.02em;
  line-height: 1.2;
}
.label-h span {
  display: block;
  font-family: var(--mono);
  font-size: 8.5px;
  letter-spacing: 0.18em;
  color: var(--muted);
  font-weight: 400;
  margin-top: 4px;
}
.label-serv { font-size: 9.5px; color: var(--muted); text-align: right; line-height: 1.8; letter-spacing: 0.04em; }

.lrow {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 86px 44px;
  align-items: baseline;
  gap: 8px;
  padding: 8px 0;
  border-bottom: 1px solid var(--line);
}
.lrow.big { border-bottom: 9px solid var(--ink); padding: 6px 0 8px; }
.lrow.big .lk { font-size: 15px; font-weight: 700; }
.lrow.big .lv { font-size: 21px; font-weight: 700; }
.lk { font-size: 12.5px; color: var(--ink-2); }
.lrow.p { --m: var(--accent); }
.lrow.f { --m: var(--ochre); }
.lrow.c { --m: var(--sage); }
.lrow.p .lk, .lrow.f .lk, .lrow.c .lk { color: var(--m); }
.lrow.p .lv, .lrow.f .lv, .lrow.c .lv { color: var(--m); }
.lk small {
  display: block;
  font-family: var(--mono);
  font-size: 8.5px;
  color: var(--muted);
  letter-spacing: 0.1em;
  margin-top: 2px;
}
.lv {
  font-family: var(--mono);
  font-variant-numeric: tabular-nums;
  font-size: 13px;
  text-align: right;
  font-weight: 600;
  color: var(--ink);
}
.lp {
  font-family: var(--mono);
  font-size: 9.5px;
  color: var(--muted);
  text-align: right;
}
.label-foot {
  display: flex;
  justify-content: space-between;
  gap: 12px;
  margin-top: 10px;
  font-size: 8.5px;
  letter-spacing: 0.06em;
  color: var(--muted);
}

/* 标签药丸 */
.meta-row { display: flex; align-items: center; gap: 7px; flex-wrap: wrap; margin-top: 12px; }
.tag-pill {
  font-size: 10.5px;
  letter-spacing: 0.02em;
  padding: 3px 9px;
  border-radius: 999px;
  border: 1px solid var(--line-2);
  color: var(--ink-2);
  background: var(--surface);
}
.tag-pill.ok { border-color: #d6dccd; background: #eef0e9; color: #4c5c45; }
.tag-pill.warn-pill { border-color: #f3d9b7; background: var(--ochre-soft); color: var(--ochre-deep); }
.tag-pill.mock { border-color: #e8cdc4; background: var(--clay-soft); color: var(--clay-deep); }
.conf {
  margin-left: auto;
  font-family: var(--mono);
  font-size: 10px;
  color: var(--muted);
  display: flex;
  align-items: center;
  gap: 5px;
  letter-spacing: 0.04em;
}
.conf i { width: 5px; height: 5px; border-radius: 50%; background: var(--accent); }

.basis {
  margin: 8px 0 0;
  font-size: 11px;
  color: var(--muted);
  line-height: 1.7;
}
.note-line {
  margin-top: 8px;
  border-left: 2px solid var(--ochre);
  background: var(--ochre-soft);
  color: var(--ochre-deep);
  font-size: 11.5px;
  line-height: 1.7;
  padding: 7px 11px;
  border-radius: 0 3px 3px 0;
}

/* 多菜 */
.dishes { margin-top: 16px; }
.dishes-title { font-size: 12px; color: var(--ink-2); margin-bottom: 8px; }
.dish-cards { display: flex; gap: 8px; flex-wrap: wrap; }
.dish-card {
  border: 1px solid var(--line);
  background: var(--surface);
  border-radius: 4px;
  padding: 9px 12px;
  min-width: 132px;
  display: flex;
  flex-direction: column;
  gap: 3px;
  cursor: pointer;
  text-align: left;
  font-family: var(--sans);
  transition: border-color 0.18s;
}
.dish-card:hover { border-color: var(--ink); }
.dn { font-size: 12.5px; font-weight: 600; color: var(--ink); display: flex; align-items: center; gap: 6px; }
.need { font-size: 9px; color: var(--clay-deep); border: 1px solid #e8cdc4; border-radius: 999px; padding: 0 5px; font-weight: 400; }
.dv { font-size: 9.5px; color: var(--muted); }
.dc { font-size: 9px; color: var(--accent); letter-spacing: 0.06em; }

/* 食材 */
.ings { display: flex; align-items: center; gap: 6px; flex-wrap: wrap; margin-top: 14px; }
.ings-k { font-size: 8.5px; letter-spacing: 0.16em; color: var(--muted); margin-right: 4px; }
.ing {
  font-size: 10.5px;
  color: var(--ink-2);
  border: 1px solid var(--line);
  background: var(--paper);
  border-radius: 999px;
  padding: 2px 9px;
}
.ing b { font-weight: 600; color: var(--ink); font-size: 9.5px; }

/* 候选确认 */
.confirm-box { margin-top: 16px; border: 1px solid #f3d9b7; background: var(--surface); border-radius: 4px; padding: 13px 15px; }
.confirm-head { display: flex; align-items: baseline; gap: 10px; flex-wrap: wrap; }
.confirm-k { font-size: 9px; letter-spacing: 0.16em; color: var(--ochre); }
.confirm-t { font-size: 11.5px; color: var(--ochre-deep); }
.cand-list { display: flex; flex-direction: column; gap: 6px; margin: 10px 0; }
.cand {
  border: 1px solid var(--line);
  background: var(--surface);
  border-radius: 4px;
  padding: 8px 12px;
  cursor: pointer;
  text-align: left;
  font-family: var(--sans);
  transition: border-color 0.16s, background 0.16s;
}
.cand:hover { border-color: var(--ochre); }
.cand.active { border-color: var(--ochre); background: var(--surface); box-shadow: 0 0 0 1px var(--ochre) inset; }
.cand-name { font-size: 12.5px; font-weight: 600; color: var(--ink); display: flex; align-items: center; gap: 8px; }
.cand-score { font-size: 9.5px; color: var(--muted); font-weight: 400; }
.cand-meta { display: block; font-size: 10.5px; color: var(--muted); margin-top: 3px; }
.confirm-actions { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }

/* 份量 + 操作 */
.portion-quick { display: flex; align-items: center; gap: 6px; margin-top: 14px; }
.pq-k { font-size: 8.5px; letter-spacing: 0.16em; color: var(--muted); margin-right: 4px; }
.qbtn {
  border: 1px solid var(--line-2);
  background: var(--surface);
  color: var(--ink-2);
  border-radius: 999px;
  padding: 4px 12px;
  font-size: 11px;
  cursor: pointer;
  font-family: var(--sans);
  transition: background 0.16s, color 0.16s;
}
.qbtn:hover:not(:disabled) { background: var(--ink); color: var(--paper); border-color: var(--ink); }
.qbtn:disabled { opacity: 0.5; cursor: not-allowed; }

.actions { display: flex; gap: 10px; margin-top: 14px; flex-wrap: wrap; }

/* 历史 */
.history { margin-top: 34px; }
.pager { display: flex; justify-content: flex-end; margin-top: 12px; }

.muted { font-size: 11.5px; color: var(--muted); }
.hl { color: var(--clay); font-weight: 700; }

/* 弹窗内 */
.dlg-tip { margin: 0 0 14px; font-size: 11.5px; color: var(--muted); line-height: 1.7; }
.form-row { display: flex; align-items: center; gap: 10px; margin-bottom: 14px; flex-wrap: wrap; }
.form-label { width: 40px; font-size: 12.5px; color: var(--ink-2); flex-shrink: 0; }
.preview-box {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 10px 12px;
  background: var(--paper);
  border: 1px solid var(--line);
  border-radius: 4px;
  font-size: 11.5px;
  flex-wrap: wrap;
}
.opt-extra { float: right; margin-left: 16px; font-size: 11px; color: var(--muted); }

@media (max-width: 1080px) {
  .recog { grid-template-columns: 1fr; }
  .tray :deep(.el-upload-dragger) { min-height: 260px; }
}
</style>
