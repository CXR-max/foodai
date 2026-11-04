<script setup lang="ts">
// 健康打卡页：周时间轴 + 单日任务列表 + 本月热力格 + 手动运动打卡
import { computed, onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import * as checkinApi from '@/api/checkin'
import type { BurnPreview, CheckInItem, DayStat } from '@/api/checkin'
import { currentMonth, todayStr } from '@/utils/date'
import { useAuthStore } from '@/stores/auth'
import { refreshTodayCheckin } from '@/stores/todayCheckin'

const auth = useAuthStore()
const router = useRouter()
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
  const r = await checkinApi.manual({
    check_type: 'exercise',
    check_date: selectedDateStr.value,
    exercise_type: exForm.value.exercise_type,
    duration_min: exForm.value.duration_min,
    intensity: exForm.value.intensity,
    calories_burned: Math.round(editableBurn.value * 10) / 10,
  })
  burnDialog.value = false
  const burnMsg = manualAdjusted.value
    ? `消耗已按手动微调值 ${editableBurn.value} kcal 写入`
    : `按个性化模型计算消耗 ${editableBurn.value} kcal`
  if (r.points_awarded > 0) {
    ElMessage.success(`运动打卡成功 +${r.points_awarded} 分！${burnMsg}`)
  } else {
    ElMessage.info(`已记录本次打卡（运动积分每日首次 +2，今天已领过）。${burnMsg}`)
  }
  showPraise(r.ai_praise)
  await reloadAll()
}

const statByDate = computed(() => {
  const map: Record<string, DayStat> = {}
  for (const d of monthDays.value) map[d.date] = d
  return map
})

// 统一归一化为 YYYY-MM-DD
function toDateStr(v: string | Date): string {
  return typeof v === 'string' ? v : formatDate(v)
}

const selectedDateStr = computed(() => toDateStr(selectedDate.value as string | Date))

// 防刷分：只能打卡今天；漏打的日子走商城补签卡
const isToday = computed(() => selectedDateStr.value === todayStr())

// ---- AI 教练点评（持久卡片）----
const aiPraise = ref<string | null>(localStorage.getItem(`ai_praise_${todayStr()}`))

function showPraise(text: string | null | undefined) {
  if (!text) return
  aiPraise.value = text
  localStorage.setItem(`ai_praise_${todayStr()}`, text)
}

// ---- 周条 / 月热力格 ----
const WEEK_LABEL = ['一', '二', '三', '四', '五', '六', '日']
const WEEK_FULL = ['周一', '周二', '周三', '周四', '周五', '周六', '周日']

function parseDate(s: string) {
  return new Date(`${s}T00:00:00`)
}

// 选中日期所在周的 7 天（周一开头）
const weekDays = computed(() => {
  const base = parseDate(selectedDateStr.value)
  const offset = (base.getDay() + 6) % 7
  const monday = new Date(base)
  monday.setDate(base.getDate() - offset)
  return Array.from({ length: 7 }, (_, i) => {
    const d = new Date(monday)
    d.setDate(monday.getDate() + i)
    const key = formatDate(d)
    const stat = statByDate.value[key]
    return {
      date: key,
      w: WEEK_LABEL[i],
      d: String(d.getDate()).padStart(2, '0'),
      total: stat?.total || 0,
      done: stat?.done || 0,
    }
  })
})

const weekRange = computed(() => {
  const first = weekDays.value[0]
  const last = weekDays.value[6]
  return `${first.date.slice(5).replace('-', '.')} — ${last.date.slice(5).replace('-', '.')}`
})

const selectedWeekLabel = computed(() => {
  const d = parseDate(selectedDateStr.value)
  return `${d.getMonth() + 1} 月 ${d.getDate()} 日 · ${WEEK_FULL[(d.getDay() + 6) % 7]}`
})

// 本月网格：按自然月生成全部日期，再用打卡数据着色（接口只返回有任务的日期）
const monthGrid = computed(() => {
  const [y, m] = month.value.split('-').map(Number)
  if (!y || !m) return [] as ({ date: string; day: number; level: number } | null)[]
  const total = new Date(y, m, 0).getDate()
  const first = new Date(y, m - 1, 1)
  const pad = (first.getDay() + 6) % 7
  const cells: ({ date: string; day: number; level: number } | null)[] = Array(pad).fill(null)
  for (let d = 1; d <= total; d++) {
    const date = `${y}-${String(m).padStart(2, '0')}-${String(d).padStart(2, '0')}`
    const stat = statByDate.value[date]
    const level = !stat || stat.total === 0 ? 0 : stat.done === stat.total ? 3 : stat.done > 0 ? 2 : 1
    cells.push({ date, day: d, level })
  }
  return cells
})

