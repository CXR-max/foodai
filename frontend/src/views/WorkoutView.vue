<script setup lang="ts">
// 运动首页（手机端）：本周数据 + 开始运动 + 运动记录
// 说明：只做手机端形态；桌面端菜单不加入口，不影响现有布局
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import * as workoutApi from '@/api/workout'
import type { WorkoutItem, WorkoutStats } from '@/api/workout'
import {
  fmtDistance, fmtDuration, fmtPace, fmtSpeed, fmtWhen, isRide, paceToSpeed, sportIcon,
} from '@/utils/workout'
import { refreshTodayCheckin } from '@/stores/todayCheckin'

const router = useRouter()

const stats = ref<WorkoutStats | null>(null)
const items = ref<WorkoutItem[]>([])
const total = ref(0)
const loading = ref(false)
const sport = ref('跑步')

// 只有这三种走 GPS 轨迹；跑步机/跳绳走手动补录
const GPS_TYPES = [
  { name: '跑步', icon: '🏃', desc: '记录轨迹与配速' },
  { name: '快走', icon: '🚶', desc: '轻量有氧' },
  { name: '骑行', icon: '🚴', desc: '记录速度与里程' },
]
const MANUAL_TYPES = [
  { name: '跑步机', icon: '🏃', desc: '室内跑' },
  { name: '跳绳', icon: '🤸', desc: '计数有氧' },
  { name: '瑜伽', icon: '🧘', desc: '跟练/自练' },
  { name: '拉伸', icon: '🤸', desc: '放松' },
]
const NO_DISTANCE = ['跳绳', '瑜伽', '拉伸']

const week = computed(() => stats.value?.week)
const dailyMax = computed(() => Math.max(...(stats.value?.daily || []).map((d) => d.distance_m), 1000))

function barHeight(m: number): string {
  const pct = Math.max(3, Math.round((m / dailyMax.value) * 100))
  return `${pct}%`
}

async function loadStats() {
  try {
    stats.value = await workoutApi.getWorkoutStats()
  } catch {
    /* 统计失败不影响列表 */
  }
}

async function loadList(reset = false) {
  loading.value = true
  try {
    const offset = reset ? 0 : items.value.length
    const r = await workoutApi.listWorkouts({ range: 'all', limit: 20, offset })
    total.value = r.total
    items.value = reset ? r.items : [...items.value, ...r.items]
  } finally {
    loading.value = false
  }
}

async function refresh() {
  await Promise.all([loadStats(), loadList(true)])
}

function goRun() {
  router.push({ path: '/workout/run', query: { sport: sport.value } })
}

function goDetail(id: number) {
  router.push(`/workout/${id}`)
}

function goDemo() {
  router.push({ path: '/workout/run', query: { sport: sport.value, demo: '1' } })
}

function newClientId(): string {
  const c = (globalThis as any).crypto
  if (c?.randomUUID) return c.randomUUID()
  return `m_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`
}

// ---- 手动补录 ----
const manualOpen = ref(false)
const manualType = ref('跑步机')
const manualMin = ref(30)
const manualKm = ref<number | null>(3)
const manualWhen = ref<Date>(new Date())
const manualClientId = ref('')
const saving = ref(false)

function openManual(type: string) {
  manualType.value = type
  manualMin.value = 30
  manualKm.value = NO_DISTANCE.includes(type) ? null : 3
  manualWhen.value = new Date()
  manualClientId.value = newClientId()
  manualOpen.value = true
}

async function saveManual() {
  if (saving.value) return
  saving.value = true
  try {
    const r = await workoutApi.createManualWorkout({
      sport_type: manualType.value,
      start_at: (manualWhen.value || new Date()).toISOString(),
      duration_sec: Math.max(60, Math.round(manualMin.value * 60)),
      distance_m: manualKm.value ? Math.round(manualKm.value * 1000) : null,
      client_id: manualClientId.value,
    })
    manualOpen.value = false
    ElMessage.success(r.message || '已记录')
    refreshTodayCheckin()
    await refresh()
  } finally {
    saving.value = false
  }
}

onMounted(refresh)
</script>

