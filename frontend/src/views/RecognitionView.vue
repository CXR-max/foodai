<script setup lang="ts">
// 食物识别页：拖拽上传 → AI 识别 → 结果卡片 → 保存为饮食记录
import { onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import type { UploadRequestOptions } from 'element-plus'
import { UploadFilled } from '@element-plus/icons-vue'
import * as foodApi from '@/api/food'
import type { Recognition } from '@/api/food'
import { useAuthStore } from '@/stores/auth'

const auth = useAuthStore()
const current = ref<Recognition | null>(null)   // 当前识别结果
const history = ref<Recognition[]>([])          // 识别历史
const recognizing = ref(false)
const saving = ref(false)
const saveDialog = ref(false)
const mealType = ref('lunch')
const MEAL_LABELS: Record<string, string> = { breakfast: '早餐', lunch: '午餐', dinner: '晚餐', snack: '加餐' }

// 自定义上传：拿到文件后调识别接口（120s 超时已封装在 api 层）
async function doUpload(options: UploadRequestOptions) {
  recognizing.value = true
  current.value = null
  try {
    current.value = await foodApi.recognize(options.file)
    if (!current.value.is_food) {
      ElMessage.warning('图片中未识别到食物，换一张试试？')
    } else {
      ElMessage.success('识别完成！')
    }
    await loadHistory()
  } finally {
    recognizing.value = false
  }
}

async function loadHistory() {
  history.value = (await foodApi.getRecognitions(1)).items
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
                <el-tag type="info" size="small">识别来源：{{ current.provider === 'mock' ? '本地模拟' : 'AI 视觉模型' }}</el-tag>
              </div>
              <div class="conf">
                置信度
                <el-progress :percentage="Math.round(current.confidence * 100)" :stroke-width="12" status="success" style="flex:1" />
              </div>

              <el-descriptions :column="4" border style="margin:12px 0">
                <el-descriptions-item label="热量" class="hl">{{ current.calories }} kcal</el-descriptions-item>
                <el-descriptions-item label="蛋白质">{{ current.result.nutrition.protein_g }} g</el-descriptions-item>
                <el-descriptions-item label="脂肪">{{ current.result.nutrition.fat_g }} g</el-descriptions-item>
                <el-descriptions-item label="碳水">{{ current.result.nutrition.carb_g }} g</el-descriptions-item>
              </el-descriptions>
              <el-alert v-if="!current.result.nutrition.matched" :title="current.result.nutrition.note" type="warning" :closable="false" style="margin-bottom:12px" />
              <el-alert v-else title="营养值来自本地营养数据库，口径精确一致" type="success" :closable="false" style="margin-bottom:12px" />

              <el-table :data="current.result.vision.ingredients" size="small" max-height="200">
                <el-table-column prop="name" label="食材" />
                <el-table-column prop="portion_g" label="估算克重(g)" width="140" />
              </el-table>

              <el-button type="success" style="margin-top:14px" :disabled="!!current.food_record_id" @click="openSave">
                {{ current.food_record_id ? '✓ 已保存为饮食记录' : '保存为饮食记录' }}
              </el-button>
            </template>
            <el-alert v-else title="未识别到食物，请换一张图片" type="warning" :closable="false" />
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
        <el-table-column label="状态" width="140">
          <template #default="{ row }">
            <el-tag v-if="row.food_record_id" type="success" size="small">已保存记录</el-tag>
            <el-tag v-else-if="!row.is_food" type="info" size="small">非食物</el-tag>
            <el-tag v-else type="warning" size="small">未保存</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="created_at" label="时间" min-width="150">
          <template #default="{ row }">{{ new Date(row.created_at).toLocaleString('zh-CN') }}</template>
        </el-table-column>
      </el-table>
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
.hl { color: #f56c6c; font-weight: 700; }
</style>
