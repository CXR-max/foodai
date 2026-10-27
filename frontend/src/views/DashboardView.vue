<script setup lang="ts">
// 仪表盘：提醒横幅 + 今日核心数据 + 今日任务进度 + 近7天热量 + 快捷入口 + 分享海报
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import * as statsApi from '@/api/stats'
import type { Overview } from '@/api/stats'
import * as foodApi from '@/api/food'
import StatTile from '@/components/StatTile.vue'
import TrendChart from '@/components/TrendChart.vue'
import SharePosterDialog from '@/components/SharePosterDialog.vue'

const router = useRouter()
const ov = ref<Overview | null>(null)
const todayRecords = ref<any[]>([])
const calSeries = ref<{ date: string; calories: number }[]>([])
const calRange = ref<7 | 30>(7) // 热量趋势区间：近 7 天 / 近 30 天
const posterVisible = ref(false)

const MEAL_LABELS: Record<string, string> = { breakfast: '早餐', lunch: '午餐', dinner: '晚餐', snack: '加餐' }

// 今日未打卡提醒（横幅）
const reminders = computed(() => {
  if (!ov.value) return []
  const list: string[] = []
  if (!ov.value.today_checked.diet) list.push('🍽 今天的饮食还没打卡')
  if (!ov.value.today_checked.exercise) list.push('🏃 今天的运动还没打卡')
  return list
})

// 热量趋势图：随 7/30 天切换重算，含每日目标虚线
const chartOption = computed(() => {
  const list = calSeries.value.slice(-calRange.value)
  const target = ov.value?.daily_target
  const isMonth = calRange.value === 30
  return {
    tooltip: { trigger: 'axis', valueFormatter: (v: number) => `${v} kcal` },
    legend: target
      ? { data: ['摄入热量', '每日目标'], top: 4, right: 6, itemWidth: 12, itemHeight: 8, textStyle: { fontSize: 12, color: '#606266' } }
      : { show: false },
    grid: { left: 10, right: 18, top: target ? 44 : 26, bottom: 6, containLabel: true },
    xAxis: {
      type: 'category',
      boundaryGap: false,
      data: list.map((c) => c.date.slice(5)),
      axisTick: { show: false },
      axisLine: { lineStyle: { color: '#e4e7ed' } },
      axisLabel: { color: '#909399', fontSize: 11, hideOverlap: true },
    },
    yAxis: {
      type: 'value',
      name: 'kcal',
      nameTextStyle: { color: '#909399', fontSize: 11 },
      axisLabel: { color: '#909399', fontSize: 11 },
      splitLine: { lineStyle: { color: '#f0f2f5', type: 'dashed' } },
    },
    series: [
      {
        name: '摄入热量',
        type: isMonth ? 'bar' : 'line',
        smooth: true,
        symbol: 'circle',
        symbolSize: 5,
        barMaxWidth: 14,
        data: list.map((c) => c.calories),
        itemStyle: { color: '#67c23a', borderRadius: isMonth ? [3, 3, 0, 0] : 0 },
        lineStyle: { color: '#67c23a', width: 2.5 },
        areaStyle: isMonth ? undefined : { color: 'rgba(103,194,58,0.15)' },
      },
      ...(target
        ? [{
            name: '每日目标',
            type: 'line',
            data: list.map(() => target),
            symbol: 'none',
            lineStyle: { color: '#f56c6c', type: 'dashed', width: 1.5 },
            itemStyle: { color: '#f56c6c' },
          }]
        : []),
    ],
  }
})

async function load() {
  ov.value = await statsApi.getOverview()
  const today = await foodApi.getFoodRecords()
  todayRecords.value = today.records
  // 一次拉满 30 天，切 7/30 天时不再请求，直接前端截取
  calSeries.value = await statsApi.getCaloriesTrend(30)
}

onMounted(load)
</script>

