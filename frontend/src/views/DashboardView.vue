<script setup lang="ts">
// 仪表盘：今日核心数据 + 今日任务进度 + 近7天热量 + 快捷入口
import { onMounted, ref } from 'vue'
import * as statsApi from '@/api/stats'
import type { Overview } from '@/api/stats'
import * as foodApi from '@/api/food'
import StatTile from '@/components/StatTile.vue'
import TrendChart from '@/components/TrendChart.vue'

const ov = ref<Overview | null>(null)
const todayRecords = ref<any[]>([])
const chartOption = ref({})

const MEAL_LABELS: Record<string, string> = { breakfast: '早餐', lunch: '午餐', dinner: '晚餐', snack: '加餐' }

async function load() {
  ov.value = await statsApi.getOverview()
  const today = await foodApi.getFoodRecords()
  todayRecords.value = today.records
  const cal = (await statsApi.getCaloriesTrend(7)).slice(-7)
  chartOption.value = {
    tooltip: { trigger: 'axis' },
    grid: { left: 45, right: 10, top: 20, bottom: 25 },
    xAxis: { type: 'category', data: cal.map((c) => c.date.slice(5)) },
    yAxis: { type: 'value' },
    series: [{
      type: 'line', smooth: true, data: cal.map((c) => c.calories),
      lineStyle: { color: '#67c23a' }, itemStyle: { color: '#67c23a' },
      areaStyle: { color: 'rgba(103,194,58,0.15)' },
    }],
  }
}

onMounted(load)
</script>

<template>
  <div class="page" v-if="ov">
    <el-row :gutter="14">
      <el-col :span="6"><StatTile title="今日摄入" :value="ov.today_calories" :sub="ov.daily_target ? `/ ${ov.daily_target} kcal` : ''" icon="🔥" color="#f56c6c" /></el-col>
      <el-col :span="6"><StatTile title="连续打卡" :value="ov.streak_days" sub="天" icon="📅" color="#e6a23c" /></el-col>
      <el-col :span="6"><StatTile title="总积分" :value="ov.points_total" icon="💰" color="#67c23a" /></el-col>
      <el-col :span="6"><StatTile title="当前排名" :value="`No.${ov.rank}`" icon="🏆" color="#409eff" /></el-col>
    </el-row>

    <el-row :gutter="14" style="margin-top:14px">
      <el-col :span="14">
        <div class="page-card">
          <h3 class="page-title">📈 近 7 天热量趋势</h3>
          <TrendChart :option="chartOption" height="240px" />
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
          </div>
        </div>
      </el-col>
    </el-row>
  </div>
</template>

<style scoped>
.checks { display: flex; gap: 10px; margin-top: 12px; }
.record { display: flex; justify-content: space-between; padding: 6px 0; border-bottom: 1px dashed #f0f0f0; font-size: 14px; }
.shortcuts { display: flex; gap: 10px; }
</style>
