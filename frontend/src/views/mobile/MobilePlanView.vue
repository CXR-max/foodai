<script setup lang="ts">
// 移动端「计划」：生成计划 + 我的计划（进行中 / 历史方案分开；历史可查看、沿用、再导入）
import { computed, onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import * as planApi from '@/api/plan'
import type { Plan, PlanReview } from '@/api/plan'
import PlansView from '@/views/PlansView.vue'
import { notifyReactivated } from '@/utils/planFeedback'
import MobilePlanDetail from './MobilePlanDetail.vue'

const router = useRouter()
const tab = ref<'mine' | 'generate'>('mine')
const plans = ref<Plan[]>([])
const loading = ref(false)
const detailId = ref<number | null>(null)
const busyId = ref<number | null>(null)
const activeReview = ref<PlanReview | null>(null)

const STATUS_LABEL: Record<string, string> = {
  active: '进行中', scheduled: '待开始', archived: '历史方案', draft: '草稿',
}

// 进行中 = 已到开始日、正在执行的那一份
const currentPlans = computed(() =>
  plans.value.filter((p) => p.status === 'active' && p.phase !== 'scheduled'))
// 待开始 = 提前排好的下一期，到点自动接上，期间不抢当前方案的位子（也不会把它归档）
const scheduledPlans = computed(() =>
  plans.value.filter((p) => p.status === 'active' && p.phase === 'scheduled'))
const historyPlans = computed(() => plans.value.filter((p) => p.status !== 'active'))

const now = new Date()
const todayStr = `${now.getFullYear()}-${`${now.getMonth() + 1}`.padStart(2, '0')}-${`${now.getDate()}`.padStart(2, '0')}`

/** 2026-11-16T17:03:21 → 11-16 17:03 */
function reactivatedLabel(iso?: string | null) {
  if (!iso) return ''
  const [, , m, d, hh, mm] = iso.match(/^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})/) || []
  return m ? `${m}-${d} ${hh}:${mm}` : iso
}

async function load() {
  loading.value = true
  try {
    plans.value = await planApi.getPlans()
    // 进行中的方案实时算一次执行进度（通常只有一份）
    const active = plans.value.find((p) => p.status === 'active' && p.phase !== 'scheduled')
    activeReview.value = active
      ? await planApi.getPlanReview(active.id).catch(() => null)
      : null
  } finally {
    loading.value = false
  }
}

function openDetail(plan: Plan) {
  detailId.value = plan.id
}

/** 历史方案重新导入：会置为进行中、日期顺延到今天，当前方案自动归档 */
async function reimport(plan: Plan) {
  try {
    await ElMessageBox.confirm(
      `「${plan.title}」会置为进行中并从今天重新排期，当前进行中的方案自动归档到历史方案。`,
      '导入日程', { confirmButtonText: '导入', cancelButtonText: '取消', type: 'warning' },
    )
  } catch {
    return
  }
  busyId.value = plan.id
  try {
    const r = await planApi.importPlanToSchedule(plan.id, { mode: 'week' })
    if (!notifyReactivated(r)) ElMessage.success(r.message)
    await load()
    router.push('/m/schedule')
  } finally {
    busyId.value = null
  }
}

async function reuse(plan: Plan) {
  busyId.value = plan.id
  try {
    const created = await planApi.reusePlan(plan.id, { mode: 'reframe' })
    ElMessage.success(`已排到 ${created.start_date} 起，共 ${created.days?.length || 0} 天`)
    await load()
    if (created.id) detailId.value = created.id
  } finally {
    busyId.value = null
  }
}

watch(tab, (value) => {
  if (value === 'mine' && detailId.value === null) load()
})

onMounted(load)
</script>

