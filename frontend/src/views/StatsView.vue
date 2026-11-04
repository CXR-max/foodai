<script setup lang="ts">
// 数据追踪页：周/月切换 + 热量/运动/营养素/体重多形态图表（柱状·折线·饼图）+ AI 健康周报
import { computed, onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import * as statsApi from '@/api/stats'
import type { TrendsResult, WeeklyReport } from '@/api/stats'
import TrendChart from '@/components/TrendChart.vue'

type BarLine = 'bar' | 'line'
type NutKind = 'bar' | 'line' | 'pie'

const range = ref<'week' | 'month'>('week')
const trends = ref<TrendsResult | null>(null)
const nutrition = ref({ days_with_data: 0, avg_protein_g: 0, avg_fat_g: 0, avg_carb_g: 0 })

// 每张图的展示形态
const calKind = ref<BarLine>('bar')
const burnKind = ref<BarLine>('bar')
const nutKind = ref<NutKind>('bar')

const pts = computed(() => trends.value?.points ?? [])
const labels = computed(() => pts.value.map((p) => p.date.slice(5)))

// ---- 坐标轴/网格通用配置：containLabel + hideOverlap 防止标签压到图形上 ----
function xAxisCfg() {
  return {
    type: 'category',
    boundaryGap: true,
    data: labels.value,
    axisTick: { show: false },
    axisLine: { lineStyle: { color: '#e4e7ed' } },
    axisLabel: { color: '#909399', fontSize: 11, hideOverlap: true, margin: 10 },
  }
}
function yAxisCfg(unit: string) {
  return {
    type: 'value',
    name: unit,
    nameGap: 12,
    nameTextStyle: { color: '#909399', fontSize: 11, align: 'right', padding: [0, 0, 4, 0] },
    axisLabel: { color: '#909399', fontSize: 11 },
    splitLine: { lineStyle: { color: '#f0f2f5', type: 'dashed' } },
  }
}
const legendCfg = {
  top: 6,
  right: 6,
  icon: 'roundRect',
  itemWidth: 12,
  itemHeight: 8,
  itemGap: 12,
  textStyle: { fontSize: 12, color: '#606266' },
}
const baseGrid = { left: 6, right: 14, top: 46, bottom: 4, containLabel: true }

// 单序列：柱状 / 折线（同色，折线带渐变面积）
function seriesOf(kind: BarLine, name: string, data: number[], color: string) {
  if (kind === 'bar') {
    return { name, type: 'bar', data, barMaxWidth: 34, itemStyle: { color, borderRadius: [4, 4, 0, 0] } }
  }
  return {
    name,
    type: 'line',
    data,
    smooth: true,
    symbol: 'circle',
    symbolSize: 5,
    lineStyle: { color, width: 2.5 },
    itemStyle: { color },
    areaStyle: { color: `${color}22` },
  }
}

// ---- 热量摄入 vs 目标 ----
const calOption = computed(() => {
  const target = trends.value?.daily_target ?? null
  const series: any[] = [
    seriesOf(calKind.value, '摄入热量', pts.value.map((p) => p.calories), '#67c23a'),
  ]
  if (target) {
    series.push({
      name: '每日目标',
      type: 'line',
      data: pts.value.map(() => target),
      symbol: 'none',
      lineStyle: { color: '#f56c6c', type: 'dashed', width: 1.5 },
      itemStyle: { color: '#f56c6c' },
      z: 3,
    })
  }
  return {
    tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' }, valueFormatter: (v: number) => `${v} kcal` },
    legend: { ...legendCfg, data: target ? ['摄入热量', '每日目标'] : ['摄入热量'] },
    grid: baseGrid,
    xAxis: xAxisCfg(),
    yAxis: yAxisCfg('kcal'),
    series,
  }
})

// ---- 运动消耗 ----
const burnOption = computed(() => {
  const s: any = seriesOf(burnKind.value, '运动消耗', pts.value.map((p) => p.burn), '#e6a23c')
  // 柱状时给每一天画一个浅色底槽，空白天也能看出"这天没运动"（30 天太密就不画，避免条纹噪点）
  if (burnKind.value === 'bar' && pts.value.length <= 14) {
    s.showBackground = true
    s.backgroundStyle = { color: '#f7f8fa', borderRadius: [4, 4, 0, 0] }
  }
  return {
    tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' }, valueFormatter: (v: number) => `${v} kcal` },
    legend: { ...legendCfg, data: ['运动消耗'] },
    grid: baseGrid,
    xAxis: xAxisCfg(),
    yAxis: yAxisCfg('kcal'),
    series: [s],
  }
})

// ---- 营养素：堆叠柱 / 折线 / 饼图（区间总量占比） ----
const nutrientColors = { protein: '#409eff', fat: '#e6a23c', carb: '#67c23a' }

const nutrientSums = computed(() => {
  const s = { protein: 0, fat: 0, carb: 0 }
  for (const p of pts.value) {
    s.protein += p.protein_g || 0
    s.fat += p.fat_g || 0
    s.carb += p.carb_g || 0
  }
  return { protein: Math.round(s.protein), fat: Math.round(s.fat), carb: Math.round(s.carb) }
})

const nutrientOption = computed(() => {
  if (nutKind.value === 'pie') {
    const s = nutrientSums.value
    return {
      tooltip: { trigger: 'item', formatter: '{b}：{c} g（{d}%）' },
      series: [{
        type: 'pie',
        radius: ['44%', '68%'],
        center: ['50%', '50%'],
        avoidLabelOverlap: true,
        itemStyle: { borderColor: '#fff', borderWidth: 2 },
        label: { formatter: '{b}\n{d}%', fontSize: 12, color: '#606266', lineHeight: 16 },
        labelLine: { length: 10, length2: 12 },
        data: [
          { name: '蛋白质', value: s.protein, itemStyle: { color: nutrientColors.protein } },
          { name: '脂肪', value: s.fat, itemStyle: { color: nutrientColors.fat } },
          { name: '碳水', value: s.carb, itemStyle: { color: nutrientColors.carb } },
        ],
      }],
    }
  }
  const stack = nutKind.value === 'bar'
  const mk = (name: string, key: 'protein_g' | 'fat_g' | 'carb_g', color: string) => ({
    name,
    type: nutKind.value,
    ...(stack ? { stack: 'total', barMaxWidth: 34 } : { smooth: true, symbol: 'circle', symbolSize: 4, lineStyle: { width: 2.3 } }),
    data: pts.value.map((p) => p[key]),
    itemStyle: { color, ...(stack ? { borderColor: '#fff', borderWidth: 0 } : {}) },
  })
  return {
    tooltip: { trigger: 'axis', valueFormatter: (v: number) => `${v} g` },
    legend: { ...legendCfg, data: ['蛋白质', '脂肪', '碳水'] },
    grid: baseGrid,
    xAxis: xAxisCfg(),
    yAxis: yAxisCfg('g'),
    series: [
      mk('蛋白质', 'protein_g', nutrientColors.protein),
      mk('脂肪', 'fat_g', nutrientColors.fat),
      mk('碳水', 'carb_g', nutrientColors.carb),
    ],
  }
})

// ---- 体重曲线 ----
const weightPts = computed(() => pts.value.filter((p) => p.weight_kg != null))
const hasWeight = computed(() => weightPts.value.length > 0)
const weightOption = computed(() => ({
  tooltip: { trigger: 'axis', valueFormatter: (v: number) => `${v} kg` },
  grid: { ...baseGrid, top: 20 },
  xAxis: { ...xAxisCfg(), data: weightPts.value.map((p) => p.date.slice(5)) },
  yAxis: { ...yAxisCfg('kg'), scale: true },
  series: [{
    name: '体重',
    type: 'line',
    data: weightPts.value.map((p) => p.weight_kg),
    smooth: true,
    symbol: 'circle',
    symbolSize: 5,
    lineStyle: { color: '#409eff', width: 2.5 },
    itemStyle: { color: '#409eff' },
    areaStyle: { color: 'rgba(64,158,255,0.14)' },
  }],
}))

// ---- 顶部指标卡 ----
const kpi = computed(() => {
  const list = pts.value
  const dayWith = list.filter((p) => p.calories > 0)
  const avgIntake = dayWith.length ? Math.round(dayWith.reduce((s, p) => s + p.calories, 0) / dayWith.length) : 0
  const totalBurn = Math.round(list.reduce((s, p) => s + p.burn, 0))
  const w = list.filter((p) => p.weight_kg != null)
  const delta = w.length > 1 ? +(w[w.length - 1].weight_kg! - w[0].weight_kg!).toFixed(1) : null
  const target = trends.value?.daily_target ?? null
  const achieve = target ? Math.round((avgIntake / target) * 100) : null
  return { avgIntake, totalBurn, recorded: dayWith.length, total: list.length, delta, achieve }
})

// 营养结构均值（带占比条）
const nutBars = computed(() => {
  const items = [
    { name: '蛋白质', value: nutrition.value.avg_protein_g, color: nutrientColors.protein },
    { name: '脂肪', value: nutrition.value.avg_fat_g, color: nutrientColors.fat },
    { name: '碳水', value: nutrition.value.avg_carb_g, color: nutrientColors.carb },
  ]
  const max = Math.max(...items.map((i) => i.value), 1)
  return items.map((i) => ({ ...i, pct: Math.max(Math.round((i.value / max) * 100), i.value > 0 ? 6 : 0) }))
})

// ---- AI 周报 ----
const reportVisible = ref(false)
const reportLoading = ref(false)
const report = ref<WeeklyReport | null>(null)

async function loadCharts() {
  const [t, nut] = await Promise.all([statsApi.getTrends(range.value), statsApi.getNutrition(range.value === 'week' ? 7 : 30)])
  trends.value = t
  nutrition.value = nut
}

function switchRange(r: 'week' | 'month') {
  range.value = r
  loadCharts()
}

async function openReport() {
  reportVisible.value = true
  reportLoading.value = true
  try {
    report.value = await statsApi.getWeeklyReport()
  } catch {
    ElMessage.error('周报生成失败，请稍后再试')
  } finally {
    reportLoading.value = false
  }
}

onMounted(() => { loadCharts() })
</script>

<template>
  <div class="page">
    <!-- 顶部：范围切换 + 周报入口 -->
    <div class="page-card toolbar">
      <el-radio-group v-model="range" @change="(v: any) => switchRange(v)">
        <el-radio-button value="week">📅 近 7 天</el-radio-button>
        <el-radio-button value="month">🗓 近 30 天</el-radio-button>
      </el-radio-group>
      <div class="toolbar-right">
        <span v-if="trends" class="hint">已记录 {{ trends.recorded_days }}/{{ trends.days }} 天</span>
        <el-button type="primary" @click="openReport">🤖 AI 健康周报</el-button>
      </div>
    </div>

    <!-- 关键指标 -->
    <div class="page-card kpi-row">
      <div class="kpi">
        <span class="kpi-label">日均摄入</span>
        <span class="kpi-value">{{ kpi.avgIntake }}<em>kcal</em></span>
        <span class="kpi-sub">
          <template v-if="kpi.achieve != null">目标达成 <b :class="kpi.achieve > 110 ? 'up' : 'ok'">{{ kpi.achieve }}%</b></template>
          <template v-else>未设置目标</template>
        </span>
      </div>
      <div class="kpi">
        <span class="kpi-label">运动消耗合计</span>
        <span class="kpi-value">{{ kpi.totalBurn }}<em>kcal</em></span>
        <span class="kpi-sub">{{ range === 'week' ? '近 7 天' : '近 30 天' }}累计</span>
      </div>
      <div class="kpi">
        <span class="kpi-label">有效记录</span>
        <span class="kpi-value">{{ kpi.recorded }}<em>/ {{ kpi.total }} 天</em></span>
        <span class="kpi-sub">有饮食记录的天数</span>
      </div>
      <div class="kpi">
        <span class="kpi-label">体重变化</span>
        <span class="kpi-value" :class="kpi.delta == null ? '' : kpi.delta > 0 ? 'up' : 'down'">
          {{ kpi.delta == null ? '—' : (kpi.delta > 0 ? '+' : '') + kpi.delta }}<em v-if="kpi.delta != null">kg</em>
        </span>
        <span class="kpi-sub">区间首尾对比</span>
      </div>
    </div>

    <!-- 热量趋势（含目标对比线） -->
    <div class="page-card">
      <div class="card-head">
        <h3 class="page-title">🔥 热量摄入 vs 目标</h3>
        <el-radio-group v-model="calKind" size="small">
          <el-radio-button value="bar">柱状图</el-radio-button>
          <el-radio-button value="line">折线图</el-radio-button>
        </el-radio-group>
      </div>
      <TrendChart v-if="trends" :option="calOption" height="320px" />
      <p class="note">红色虚线为档案中的每日热量目标{{ trends?.daily_target ? `（${trends.daily_target} kcal）` : '' }}（到「健康档案」填写后显示）</p>
    </div>

    <el-row :gutter="14" style="margin-top:14px">
      <el-col :span="12">
        <div class="page-card">
          <div class="card-head">
            <h3 class="page-title">🏃 运动消耗</h3>
            <el-radio-group v-model="burnKind" size="small">
              <el-radio-button value="bar">柱状图</el-radio-button>
              <el-radio-button value="line">折线图</el-radio-button>
            </el-radio-group>
          </div>
          <TrendChart v-if="trends" :option="burnOption" height="280px" />
        </div>
      </el-col>
      <el-col :span="12">
        <div class="page-card">
          <div class="card-head">
            <h3 class="page-title">🥗 营养素摄入</h3>
            <el-radio-group v-model="nutKind" size="small">
              <el-radio-button value="bar">堆叠柱</el-radio-button>
              <el-radio-button value="line">折线图</el-radio-button>
              <el-radio-button value="pie">饼图</el-radio-button>
            </el-radio-group>
          </div>
          <TrendChart v-if="trends" :option="nutrientOption" height="280px" />
          <p v-if="nutKind === 'pie'" class="note">饼图按区间内三大营养素累计克数计算占比</p>
        </div>
      </el-col>
    </el-row>

    <el-row :gutter="14" style="margin-top:14px">
      <el-col :span="12">
        <div class="page-card">
          <h3 class="page-title">⚖️ 体重变化（{{ range === 'week' ? '近7天' : '近30天' }}）</h3>
          <TrendChart v-if="hasWeight" :option="weightOption" height="270px" />
          <el-empty v-else description="到「健康档案」页记录体重后显示曲线" :image-size="70" />
        </div>
      </el-col>
      <el-col :span="12">
        <div class="page-card">
          <h3 class="page-title">🧮 营养结构均值</h3>
          <div v-if="nutrition.days_with_data" class="nut-rows">
            <div v-for="n in nutBars" :key="n.name" class="nut-row">
              <span class="dot" :style="{ background: n.color }" />
              <span class="nut-name">{{ n.name }}</span>
              <div class="nut-bar"><i :style="{ width: n.pct + '%', background: n.color }" /></div>
              <b>{{ n.value }}<em>g/天</em></b>
            </div>
            <p class="note" style="text-align:left;margin-top:14px">基于有记录的 {{ nutrition.days_with_data }} 天折算的日均摄入，条形长度按三者最高值归一化</p>
          </div>
          <el-empty v-else description="记录饮食后显示营养结构" :image-size="70" />
        </div>
      </el-col>
    </el-row>

    <!-- AI 周报弹窗 -->
    <el-dialog v-model="reportVisible" title="🤖 AI 健康周报" width="560px">
      <div v-loading="reportLoading" style="min-height:200px">
        <template v-if="report">
          <div class="report-head">
            <div class="score-ring" :style="{ borderColor: report.score >= 75 ? '#67c23a' : report.score >= 50 ? '#e6a23c' : '#f56c6c' }">
              <b>{{ report.score }}</b><span>健康分</span>
            </div>
            <div class="head-stats">
              <div><b>{{ report.week_stats.avg_intake }}</b> 日均摄入 kcal</div>
              <div><b>{{ report.week_stats.total_burn }}</b> 运动消耗 kcal</div>
              <div><b>{{ report.week_stats.streak_days }}</b> 连续打卡天</div>
              <div v-if="report.week_stats.weight_change != null"><b>{{ report.week_stats.weight_change! > 0 ? '+' : '' }}{{ report.week_stats.weight_change }}</b> 体重变化 kg</div>
            </div>
          </div>

          <el-alert :title="report.summary" type="info" :closable="false" style="margin:12px 0" />

          <template v-if="report.highlights.length">
            <h4>✨ 做得好</h4>
            <ul class="report-list">
              <li v-for="(h, i) in report.highlights" :key="i">{{ h }}</li>
            </ul>
          </template>
          <template v-if="report.suggestions.length">
            <h4>💡 下周建议</h4>
            <ul class="report-list">
              <li v-for="(s2, i) in report.suggestions" :key="i">{{ s2 }}</li>
            </ul>
          </template>
          <p class="note" style="text-align:right">生成来源：{{ report.source }} · 数据区间 {{ report.week_stats.start }} ~ {{ report.week_stats.end }}</p>
        </template>
      </div>
    </el-dialog>
  </div>
</template>

<style scoped>
.toolbar { display: flex; align-items: center; justify-content: space-between; }
.toolbar-right { display: flex; align-items: center; gap: 12px; }
.hint { color: #909399; font-size: 13px; }
.note { color: #909399; font-size: 12px; text-align: center; margin: 6px 0 0; }

/* 卡片标题 + 图表形态切换 */
.card-head { display: flex; align-items: center; justify-content: space-between; gap: 12px; margin-bottom: 6px; }
.card-head .page-title { margin: 0; font-size: 16px; }

/* 指标卡 */
.kpi-row { display: grid; grid-template-columns: repeat(4, 1fr); gap: 8px; padding: 18px 20px; }
.kpi { display: flex; flex-direction: column; gap: 4px; padding: 4px 14px; border-left: 3px solid #ebeef5; }
.kpi:first-child { border-left: none; padding-left: 4px; }
.kpi-label { font-size: 13px; color: #909399; }
.kpi-value { font-size: 24px; font-weight: 700; color: #303133; line-height: 1.2; }
.kpi-value em { font-size: 12px; font-weight: 400; color: #909399; font-style: normal; margin-left: 4px; }
.kpi-sub { font-size: 12px; color: #909399; }
.kpi-sub b.ok { color: #67c23a; }
.kpi-value.up, .kpi-sub b.up { color: #f56c6c; }
.kpi-value.down { color: #67c23a; }

/* 营养结构均值 */
.nut-rows { padding: 6px 8px 0; }
.nut-row { display: flex; align-items: center; gap: 10px; padding: 12px 0; border-bottom: 1px dashed #f2f3f5; font-size: 14px; }
.nut-row:last-of-type { border-bottom: none; }
.nut-name { width: 46px; color: #606266; flex-shrink: 0; }
.nut-bar { flex: 1; height: 10px; background: #f5f7fa; border-radius: 6px; overflow: hidden; }
.nut-bar i { display: block; height: 100%; border-radius: 6px; transition: width .4s; }
.nut-row b { min-width: 78px; text-align: right; font-size: 15px; }
.nut-row b em { font-size: 11px; color: #909399; font-style: normal; margin-left: 3px; }
.dot { width: 9px; height: 9px; border-radius: 50%; display: inline-block; flex-shrink: 0; }

/* AI 周报 */
.report-head { display: flex; align-items: center; gap: 20px; }
.score-ring {
  width: 84px; height: 84px; border-radius: 50%; border: 5px solid #67c23a;
  display: flex; flex-direction: column; align-items: center; justify-content: center; flex-shrink: 0;
}
.score-ring b { font-size: 26px; }
.score-ring span { font-size: 11px; color: #909399; }
.head-stats { display: grid; grid-template-columns: 1fr 1fr; gap: 6px 18px; font-size: 13px; color: #606266; }
.head-stats b { font-size: 17px; color: #303133; margin-right: 4px; }
.report-list { margin: 6px 0 12px; padding-left: 20px; color: #606266; font-size: 14px; line-height: 1.8; }
</style>