<template>
  <div class="wk">
    <!-- 本周概览 -->
    <section class="hero">
      <div class="hero-head">
        <span class="hero-title">本周运动</span>
        <span class="hero-sub">{{ stats?.streak_days || 0 }} 天连续打卡</span>
      </div>

      <div class="hero-main">
        <div class="hero-distance">
          <b>{{ fmtDistance(week?.distance_m).value }}</b>
          <span>{{ fmtDistance(week?.distance_m).unit }}</span>
        </div>
        <div class="hero-side">
          <div><b>{{ week?.count || 0 }}</b><span>次</span></div>
          <div><b>{{ fmtDuration(week?.duration_sec).replace(/^0:/, '') }}</b><span>时长</span></div>
          <div><b>{{ Math.round(week?.calories || 0) }}</b><span>kcal</span></div>
        </div>
      </div>

      <!-- 近 7 天里程条（纯 div，不引图表库） -->
      <div class="spark">
        <div v-for="d in stats?.daily || []" :key="d.date" class="spark-col">
          <div class="spark-bar" :style="{ height: barHeight(d.distance_m) }" />
          <span class="spark-label">{{ d.date.slice(8) }}</span>
        </div>
      </div>
    </section>

    <!-- 选运动 + 开始 -->
    <section class="start">
      <div class="type-grid">
        <button
          v-for="t in GPS_TYPES"
          :key="t.name"
          class="type"
          :class="{ active: sport === t.name }"
          @click="sport = t.name"
        >
          <span class="type-icon">{{ t.icon }}</span>
          <span class="type-name">{{ t.name }}</span>
          <span class="type-desc">{{ t.desc }}</span>
        </button>
      </div>

      <button class="go-btn" @click="goRun">
        <span class="go-icon">▶</span>
        开始{{ sport }}
      </button>

      <div class="manual-row">
        <span class="manual-label">没有轨迹？</span>
        <button v-for="t in MANUAL_TYPES" :key="t.name" class="manual-chip" @click="openManual(t.name)">
          {{ t.icon }} {{ t.name }}补录
        </button>
      </div>
      <button class="demo-link" @click="goDemo">没有 GPS / 在室内？看演示轨迹 →</button>
    </section>

    <!-- 运动记录 -->
    <section class="records">
      <div class="rec-head">
        <h3>运动记录</h3>
        <span class="rec-total">共 {{ total }} 次</span>
      </div>

      <div v-if="!items.length && !loading" class="empty">
        <div class="empty-icon">🏃</div>
        <p>还没有运动记录</p>
        <p class="empty-tip">选一个运动开始，跑完自动同步到打卡</p>
      </div>

      <div
        v-for="w in items"
        :key="w.id"
        class="rec"
        @click="goDetail(w.id)"
      >
        <div class="rec-icon">{{ sportIcon(w.sport_type) }}</div>
        <div class="rec-body">
          <div class="rec-line1">
            <b>{{ w.sport_type }}</b>
            <span class="rec-when">{{ fmtWhen(w.start_at) }}</span>
          </div>
          <div class="rec-line2">
            <span v-if="(w.distance_m || 0) > 0" class="rec-strong">
              {{ fmtDistance(w.distance_m).value }} {{ fmtDistance(w.distance_m).unit }}
            </span>
            <span>{{ fmtDuration(w.duration_sec) }}</span>
            <span v-if="w.avg_pace_sec_km" class="rec-pace">
              {{ isRide(w.sport_type)
                ? fmtSpeed(paceToSpeed(w.avg_pace_sec_km)) + ' km/h'
                : fmtPace(w.avg_pace_sec_km) + '/km' }}
            </span>
            <span v-if="w.source === 'manual'" class="rec-tag">补录</span>
            <span v-if="w.source === 'band'" class="rec-tag">手环</span>
          </div>
        </div>
        <div class="rec-right">
          <b>{{ Math.round(w.calories || 0) }}</b>
          <span>kcal</span>
        </div>
      </div>

      <button
        v-if="items.length < total"
        class="load-more"
        :disabled="loading"
        @click="loadList()"
      >{{ loading ? '加载中…' : '加载更多' }}</button>
    </section>

    <!-- 手动补录抽屉 -->
    <el-drawer v-model="manualOpen" direction="btt" size="340px" :with-header="false">
      <div class="drawer">
        <div class="drawer-handle" />
        <h3 class="drawer-title">{{ sportIcon(manualType) }} {{ manualType }}补录</h3>
        <p class="drawer-tip">没有轨迹的运动（室内/忘带手机）用这里记，消耗仍按你的个性化模型算</p>

        <div class="field">
          <label>时间</label>
          <div class="field-input">
            <el-date-picker
              v-model="manualWhen"
              type="datetime"
              format="MM-DD HH:mm"
              placeholder="选择时间"
              style="width: 170px"
            />
          </div>
        </div>

        <div class="field">
          <label>时长</label>
          <div class="field-input">
            <el-input-number v-model="manualMin" :min="1" :max="600" :step="5" />
            <span>分钟</span>
          </div>
        </div>

        <div v-if="!NO_DISTANCE.includes(manualType)" class="field">
          <label>距离（可不填）</label>
          <div class="field-input">
            <el-input-number v-model="manualKm" :min="0" :max="200" :step="0.5" :precision="2" />
            <span>公里</span>
          </div>
        </div>

        <div class="drawer-actions">
          <el-button size="large" @click="manualOpen = false">取消</el-button>
          <el-button size="large" type="success" :loading="saving" @click="saveManual">保存</el-button>
        </div>
      </div>
    </el-drawer>
  </div>
