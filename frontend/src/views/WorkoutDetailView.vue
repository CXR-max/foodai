<script setup lang="ts">
// 运动报告页：轨迹 + 核心数据 + 与上次对比 + 分段 + 消耗明细
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import * as workoutApi from '@/api/workout'
import type { WorkoutDetail } from '@/api/workout'
import TrackMap from '@/components/TrackMap.vue'
import {
  fmtDistance, fmtDuration, fmtPace, fmtSpeed, fmtWhen, isRide, paceColor, paceToSpeed, sportIcon,
} from '@/utils/workout'
import { refreshTodayCheckin } from '@/stores/todayCheckin'
import { isMobile } from '@/composables/useIsMobile'

const route = useRoute()
const router = useRouter()
const id = Number(route.params.id)

/** 手机端回新的运动首页；桌面端才回 /workout */
function backToList() {
  router.replace(isMobile.value ? '/m/sport' : '/workout')
}

const w = ref<WorkoutDetail | null>(null)
const loading = ref(true)
const showBurn = ref(false)

const dist = computed(() => fmtDistance(w.value?.distance_m || 0))
const ride = computed(() => isRide(w.value?.sport_type || ''))
const speedKmh = computed(() => {
  const d = w.value?.distance_m || 0
  const s = w.value?.duration_sec || 0
  if (!d || !s) return null
  return (d / 1000) / (s / 3600)
})
const avgSpeed = computed(() => paceToSpeed(w.value?.avg_pace_sec_km) || speedKmh.value)
const intensityLabel = computed(() => ({ light: '轻松', moderate: '适中', intense: '吃力' } as any)[w.value?.intensity || ''] || '适中')

const splits = computed(() => w.value?.splits || [])
const paceRange = computed(() => {
  const arr = splits.value.map((s) => s.pace_sec_km).filter((p) => p > 0)
  return { min: Math.min(...arr), max: Math.max(...arr) }
})
function splitWidth(p: number) {
  if (!p || !paceRange.value.min) return 40
  return Math.max(18, Math.min(100, Math.round((paceRange.value.min / p) * 100)))
}
function splitText(p: number) {
  if (!p) return '--'
  return ride.value ? `${fmtSpeed(3600 / p)} km/h` : fmtPace(p)
}

const cmp = computed(() => w.value?.compare || null)
const cmpDistance = computed(() => {
  const d = cmp.value?.diff_distance_m || 0
  return `${d >= 0 ? '+' : ''}${(d / 1000).toFixed(2)} km`
})
const cmpDuration = computed(() => {
  const s = cmp.value?.diff_duration_sec || 0
  return `${s <= 0 ? '快' : '慢'} ${fmtDuration(Math.abs(s))}`
})
const cmpPace = computed(() => {
  const s = cmp.value?.diff_pace_sec_km
  if (s == null) return null
  if (ride.value) {
    const cur = paceToSpeed(w.value?.avg_pace_sec_km)
    const prev = paceToSpeed(cmp.value?.avg_pace_sec_km)
    if (!cur || !prev) return null
    return `${s <= 0 ? '快' : '慢'} ${Math.abs(cur - prev).toFixed(1)} km/h`
  }
  return `${s <= 0 ? '快' : '慢'} ${Math.abs(s).toFixed(0)}″/km`
})

const stats = computed(() => w.value?.track_stats || null)

async function load() {
  loading.value = true
  try {
    w.value = await workoutApi.getWorkout(id)
  } catch {
    ElMessage.error('运动记录不存在')
    backToList()
  } finally {
    loading.value = false
  }
}

async function remove() {
  try {
    await ElMessageBox.confirm('删除后不可恢复，当天的运动记录也会一并移除（若它由本条记录生成）', '删除记录', {
      confirmButtonText: '删除', cancelButtonText: '取消', type: 'warning',
    })
  } catch {
    return
  }
  await workoutApi.deleteWorkout(id)
  refreshTodayCheckin()
  ElMessage.success('已删除')
  backToList()
}

async function share() {
  if (!w.value) return
  const text = `我刚完成 ${w.value.sport_type} ${dist.value.value}${dist.value.unit}，`
    + `用时 ${fmtDuration(w.value.duration_sec)}，消耗 ${Math.round(w.value.calories || 0)} kcal。`
  try {
    if (navigator.share) {
      await navigator.share({ title: '我的运动报告', text })
    } else if (navigator.clipboard) {
      await navigator.clipboard.writeText(text)
      ElMessage.success('成绩已复制，去粘贴分享吧')
    } else {
      ElMessage.info(text)
    }
  } catch {
    /* 用户取消分享 */
  }
}

onMounted(load)
</script>