<template>
  <div class="page" v-if="ov">
    <!-- 未打卡提醒横幅 -->
    <el-alert v-if="reminders.length" type="warning" :closable="false" class="banner">
      <template #title>
        <span v-for="(r, i) in reminders" :key="r">{{ r }}<span v-if="i < reminders.length - 1" style="margin:0 10px;color:#e6a23c">·</span></span>
        <el-button size="small" type="warning" style="margin-left:16px" @click="router.push('/checkin')">去打卡 →</el-button>
      </template>
    </el-alert>
    <el-alert v-else title="🎉 今天饮食和运动都打卡了，保持住！" type="success" :closable="false" class="banner" />

    <el-row :gutter="14" style="margin-top:14px">
      <el-col :span="6"><StatTile title="今日摄入" :value="ov.today_calories" :sub="ov.daily_target ? `/ ${ov.daily_target} kcal` : ''" icon="🔥" color="#f56c6c" /></el-col>
      <el-col :span="6"><StatTile title="今日消耗" :value="ov.today_burn" sub="kcal" icon="🏃" color="#e6a23c" /></el-col>
      <el-col :span="6"><StatTile title="连续打卡" :value="ov.streak_days" sub="天" icon="📅" color="#67c23a" /></el-col>
      <el-col :span="6"><StatTile title="当前排名" :value="`No.${ov.rank}`" :sub="`积分 ${ov.points_total}`" icon="🏆" color="#409eff" /></el-col>
    </el-row>

    <el-row :gutter="14" style="margin-top:14px">
      <el-col :span="14">
        <div class="page-card">
          <h3 class="page-title">
            📈 热量趋势
            <el-radio-group v-model="calRange" size="small" class="cal-switch">
              <el-radio-button :value="7">近 7 天</el-radio-button>
              <el-radio-button :value="30">近 30 天</el-radio-button>
            </el-radio-group>
          </h3>
          <TrendChart :option="chartOption" height="240px" />
          <p v-if="calRange === 30" class="chart-note">近 30 天按柱状展示，更易看出每日波动</p>
        </div>
      </el-col>
      <el-col :span="10">
        <div class="page-card">
          <h3 class="page-title">
            ✅ 今日任务
            <el-tag style="margin-left:auto" :type="ov.today_tasks.done === ov.today_tasks.total && ov.today_tasks.total ? 'success' : 'info'">
              {{ ov.today_tasks.done }}/{{ ov.today_tasks.total }}
            </el-tag>
          </h3>
          <el-progress :percentage="ov.today_tasks.total ? Math.round(ov.today_tasks.done / ov.today_tasks.total * 100) : 0" :stroke-width="16" status="success" />
          <div class="checks">
            <el-tag :type="ov.today_checked.diet ? 'success' : 'info'">🍽 饮食 {{ ov.today_checked.diet ? '已打卡' : '未打卡' }}</el-tag>
            <el-tag :type="ov.today_checked.exercise ? 'success' : 'info'">🏃 运动 {{ ov.today_checked.exercise ? '已打卡' : '未打卡' }}</el-tag>
          </div>

          <h3 class="page-title" style="margin-top:20px">🍽 今日饮食</h3>
          <el-empty v-if="!todayRecords.length" description="还没有记录，去识别一张食物照片吧" :image-size="60" />
          <div v-for="r in todayRecords" :key="r.id" class="record">
            <span>{{ MEAL_LABELS[r.meal_type] || '' }} · {{ r.dish_name }}（{{ r.portion_g }}g）</span>
            <b style="color:#f56c6c">{{ r.calories }} kcal</b>
          </div>

          <h3 class="page-title" style="margin-top:20px">⚡ 快捷入口</h3>
          <div class="shortcuts">
            <router-link to="/recognition"><el-button type="success" plain>📷 识别食物</el-button></router-link>
            <router-link to="/plans"><el-button type="primary" plain>✨ 生成方案</el-button></router-link>
            <router-link to="/checkin"><el-button type="warning" plain>📅 去打卡</el-button></router-link>
            <router-link to="/stats"><el-button type="info" plain>📊 数据追踪</el-button></router-link>
          </div>
          <div class="shortcuts" style="margin-top:10px">
            <el-button type="danger" plain @click="router.push('/stats')">🤖 AI 健康周报</el-button>
            <el-button type="success" plain @click="posterVisible = true">🖼 生成分享海报</el-button>
          </div>
        </div>
      </el-col>
    </el-row>

    <SharePosterDialog v-model:visible="posterVisible" />
  </div>
</template>

<style scoped>
.banner { border-radius: 10px; }
.cal-switch { margin-left: auto; }
.chart-note { margin: 4px 0 0; font-size: 12px; color: #909399; }
.checks { display: flex; gap: 10px; margin-top: 12px; }
.record { display: flex; justify-content: space-between; padding: 6px 0; border-bottom: 1px dashed #f0f0f0; font-size: 14px; }
.shortcuts { display: flex; gap: 10px; flex-wrap: wrap; }
</style>
