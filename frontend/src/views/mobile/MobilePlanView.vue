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
.m-plan { display: flex; flex-direction: column; gap: 10px; }
.tabs { display: flex; gap: 6px; background: #f2f3f5; border-radius: 10px; padding: 3px; }
.tab {
  flex: 1;
  border: none;
  background: transparent;
  border-radius: 8px;
  padding: 8px 0;
  font-size: 13.5px;
  color: #606266;
  cursor: pointer;
  transition: all 0.15s;
}
.tab.on { background: #fff; color: #2f6620; font-weight: 600; box-shadow: 0 1px 3px rgba(0, 0, 0, 0.08); }
.quick {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  background: linear-gradient(135deg, #f2f8ec, #e8f5dc);
  border: 1px solid #dceecd;
  border-radius: 12px;
  padding: 12px 14px;
}
.quick-text { display: flex; flex-direction: column; min-width: 0; }
.quick-text b { font-size: 13px; color: #2f6620; font-weight: 600; }
.quick-text span { font-size: 12px; color: #6b8f5a; margin-top: 2px; }
.plan-card {
  background: #fff;
  border-radius: 12px;
  padding: 12px 14px;
  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.05);
}
.pc-head { display: flex; align-items: center; justify-content: space-between; gap: 8px; }
.pc-head b { font-size: 14px; color: #303133; }
.group { display: flex; flex-direction: column; gap: 8px; }
.group-title { display: flex; align-items: center; gap: 6px; font-size: 13.5px; font-weight: 600; color: #303133; margin: 4px 2px 0; }
.group-title em { font-style: normal; font-size: 11px; font-weight: 400; color: #a8abb2; }
.group-hint { font-size: 12px; color: #a8abb2; margin: 0 2px 4px; }
.plan-card.active { border: 1px solid #dceecd; }
.plan-card.history { opacity: 0.96; }
.plan-card.scheduled { border: 1px dashed #f0d9b5; background: #fffdf8; }
.scheduled-tip { margin: 7px 0 0; font-size: 11px; line-height: 1.5; color: #a68a5b; }
.pc-actions { display: flex; gap: 6px; margin-top: 10px; }
.pc-actions :deep(.el-button) { flex: 1; margin-left: 0; }
.expired-flag { margin-left: 6px; font-size: 11px; color: #c45656; background: #fdf2f2; border-radius: 4px; padding: 1px 5px; }
.pc-range { font-size: 12px; color: #909399; margin-top: 4px; }
.reuse-flag { margin-left: 6px; font-size: 11px; color: #4f9d2f; background: #f2f8ec; border-radius: 4px; padding: 1px 5px; }
.reactivate-bar {
  display: flex; align-items: center; gap: 6px; margin-top: 7px;
  background: #eef7ff; border: 1px solid #cfe6fb; color: #2f7bbf;
  border-radius: 6px; padding: 4px 8px; font-size: 11.5px; line-height: 1.3;
}
.reactivate-bar .rb-icon { font-size: 12px; }
.reactivate-bar .rb-text { font-weight: 500; }
.reactivate-bar em { margin-left: auto; font-style: normal; color: #7aa9d4; }
.reactivate-flag { margin-left: 6px; font-size: 11px; color: #2f7bbf; background: #eef7ff; border-radius: 4px; padding: 1px 5px; }
.pc-review {
  display: flex; align-items: baseline; gap: 4px; margin-top: 8px;
  background: #f4faf0; border: 1px solid #e2f0d6; border-radius: 8px; padding: 6px 9px;
}
.pc-review .pr-grade { background: #4f9d2f; color: #fff; font-size: 10px; line-height: 15px; padding: 0 5px; border-radius: 7px; align-self: center; }
.pc-review b { font-size: 16px; color: #2f6620; }
.pc-review em { font-style: normal; font-size: 10px; color: #8aa87a; }
.pc-review .pr-text { font-size: 11.5px; color: #5b7a4d; line-height: 1.4; margin-left: 2px; }
.pc-review.muted { background: #f7f8fa; border-color: #eef0f2; color: #a8abb2; font-size: 11.5px; }
.pc-review.ongoing { background: #f2f8ff; border-color: #d8e8fb; }
.pc-review.ongoing .pr-badge { background: #3f88d6; color: #fff; font-size: 10px; line-height: 15px; padding: 0 6px; border-radius: 7px; align-self: center; }
.pc-review.ongoing b { color: #2f6a9e; }
.pc-review.ongoing em, .pc-review.ongoing .pr-text { color: #6c8fae; }
.pc-stats { display: grid; grid-template-columns: repeat(3, 1fr); gap: 6px; margin-top: 10px; }
.pc-stats div { background: #fafbfc; border-radius: 8px; padding: 8px 6px; display: flex; flex-direction: column; align-items: center; }
.pc-stats span { font-size: 11px; color: #909399; }
.pc-stats b { font-size: 15px; color: #2f6620; margin-top: 2px; }
.pc-stats em { font-style: normal; font-size: 10px; color: #a8abb2; }
.pc-foot { margin-top: 8px; text-align: right; }
.pc-more { font-size: 12px; color: #4f9d2f; }
.skeleton { text-align: center; font-size: 13px; color: #909399; padding: 20px 0; }
</style>
