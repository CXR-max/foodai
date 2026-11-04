<script setup lang="ts">
// 仪表盘：报告式首屏（问候 + 环形热量 + 餐次轴 + 数据票）+ 热量趋势 + 今日记录 + 快捷入口
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import * as statsApi from '@/api/stats'
import type { Overview } from '@/api/stats'
import * as foodApi from '@/api/food'
import type { FoodRecord } from '@/api/food'
import TrendChart from '@/components/TrendChart.vue'
import SharePosterDialog from '@/components/SharePosterDialog.vue'
import { useAuthStore } from '@/stores/auth'

const auth = useAuthStore()
const router = useRouter()
const ov = ref<Overview | null>(null)
const todayRecords = ref<FoodRecord[]>([])
const calSeries = ref<{ date: string; calories: number }[]>([])
const calRange = ref<7 | 30>(7) // 热量趋势区间：近 7 天 / 近 30 天
const posterVisible = ref(false)

const MEAL_LABELS: Record<string, string> = { breakfast: '早餐', lunch: '午餐', dinner: '晚餐', snack: '加餐' }
const WEEK = ['周日', '周一', '周二', '周三', '周四', '周五', '周六']

// 今日未打卡提醒
const reminders = computed(() => {
  if (!ov.value) return []
  const list: string[] = []
  if (!ov.value.today_checked.diet) list.push('饮食还没打卡')
  if (!ov.value.today_checked.exercise) list.push('运动还没打卡')
  return list
})

const hour = new Date().getHours()
const greeting = hour < 6 ? '夜深了' : hour < 11 ? '早上好' : hour < 14 ? '中午好' : hour < 18 ? '下午好' : '晚上好'
const nickname = computed(() => auth.user?.nickname || '朋友')

const ringPct = computed(() => {
  if (!ov.value?.daily_target) return 0
  return Math.min(100, Math.round((ov.value.today_calories / ov.value.daily_target) * 100))
})

const remaining = computed(() => {
  if (!ov.value?.daily_target) return null
  return Math.round(ov.value.daily_target - ov.value.today_calories)
})

// 一句基于真实数据的提示（不编造 AI 结论）
const tip = computed(() => {
  const o = ov.value
  if (!o) return ''
  const parts: string[] = []
  if (remaining.value !== null) {
    parts.push(remaining.value >= 0 ? `距今日目标还差 ${remaining.value} kcal` : `已超出目标 ${-remaining.value} kcal`)
  }
  if (!o.today_checked.diet) parts.push('饮食待打卡')
  if (!o.today_checked.exercise) parts.push('运动待打卡')
  if (o.today_tasks.total > o.today_tasks.done) parts.push(`今日还有 ${o.today_tasks.total - o.today_tasks.done} 项任务`)
  return parts.length ? parts.join(' · ') : '今天的任务已经全部完成。'
})

// 今日餐次轴：按餐次汇总热量
const meals = computed(() => {
  const sums: Record<string, number> = { breakfast: 0, lunch: 0, dinner: 0, snack: 0 }
  for (const r of todayRecords.value) {
    if (sums[r.meal_type] === undefined) sums[r.meal_type] = 0
    sums[r.meal_type] += r.calories
  }
  return ['breakfast', 'lunch', 'dinner', 'snack'].map((key) => ({
    key,
    label: MEAL_LABELS[key],
    kcal: Math.round(sums[key] || 0),
  }))
})

const totalRecordKcal = computed(() => Math.round(todayRecords.value.reduce((s, r) => s + r.calories, 0)))

