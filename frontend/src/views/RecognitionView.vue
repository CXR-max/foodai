<script setup lang="ts">
// 食物识别页：拖拽上传 → AI 识别 → 结果卡片 → 手动校准 → 保存为饮食记录
import { computed, onMounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import type { UploadRequestOptions } from 'element-plus'
import { UploadFilled } from '@element-plus/icons-vue'
import * as foodApi from '@/api/food'
import type { FoodOption, Recognition } from '@/api/food'
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

// 自定义上传：拿到文件后调识别接口（120s 超时已封装在 api 层）
async function doUpload(options: UploadRequestOptions) {
  recognizing.value = true
  current.value = null
  try {
    current.value = await foodApi.recognize(options.file)
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
    if (current.value) current.value.food_record_id = r.food_record_id
  } finally {
    saving.value = false
  }
}

// ---- 校准交互 ----
async function openCorrect() {
  if (!current.value) return
  const g = Math.round(current.value.result?.vision?.portion_g || 0)
  correctForm.value = {
    dish_name: current.value.is_food ? current.value.dish_name : '',
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
    })
    ElMessage.success('已校准，营养值已按本地营养库重算')
    correctDialog.value = false
    await loadHistory()
  } finally {
    correctSaving.value = false
  }
}

onMounted(loadHistory)
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
              <div class="result-head">
                <span class="dish">{{ current.dish_name }}</span>
                <el-tag v-if="current.result.vision.corrected" type="warning" size="small">已手动校准</el-tag>
                <el-tag type="info" size="small">识别来源：{{ current.provider === 'mock' ? '本地模拟' : 'AI 视觉模型' }}</el-tag>
              </div>
              <div v-if="!current.result.vision.corrected" class="conf">
                置信度
                <el-progress :percentage="Math.round(current.confidence * 100)" :stroke-width="12" status="success" style="flex:1" />
              </div>
              <p v-if="current.result.vision.basis && !current.result.vision.corrected" class="basis">📏 {{ current.result.vision.basis }}</p>
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

              <div class="actions">
                <el-button type="success" :disabled="!!current.food_record_id" @click="openSave">
                  {{ current.food_record_id ? '✓ 已保存为饮食记录' : '保存为饮食记录' }}
                </el-button>
                <el-button type="warning" plain @click="openCorrect">识别有误？手动校准</el-button>
              </div>
            </template>
            <template v-else>
              <el-alert title="未识别到食物，请换一张图片" type="warning" :closable="false" />
              <el-button type="warning" plain style="margin-top:12px" @click="openCorrect">其实是食物？手动校准</el-button>
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