<template>
  <div class="m-plan">
    <!-- 详情页：整页替换列表，避免手机上层层叠叠 -->
    <MobilePlanDetail
      v-if="detailId !== null"
      :plan-id="detailId"
      @close="detailId = null; load()"
      @changed="load"
    />

    <template v-else>
      <div class="tabs">
        <button :class="['tab', { on: tab === 'mine' }]" @click="tab = 'mine'">我的计划</button>
        <button :class="['tab', { on: tab === 'generate' }]" @click="tab = 'generate'">生成计划</button>
      </div>

      <!-- 我的计划 -->
      <template v-if="tab === 'mine'">
        <div class="quick">
          <div class="quick-text">
            <b>定计划前，先拍一张</b>
            <span>看看这餐大概多少热量</span>
          </div>
          <el-button type="success" size="small" round @click="router.push('/recognition')">📷 识别热量</el-button>
        </div>

        <div v-if="loading" class="skeleton">加载中…</div>
        <el-empty
          v-else-if="!plans.length"
          description="还没有方案，去「生成计划」排一份"
          :image-size="72"
        >
          <el-button type="success" @click="tab = 'generate'">去生成</el-button>
        </el-empty>

        <template v-else>
          <!-- 进行中 -->
          <section v-if="currentPlans.length" class="group">
            <h3 class="group-title">进行中</h3>
            <div v-for="p in currentPlans" :key="p.id" class="plan-card active" @click="openDetail(p)">
              <div class="pc-head">
                <b>{{ p.title }}</b>
                <el-tag size="small" type="success" effect="dark" round>进行中</el-tag>
              </div>
              <div class="pc-range">
                {{ p.start_date }} → {{ p.end_date || '—' }} · 共 {{ p.days_count || 0 }} 天
                <span v-if="p.plan_version && p.plan_version > 1" class="reuse-flag">v{{ p.plan_version }}</span>
              </div>
              <div v-if="p.reactivated_at" class="reactivate-bar">
                <span class="rb-icon">↻</span>
                <span class="rb-text">已重新启用 · {{ reactivatedLabel(p.reactivated_at) }}</span>
                <em v-if="(p.reactivate_count || 0) > 1">第 {{ p.reactivate_count }} 次</em>
              </div>
              <div class="pc-stats">
                <div><span>日均</span><b>{{ p.avg_calories || '—' }}</b><em>kcal</em></div>
                <div><span>运动消耗</span><b>{{ p.total_burn || 0 }}</b><em>kcal</em></div>
                <div><span>热量上限</span><b>{{ p.targets?.calorie_max || '—' }}</b><em>kcal</em></div>
              </div>
              <div v-if="activeReview && activeReview.score != null" class="pc-review ongoing">
                <span class="pr-badge">执行中</span>
                <b>{{ activeReview.stats.tasks_done }}<em>/{{ activeReview.stats.tasks_total }} 项</em></b>
                <span class="pr-text">
                  连续 {{ activeReview.stats.longest_streak || 0 }} 天全清 · 停用后给最终评价
                </span>
              </div>
              <div class="pc-foot">
                <span class="pc-more">查看 / 改菜 / 导入日程 ›</span>
              </div>
            </div>
          </section>

          <!-- 待开始：提前排好的下一期，到开始日自动接上 -->
          <section v-if="scheduledPlans.length" class="group">
            <h3 class="group-title">待开始</h3>
            <div v-for="p in scheduledPlans" :key="p.id" class="plan-card scheduled" @click="openDetail(p)">
              <div class="pc-head">
                <b>{{ p.title }}</b>
                <el-tag size="small" type="warning" effect="plain" round>待开始</el-tag>
              </div>
              <div class="pc-range">
                {{ p.start_date }} 起 → {{ p.end_date || '—' }} · 共 {{ p.days_count || 0 }} 天
              </div>
              <p class="scheduled-tip">
                {{ p.start_date }} 到点自动接手，期间仍按「进行中」那份方案执行，不会被归档。
              </p>
              <div class="pc-foot">
                <span class="pc-more">查看 / 改菜 ›</span>
              </div>
            </div>
          </section>

          <!-- 历史方案 -->
          <section class="group">
            <h3 class="group-title">
              历史方案
              <em v-if="historyPlans.length">{{ historyPlans.length }} 份</em>
            </h3>
            <p v-if="!historyPlans.length" class="group-hint">用过的方案会留在这里，随时可以沿用或再导入。</p>
            <div v-for="p in historyPlans" :key="p.id" class="plan-card history">
              <div class="pc-head" @click="openDetail(p)">
                <b>{{ p.title }}</b>
                <el-tag size="small" type="info" effect="plain">{{ STATUS_LABEL[p.status] || p.status }}</el-tag>
              </div>
              <div class="pc-range" @click="openDetail(p)">
                {{ p.start_date }} → {{ p.end_date || '—' }} · {{ p.days_count || 0 }} 天
                <span v-if="p.reused_from" class="reuse-flag">沿用</span>
                <span v-if="p.reactivated_at" class="reactivate-flag">曾重新启用</span>
                <span v-if="p.start_date && p.end_date && p.end_date < todayStr" class="expired-flag">已过期</span>
              </div>
              <!-- 这一期做的怎么样：停用后自动生成 -->
              <div v-if="p.review_score != null" class="pc-review" @click="openDetail(p)">
                <span class="pr-grade">{{ p.review_grade }}</span>
                <b>{{ p.review_score }}</b><em>分</em>
                <span class="pr-text">{{ p.review_headline }}</span>
              </div>
              <div v-else-if="p.review_status === 'not_started'" class="pc-review muted" @click="openDetail(p)">
                这一期没有执行记录（未导入日程、也没打卡）
              </div>
              <div class="pc-actions">
                <el-button size="small" plain @click.stop="openDetail(p)">查看</el-button>
                <el-button size="small" plain :loading="busyId === p.id" @click.stop="reuse(p)">沿用</el-button>
                <el-button size="small" type="success" :loading="busyId === p.id" @click.stop="reimport(p)">导入日程</el-button>
              </div>
            </div>
          </section>
        </template>
      </template>

      <!-- 生成计划：复用原方案页的向导（只保留生成部分） -->
      <template v-else>
        <PlansView generation-only />
      </template>
    </template>
  </div>