const monthDoneDays = computed(() => monthDays.value.filter((d) => d.total > 0 && d.done === d.total).length)
const monthTrackedDays = computed(() => monthDays.value.filter((d) => d.total > 0).length)

const dayPct = computed(() => {
  if (!daySummary.value.total) return 0
  return Math.round((daySummary.value.done / daySummary.value.total) * 100)
})

function shiftWeek(n: number) {
  const d = parseDate(selectedDateStr.value)
  d.setDate(d.getDate() + n * 7)
  selectedDate.value = formatDate(d)
}

function goToday() {
  selectedDate.value = todayStr()
  if (month.value !== todayStr().slice(0, 7)) {
    month.value = todayStr().slice(0, 7)
    loadMonth().catch(() => {})
  }
}

// 切日期时同步右侧任务与月数据
watch(selectedDate, (v) => {
  const d = toDateStr(v as unknown as string | Date)
  if (typeof v !== 'string') {
    selectedDate.value = d
    return
  }
  const m = d.slice(0, 7)
  if (m !== month.value) {
    month.value = m
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
  await refreshTodayCheckin()
  auth.fetchMe().catch(() => {})
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
  showPraise(r.ai_praise)
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
    <!-- 周导航 -->
    <div class="wk-head">
      <div class="wk-nav">
        <button class="wk-arrow" title="上一周" @click="shiftWeek(-1)">‹</button>
        <span class="wk-range mono">{{ weekRange }}</span>
        <button class="wk-arrow" title="下一周" @click="shiftWeek(1)">›</button>
      </div>
      <div class="wk-tools">
        <span class="wk-month mono">{{ month }}</span>
        <button class="btn-ghost" @click="goToday">回到今天</button>
        <button class="btn-ghost" @click="router.push('/mall')">补签卡</button>
      </div>
    </div>

    <div class="week">
      <button
        v-for="d in weekDays" :key="d.date"
        class="day" :class="{ marked: d.done > 0, on: d.date === selectedDateStr, full: d.total > 0 && d.done === d.total }"
        @click="selectedDate = d.date"
      >
        <span class="day-w mono">{{ d.w }}</span>
        <span class="day-d mono">{{ d.d }}</span>
        <span class="day-mark"></span>
      </button>
    </div>

    <div class="two-col">
      <!-- 左：当日时间轴 -->
      <div>
        <div class="sec" style="margin-top:0">
          <span class="sec-no">TODAY</span>
          <span class="sec-title">{{ selectedWeekLabel }}</span>
          <span class="sec-line"></span>
          <span class="sec-act mono">{{ daySummary.done }} / {{ daySummary.total }}</span>
        </div>

        <div v-if="daySummary.all_done && daySummary.total" class="all-done">
          <span class="dot-status"></span>
          当日任务全部完成，保持这个节奏。
        </div>
        <div v-if="!isToday" class="past-note">
          仅当天可打卡：历史漏打的日子可在「积分商城」兑换补签卡。
        </div>

        <div v-if="!dayItems.length" class="empty-line">
          <span class="empty-k mono">NO TASKS</span>
          <span class="empty-t">这一天还没有打卡任务。先去「养生方案」把餐单导入日历，任务会出现在这里。</span>
          <button class="btn-ghost" @click="router.push('/plans')">去导入方案</button>
        </div>

        <div v-else class="tl">
          <div v-for="(item, i) in dayItems" :key="item.id" class="tl-item" :class="item.status">
            <span class="tl-time mono">{{ String(i + 1).padStart(2, '0') }}</span>
            <span class="tl-node"></span>
            <div class="tl-card">
              <div class="tl-body">
                <div class="tl-h">
                  <span class="type" :class="item.check_type">{{ item.check_type === 'diet' ? '饮食' : '运动' }}</span>
                  {{ item.label }}
                </div>
                <div v-if="item.status === 'pending' && item.detail" class="tl-s">
                  <template v-if="item.detail.items">
                    <span v-for="i2 in item.detail.items" :key="i2.name" class="dish">{{ i2.name }} 约{{ i2.portion_g }}g</span>
                    <span class="tl-cal mono">约 {{ item.detail.calories }} kcal</span>
                  </template>
                  <template v-else>
                    {{ item.detail.type }} {{ item.detail.duration_min }} 分钟（约消耗 {{ item.detail.calories_burned }} kcal）
                  </template>
                </div>
                <div v-else-if="item.status === 'done' && item.exercise_type" class="tl-s">
                  {{ item.exercise_type }} · {{ item.duration_min }} 分钟 · 消耗 {{ item.calories_burned }} kcal
                </div>
                <div v-else-if="item.status === 'done'" class="tl-s">已完成打卡</div>
              </div>
              <div class="tl-r">
                <span v-if="item.status === 'done'" class="pts mono">+{{ item.points_awarded }}</span>
                <button v-if="item.status === 'pending'" class="btn-ghost" :disabled="!isToday" @click="complete(item)">
                  打卡 +3
                </button>
                <span v-else class="tag-done mono">DONE</span>
              </div>
            </div>
          </div>
        </div>

        <!-- 手动运动打卡 -->
        <div class="sec">
          <span class="sec-no">EXTRA</span>
          <span class="sec-title">手动运动打卡</span>
          <span class="sec-line"></span>
          <span class="sec-act mono">每日首次 +2</span>
        </div>

        <div class="ex-card">
          <p class="ex-tip">
            按个性化模型计算消耗：MET × 体重 × 时长，再结合你的性别 / 年龄 / 体脂 / 体能修正。
          </p>
          <div class="ex-form">
            <el-select v-model="exForm.exercise_type" style="width: 120px">
              <el-option v-for="t in EX_TYPES" :key="t" :value="t" :label="t" />
            </el-select>
            <el-input-number v-model="exForm.duration_min" :min="5" :max="300" />
            <span class="ex-unit">分钟</span>
            <button class="btn-ghost solid" :disabled="!isToday" @click="manualCheckin">打卡</button>
          </div>
          <div class="intensity-row">
            <span class="intensity-label">强度</span>
            <el-radio-group v-model="exForm.intensity">
              <el-radio-button v-for="o in INTENSITY_OPTIONS" :key="o.value" :value="o.value">
                {{ o.label }}
              </el-radio-button>
            </el-radio-group>
            <span class="intensity-desc">{{ INTENSITY_OPTIONS.find((o) => o.value === exForm.intensity)?.desc }}</span>
          </div>
        </div>
      </div>

      <!-- 右：进度 / 热力格 / AI -->
      <aside>
        <div class="side-panel">
          <div class="sp-k mono">TODAY PROGRESS</div>
          <div class="sp-big mono">{{ daySummary.done }}<small>/ {{ daySummary.total }} 项</small></div>
          <div class="bar"><i :style="{ width: dayPct + '%' }"></i></div>
          <div class="sp-meta mono">{{ dayPct }}% · 本月达标 {{ monthDoneDays }} 天 / 已排 {{ monthTrackedDays }} 天</div>

          <div class="month-grid">
            <button
              v-for="(c, i) in monthGrid" :key="i"
              class="cell" :class="[c ? 'lv' + c.level : 'pad', { sel: c && c.date === selectedDateStr }]"
              :disabled="!c" :title="c ? c.date : ''"
              @click="c && (selectedDate = c.date)"
            >
              <span v-if="c" class="cell-d mono">{{ c.day }}</span>
            </button>
          </div>
          <div class="legend mono">
            <span>LESS</span>
            <i class="lg lv0"></i><i class="lg lv1"></i><i class="lg lv2"></i><i class="lg lv3"></i>
            <span>MORE</span>
          </div>
        </div>

        <div v-if="aiPraise" class="side-panel ai">
          <div class="sp-k mono">AI COACH · 今日点评</div>
          <p class="ai-text">“{{ aiPraise }}”</p>
          <div class="ai-foot mono">根据今天的打卡数据生成</div>
        </div>

        <div class="side-panel tip-panel">
          <div class="sp-k mono">HOW IT WORKS</div>
          <p class="side-text">饮食打卡来自方案餐单或食物识别；运动打卡按 MET 模型估算消耗。每日全部完成可拿满积分。</p>
        </div>
      </aside>
    </div>

    <!-- 运动消耗确认弹窗 -->
    <el-dialog v-model="burnDialog" title="运动消耗预估（个性化）" width="480px">
      <div v-loading="burnLoading" style="min-height: 160px">
        <template v-if="burnInfo">
          <div class="dlg-row">
            <span class="dlg-label">强度</span>
            <el-radio-group v-model="exForm.intensity" size="small">
              <el-radio-button v-for="o in INTENSITY_OPTIONS" :key="o.value" :value="o.value">{{ o.label }}</el-radio-button>
            </el-radio-group>
          </div>

          <el-descriptions :column="2" border size="small" style="margin-top: 12px">
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

          <div class="steps">
            <div v-for="(s, i) in burnInfo.steps" :key="i" class="step" :class="{ final: i === burnInfo.steps.length - 1 }">
              <span>{{ s }}</span>
            </div>
          </div>

          <div class="result-row">
            <span>预估消耗</span>
            <b class="mono">≈ {{ burnInfo.calories_burned }} kcal</b>
            <span class="range mono">区间 {{ burnInfo.low }} ~ {{ burnInfo.high }}</span>
          </div>

          <div class="adjust-row">
            <p class="adjust-label">
              感觉不准？手动微调（
              <span v-if="manualAdjusted" class="adj-on">已调整</span>
              <span v-else class="muted">未调整</span>）
            </p>
            <el-input-number v-model="editableBurn" :min="0" :max="3000" :step="10" @change="adjustBurn" />
            <span class="muted" style="margin-left: 6px">kcal</span>
          </div>
        </template>
      </div>
      <template #footer>
        <el-button @click="burnDialog = false">取消</el-button>
        <el-button type="primary" :disabled="burnLoading" @click="confirmCheckin">确认打卡</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
/* ---------- 周条 ---------- */
.wk-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 14px;
  gap: 16px;
  flex-wrap: wrap;
}
.wk-nav { display: flex; align-items: center; gap: 10px; }
.wk-arrow {
  width: 28px;
  height: 28px;
  border-radius: 50%;
  border: 1px solid var(--line-2);
  background: var(--surface);
  color: var(--ink-2);
  cursor: pointer;
  font-size: 15px;
  line-height: 1;
  display: grid;
  place-items: center;
  transition: background 0.18s, color 0.18s;
}
.wk-arrow:hover { background: var(--ink); color: var(--paper); border-color: var(--ink); }
.wk-range { font-size: 12px; letter-spacing: 0.08em; color: var(--ink-2); }
.wk-tools { display: flex; align-items: center; gap: 10px; }
.wk-month { font-size: 10px; letter-spacing: 0.14em; color: var(--muted); margin-right: 4px; }

.week { display: grid; grid-template-columns: repeat(7, 1fr); gap: 8px; margin-bottom: 26px; }
.day {
  border: 1px solid var(--line);
  background: var(--surface);
  border-radius: 4px;
  padding: 10px 8px 8px;
  cursor: pointer;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 2px;
  transition: border-color 0.18s, background 0.18s;
  font-family: var(--sans);
}
.day:hover { border-color: var(--line-2); }
.day-w { font-size: 10px; letter-spacing: 0.12em; color: var(--muted); }
.day-d { font-size: 17px; font-weight: 600; color: var(--ink); line-height: 1.3; }
.day-mark { width: 4px; height: 4px; border-radius: 50%; background: transparent; margin-top: 4px; }
.day.marked .day-mark { background: var(--line-2); }
.day.full .day-mark { background: var(--accent); }
.day.on { background: var(--accent); border-color: var(--accent); }
.day.on .day-w { color: #bfe5df; }
.day.on .day-d { color: #fff; }
.day.on .day-mark { background: var(--lime); }

/* ---------- 两栏 ---------- */
.two-col {
  display: grid;
  grid-template-columns: minmax(0, 1.6fr) minmax(0, 1fr);
  gap: 34px;
  align-items: start;
}

.all-done {
  display: flex;
  align-items: center;
  gap: 8px;
  border: 1px solid #cce3d5;
  background: var(--sage-soft);
  color: var(--sage-deep);
  font-size: 12.5px;
  padding: 9px 13px;
  border-radius: 4px;
  margin-bottom: 14px;
}
.past-note {
  border: 1px solid #ead9c4;
  background: var(--ochre-soft);
  color: var(--ochre-deep);
  font-size: 12.5px;
  padding: 9px 13px;
  border-radius: 4px;
  margin-bottom: 14px;
}

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

/* ---------- 时间轴 ---------- */
.tl { position: relative; padding-left: 70px; }
.tl::before {
  content: '';
  position: absolute;
  left: 56px;
  top: 10px;
  bottom: 10px;
  width: 1px;
  background: var(--line-2);
}
.tl-item { position: relative; padding-bottom: 12px; }
.tl-time {
  position: absolute;
  left: -70px;
  top: 15px;
  width: 46px;
  text-align: right;
  font-size: 10.5px;
  color: var(--muted);
  letter-spacing: 0.06em;
}
.tl-node {
  position: absolute;
  left: -18px;
  top: 19px;
  width: 9px;
  height: 9px;
  border-radius: 50%;
  background: var(--paper);
  border: 1.5px solid var(--line-2);
  z-index: 1;
}
.tl-item.done .tl-node { background: var(--accent); border-color: var(--accent); }
.tl-item.pending .tl-node { background: var(--lime); border-color: var(--sage); box-shadow: 0 0 0 4px rgba(47, 122, 69, 0.16); }

.tl-card {
  border: 1px solid var(--line);
  background: var(--surface);
  border-radius: 4px;
  padding: 13px 16px;
  display: flex;
  justify-content: space-between;
  gap: 14px;
  align-items: flex-start;
}
.tl-item.done .tl-card { background: transparent; border-style: dashed; }
.tl-body { min-width: 0; }
.tl-h { font-size: 13.5px; font-weight: 600; color: var(--ink); display: flex; align-items: center; gap: 8px; }
.type {
  font-size: 10px;
  letter-spacing: 0.06em;
  padding: 1px 7px;
  border-radius: 999px;
  border: 1px solid #ecd2d0;
  background: var(--clay-soft);
  color: var(--clay-deep);
  font-weight: 500;
  flex: 0 0 auto;
}
.type.exercise { border-color: #cce3d5; background: var(--sage-soft); color: var(--sage-deep); }
.tl-s { font-size: 11.5px; color: var(--muted); margin-top: 6px; line-height: 1.7; }
.dish {
  border: 1px solid var(--line);
  border-radius: 999px;
  padding: 1px 8px;
  margin-right: 5px;
  background: var(--paper);
  font-size: 10.5px;
  color: var(--ink-2);
  white-space: nowrap;
  display: inline-block;
}
.tl-cal { margin-left: 6px; font-size: 10.5px; color: var(--clay); }
.tl-r { display: flex; align-items: center; gap: 10px; flex: 0 0 auto; }
.pts { font-size: 12px; color: var(--clay); font-weight: 600; }
.tag-done { font-size: 9.5px; letter-spacing: 0.14em; color: var(--muted); }

/* ---------- 手动运动 ---------- */
.ex-card {
  border: 1px solid var(--line);
  background: var(--surface);
  border-radius: 4px;
  padding: 16px 18px;
}
.ex-tip { margin: 0 0 12px; font-size: 11.5px; color: var(--muted); line-height: 1.7; }
.ex-form { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
.ex-unit { font-size: 12px; color: var(--muted); }
.btn-ghost.solid { background: var(--accent); color: #fff; border-color: var(--accent); }
.btn-ghost.solid:hover { background: var(--accent-deep); border-color: var(--accent-deep); }
.intensity-row { display: flex; align-items: center; gap: 10px; margin-top: 12px; flex-wrap: wrap; }
.intensity-label { font-size: 12px; color: var(--muted); }
.intensity-desc { font-size: 11px; color: var(--muted); }

/* ---------- 右栏 ---------- */
.side-panel {
  border: 1px solid var(--line);
  background: var(--surface);
  border-radius: 4px;
  padding: 18px;
  margin-bottom: 14px;
}
.sp-k { font-size: 9.5px; letter-spacing: 0.16em; color: var(--muted); text-transform: uppercase; }
.sp-big { font-size: 40px; font-weight: 600; letter-spacing: -0.03em; line-height: 1.1; margin: 8px 0 10px; }
.sp-big small { font-family: var(--sans); font-size: 12px; color: var(--muted); font-weight: 400; margin-left: 6px; letter-spacing: 0; }
.bar { height: 5px; background: var(--paper-2); border-radius: 999px; overflow: hidden; }
.bar i {
  display: block;
  height: 100%;
  background: linear-gradient(90deg, var(--accent), var(--teal));
  border-radius: 999px;
  transition: width 0.4s;
}
.sp-meta { margin-top: 10px; font-size: 10px; letter-spacing: 0.04em; color: var(--muted); }

.month-grid { display: grid; grid-template-columns: repeat(7, 1fr); gap: 5px; margin-top: 18px; }
.cell {
  aspect-ratio: 1;
  border: 0;
  border-radius: 2px;
  background: var(--paper-2);
  padding: 0;
  cursor: pointer;
  display: grid;
  place-items: center;
  transition: outline-color 0.15s;
}
.cell.pad { background: transparent; cursor: default; }
.cell .cell-d { font-size: 8.5px; color: rgba(23, 26, 28, 0.45); }
.cell.lv1 { background: #cde9e4; }
.cell.lv2 { background: #7fc3ba; }
.cell.lv3 { background: var(--accent); }
.cell.lv3 .cell-d { color: #fff; }
.cell:hover:not(.pad) { outline: 1px solid var(--accent); outline-offset: 1px; }
.cell.sel { outline: 1.5px solid var(--accent); outline-offset: 1px; }
.legend { display: flex; align-items: center; gap: 5px; margin-top: 10px; font-size: 8.5px; letter-spacing: 0.1em; color: var(--muted); }
.lg { width: 9px; height: 9px; border-radius: 2px; display: inline-block; }
.lg.lv0 { background: var(--paper-2); }
.lg.lv1 { background: #cde9e4; }
.lg.lv2 { background: #7fc3ba; }
.lg.lv3 { background: var(--accent); }

/* AI 点评 */
.ai { border-left: 2px solid var(--accent); background: #f0f9f7; }
.ai .sp-k { color: var(--accent); }
.ai-text {
  font-family: var(--serif);
  font-size: 14px;
  line-height: 1.9;
  color: var(--ink);
  margin: 10px 0 10px;
}
.ai-foot { font-size: 9.5px; letter-spacing: 0.06em; color: var(--muted); }
.tip-panel .side-text { margin: 8px 0 0; font-size: 11.5px; line-height: 1.8; color: var(--ink-2); }

/* ---------- 弹窗 ---------- */
.dlg-row { display: flex; align-items: center; gap: 10px; }
.dlg-label { font-size: 12px; color: var(--muted); }
.unknown-mark {
  display: inline-block;
  width: 14px;
  height: 14px;
  line-height: 14px;
  text-align: center;
  border: 1px solid var(--ochre);
  color: var(--ochre);
  border-radius: 50%;
  font-size: 10px;
  cursor: help;
}
.steps { margin-top: 12px; border: 1px dashed var(--line-2); border-radius: 4px; padding: 8px 12px; background: var(--paper); }
.step { font-size: 11.5px; color: var(--ink-2); line-height: 1.9; }
.step.final { color: var(--ink); font-weight: 600; border-top: 1px dashed var(--line-2); margin-top: 4px; padding-top: 4px; }
.result-row { display: flex; align-items: baseline; gap: 10px; margin-top: 12px; font-size: 13px; color: var(--ink-2); }
.result-row b { color: var(--clay); font-size: 21px; }
.result-row .range { font-size: 10.5px; color: var(--muted); }
.adjust-row { margin-top: 14px; }
.adjust-label { font-size: 12px; color: var(--muted); margin: 0 0 6px; }
.adj-on { color: var(--ochre); }

@media (max-width: 1080px) {
  .two-col { grid-template-columns: 1fr; }
  .tl { padding-left: 46px; }
  .tl::before { left: 34px; }
  .tl-time { left: -46px; width: 28px; top: 16px; }
  .tl-node { left: -16px; }
  .greet { font-size: 30px; }
}
</style>