</template>

<style scoped>
.wk {
  max-width: 560px;
  margin: 0 auto;
  padding-bottom: 8px;
}

/* ---- 本周概览 ---- */
.hero {
  background: linear-gradient(135deg, #2f3a45 0%, #1d2530 100%);
  border-radius: 16px;
  padding: 16px 16px 12px;
  color: #fff;
  margin-bottom: 12px;
}
.hero-head { display: flex; justify-content: space-between; align-items: baseline; }
.hero-title { font-size: 14px; opacity: 0.85; }
.hero-sub { font-size: 12px; opacity: 0.65; }
.hero-main { display: flex; align-items: flex-end; gap: 16px; margin: 8px 0 4px; }
.hero-distance b { font-size: 40px; line-height: 1; font-weight: 700; }
.hero-distance span { font-size: 14px; margin-left: 4px; opacity: 0.75; }
.hero-side { display: flex; gap: 14px; margin-left: auto; padding-bottom: 4px; }
.hero-side div { display: flex; flex-direction: column; align-items: flex-end; }
.hero-side b { font-size: 16px; }
.hero-side span { font-size: 11px; opacity: 0.6; margin-top: 2px; }

.spark {
  display: flex;
  align-items: flex-end;
  gap: 6px;
  height: 46px;
  margin-top: 10px;
}
.spark-col { flex: 1; display: flex; flex-direction: column; align-items: center; height: 100%; justify-content: flex-end; }
.spark-bar {
  width: 100%;
  max-width: 18px;
  background: linear-gradient(180deg, #8ee06a, #67c23a);
  border-radius: 3px 3px 0 0;
  min-height: 2px;
  transition: height 0.3s;
}
.spark-label { font-size: 9px; opacity: 0.45; margin-top: 3px; }

/* ---- 选运动 ---- */
.start {
  background: #fff;
  border-radius: 16px;
  padding: 14px;
  margin-bottom: 12px;
}
.type-grid { display: grid; grid-template-columns: repeat(3, 1fr); gap: 8px; }
.type {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 2px;
  padding: 12px 4px 10px;
  border: 1.5px solid #ebeef5;
  border-radius: 12px;
  background: #fafbfc;
  cursor: pointer;
  transition: all 0.15s;
}
.type.active {
  border-color: #67c23a;
  background: #f0f9eb;
  box-shadow: 0 2px 8px rgba(103, 194, 58, 0.15);
}
.type-icon { font-size: 24px; line-height: 1.2; }
.type-name { font-size: 14px; font-weight: 600; color: #303133; }
.type-desc { font-size: 10px; color: #a8abb2; }

.go-btn {
  width: 100%;
  margin-top: 12px;
  height: 52px;
  border: none;
  border-radius: 26px;
  background: linear-gradient(135deg, #67c23a, #52a828);
  color: #fff;
  font-size: 17px;
  font-weight: 700;
  letter-spacing: 1px;
  box-shadow: 0 6px 16px rgba(103, 194, 58, 0.35);
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
}
.go-btn:active { transform: scale(0.985); }
.go-icon { font-size: 13px; }

.manual-row { display: flex; align-items: center; gap: 8px; margin-top: 10px; flex-wrap: wrap; }
.manual-label { font-size: 12px; color: #a8abb2; }
.manual-chip {
  border: 1px solid #e4e7ed;
  background: #fff;
  border-radius: 14px;
  padding: 5px 10px;
  font-size: 12px;
  color: #606266;
  cursor: pointer;
}
.demo-link {
  margin-top: 10px;
  border: none;
  background: transparent;
  color: #909399;
  font-size: 11px;
  padding: 0;
  cursor: pointer;
  text-decoration: underline;
  text-underline-offset: 3px;
}

/* ---- 记录 ---- */
.records { background: #fff; border-radius: 16px; padding: 14px; }
.rec-head { display: flex; align-items: baseline; justify-content: space-between; margin-bottom: 6px; }
.rec-head h3 { margin: 0; font-size: 15px; }
.rec-total { font-size: 12px; color: #a8abb2; }

.rec {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 12px 0;
  border-bottom: 1px solid #f4f6f8;
  cursor: pointer;
}
.rec:last-of-type { border-bottom: none; }
.rec-icon {
  width: 40px; height: 40px;
  border-radius: 12px;
  background: #f4f8ee;
  display: flex; align-items: center; justify-content: center;
  font-size: 20px;
  flex: 0 0 40px;
}
.rec-body { flex: 1; min-width: 0; }
.rec-line1 { display: flex; align-items: baseline; gap: 8px; }
.rec-line1 b { font-size: 14px; }
.rec-when { font-size: 11px; color: #a8abb2; }
.rec-line2 { display: flex; align-items: center; gap: 8px; margin-top: 3px; font-size: 12px; color: #909399; flex-wrap: wrap; }
.rec-strong { color: #303133; font-weight: 600; }
.rec-pace { color: #e6a23c; }
.rec-tag { font-size: 10px; color: #909399; border: 1px solid #e4e7ed; border-radius: 4px; padding: 0 3px; }
.rec-right { text-align: right; }
.rec-right b { font-size: 16px; color: #303133; }
.rec-right span { font-size: 10px; color: #a8abb2; margin-left: 2px; }

.empty { text-align: center; padding: 26px 0 22px; }
.empty-icon { font-size: 34px; opacity: 0.5; }
.empty p { margin: 6px 0 0; font-size: 14px; color: #606266; }
.empty-tip { font-size: 12px !important; color: #a8abb2 !important; }

.load-more {
  width: 100%;
  margin-top: 10px;
  height: 40px;
  border: 1px solid #e4e7ed;
  background: #fff;
  border-radius: 20px;
  color: #606266;
  font-size: 13px;
}

/* ---- 抽屉 ---- */
.drawer { padding: 6px 4px 4px; }
.drawer-handle { width: 34px; height: 4px; border-radius: 2px; background: #dcdfe6; margin: 0 auto 12px; }
.drawer-title { margin: 0 0 4px; font-size: 17px; }
.drawer-tip { margin: 0 0 14px; font-size: 12px; color: #909399; line-height: 1.5; }
.field { display: flex; align-items: center; justify-content: space-between; padding: 10px 0; border-bottom: 1px solid #f4f6f8; }
.field label { font-size: 14px; color: #303133; }
.field-input { display: flex; align-items: center; gap: 6px; font-size: 13px; color: #909399; }
.drawer-actions { display: flex; gap: 10px; margin-top: 16px; }
.drawer-actions .el-button { flex: 1; }
</style>