</template>

<style scoped>
/* ============ 设计 token（与日程 / 运动 / 我的页同一套，仅本页作用域） ============ */
.m-plan {
  --paper: #f0f7de;
  --surface: #ffffff;
  --teal: #c2e07c;
  --teal-dk: #b4d46a;
  --teal-tx: #171a14;
  --mint: #e9f3cc;
  --mint-2: #d3e79f;
  --ink: #171a14;
  --ink-2: #3f4639;
  --muted: #6d7565;
  --line: #e6edd6;
  --amber: #b8791a;
  --red: #be4a3c;
  --r-lg: 22px;
  --r-md: 16px;
  --shadow: 0 10px 28px rgba(28, 36, 22, 0.07);
  --shadow-lime: 0 18px 38px rgba(122, 152, 40, 0.2);

  /* 吃掉 el-main 的 10px 内边距与底部 tabBar 预留 */
  margin: -10px 0 calc(-70px - env(safe-area-inset-bottom, 0px));
  padding: 10px 16px calc(72px + env(safe-area-inset-bottom, 0px));
  min-height: calc(100vh - 52px);
  background: var(--paper);
  box-shadow: 0 0 0 10px var(--paper);
  color: var(--ink);
  font-family: 'Manrope', -apple-system, BlinkMacSystemFont, 'PingFang SC', 'HarmonyOS Sans SC',
    'Microsoft YaHei', sans-serif;
  font-size: 14px;
  line-height: 1.55;
  -webkit-font-smoothing: antialiased;
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.m-plan *,
.m-plan *::before,
.m-plan *::after { box-sizing: border-box; }
.m-plan h3 { margin: 0; }

/* ============ 分段标签 ============ */
.tabs { display: flex; gap: 4px; padding: 4px; border-radius: 999px; background: var(--mint); }
.tab {
  flex: 1;
  border: none;
  background: transparent;
  border-radius: 999px;
  padding: 8px 0;
  font-size: 12.5px;
  font-weight: 700;
  color: var(--ink-2);
  cursor: pointer;
  transition: background 0.16s ease, color 0.16s ease;
}
.tab.on { background: var(--teal); color: var(--teal-tx); box-shadow: 0 6px 14px rgba(122, 152, 40, 0.18); }

/* ============ 定计划前先拍一张 ============ */
.quick {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  background: var(--mint);
  border: 1px solid rgba(255, 255, 255, 0.7);
  border-radius: var(--r-lg);
  padding: 13px 14px;
}
.quick-text { display: flex; flex-direction: column; min-width: 0; }
.quick-text b { font-size: 13px; font-weight: 800; color: var(--ink); }
.quick-text span { font-size: 11.5px; color: var(--muted); margin-top: 2px; }

/* ============ 分组 ============ */
.group { display: flex; flex-direction: column; gap: 9px; }
.group-title {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 12.5px;
  font-weight: 800;
  color: var(--ink);
  margin: 4px 2px 0;
}
.group-title em { font-style: normal; font-size: 10.5px; font-weight: 500; color: var(--muted); }
.group-hint { font-size: 11px; color: var(--muted); margin: 0 2px 4px; }

/* ============ 方案卡 ============ */
.plan-card {
  background: var(--surface);
  border: 1px solid var(--line);
  border-radius: 18px;
  padding: 14px;
  box-shadow: var(--shadow);
}
.plan-card.active { border-color: #cfe29b; }
.plan-card.scheduled { border: 1px dashed #d9e6bf; background: #fbfdf4; box-shadow: none; }
.plan-card.history { opacity: 1; }
.pc-head { display: flex; align-items: center; justify-content: space-between; gap: 8px; }
.pc-head b { font-size: 14px; font-weight: 800; color: var(--ink); letter-spacing: -0.01em; }
.pc-range { font-size: 11.5px; color: var(--muted); margin-top: 5px; }
.scheduled-tip { margin: 7px 0 0; font-size: 10.5px; line-height: 1.6; color: var(--muted); }

/* 小旗标 */
.reuse-flag {
  margin-left: 6px;
  font-size: 9.5px;
  font-weight: 800;
  color: var(--ink-2);
  background: var(--mint);
  border-radius: 6px;
  padding: 1px 6px;
}
.expired-flag {
  margin-left: 6px;
  font-size: 9.5px;
  font-weight: 800;
  color: var(--red);
  background: #fdeeec;
  border-radius: 6px;
  padding: 1px 6px;
}
.reactivate-flag {
  margin-left: 6px;
  font-size: 9.5px;
  font-weight: 800;
  color: var(--ink-2);
  background: var(--mint);
  border-radius: 6px;
  padding: 1px 6px;
}

/* 重新启用提示条 */
.reactivate-bar {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-top: 8px;
  background: var(--mint);
  border: 1px solid var(--line);
  color: var(--ink-2);
  border-radius: 12px;
  padding: 6px 9px;
  font-size: 11px;
  line-height: 1.4;
}
.reactivate-bar .rb-icon { font-size: 12px; color: var(--ink); }
.reactivate-bar .rb-text { font-weight: 700; }
.reactivate-bar em { margin-left: auto; font-style: normal; color: var(--muted); }

/* 三个数据格 */
.pc-stats { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 6px; margin-top: 10px; }
.pc-stats div {
  background: var(--mint);
  border-radius: 12px;
  padding: 8px 6px;
  display: flex;
  flex-direction: column;
  align-items: center;
}
.pc-stats span { font-size: 10px; color: var(--muted); }
.pc-stats b {
  font-size: 15px;
  font-weight: 800;
  color: var(--ink);
  margin-top: 2px;
  font-variant-numeric: tabular-nums;
}
.pc-stats em { font-style: normal; font-size: 9.5px; color: var(--muted); }

/* 执行进度 / 本期评价 */
.pc-review {
  display: flex;
  align-items: baseline;
  gap: 5px;
  margin-top: 10px;
  background: var(--mint);
  border: 1px solid transparent;
  border-radius: 12px;
  padding: 8px 10px;
}
.pc-review .pr-grade,
.pc-review .pr-badge {
  background: var(--teal-tx);
  color: var(--teal);
  font-size: 9.5px;
  font-weight: 800;
  line-height: 16px;
  padding: 0 6px;
  border-radius: 7px;
  align-self: center;
}
.pc-review b { font-size: 15px; font-weight: 800; color: var(--ink); font-variant-numeric: tabular-nums; }
.pc-review b em,
.pc-review em { font-style: normal; font-size: 9.5px; color: var(--muted); }
.pc-review .pr-text { font-size: 11px; color: var(--ink-2); line-height: 1.45; margin-left: 2px; }
.pc-review.muted { background: #f4f6ee; border-color: var(--line); color: var(--muted); font-size: 11px; }
.pc-review.ongoing { background: var(--mint); border-color: transparent; }
.pc-review.ongoing b { color: var(--ink); }
.pc-review.ongoing em,
.pc-review.ongoing .pr-text { color: var(--ink-2); }

.pc-foot { margin-top: 10px; text-align: right; }
.pc-more { font-size: 11.5px; font-weight: 700; color: var(--ink); }
.pc-actions { display: flex; gap: 6px; margin-top: 10px; }
.pc-actions :deep(.el-button) { flex: 1; margin-left: 0; }
.skeleton { text-align: center; font-size: 12.5px; color: var(--muted); padding: 24px 0; }

/* ============ Element Plus 本地换肤（只在本页作用域内） ============ */
.m-plan :deep(.el-button) {
  border-radius: 999px;
  font-weight: 700;
  border-color: var(--line);
  background: var(--surface);
  color: var(--ink-2);
}
.m-plan :deep(.el-button:hover) { border-color: var(--teal-dk); color: var(--ink); background: #fbfdf4; }
.m-plan :deep(.el-button--success) {
  background: var(--teal);
  border-color: var(--teal);
  color: var(--teal-tx);
}
.m-plan :deep(.el-button--success:hover) {
  background: var(--teal-dk);
  border-color: var(--teal-dk);
  color: var(--teal-tx);
}
/* 文字按钮 / 链接按钮不要把上面的白底带进来 */
.m-plan :deep(.el-button.is-text),
.m-plan :deep(.el-button.is-link),
.m-plan :deep(.el-button--primary.is-text),
.m-plan :deep(.el-button--danger.is-text) {
  background: transparent;
  border-color: transparent;
  box-shadow: none;
}
.m-plan :deep(.el-button--primary.is-text) { color: #5f7d22; }
.m-plan :deep(.el-button--danger.is-text) { color: var(--red); }
.m-plan :deep(.el-tag) {
  height: 20px;
  padding: 0 7px;
  border: none;
  border-radius: 7px;
  font-size: 10px;
  font-weight: 800;
}
.m-plan :deep(.el-tag--success) { background: var(--mint); color: var(--ink-2); }
.m-plan :deep(.el-tag--success.el-tag--dark) { background: var(--teal-tx); color: var(--teal); }
.m-plan :deep(.el-tag--warning) { background: #fbf4e7; color: var(--amber); }
.m-plan :deep(.el-tag--info) { background: #f4f6ee; color: var(--muted); }
.m-plan :deep(.el-empty) { padding: 18px 0; }
.m-plan :deep(.el-empty__description p) { font-size: 12.5px; color: var(--muted); }

/* ============ 「生成计划」tab：给共用向导换一层纸绿皮（只在本页祖先下生效，桌面 /plans 不受影响） ============ */
.m-plan :deep(.page) { color: var(--ink); }
.m-plan :deep(.page-card) {
  background: var(--surface);
  border: 1px solid var(--line);
  border-radius: 18px;
  box-shadow: var(--shadow);
  padding: 14px;
  margin-bottom: 10px;
}
.m-plan :deep(.page-title) { font-size: 14px; font-weight: 800; color: var(--ink); letter-spacing: -0.01em; }
.m-plan :deep(.sub) { font-size: 11px; color: var(--muted); line-height: 1.6; }
.m-plan :deep(.wizard-box) {
  background: var(--mint);
  border: 1px solid transparent;
  border-radius: 16px;
  padding: 12px 13px;
}
.m-plan :deep(.wizard-title) { font-size: 12px; font-weight: 800; color: var(--ink); margin-bottom: 8px; }
.m-plan :deep(.pref-label) { font-size: 11.5px; font-weight: 700; color: var(--ink-2); margin: 10px 0 4px; }
.m-plan :deep(.chat-list) { min-height: 160px; }
.m-plan :deep(.bubble) { font-size: 12.5px; border-radius: 14px; }
.m-plan :deep(.chat-row.user .bubble) { background: var(--teal); color: var(--teal-tx); }
.m-plan :deep(.chat-row.assistant .bubble) { background: var(--mint); color: var(--ink); }
.m-plan :deep(.chat-empty) { color: var(--muted); font-size: 12px; }

/* 勾选框 / 单选框：蓝 → 柠檬绿 */
.m-plan :deep(.el-checkbox__input.is-checked .el-checkbox__inner),
.m-plan :deep(.el-checkbox__input.is-indeterminate .el-checkbox__inner),
.m-plan :deep(.el-radio__input.is-checked .el-radio__inner) {
  background: var(--teal-dk);
  border-color: var(--teal-dk);
}
.m-plan :deep(.el-checkbox__input.is-checked .el-checkbox__inner::after) { border-color: var(--teal-tx); }
.m-plan :deep(.el-radio__input.is-checked .el-radio__inner::after) { background: var(--teal-tx); }
.m-plan :deep(.el-checkbox__input.is-checked + .el-checkbox__label),
.m-plan :deep(.el-radio__input.is-checked + .el-radio__label) { color: var(--ink); }
.m-plan :deep(.el-checkbox__label) { font-size: 12px; color: var(--ink-2); }
</style>
