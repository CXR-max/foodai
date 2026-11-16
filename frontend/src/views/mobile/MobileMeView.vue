<script setup lang="ts">
// 移动端「我的」：数据总览 + 今日三环（饮食/运动/睡眠，读计划模块的目标）+ 功能收口
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import * as statsApi from '@/api/stats'
import * as wellnessApi from '@/api/wellness'
import type { DaySummary } from '@/api/wellness'
import type { Overview } from '@/api/stats'
import { useAuthStore } from '@/stores/auth'

const router = useRouter()
const auth = useAuthStore()

const ov = ref<Overview | null>(null)
const day = ref<DaySummary | null>(null)
const loading = ref(true)

const shortcuts = [
  { title: '健康档案', icon: '👤', path: '/profile' },
  { title: '数据追踪', icon: '📈', path: '/stats' },
  { title: '积分商城', icon: '🛍', path: '/mall' },
  { title: '成就徽章', icon: '🎖', path: '/achievements' },
  { title: '排行榜', icon: '🏆', path: '/leaderboard' },
  { title: '饮食记录', icon: '🍽', path: '/recognition' },
]

const calPct = computed(() => {
  const max = day.value?.targets.calorie_max
  if (!max) return 0
  return Math.min(100, Math.round(((day.value?.intake.calories || 0) / max) * 100))
})

const dietPct = computed(() => calPct.value)

const sportPct = computed(() => {
  const min = day.value?.targets.exercise_burn_min
  if (!min) return 0
  return Math.min(100, Math.round(((day.value?.burn.exercise_kcal || 0) / min) * 100))
})

const sleepPct = computed(() => {
  const min = day.value?.targets.sleep_min_h
  if (!min) return 0
  const hours = day.value?.sleep?.duration_h || 0
  return Math.min(100, Math.round((hours / min) * 100))
})

const waterPct = computed(() => {
  const target = day.value?.water.target_ml
  if (!target) return 0
  return Math.min(100, Math.round(((day.value?.water.total_ml || 0) / target) * 100))
})

// 环下面显示「实际 / 推荐区间」，区间来自后端 metric_bands，和日程页同源
const bands = computed(() => day.value?.bands || [])
const bandText = (key: string) => {
  const b = bands.value.find((x) => x.key === key)
  return b ? b.range_text : ''
}

async function load() {
  loading.value = true
  try {
    const [overview, summary] = await Promise.all([
      statsApi.getOverview(),
      wellnessApi.getDaySummary(),
    ])
    ov.value = overview
    day.value = summary
  } catch {
    // 拦截器已提示
  } finally {
    loading.value = false
  }
}

onMounted(() => {
  auth.fetchMe().catch(() => {})
  load()
})
</script>

