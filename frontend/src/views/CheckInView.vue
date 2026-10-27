<script setup lang="ts">
// 健康打卡页：月历总览（完成度打点）+ 单日任务逐项打卡 + 手动运动打卡
import { computed, onMounted, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import * as checkinApi from '@/api/checkin'
import type { BurnPreview, CheckInItem, DayStat } from '@/api/checkin'
import { currentMonth, todayStr } from '@/utils/date'
import { useAuthStore } from '@/stores/auth'
import { refreshTodayCheckin } from '@/stores/todayCheckin'

const auth = useAuthStore()
const selectedDate = ref(todayStr())
const dayItems = ref<CheckInItem[]>([])
const daySummary = ref({ total: 0, done: 0, all_done: false })
const monthDays = ref<DayStat[]>([])
const month = ref(currentMonth())

// 手动运动打卡表单（类型默认取档案里的第一个运动偏好）
const exForm = ref({ exercise_type: '快走', duration_min: 30, intensity: 'moderate' as 'light' | 'moderate' | 'intense' })
const INTENSITY_OPTIONS = [
  { value: 'light', label: '轻松', desc: '还能聊天' },
  { value: 'moderate', label: '适中', desc: '微喘' },
  { value: 'intense', label: '吃力', desc: '说不出话' },
]

// ---- 个性化消耗预估弹窗 ----
const burnDialog = ref(false)
const burnLoading = ref(false)
const burnInfo = ref<BurnPreview | null>(null)
const editableBurn = ref(0)
const manualAdjusted = ref(false)

async function openBurnDialog() {
  burnDialog.value = true
  burnLoading.value = true
  manualAdjusted.value = false
  try {
    burnInfo.value = await checkinApi.getBurnPreview(exForm.value.exercise_type, exForm.value.duration_min, exForm.value.intensity)
    editableBurn.value = burnInfo.value.calories_burned
  } finally {
    burnLoading.value = false
  }
}

// 弹窗开着时切强度/改时长 → 实时重算预估
watch(() => [exForm.value.intensity, exForm.value.duration_min], async () => {
  if (!burnDialog.value || burnLoading.value) return
  manualAdjusted.value = false
  burnLoading.value = true
  try {
    burnInfo.value = await checkinApi.getBurnPreview(exForm.value.exercise_type, exForm.value.duration_min, exForm.value.intensity)
    editableBurn.value = burnInfo.value.calories_burned
  } finally {
    burnLoading.value = false
  }
})

function adjustBurn() { manualAdjusted.value = true }

async function confirmCheckin() {
  await checkinApi.manual({
    check_type: 'exercise',
    check_date: selectedDate.value,
    exercise_type: exForm.value.exercise_type,
    duration_min: exForm.value.duration_min,
    intensity: exForm.value.intensity,
    calories_burned: Math.round(editableBurn.value * 10) / 10,
  })
  burnDialog.value = false
  ElMessage.success(manualAdjusted.value
    ? `运动打卡成功！消耗已按手动微调值 ${editableBurn.value} kcal 写入`
    : `运动打卡成功！按个性化模型计算消耗 ${editableBurn.value} kcal`)
  await reloadAll()
}

const statByDate = computed(() => {
  const map: Record<string, DayStat> = {}
  for (const d of monthDays.value) map[d.date] = d
  return map
})

async function loadDay() {
  const r = await checkinApi.getDay(selectedDate.value)
  dayItems.value = r.items
  daySummary.value = r.summary
}

async function loadMonth() {
  monthDays.value = (await checkinApi.getMonth(month.value)).days
}

async function reloadAll() {
  await Promise.all([loadDay(), loadMonth()])
  // 同步全局"今日打卡状态"：左侧菜单红点会据此即时消失
  await refreshTodayCheckin()
  auth.fetchMe().catch(() => {})
}

// 日历单元格：显示完成度 n/n，全清显示 🎉
function cellSlot(date: Date) {
  const key = formatDate(date)
  const stat = statByDate.value[key]
  return { key, stat }
}

function formatDate(d: Date): string {
  const y = d.getFullYear()
  const m = String(d.getMonth() + 1).padStart(2, '0')
  const day = String(d.getDate()).padStart(2, '0')
  return `${y}-${m}-${day}`
}

async function complete(item: CheckInItem) {
  const r = await checkinApi.complete(item.id)
  ElMessage.success(r.message)
  await reloadAll()
}

async function manualCheckin() {
  await openBurnDialog()
}

const EX_TYPES = ['跑步', '游泳', '瑜伽', '骑行', '力量训练', '球类', '快走', '跳绳']

onMounted(reloadAll)
</script>

<template>
  <div class="page">
    <el-row :gutter="16">
      <!-- 左：月历 -->
      <el-col :span="13">
        <div class="page-card">
          <h3 class="page-title">📅 打卡日历（显示每日完成度）</h3>
          <el-calendar v-model="selectedDate as any">
            <template #date-cell="{ data }">
              <div class="cell" @click="selectedDate = data.day">
                <span>{{ Number(data.day.slice(-2)) }}</span>
                <span v-if="cellSlot(new Date(data.day)).stat" class="mark"
                      :class="{ alldone: cellSlot(new Date(data.day)).stat!.done === cellSlot(new Date(data.day)).stat!.total }">
                  {{ cellSlot(new Date(data.day)).stat!.done }}/{{ cellSlot(new Date(data.day)).stat!.total }}
                  <i v-if="cellSlot(new Date(data.day)).stat!.done === cellSlot(new Date(data.day)).stat!.total">🎉</i>
                </span>
              </div>
            </template>
          </el-calendar>
        </div>
      </el-col>

      <!-- 右：当日任务 -->
      <el-col :span="11">
        <div class="page-card">
          <h3 class="page-title">
            ✅ {{ selectedDate }} 的任务
            <el-tag :type="daySummary.all_done ? 'success' : 'info'" style="margin-left:auto">
              {{ daySummary.done }}/{{ daySummary.total }} 已完成
            </el-tag>
          </h3>
          <el-alert v-if="daySummary.all_done" title="当日任务全部完成，太棒了！🎉" type="success" :closable="false" style="margin-bottom:10px" />
          <el-empty v-if="!dayItems.length" description="这一天还没有任务 · 去「养生方案」页导入" :image-size="70" />

          <div v-for="item in dayItems" :key="item.id" class="task">
            <div class="task-main">
              <div class="task-title">
                <el-tag size="small" :type="item.check_type === 'diet' ? 'success' : 'warning'">
                  {{ item.check_type === 'diet' ? '饮食' : '运动' }}
                </el-tag>
                <b style="margin-left:8px">{{ item.label }}</b>
                <span v-if="item.status === 'done'" class="pts">+{{ item.points_awarded }}分</span>
              </div>
              <!-- 待完成任务展示方案餐详情 -->
              <div v-if="item.status === 'pending' && item.detail" class="task-detail">
                <template v-if="item.detail.items">
                  <span v-for="i2 in item.detail.items" :key="i2.name" class="dish">{{ i2.name }} 约{{ i2.portion_g }}g；</span>
                  <span class="cal">约 {{ item.detail.calories }} kcal</span>
                </template>
                <template v-else>
                  {{ item.detail.type }} {{ item.detail.duration_min }} 分钟（约消耗 {{ item.detail.calories_burned }} kcal）
                </template>
              </div>
              <div v-else-if="item.status === 'done' && item.exercise_type" class="task-detail">
                {{ item.exercise_type }} · {{ item.duration_min }} 分钟 · 消耗 {{ item.calories_burned }} kcal
              </div>
            </div>
            <el-button
              v-if="item.status === 'pending'"
              type="success" size="small" @click="complete(item)"
            >打卡 +3</el-button>
            <el-tag v-else type="success" effect="plain">✓ 完成</el-tag>
          </div>
        </div>

        <!-- 手动运动打卡 -->
        <div class="page-card">
          <h3 class="page-title">🏃 手动运动打卡（每日首次 +2 分）</h3>
          <p class="ex-tip">按 <b>个性化模型</b> 计算消耗：MET × 体重 × 时长，再结合你的性别 / 年龄 / 体脂 / 体能修正</p>
          <div class="ex-form">
            <el-select v-model="exForm.exercise_type" style="width:130px">
              <el-option v-for="t in EX_TYPES" :key="t" :value="t" :label="t" />
            </el-select>
            <el-input-number v-model="exForm.duration_min" :min="5" :max="300" placeholder="分钟" />
            <span>分钟</span>
            <el-button type="success" @click="manualCheckin">打卡</el-button>
          </div>
          <div class="intensity-row">
            <span class="intensity-label">强度</span>
            <el-radio-group v-model="exForm.intensity">
              <el-radio-button v-for="o in INTENSITY_OPTIONS" :key="o.value" :value="o.value">
                {{ o.label }} · {{ o.desc }}
              </el-radio-button>
            </el-radio-group>
          </div>
        </div>
      </el-col>
    </el-row>

    <!-- 运动消耗确认弹窗 -->
    <el-dialog v-model="burnDialog" title="🏃 运动消耗预估（个性化）" width="480px">
      <div v-loading="burnLoading" style="min-height:160px">
        <template v-if="burnInfo">
          <!-- 强度切换（弹窗内也可改，实时重算） -->
          <div class="dlg-row">
            <span class="dlg-label">强度</span>
            <el-radio-group v-model="exForm.intensity" size="small">
              <el-radio-button v-for="o in INTENSITY_OPTIONS" :key="o.value" :value="o.value">{{ o.label }}</el-radio-button>
            </el-radio-group>
          </div>

          <el-descriptions :column="2" border size="small" style="margin-top:12px">
            <el-descriptions-item label="运动">{{ exForm.exercise_type }}</el-descriptions-item>
            <el-descriptions-item label="时长">{{ exForm.duration_min }} 分钟</el-descriptions-item>
            <el-descriptions-item :label="`MET（${burnInfo.intensity_label}档）`">
              {{ burnInfo.met }}
              <el-tooltip v-if="!burnInfo.met_known" content="该类型未收录，按同档默认值估算" placement="top">
                <span class="unknown-mark">?</span>
              </el-tooltip>
            </el-descriptions-item>
            <el-descriptions-item label="体重">{{ burnInfo.weight_kg }} kg</el-descriptions-item>
          </el-descriptions>

          <!-- 逐层明细：基础值 → 每个系数 → 最终值 -->
          <div class="steps">
            <div v-for="(s, i) in burnInfo.steps" :key="i" class="step" :class="{ final: i === burnInfo.steps.length - 1 }">
              <span>{{ s }}</span>
            </div>
          </div>

          <div class="result-row">
            <span>预估消耗</span>
            <b>≈ {{ burnInfo.calories_burned }} kcal</b>
            <span class="range">区间 {{ burnInfo.low }} ~ {{ burnInfo.high }}</span>
          </div>

          <div style="margin-top:14px">
            <p style="font-size:13px;color:#606266;margin-bottom:6px">
              感觉不准？手动微调（<span v-if="manualAdjusted" style="color:#e6a23c">已调整</span><span v-else style="color:#909399">未调整</span>）
            </p>
            <el-input-number v-model="editableBurn" :min="0" :max="3000" :step="10" @change="adjustBurn" />
            <span style="margin-left:6px;color:#909399">kcal</span>
          </div>
        </template>
      </div>
      <template #footer>
        <el-button @click="burnDialog = false">取消</el-button>
        <el-button type="success" :disabled="burnLoading" @click="confirmCheckin">确认打卡</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
.cell { display: flex; flex-direction: column; align-items: center; height: 100%; }
.mark { font-size: 11px; color: #e6a23c; }
.mark.alldone { color: #67c23a; font-weight: 700; }
.task {
  display: flex; justify-content: space-between; align-items: center;
  padding: 10px; border: 1px solid #ebeef5; border-radius: 8px; margin-bottom: 8px;
}
.task-title { display: flex; align-items: center; }
.task-detail { color: #909399; font-size: 13px; margin-top: 4px; }
.task-detail .dish { margin-right: 4px; }
.task-detail .cal { color: #f56c6c; }
.pts { color: #e6a23c; font-size: 12px; margin-left: 8px; }
.ex-form { display: flex; gap: 8px; align-items: center; }
.ex-tip { color: #909399; font-size: 12px; margin-bottom: 10px; }
.intensity-row { display: flex; align-items: center; gap: 10px; margin-top: 12px; }
.intensity-label { font-size: 13px; color: #606266; }

/* 弹窗明细 */
.dlg-row { display: flex; align-items: center; gap: 10px; }
.dlg-label { font-size: 13px; color: #606266; }
.unknown-mark {
  display: inline-block; width: 14px; height: 14px; line-height: 14px; text-align: center;
  border: 1px solid #e6a23c; color: #e6a23c; border-radius: 50%; font-size: 11px; cursor: help;
}
.steps { margin-top: 12px; border: 1px dashed #e4e7ed; border-radius: 8px; padding: 8px 12px; background: #fafbfc; }
.step { font-size: 12px; color: #606266; line-height: 1.9; }
.step.final { color: #303133; font-weight: 600; border-top: 1px dashed #e4e7ed; margin-top: 4px; padding-top: 4px; }
.result-row { display: flex; align-items: baseline; gap: 10px; margin-top: 12px; font-size: 14px; color: #606266; }
.result-row b { color: #e6a23c; font-size: 22px; }
.result-row .range { font-size: 12px; color: #909399; }
</style>