// 热量趋势图：随 7/30 天切换重算，含每日目标虚线
const chartOption = computed(() => {
  const list = calSeries.value.slice(-calRange.value)
  const target = ov.value?.daily_target
  const isMonth = calRange.value === 30
  return {
    tooltip: {
      trigger: 'axis',
      valueFormatter: (v: number) => `${v} kcal`,
      backgroundColor: '#fdfbf6',
      borderColor: '#cfc6b2',
      textStyle: { color: '#1c1a17', fontSize: 12 },
    },
    legend: target
      ? { data: ['摄入热量', '每日目标'], top: 4, right: 6, itemWidth: 12, itemHeight: 8, textStyle: { fontSize: 11, color: '#7c7669' } }
      : { show: false },
    grid: { left: 10, right: 18, top: target ? 44 : 26, bottom: 6, containLabel: true },
    xAxis: {
      type: 'category',
      boundaryGap: false,
      data: list.map((c) => c.date.slice(5)),
      axisTick: { show: false },
      axisLine: { lineStyle: { color: '#e2dbcb' } },
      axisLabel: { color: '#7c7669', fontSize: 11, hideOverlap: true },
    },
    yAxis: {
      type: 'value',
      name: 'kcal',
      nameTextStyle: { color: '#7c7669', fontSize: 11 },
      axisLabel: { color: '#7c7669', fontSize: 11 },
      splitLine: { lineStyle: { color: '#eae4d7', type: 'dashed' } },
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
        itemStyle: { color: '#5a5f8c', borderRadius: isMonth ? [3, 3, 0, 0] : 0 },
        lineStyle: { color: '#5a5f8c', width: 2 },
        areaStyle: isMonth ? undefined : { color: 'rgba(90,95,140,0.10)' },
      },
      ...(target
        ? [{
            name: '每日目标',
            type: 'line',
            data: list.map(() => target),
            symbol: 'none',
            lineStyle: { color: '#a9563c', type: 'dashed', width: 1.2 },
            itemStyle: { color: '#a9563c' },
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
  <div class="page">
    <div v-if="!ov" class="loading-note mono">LOADING TODAY'S REPORT …</div>

    <template v-else>
      <!-- 提醒条 -->
      <div class="notice" :class="{ clear: !reminders.length }">
        <span class="notice-dot"></span>
        <span class="notice-text">
          <template v-if="reminders.length">{{ reminders.join(' · ') }}</template>
          <template v-else>今天饮食和运动都已打卡</template>
        </span>
        <span class="notice-sub mono">{{ ov.today_tasks.total ? `${ov.today_tasks.done}/${ov.today_tasks.total} TASKS` : 'NO TASKS' }}</span>
        <button class="btn-ghost" @click="router.push('/checkin')">
          {{ reminders.length ? '去打卡' : '查看打卡' }}
        </button>
      </div>

      <!-- 首屏报告头 -->
      <section class="hero">
        <div class="hero-left">
          <div>
            <div class="greet-kicker mono">
              {{ WEEK[new Date().getDay()] }} · 连续第 {{ ov.streak_days }} 天
            </div>
            <h1 class="greet">
              {{ greeting }}，{{ nickname }}<br />
              <span>今天也吃好一点。</span>
            </h1>
            <p class="greet-sub">{{ tip }}</p>
          </div>

          <div class="kcal">
            <div class="ring" :style="{ '--p': ringPct }">
              <div class="ring-in">
                <span class="ring-num">{{ Math.round(ov.today_calories) }}</span>
                <span class="ring-unit mono">/ {{ ov.daily_target ?? '—' }} KCAL</span>
              </div>
            </div>
            <div class="kcal-side">
              <div class="kcal-row">
                <span class="kcal-label">已摄入</span>
                <span class="kcal-val mono">{{ Math.round(ov.today_calories) }}<small>kcal</small></span>
              </div>
              <div class="kcal-row">
                <span class="kcal-label">运动消耗</span>
                <span class="kcal-val mono">{{ Math.round(ov.today_burn) }}<small>kcal</small></span>
              </div>
              <div class="kcal-row">
                <span class="kcal-label">{{ remaining !== null && remaining < 0 ? '超出目标' : '今日剩余' }}</span>
                <span class="kcal-val mono" :class="{ over: remaining !== null && remaining < 0 }">
                  {{ remaining === null ? '—' : Math.abs(remaining) }}<small>kcal</small>
                </span>
              </div>
            </div>
          </div>

          <div class="meals">
            <div v-for="m in meals" :key="m.key" class="meal" :class="{ done: todayRecords.some((r) => r.meal_type === m.key) }">
              <div class="meal-name">{{ m.label }}</div>
              <div class="meal-val mono" :class="{ empty: !m.kcal }">{{ m.kcal ? m.kcal : '待记录' }}</div>
            </div>
          </div>
        </div>

        <aside class="stats-card">
          <div class="ticket-head">
            <span class="ticket-k mono">TODAY'S TICKET</span>
            <span class="ticket-d mono">{{ new Date().getMonth() + 1 }} / {{ new Date().getDate() }}</span>
          </div>
          <div class="stat-row">
            <span class="stat-k"><i></i>连续打卡</span>
            <span class="stat-v mono">{{ ov.streak_days }}<small>天</small></span>
          </div>
          <div class="stat-row">
            <span class="stat-k"><i></i>当前排名</span>
            <span class="stat-v mono">No.{{ ov.rank }}</span>
          </div>
          <div class="stat-row">
            <span class="stat-k"><i></i>可用积分</span>
            <span class="stat-v mono">{{ ov.points_total.toLocaleString() }}</span>
          </div>
          <div class="stat-row">
            <span class="stat-k"><i></i>今日消耗</span>
            <span class="stat-v mono">{{ Math.round(ov.today_burn) }}<small>kcal</small></span>
          </div>
          <div class="stat-foot mono">
            <span>本周均值 {{ Math.round(ov.week_avg_intake) }} kcal/天</span>
            <span>本周消耗 {{ Math.round(ov.week_burn) }} kcal</span>
          </div>
        </aside>
      </section>

      <!-- 热量趋势 -->
      <div class="sec">
        <span class="sec-no">01</span>
        <span class="sec-title">热量趋势</span>
        <span class="sec-line"></span>
        <span class="seg">
          <button :class="{ on: calRange === 7 }" @click="calRange = 7">近 7 天</button>
          <button :class="{ on: calRange === 30 }" @click="calRange = 30">近 30 天</button>
        </span>
      </div>

      <div class="chart-card">
        <div class="chart-head">
          <span class="chart-k mono">DAILY INTAKE</span>
          <span class="chart-hint">虚线为每日目标 {{ ov.daily_target ?? '未设置' }}</span>
        </div>
        <TrendChart :option="chartOption" height="248px" />
        <p v-if="calRange === 30" class="chart-note mono">近 30 天按柱状展示，更易看出每日波动</p>
      </div>

      <!-- 今日记录 + 快捷入口 -->
      <div class="lower">
        <div>
          <div class="sec">
            <span class="sec-no">02</span>
            <span class="sec-title">今日记录</span>
            <span class="sec-line"></span>
            <span class="sec-act" @click="router.push('/recognition')">去识别 →</span>
          </div>

          <div v-if="!todayRecords.length" class="empty-line">
            <span class="empty-k mono">NO RECORDS YET</span>
            <span class="empty-t">今天还没有饮食记录。识别一张食物照片，营养会自动入账。</span>
            <button class="btn-ghost" @click="router.push('/recognition')">识别食物</button>
          </div>

          <div v-else class="rec-list">
            <div v-for="r in todayRecords" :key="r.id" class="rec-row">
              <span class="rec-meal">{{ MEAL_LABELS[r.meal_type] || r.meal_type }}</span>
              <span class="rec-name">
                {{ r.dish_name }}
                <small class="mono">{{ r.portion_g }} g</small>
              </span>
              <span class="rec-kcal mono">{{ Math.round(r.calories) }} kcal</span>
            </div>
            <div class="rec-total">
              <span class="mono">合计 {{ todayRecords.length }} 条记录</span>
              <span class="mono rec-total-v">{{ totalRecordKcal }} kcal</span>
            </div>
          </div>
        </div>

        <div>
          <div class="sec">
            <span class="sec-no">03</span>
            <span class="sec-title">快捷入口</span>
            <span class="sec-line"></span>
          </div>
          <div class="quick">
            <button class="quick-item" @click="router.push('/recognition')">
              <span class="quick-k mono">01</span>
              <span class="quick-t">识别食物</span>
              <span class="quick-s">拍一张，自动算营养</span>
            </button>
            <button class="quick-item" @click="router.push('/plans')">
              <span class="quick-k mono">02</span>
              <span class="quick-t">养生方案</span>
              <span class="quick-s">按目标生成一周餐单</span>
            </button>
            <button class="quick-item" @click="router.push('/stats')">
              <span class="quick-k mono">03</span>
              <span class="quick-t">AI 健康周报</span>
              <span class="quick-s">近 7 天数据总结</span>
            </button>
            <button class="quick-item" @click="posterVisible = true">
              <span class="quick-k mono">04</span>
              <span class="quick-t">分享海报</span>
              <span class="quick-s">把坚持导出一张图</span>
            </button>
          </div>
        </div>
      </div>

      <SharePosterDialog v-model:visible="posterVisible" />
    </template>
  </div>
</template>

<style scoped>
.loading-note {
  padding: 80px 0;
  text-align: center;
  color: var(--muted);
  font-size: 11px;
  letter-spacing: 0.18em;
}

/* ---------- 提醒条 ---------- */
.notice {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 10px 16px;
  border: 1px solid #e7d5bb;
  background: #f7efe3;
  border-radius: 4px;
  margin-bottom: 26px;
}
.notice.clear { border-color: #d6dccd; background: #eef0e9; }
.notice-dot { width: 6px; height: 6px; border-radius: 50%; background: var(--ochre); flex: 0 0 6px; }
.notice.clear .notice-dot { background: var(--sage); }
.notice-text { font-size: 12.5px; color: #7d5626; }
.notice.clear .notice-text { color: #4c5c45; }
.notice-sub { margin-left: auto; font-size: 10px; letter-spacing: 0.12em; color: var(--muted); }
.notice .btn-ghost { border-color: currentColor; color: inherit; }
.notice .btn-ghost:hover { background: var(--ink); color: var(--paper); }

/* ---------- 首屏 ---------- */
.hero {
  display: grid;
  grid-template-columns: minmax(0, 1.55fr) minmax(0, 1fr);
  gap: 30px;
  align-items: stretch;
}

.hero-left { display: flex; flex-direction: column; justify-content: space-between; }

.greet-kicker {
  font-size: 10.5px;
  letter-spacing: 0.18em;
  text-transform: uppercase;
  color: var(--muted);
  margin-bottom: 10px;
}
.greet {
  font-family: var(--serif);
  font-size: 38px;
  line-height: 1.18;
  font-weight: 600;
  letter-spacing: -0.01em;
  margin: 0;
}
.greet span { color: var(--muted); }
.greet-sub {
  margin: 12px 0 0;
  font-size: 12.5px;
  color: var(--ink-2);
  line-height: 1.7;
}

/* 环形热量 */
.kcal { display: flex; align-items: center; gap: 32px; margin-top: 26px; }
.ring {
  --p: 0;
  position: relative;
  width: 150px;
  height: 150px;
  flex: 0 0 150px;
  border-radius: 50%;
  background: conic-gradient(var(--accent) calc(var(--p) * 1%), var(--line-2) 0);
}
.ring::after {
  content: '';
  position: absolute;
  inset: 0;
  border-radius: 50%;
  background: radial-gradient(circle at 30% 20%, rgba(255, 255, 255, 0.5), transparent 62%);
}
.ring-in {
  position: absolute;
  inset: 15px;
  background: var(--paper);
  border-radius: 50%;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  z-index: 1;
}
.ring-num { font-family: var(--mono); font-size: 28px; font-weight: 600; letter-spacing: -0.02em; line-height: 1; }
.ring-unit { font-size: 9.5px; color: var(--muted); letter-spacing: 0.1em; margin-top: 7px; }

.kcal-side { flex: 1; min-width: 0; }
.kcal-row {
  display: flex;
  justify-content: space-between;
  align-items: baseline;
  padding: 10px 0;
  border-top: 1px solid var(--line);
}
.kcal-row:first-child { border-top: 0; padding-top: 0; }
.kcal-label { font-size: 12px; color: var(--muted); }
.kcal-val { font-size: 15px; font-weight: 600; }
.kcal-val small { font-size: 10px; color: var(--muted); font-weight: 400; margin-left: 3px; }
.kcal-val.over { color: var(--clay); }

/* 餐次轴 */
.meals {
  margin-top: 24px;
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  border-top: 1px solid var(--line);
  padding-top: 18px;
}
.meal { position: relative; padding-right: 10px; }
.meal::before {
  content: '';
  position: absolute;
  top: -22px;
  left: 0;
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: var(--line-2);
  border: 2px solid var(--paper);
}
.meal.done::before { background: var(--accent); }
.meal-name { font-size: 11.5px; color: var(--muted); margin-bottom: 5px; }
.meal-val { font-size: 13px; font-weight: 600; }
.meal-val.empty { color: #b9b09c; font-weight: 400; font-size: 11.5px; }

/* 数据票 */
.stats-card {
  border: 1px solid var(--line);
  background: var(--surface);
  border-radius: 4px;
  display: flex;
  flex-direction: column;
}
.ticket-head {
  display: flex;
  justify-content: space-between;
  align-items: baseline;
  padding: 13px 18px;
  border-bottom: 1px solid var(--line-2);
  background: var(--paper-2);
}
.ticket-k { font-size: 9px; letter-spacing: 0.16em; color: var(--muted); }
.ticket-d { font-size: 9px; letter-spacing: 0.1em; color: var(--muted); }
.stat-row {
  flex: 1;
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 12px 18px;
  border-bottom: 1px solid var(--line);
}
.stat-k { font-size: 12px; color: var(--muted); display: flex; align-items: center; gap: 8px; }
.stat-k i { width: 4px; height: 4px; border-radius: 50%; background: var(--accent); display: inline-block; }
.stat-v { font-size: 20px; font-weight: 600; letter-spacing: -0.01em; }
.stat-v small { font-size: 10.5px; color: var(--muted); font-weight: 400; margin-left: 4px; }
.stat-foot {
  margin-top: auto;
  padding: 12px 18px;
  display: flex;
  flex-direction: column;
  gap: 4px;
  font-size: 10px;
  letter-spacing: 0.04em;
  color: var(--muted);
  background: var(--paper-2);
  white-space: nowrap;
}

/* ---------- 趋势 ---------- */
.seg { display: inline-flex; border: 1px solid var(--line-2); border-radius: 999px; overflow: hidden; }
.seg button {
  border: 0;
  background: var(--surface);
  color: var(--muted);
  font-family: var(--sans);
  font-size: 11.5px;
  padding: 5px 13px;
  cursor: pointer;
  transition: background 0.18s, color 0.18s;
}
.seg button + button { border-left: 1px solid var(--line-2); }
.seg button.on { background: var(--ink); color: var(--paper); }

.chart-card {
  border: 1px solid var(--line);
  background: var(--surface);
  border-radius: 4px;
  padding: 16px 18px 12px;
}
.chart-head { display: flex; align-items: baseline; justify-content: space-between; margin-bottom: 4px; }
.chart-k { font-size: 10px; letter-spacing: 0.16em; color: var(--muted); }
.chart-hint { font-size: 11px; color: var(--muted); }
.chart-note { margin: 6px 0 0; font-size: 10.5px; color: var(--muted); letter-spacing: 0.04em; }

/* ---------- 下区 ---------- */
.lower { display: grid; grid-template-columns: minmax(0, 1.55fr) minmax(0, 1fr); gap: 34px; margin-top: 4px; }

.empty-line {
  border: 1px dashed var(--line-2);
  border-radius: 4px;
  padding: 22px;
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 8px;
}
.empty-k { font-size: 10px; letter-spacing: 0.16em; color: var(--muted); }
.empty-t { font-size: 12.5px; color: var(--ink-2); line-height: 1.75; }

.rec-list { border-top: 1px solid var(--line-2); }
.rec-row {
  display: grid;
  grid-template-columns: 62px minmax(0, 1fr) auto;
  align-items: baseline;
  gap: 12px;
  padding: 12px 2px;
  border-bottom: 1px solid var(--line);
}
.rec-meal { font-size: 11.5px; color: var(--muted); }
.rec-name { font-size: 13.5px; color: var(--ink); }
.rec-name small { margin-left: 8px; font-size: 10.5px; color: var(--muted); }
.rec-kcal { font-size: 12.5px; color: var(--clay); font-weight: 600; }
.rec-total {
  display: flex;
  justify-content: space-between;
  padding: 11px 2px 0;
  font-size: 10.5px;
  letter-spacing: 0.06em;
  color: var(--muted);
}
.rec-total-v { color: var(--ink); font-weight: 600; }

/* 快捷入口 */
.quick { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; }
.quick-item {
  text-align: left;
  border: 1px solid var(--line);
  background: var(--surface);
  border-radius: 4px;
  padding: 13px 14px;
  cursor: pointer;
  display: flex;
  flex-direction: column;
  gap: 3px;
  transition: border-color 0.18s, transform 0.18s;
  font-family: var(--sans);
}
.quick-item:hover { border-color: var(--ink); transform: translateY(-1px); }
.quick-k { font-size: 9.5px; letter-spacing: 0.14em; color: var(--accent); }
.quick-t { font-size: 13px; font-weight: 600; color: var(--ink); }
.quick-s { font-size: 10.5px; color: var(--muted); line-height: 1.5; }

@media (max-width: 1080px) {
  .hero, .lower { grid-template-columns: 1fr; }
  .greet { font-size: 30px; }
}
</style>