<template>
  <div class="detail">
    <header class="head">
      <button class="head-back" @click="router.back()">‹</button>
      <span class="head-title">运动报告</span>
      <button class="head-act" @click="share">分享</button>
      <button class="head-del" @click="remove">删除</button>
    </header>

    <div v-if="loading" class="loading">加载中…</div>

    <template v-else-if="w">
      <!-- 轨迹 -->
      <TrackMap
        v-if="w.has_track && w.track.length > 1"
        :points="w.track"
        :sport="w.sport_type"
        height="230px"
      />
      <div v-else class="no-track">
        <span>{{ sportIcon(w.sport_type) }}</span>
        <p>本次运动没有轨迹（{{ w.source === 'manual' ? '手动补录' : w.source === 'band' ? '手环导入' : '未采集到定位' }}）</p>
      </div>

      <!-- 核心四格 -->
      <div class="cards">
        <div class="card">
          <b>{{ dist.value }}</b><span>{{ dist.unit }}</span>
          <em>距离</em>
        </div>
        <div class="card">
          <b>{{ fmtDuration(w.duration_sec) }}</b>
          <em>时长</em>
        </div>
        <div class="card">
          <b>{{ ride ? fmtSpeed(avgSpeed) : fmtPace(w.avg_pace_sec_km) }}</b>
          <em>{{ ride ? '平均速度 km/h' : '平均配速' }}</em>
        </div>
        <div class="card">
          <b>{{ Math.round(w.calories || 0) }}</b><span>kcal</span>
          <em>消耗</em>
        </div>
      </div>

      <!-- 名称 + 时间 -->
      <div class="meta">
        <span class="meta-sport">{{ sportIcon(w.sport_type) }} {{ w.sport_type }}</span>
        <span class="meta-when">{{ fmtWhen(w.start_at) }}</span>
      </div>

      <!-- 与上次同类型对比（太短的距离不比，避免“快 112″/km”这种没意义的数字） -->
      <section v-if="cmp && (w.distance_m || 0) >= 500 && (cmp.distance_m || 0) >= 500" class="block compare">
        <div class="cmp-head">
          <span>对比上次</span>
          <span class="cmp-date">{{ fmtWhen(cmp.start_at) }}</span>
        </div>
        <div class="cmp-row">
          <div><span>距离</span><b>{{ cmpDistance }}</b></div>
          <div><span>用时</span><b>{{ cmpDuration }}</b></div>
          <div v-if="cmpPace"><span>{{ ride ? '均速' : '配速' }}</span><b>{{ cmpPace }}</b></div>
        </div>
      </section>

      <!-- 次要数据 -->
      <div class="grid">
        <div v-if="!ride && w.best_pace_sec_km" class="grid-item">
          <b>{{ fmtPace(w.best_pace_sec_km) }}</b>
          <span>最快公里</span>
        </div>
        <div class="grid-item">
          <b>{{ fmtSpeed(speedKmh) }}</b>
          <span>平均时速 km/h</span>
        </div>
        <div class="grid-item">
          <b>{{ intensityLabel }}</b>
          <span>运动强度</span>
        </div>
        <div v-if="w.avg_hr" class="grid-item">
          <b>{{ w.avg_hr }}</b>
          <span>平均心率 bpm</span>
        </div>
        <div v-if="!w.avg_hr" class="grid-item">
          <b>{{ stats?.point_count || 0 }}</b>
          <span>轨迹点</span>
        </div>
      </div>

      <!-- 分段 -->
      <section v-if="splits.length" class="block">
        <h3>分段{{ ride ? '速度' : '配速' }} <span>每段 1 公里</span></h3>
        <div v-for="(s, i) in splits" :key="i" class="split">
          <span class="split-km">{{ s.partial ? ((w.distance_m || 0) / 1000).toFixed(1) : s.km }}</span>
          <div class="split-track">
            <div
              class="split-bar"
              :style="{ width: splitWidth(s.pace_sec_km) + '%', background: paceColor(s.pace_sec_km, paceRange.min, paceRange.max) }"
            />
          </div>
          <span class="split-pace">{{ splitText(s.pace_sec_km) }}</span>
        </div>
      </section>

      <!-- 消耗明细（复用现有个性化模型的可解释输出） -->
      <section v-if="w.burn_detail?.steps?.length" class="block">
        <h3 @click="showBurn = !showBurn">
          消耗怎么算的
          <span class="toggle">{{ showBurn ? '收起' : '展开' }}</span>
        </h3>
        <div v-if="showBurn" class="burn">
          <p v-for="(s, i) in w.burn_detail.steps" :key="i" class="burn-step">{{ s }}</p>
          <p class="burn-range">
            估算区间 {{ w.burn_detail.low }} ~ {{ w.burn_detail.high }} kcal
            <span v-if="w.burn_detail.source">· {{ w.burn_detail.source }}</span>
          </p>
        </div>
      </section>

      <!-- 数据来源 -->
      <p class="source">
        数据来源：{{ w.source === 'gps' ? '手机定位' : w.source === 'band' ? '手环导入' : w.source === 'course' ? '跟练课程' : '手动补录' }}
        <span v-if="w.has_track"> · {{ w.track.length }} 个轨迹点</span>
        <span v-if="stats?.avg_accuracy"> · 平均精度 ±{{ stats.avg_accuracy }}m</span>
      </p>
    </template>
  </div>
