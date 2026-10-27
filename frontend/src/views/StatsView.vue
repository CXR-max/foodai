<script setup lang="ts">
// 数据追踪页：热量趋势（14天柱状+目标线）/ 体重曲线 / 营养结构饼图
import { onMounted, ref } from 'vue'
import * as statsApi from '@/api/stats'
import TrendChart from '@/components/TrendChart.vue'

const tab = ref('calories')
const calories = ref<{ date: string; calories: number }[]>([])
const weights = ref<{ date: string; weight_kg: number }[]>([])
const nutrition = ref({ days_with_data: 0, avg_protein_g: 0, avg_fat_g: 0, avg_carb_g: 0 })
const dailyTarget = ref<number | null>(null)

const caloriesOption = ref({})
const weightOption = ref({})
const nutritionOption = ref({})

async function load() {
  const [cal, w, nut, ov] = await Promise.all([
    statsApi.getCaloriesTrend(14),
    statsApi.getWeightTrend(90),
    statsApi.getNutrition(7),
    statsApi.getOverview(),
  ])
  calories.value = cal
  weights.value = w
  nutrition.value = nut
  dailyTarget.value = ov.daily_target

  caloriesOption.value = {
    tooltip: { trigger: 'axis' },
    grid: { left: 50, right: 20, top: 40, bottom: 30 },
    xAxis: { type: 'category', data: cal.map((c) => c.date.slice(5)) },
    yAxis: { type: 'value', name: 'kcal' },
    series: [{
      type: 'bar', data: cal.map((c) => c.calories), name: '摄入热量',
      itemStyle: { color: '#67c23a', borderRadius: [4, 4, 0, 0] },
      ...(dailyTarget.value
        ? { markLine: { data: [{ yAxis: dailyTarget.value, name: '目标' }], lineStyle: { color: '#f56c6c', type: 'dashed' }, label: { formatter: `目标 {c}` } } }
        : {}),
    }],
  }

  weightOption.value = {
    tooltip: { trigger: 'axis' },
    grid: { left: 50, right: 20, top: 30, bottom: 30 },
    xAxis: { type: 'category', data: weights.value.map((x) => x.date.slice(5)) },
    yAxis: { type: 'value', scale: true, name: 'kg' },
    series: [{
      type: 'line', smooth: true, data: weights.value.map((x) => x.weight_kg),
      lineStyle: { color: '#409eff' }, itemStyle: { color: '#409eff' },
      areaStyle: { color: 'rgba(64,158,255,0.12)' },
    }],
  }

  nutritionOption.value = {
    tooltip: { trigger: 'item' },
    legend: { bottom: 0 },
    series: [{
      type: 'pie', radius: ['38%', '62%'],
      data: [
        { name: '蛋白质', value: nutrition.value.avg_protein_g, itemStyle: { color: '#409eff' } },
        { name: '脂肪', value: nutrition.value.avg_fat_g, itemStyle: { color: '#e6a23c' } },
        { name: '碳水', value: nutrition.value.avg_carb_g, itemStyle: { color: '#67c23a' } },
      ],
      label: { formatter: '{b}: {c}g' },
    }],
  }
}

onMounted(load)
</script>

<template>
  <div class="page">
    <div class="page-card">
      <el-tabs v-model="tab">
        <el-tab-pane label="🔥 热量趋势（14天）" name="calories">
          <TrendChart :option="caloriesOption" />
          <p class="note">红色虚线为你档案里的每日热量目标{{ dailyTarget ? `（${dailyTarget} kcal）` : '' }}（先到健康档案填写后显示）</p>
        </el-tab-pane>
        <el-tab-pane label="⚖️ 体重变化" name="weight">
          <TrendChart v-if="weights.length" :option="weightOption" />
          <el-empty v-else description="去健康档案页记录体重后显示曲线" :image-size="80" />
        </el-tab-pane>
        <el-tab-pane label="🥗 营养结构（近7天日均）" name="nutrition">
          <TrendChart v-if="nutrition.days_with_data" :option="nutritionOption" />
          <el-empty v-else description="记录饮食后显示营养结构" :image-size="80" />
        </el-tab-pane>
      </el-tabs>
    </div>
  </div>
</template>

<style scoped>
.note { color: #909399; font-size: 12px; text-align: center; }
</style>
