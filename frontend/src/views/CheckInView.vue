<script setup lang="ts">
// 健康打卡页：月历总览（完成度打点）+ 单日任务逐项打卡 + 手动运动打卡
import { computed, onMounted, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import * as checkinApi from '@/api/checkin'
import type { CheckInItem, DayStat } from '@/api/checkin'
import { currentMonth, todayStr } from '@/utils/date'
import { useAuthStore } from '@/stores/auth'

const auth = useAuthStore()
const selectedDate = ref(todayStr())
const dayItems = ref<CheckInItem[]>([])
const daySummary = ref({ total: 0, done: 0, all_done: false })
const monthDays = ref<DayStat[]>([])
const month = ref(currentMonth())

// 手动运动打卡表单（类型默认取档案里的第一个运动偏好）
const exForm = ref({ exercise_type: '快走', duration_min: 30, calories_burned: 100 })

const statByDate = computed(() => {
  const map: Record<string, DayStat> = {}
  for (const d of monthDays.value) map[d.date] = d
  return map
})

// el-calendar 的 v-model 会塞进来 Date 对象，单元格点击塞进来字符串 —— 统一归一化为 YYYY-MM-DD
function toDateStr(v: string | Date): string {
  return typeof v === 'string' ? v : formatDate(v)
}

const selectedDateStr = computed(() => toDateStr(selectedDate.value as string | Date))

// ★ 修复：切换日期时右侧任务面板要跟着刷新（之前只在页面加载时请求一次，
//   导致点任何日期右侧显示的都是进页面那天的数据）
watch(selectedDate, (v) => {
  const d = toDateStr(v as unknown as string | Date)
  if (typeof v !== 'string') {
    selectedDate.value = d      // Date → 字符串归一化（会再触发一次本 watch，走下面的分支）
    return
  }
  const m = d.slice(0, 7)
  if (m !== month.value) {
    month.value = m             // 跨月了：月历打点也要跟着换
    loadMonth().catch(() => {})
  }
  loadDay().catch(() => {})
})

async function loadDay() {
  const r = await checkinApi.getDay(toDateStr(selectedDate.value as string | Date))
  dayItems.value = r.items
  daySummary.value = r.summary
}

async function loadMonth() {
  monthDays.value = (await checkinApi.getMonth(month.value)).days
}

async function reloadAll() {
  await Promise.all([loadDay(), loadMonth()])
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
  const r = await checkinApi.manual({
    check_type: 'exercise',
    check_date: selectedDateStr.value,
    exercise_type: exForm.value.exercise_type,
    duration_min: exForm.value.duration_min,
    calories_burned: exForm.value.calories_burned,
  })
  if (r.points_awarded > 0) {
    ElMessage.success(`运动打卡成功 +${r.points_awarded} 分！`)
  } else {
    ElMessage.info('已记录本次打卡（运动积分每日首次 +2，今天已领过）')
  }
  await reloadAll()
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
            ✅ {{ selectedDateStr }} 的任务
            <el-tag :type="daySummary.all_done ? 'success' : 'info'" style="margin-left:auto">
              {{ daySummary.done }}/{{ daySummary.total }} 已完成
            </el-tag>
          </h3>
          <el-alert v-if="daySummary.all_done" title="当日任务全部完成，太棒了！🎉" type="success" :closable="false" style="margin-bottom:10px" />
          <el-empty v-if="!dayItems.length" description="这一天还没有任务 · 说明方案还没导入到这天，去「养生方案」页导入" :image-size="70" />

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
          <div class="ex-form">
            <el-select v-model="exForm.exercise_type" style="width:130px">
              <el-option v-for="t in EX_TYPES" :key="t" :value="t" :label="t" />
            </el-select>
            <el-input-number v-model="exForm.duration_min" :min="5" :max="300" placeholder="分钟" />
            <span>分钟</span>
            <el-button type="success" @click="manualCheckin">打卡</el-button>
          </div>
        </div>
      </el-col>
    </el-row>
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
</style>