</template>

<style scoped>
.detail {
  max-width: 560px;
  margin: 0 auto;
  padding-bottom: 12px;
}

.head {
  display: flex;
  align-items: center;
  height: 44px;
  gap: 8px;
}
.head-back {
  width: 32px; height: 32px;
  border: none; background: transparent;
  font-size: 24px; line-height: 1; color: #303133;
  padding-bottom: 4px;
}
.head-title { flex: 1; font-size: 16px; font-weight: 600; }
.head-act { border: none; background: transparent; color: #409eff; font-size: 13px; }
.head-del { border: none; background: transparent; color: #f56c6c; font-size: 13px; }

.loading { padding: 60px 0; text-align: center; color: #909399; font-size: 14px; }

.no-track {
  background: #fff;
  border-radius: 14px;
  padding: 28px 16px;
  text-align: center;
  color: #909399;
}
.no-track span { font-size: 34px; }
.no-track p { margin: 8px 0 0; font-size: 13px; }

.cards {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 8px;
  margin-top: 12px;
}
.card {
  background: #fff;
  border-radius: 12px;
  padding: 12px 4px 10px;
  text-align: center;
  display: flex;
  flex-direction: column;
  align-items: center;
}
.card b { font-size: 19px; font-weight: 700; line-height: 1.1; }
.card span { font-size: 10px; color: #909399; margin-left: 2px; }
.card em { font-style: normal; font-size: 10px; color: #a8abb2; margin-top: 4px; }

.meta { display: flex; align-items: baseline; justify-content: space-between; margin: 12px 2px 0; }
.meta-sport { font-size: 14px; font-weight: 600; }
.meta-when { font-size: 12px; color: #909399; }

/* 对比上次 */
.compare { background: linear-gradient(135deg, #f0f9eb, #f7fbf3); }
.cmp-head { display: flex; justify-content: space-between; align-items: baseline; margin-bottom: 10px; }
.cmp-head span:first-child { font-size: 14px; font-weight: 600; color: #303133; }
.cmp-date { font-size: 11px; color: #909399; }
.cmp-row { display: flex; gap: 8px; }
.cmp-row div {
  flex: 1; display: flex; flex-direction: column; gap: 3px;
  padding: 8px 10px; background: rgba(255, 255, 255, 0.75); border-radius: 10px;
}
.cmp-row span { font-size: 11px; color: #909399; }
.cmp-row b { font-size: 14px; color: #67c23a; }

.grid {
  display: grid;
  grid-template-columns: repeat(2, 1fr);
  gap: 8px;
  margin-top: 10px;
}
.grid-item {
  background: #fff;
  border-radius: 12px;
  padding: 12px 14px;
  display: flex;
  align-items: baseline;
  justify-content: space-between;
}
.grid-item b { font-size: 16px; font-weight: 600; }
.grid-item span { font-size: 11px; color: #909399; }

.block {
  background: #fff;
  border-radius: 14px;
  padding: 14px;
  margin-top: 12px;
}
.block h3 { margin: 0 0 10px; font-size: 14px; display: flex; align-items: baseline; justify-content: space-between; }
.block h3 span { font-size: 11px; color: #a8abb2; font-weight: 400; }
.toggle { color: #67c23a !important; }

.split { display: flex; align-items: center; gap: 10px; padding: 5px 0; }
.split-km { width: 34px; font-size: 12px; color: #606266; text-align: right; }
.split-track { flex: 1; height: 14px; background: #f4f6f8; border-radius: 7px; overflow: hidden; }
.split-bar { height: 100%; border-radius: 7px; transition: width 0.3s; }
.split-pace { width: 64px; font-size: 12px; color: #303133; text-align: right; font-variant-numeric: tabular-nums; }

.burn-step { margin: 0 0 6px; font-size: 12px; color: #606266; line-height: 1.6; }
.burn-range { margin: 8px 0 0; font-size: 12px; color: #e6a23c; }

.source { margin: 12px 2px 0; font-size: 11px; color: #a8abb2; text-align: center; }
</style>