<template>
  <div class="m-page">
    <!-- 用户卡 -->
    <div class="profile">
      <div class="avatar">{{ (auth.user?.nickname || 'U').slice(0, 1) }}</div>
      <div class="profile-main">
        <div class="nickname">{{ auth.user?.nickname || auth.user?.username }}</div>
        <div class="meta">
          💰 {{ auth.user?.points_total ?? 0 }} 积分
          <span v-if="ov"> · 排名 No.{{ ov.rank }}</span>
        </div>
      </div>
    </div>

    <!-- 今日数据 -->
    <section class="block">
      <h3 class="block-title">今日数据</h3>
      <div v-if="loading" class="card skeleton">加载中…</div>
      <div v-else-if="ov" class="card">
        <div class="stat-grid">
          <div class="stat">
            <div class="stat-value">{{ Math.round(day?.intake.calories || 0) }}</div>
            <div class="stat-label">摄入 kcal</div>
            <div class="stat-sub">
              目标 {{ day?.targets.calorie_min || '—' }}~{{ day?.targets.calorie_max || '—' }}
            </div>
          </div>
          <div class="stat">
            <div class="stat-value">{{ Math.round(day?.burn.exercise_kcal || 0) }}</div>
            <div class="stat-label">运动消耗 kcal</div>
            <div class="stat-sub">推荐 {{ bandText('exercise') || `≥${day?.targets.exercise_burn_min || '—'}` }}</div>
          </div>
          <div class="stat">
            <div class="stat-value">{{ day?.water.total_ml || 0 }}</div>
            <div class="stat-label">饮水 ml</div>
            <div class="stat-sub">推荐 {{ bandText('water') || `${day?.water.target_ml || 2000} ml` }}</div>
          </div>
          <div class="stat">
            <div class="stat-value">{{ day?.sleep?.duration_h ? day.sleep.duration_h.toFixed(1) : '—' }}</div>
            <div class="stat-label">睡眠 小时</div>
            <div class="stat-sub">推荐 {{ bandText('sleep') || `≥${day?.targets.sleep_min_h || 7} h` }}</div>
          </div>
        </div>
        <el-progress :percentage="calPct" :stroke-width="8" :show-text="false" color="#67c23a" style="margin-top: 12px" />
        <div class="stat-foot">
          <span>今日任务 {{ day?.tasks.done || 0 }}/{{ day?.tasks.total || 0 }}</span>
          <span>连续打卡 {{ ov.streak_days }} 天</span>
        </div>
      </div>
    </section>

    <!-- 快捷入口 -->
    <section class="block">
      <h3 class="block-title">功能入口</h3>
      <div class="grid">
        <div v-for="s in shortcuts" :key="s.path" class="grid-item" @click="router.push(s.path)">
          <span class="grid-icon">{{ s.icon }}</span>
          <span class="grid-title">{{ s.title }}</span>
        </div>
      </div>
    </section>

    <!-- 今日三环：目标全部来自计划模块，和日程/运动同一套数 -->
    <section class="block">
      <h3 class="block-title">
        今日三环
        <em v-if="day?.plan?.day_index">计划第 {{ day.plan.day_index }} 天</em>
      </h3>
      <div class="card">
        <div class="rings">
          <div class="ring">
            <el-progress type="circle" :percentage="dietPct" :width="72" :stroke-width="7" color="#67c23a">
              <span class="ring-num">{{ dietPct }}%</span>
            </el-progress>
            <span class="ring-label">饮食</span>
            <em>{{ Math.round(day?.intake.calories || 0) }}</em>
            <em class="ring-range">{{ bandText('calorie') || `≤${day?.targets.calorie_max || '—'} kcal` }}</em>
          </div>
          <div class="ring">
            <el-progress type="circle" :percentage="sportPct" :width="72" :stroke-width="7" color="#e6a23c">
              <span class="ring-num">{{ sportPct }}%</span>
            </el-progress>
            <span class="ring-label">运动</span>
            <em>{{ Math.round(day?.burn.exercise_kcal || 0) }}</em>
            <em class="ring-range">{{ bandText('exercise') || `≥${day?.targets.exercise_burn_min || '—'} kcal` }}</em>
          </div>
          <div class="ring">
            <el-progress type="circle" :percentage="sleepPct" :width="72" :stroke-width="7" color="#409eff">
              <span class="ring-num">{{ sleepPct }}%</span>
            </el-progress>
            <span class="ring-label">睡眠</span>
            <em>{{ day?.sleep?.duration_h ? day.sleep.duration_h.toFixed(1) : 0 }}</em>
            <em class="ring-range">{{ bandText('sleep') || `≥${day?.targets.sleep_min_h || 7} h` }}</em>
          </div>
          <div class="ring">
            <el-progress type="circle" :percentage="waterPct" :width="72" :stroke-width="7" color="#79bbff">
              <span class="ring-num">{{ waterPct }}%</span>
            </el-progress>
            <span class="ring-label">饮水</span>
            <em>{{ day?.water.total_ml || 0 }}</em>
            <em class="ring-range">{{ bandText('water') || `${day?.water.target_ml || 2000} ml` }}</em>
          </div>
        </div>
        <p class="hint">
          上方是实际值，下方是推荐区间（热量按下限~上限、运动/饮水/睡眠按区间）；
          处在浮动容忍内也算达标。目标与日程、运动页同源，改计划会自动跟着变。
        </p>
      </div>
    </section>
  </div>
</template>

<style scoped>
.m-page { display: flex; flex-direction: column; gap: 12px; }
.profile {
  display: flex;
  align-items: center;
  gap: 12px;
  background: linear-gradient(135deg, #4f9d2f, #82c45a);
  border-radius: 14px;
  padding: 16px;
  color: #fff;
}
.avatar {
  width: 46px;
  height: 46px;
  border-radius: 50%;
  background: rgba(255, 255, 255, 0.22);
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 20px;
  font-weight: 600;
}
.nickname { font-size: 16px; font-weight: 600; }
.meta { font-size: 12px; opacity: 0.92; margin-top: 2px; }
.block-title { font-size: 14px; font-weight: 600; color: #303133; margin: 4px 2px 8px; display: flex; align-items: center; gap: 6px; }
.card { background: #fff; border-radius: 12px; padding: 14px; box-shadow: 0 1px 3px rgba(0, 0, 0, 0.05); }
.card.muted { background: #fafafa; }
.stat-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 12px 8px; }
.stat { text-align: center; }
.stat-value { font-size: 20px; font-weight: 600; color: #303133; }
.stat-label { font-size: 12px; color: #909399; margin-top: 2px; }
.stat-sub { font-size: 11px; color: #b6bbc4; margin-top: 2px; }
.stat-foot { display: flex; justify-content: space-between; font-size: 12px; color: #909399; margin-top: 6px; }
.grid { display: grid; grid-template-columns: repeat(3, 1fr); gap: 8px; }
.grid-item {
  background: #fff;
  border-radius: 12px;
  padding: 14px 8px;
  text-align: center;
  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.05);
}
.grid-icon { font-size: 20px; display: block; }
.grid-title { font-size: 12px; color: #606266; display: block; margin-top: 6px; }
.hint { font-size: 12px; color: #a8abb2; margin: 10px 0 0; line-height: 1.7; }
.rings { display: grid; grid-template-columns: repeat(4, 1fr); gap: 6px; }
.ring { display: flex; flex-direction: column; align-items: center; gap: 4px; }
.ring-num { font-size: 12px; font-weight: 600; color: #303133; }
.ring-label { font-size: 12px; color: #606266; }
.ring em { font-style: normal; font-size: 10px; color: #b6bbc4; text-align: center; line-height: 1.3; }
.ring .ring-range { font-size: 9.5px; color: #a8abb2; }
.block-title em { font-style: normal; font-size: 11.5px; font-weight: 400; color: #909399; margin-left: auto; }
.skeleton { font-size: 13px; color: #909399; text-align: center; padding: 16px; }
</style>
