<script setup lang="ts">
// 跑完整屏结算：跑完那一刻是整个玩法情绪最高点，不能只用一行 toast 带过去。
// 结构：本场面积（滚动）→ 形状 → 四个数字 → 从谁手里拿的 → 段位 → 排名差距 → 动作
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import * as territoryApi from '@/api/territory'
import type { TerritoryRun } from '@/api/territory'
import * as workoutApi from '@/api/workout'
import { loopsToSvgPaths, outlineLoops } from '@/utils/territoryShape'

const route = useRoute()
const router = useRouter()
const workoutId = Number(route.params.workoutId)

const run = ref<TerritoryRun | null>(null)
const loading = ref(true)
const failed = ref('')
const shownArea = ref(0)
/** 本场跑步的基础数据：结算页要能对上「刚跑的那一趟」 */
const workout = ref<{ distance_m: number; duration_sec: number; avg_pace_sec_km: number | null } | null>(null)
/** 排行榜：排名只有一个数字太抽象，看到前后是谁才有「明天超过他」的念头 */
const board = ref<{ user_id: number; nickname: string; area_m2: number }[]>([])

const runStats = computed(() => {
  const w = workout.value
  if (!w) return []
  const km = `${(w.distance_m / 1000).toFixed(2)} km`
  const min = Math.floor(w.duration_sec / 60)
  const sec = Math.round(w.duration_sec % 60)
  const dur = min >= 60
    ? `${Math.floor(min / 60)} 时 ${min % 60} 分`
    : `${min} 分 ${String(sec).padStart(2, '0')} 秒`
  const out = [km, dur]
  if (w.avg_pace_sec_km) {
    out.push(`${Math.floor(w.avg_pace_sec_km / 60)}'${String(Math.round(w.avg_pace_sec_km % 60)).padStart(2, '0')}"`)
  }
  return out
})

const valid = computed(() => !!run.value?.valid)
const newArea = computed(() => run.value?.area_new_m2 || 0)
const cutArea = computed(() => run.value?.area_cut_m2 || 0)
const victims = computed(() => run.value?.victims || [])

function fmtArea(m2: number | null | undefined): string {
  const v = Number(m2 || 0)
  if (!v) return '0 ㎡'
  if (v >= 10000) return `${(v / 10000).toFixed(2)} 万㎡`
  return `${Math.round(v)} ㎡`
}
function splitArea(m2: number) {
  if (m2 >= 10000) return { num: (m2 / 10000).toFixed(2), unit: '万㎡' }
  return { num: String(Math.round(m2)), unit: '㎡' }
}
const shownParts = computed(() => splitArea(shownArea.value))
const courtCount = computed(() => {
  const n = newArea.value / 420
  if (n >= 10) return Math.round(n)
  return Math.max(0.1, Math.round(n * 10) / 10)
})

/* 本场圈到的形状 */
const shapePaths = computed(() => {
  const cells = run.value?.cells || []
  if (!valid.value || cells.length < 3) return []
  return loopsToSvgPaths(outlineLoops(cells), 180, 150, 10)
})

const tier = computed(() => run.value?.tier || null)

const topBoard = computed(() => board.value.slice(0, 3))
const myBoardRow = computed(() => {
  const uid = run.value?.user_id
  const idx = board.value.findIndex((b) => b.user_id === uid)
  if (idx < 0) return null
  return { rank: idx + 1, nickname: board.value[idx].nickname, area_m2: board.value[idx].area_m2 }
})
const showMyRow = computed(() => !!myBoardRow.value && myBoardRow.value.rank > 3)

/* 面积滚动：从 0 数上去，结算才有「结算感」 */
function countUp(target: number) {
  const start = performance.now()
  const dur = 900
  const tick = (t: number) => {
    const p = Math.min(1, (t - start) / dur)
    const eased = 1 - Math.pow(1 - p, 3)
    shownArea.value = target * eased
    if (p < 1) requestAnimationFrame(tick)
    else shownArea.value = target
  }
  requestAnimationFrame(tick)
}

async function load() {
  loading.value = true
  failed.value = ''
  try {
    const [r, w, lb] = await Promise.all([
      territoryApi.getTerritoryRun(workoutId),
      workoutApi.getWorkout(workoutId).catch(() => null),
      territoryApi.getTerritoryLeaderboard().catch(() => ({ items: [] })),
    ])
    run.value = r
    workout.value = w
      ? { distance_m: w.distance_m || 0, duration_sec: w.duration_sec || 0,
          avg_pace_sec_km: w.avg_pace_sec_km ?? null }
      : null
    board.value = lb.items || []
    if (r.valid) countUp(newArea.value)
  } catch {
    failed.value = '没有找到这次的领地结算'
  } finally {
    loading.value = false
  }
}

function toMap() {
  router.replace({ path: '/territory', query: { run: String(workoutId) } })
}
function toReport() {
  router.replace(`/workout/${workoutId}`)
}
function runAgain() {
  router.replace({ path: '/workout/run', query: { sport: '跑步' } })
}
function openVictim(uid: number) {
  router.push({ name: 'territory-owner', params: { id: String(uid) } })
}

async function share() {
  const r = run.value
  if (!r) return
  const tierName = r.tier?.name || ''
  const parts = [`我刚跑完一圈，圈下 ${fmtArea(r.area_new_m2)} 地盘`]
  if (r.area_cut_m2) parts.push(`从好友手里切走 ${fmtArea(r.area_cut_m2)}`)
  if (r.rank) parts.push(`现在第 ${r.rank} 名`)
  if (tierName) parts.push(`段位「${tierName}」`)
  const text = `${parts.join('，')}。来抢我啊。`
  try {
    if (navigator.share) await navigator.share({ title: '我的领地战报', text })
    else if (navigator.clipboard) {
      await navigator.clipboard.writeText(text)
      ElMessage.success('战报已复制，去粘贴给同学吧')
    } else ElMessage.info(text)
  } catch {
    /* 用户取消 */
  }
}

onMounted(load)
</script>

<template>
  <div class="settle">
    <header class="bar">
      <button class="icon-btn" @click="toReport">‹</button>
      <span class="bar-title">领地结算</span>
      <button class="icon-btn skip" @click="toReport">跳过</button>
    </header>

    <div v-if="loading" class="state">正在结算…</div>
    <div v-else-if="failed" class="state">
      {{ failed }}
      <button class="link" @click="toReport">看运动报告</button>
    </div>

    <template v-else-if="run">
      <div class="scroll">
        <!-- 本场面积 -->
        <section class="hero" :class="{ fail: !valid }">
          <div class="hero-glow" />
          <div class="kicker">{{ valid ? 'TERRITORY UPDATED' : 'NO TERRITORY' }}</div>

          <template v-if="valid">
            <div class="big">
              <em>+</em><b>{{ shownParts.num }}</b><span>{{ shownParts.unit }}</span>
            </div>
            <p class="hero-sub">≈ {{ courtCount }} 个篮球场 · 已经画到地图上了</p>
            <div v-if="runStats.length" class="hero-run">
              <template v-for="(s, i) in runStats" :key="s">
                <i v-if="i" class="dot-sep" />
                <span>{{ s }}</span>
              </template>
            </div>
          </template>
          <template v-else>
            <div class="big fail-title">
              {{ run.reason?.includes('太大') ? '圈太大了' : '这次没圈住地' }}
            </div>
            <p class="hero-sub">{{ run.reason || '跑一个闭环，圈住的面积才归你' }}</p>
          </template>

          <svg v-if="shapePaths.length" class="crest" viewBox="0 0 180 150" preserveAspectRatio="xMidYMid meet">
            <path
              v-for="(d, i) in shapePaths"
              :key="i"
              :d="d"
              fill="#ffffff"
              fill-opacity="0.16"
              stroke="#ffffff"
              stroke-opacity="0.72"
              stroke-width="2"
              stroke-linejoin="round"
            />
          </svg>
        </section>

        <template v-if="valid">
          <!-- 四个数字 -->
          <section class="grid">
            <div class="g">
              <b>{{ run.cells_new }}</b>
              <span>新增地块</span>
            </div>
            <div class="g">
              <b :class="{ hot: run.cells_cut > 0 }">{{ run.cells_cut }}</b>
              <span>切走好友</span>
            </div>
            <div class="g">
              <b>{{ run.cells_repaired }}</b>
              <span>巩固自己的</span>
            </div>
            <div class="g">
              <b>{{ run.cells_attacked }}</b>
              <span>磨活力</span>
            </div>
          </section>

          <!-- 从谁手里拿的 -->
          <section v-if="victims.length" class="card booty">
            <div class="card-head">
              <h3>这次从谁手里拿的</h3>
              <span class="muted">共 {{ fmtArea(cutArea) }}</span>
            </div>
            <div
              v-for="v in victims"
              :key="v.user_id"
              class="booty-row"
              @click="openVictim(v.user_id)"
            >
              <span class="avatar">{{ v.nickname.slice(0, 1) }}</span>
              <div class="booty-main">
                <b>{{ v.nickname }}</b>
                <span>{{ v.cells }} 块地 · {{ fmtArea(v.area_m2) }}</span>
              </div>
              <span class="booty-cta">看 TA 的地 ›</span>
            </div>
            <p v-if="run.recapture_cells" class="booty-tip">
              另有 {{ run.recapture_cells }} 块地被占着，48 小时内跑回去更容易拿回来
            </p>
          </section>

          <!-- 段位 -->
          <section v-if="tier" class="card tier">
            <div class="card-head">
              <h3>当前段位</h3>
              <span class="muted">总面积 {{ fmtArea(run.area_total_m2) }}</span>
            </div>
            <div class="tier-row">
              <span class="tier-badge">Lv.{{ tier.level }}</span>
              <b>{{ tier.name }}</b>
              <span v-if="tier.next_name" class="tier-next">
                还差 {{ fmtArea(tier.to_next_m2) }} 到「{{ tier.next_name }}」
              </span>
              <span v-else class="tier-next">已到顶，守住它</span>
            </div>
            <div class="tier-bar">
              <i :style="{ width: `${Math.round(tier.progress * 100)}%` }" />
            </div>
          </section>

          <!-- 排名 -->
          <section class="card rank">
            <div class="rank-main">
              <b>第 {{ run.rank }}<small>/{{ run.rank_of }}</small></b>
              <span>全站排名</span>
            </div>
            <p class="rank-tip">
              <template v-if="run.ahead_name">
                距离第 {{ (run.rank || 1) - 1 }} 名 {{ run.ahead_name }} 还差 {{ fmtArea(run.ahead_gap_m2) }}
              </template>
              <template v-else>你是第一名，最近没人比你大</template>
            </p>
          </section>

          <!-- 排行榜：看到前后是谁，才想明天超过他 -->
          <section v-if="board.length > 1" class="card board">
            <div class="card-head">
              <h3>这片最强</h3>
              <span class="muted">按占地面积</span>
            </div>
            <div
              v-for="(b, i) in topBoard"
              :key="b.user_id"
              class="bd-row"
              :class="{ me: b.user_id === run.user_id }"
            >
              <span class="bd-rank">{{ i + 1 }}</span>
              <span class="bd-name">
                {{ b.nickname }}<em v-if="b.user_id === run.user_id">我</em>
              </span>
              <span class="bd-area">{{ fmtArea(b.area_m2) }}</span>
            </div>
            <div v-if="showMyRow && myBoardRow" class="bd-row me">
              <span class="bd-rank">{{ myBoardRow.rank }}</span>
              <span class="bd-name">{{ myBoardRow.nickname }}<em>我</em></span>
              <span class="bd-area">{{ fmtArea(myBoardRow.area_m2) }}</span>
            </div>
          </section>

          <!-- 下一步：结算不能只报喜，要给一个立刻能做的动作 -->
          <section class="card next">
            <div class="card-head"><h3>下一步</h3></div>
            <template v-if="run.recapture_cells">
              <p class="next-tip">
                你有 {{ run.recapture_cells }} 块地被占着，48 小时内跑回去更容易拿回来。
              </p>
              <button class="next-btn" @click="toMap">去地图看看是哪块</button>
            </template>
            <template v-else>
              <p class="next-tip">
                每天会掉 8 点活力。
                <template v-if="run.tier?.next_name">
                  再圈 {{ fmtArea(run.tier.to_next_m2) }} 就能升到「{{ run.tier.next_name }}」。
                </template>
                <template v-else>明天再跑一圈，守住这片。</template>
              </p>
              <button class="next-btn" @click="runAgain">再跑一圈</button>
            </template>
          </section>
        </template>

        <p class="rules">
          闭环才算圈地 · 每天 -8 活力 · 地被抢走后 48 小时内更容易拿回来
        </p>
        <div class="bottom-space" />
      </div>

      <footer class="actions">
        <template v-if="valid">
          <button class="btn primary" @click="toMap">去地图看看</button>
          <button class="btn ghost" @click="share">分享战报</button>
        </template>
        <template v-else>
          <button class="btn primary" @click="runAgain">再跑一圈</button>
          <button class="btn ghost" @click="toReport">看运动报告</button>
        </template>
      </footer>
    </template>
  </div>
</template>

<style scoped>
.settle {
  position: fixed; inset: 0; z-index: 65;
  display: flex; flex-direction: column;
  background: #f4f6f8; color: #1f2d3d;
}
.bar {
  flex: none; display: flex; align-items: center; gap: 8px;
  padding: calc(8px + env(safe-area-inset-top, 0)) 12px 8px;
  background: rgba(255, 255, 255, 0.94);
  backdrop-filter: blur(10px);
}
.icon-btn {
  border: none; background: transparent; font-size: 22px; line-height: 1;
  color: #303133; padding: 2px 8px; border-radius: 10px;
}
.icon-btn.skip { font-size: 12px; color: #8a94a6; }
.bar-title { flex: 1; text-align: center; font-size: 15px; font-weight: 600; }

.state { padding: 60px 20px; text-align: center; font-size: 13px; color: #909399; }
.link { border: none; background: none; color: #4f9d2f; font-size: 13px; margin-left: 6px; }

.scroll { flex: 1; overflow-y: auto; -webkit-overflow-scrolling: touch; padding-bottom: 8px; }
.bottom-space { height: 10px; }

/* 头部面积 */
.hero {
  position: relative; overflow: hidden;
  margin: 12px; padding: 22px 20px 24px;
  border-radius: 22px; color: #fff;
  background: linear-gradient(146deg, #2f6b1c 0%, #4f9d2f 48%, #6fbb3e 100%);
  box-shadow: 0 12px 30px rgba(47, 107, 28, 0.28);
}
.hero.fail {
  background: linear-gradient(146deg, #3f4653 0%, #5b6472 100%);
  box-shadow: 0 12px 30px rgba(63, 70, 83, 0.22);
}
.hero-glow {
  position: absolute; right: -60px; top: -60px; width: 220px; height: 220px;
  border-radius: 50%; background: radial-gradient(circle, rgba(255, 255, 255, 0.28), transparent 68%);
  pointer-events: none;
}
.kicker {
  position: relative; font-size: 9px; letter-spacing: 1.6px;
  color: rgba(255, 255, 255, 0.72); font-weight: 600;
}
.big {
  position: relative; display: flex; align-items: baseline; gap: 2px;
  margin-top: 8px; font-variant-numeric: tabular-nums;
}
.big em { font-style: normal; font-size: 22px; font-weight: 700; opacity: 0.9; }
.big b { font-size: 44px; font-weight: 800; letter-spacing: -1.6px; line-height: 1; }
.big span { font-size: 13px; opacity: 0.86; margin-left: 4px; }
.fail-title { font-size: 22px; font-weight: 700; margin-top: 10px; }
.hero-sub {
  position: relative; margin: 8px 0 0; font-size: 11px;
  color: rgba(255, 255, 255, 0.82); padding-right: 96px;
}
.crest {
  position: absolute; right: 8px; bottom: 6px; width: 104px; height: 88px;
  opacity: 0.9;
}
.hero-run {
  position: relative; display: flex; align-items: center; gap: 7px;
  margin-top: 10px; font-size: 10px; color: rgba(255, 255, 255, 0.72);
  font-variant-numeric: tabular-nums;
}
.dot-sep {
  width: 2px; height: 2px; border-radius: 50%;
  background: rgba(255, 255, 255, 0.5);
}

/* 四格 */
.grid {
  display: grid; grid-template-columns: repeat(4, 1fr); gap: 8px;
  margin: 0 12px;
}
.g {
  background: #fff; border-radius: 14px; padding: 10px 4px;
  text-align: center; box-shadow: 0 2px 10px rgba(31, 45, 61, 0.05);
}
.g b { display: block; font-size: 18px; font-weight: 700; line-height: 1.1; }
.g b.hot { color: #f97316; }
.g span { display: block; font-size: 9px; color: #8a94a6; margin-top: 4px; }

/* 卡片 */
.card {
  margin: 12px 12px 0; padding: 14px 16px;
  background: #fff; border-radius: 16px;
  box-shadow: 0 2px 10px rgba(31, 45, 61, 0.05);
}
.card-head { display: flex; align-items: baseline; justify-content: space-between; }
.card-head h3 { margin: 0; font-size: 13px; font-weight: 600; }
.muted { font-size: 10px; color: #a8abb2; }

.booty-row {
  display: flex; align-items: center; gap: 10px;
  padding: 10px 0; border-bottom: 1px solid #f5f6f8;
}
.booty-row:last-of-type { border-bottom: none; }
.avatar {
  width: 32px; height: 32px; border-radius: 10px; flex: none;
  display: flex; align-items: center; justify-content: center;
  background: #fff1e8; color: #ea580c; font-size: 13px; font-weight: 700;
}
.booty-main { flex: 1; min-width: 0; display: flex; flex-direction: column; }
.booty-main b { font-size: 13px; }
.booty-main span { font-size: 10px; color: #8a94a6; margin-top: 2px; }
.booty-cta { font-size: 10px; color: #f97316; flex: none; }
.booty-tip {
  margin: 10px 0 0; padding-top: 10px; border-top: 1px solid #f5f6f8;
  font-size: 10px; color: #c2744a;
}

/* 段位 */
.tier-row { display: flex; align-items: center; gap: 8px; margin-top: 10px; flex-wrap: wrap; }
.tier-badge {
  font-size: 10px; font-weight: 700; color: #4f9d2f;
  background: #eef5e8; border-radius: 6px; padding: 3px 7px;
}
.tier-row b { font-size: 15px; }
.tier-next { font-size: 10px; color: #8a94a6; }
.tier-bar {
  height: 7px; border-radius: 4px; background: #eef1f5;
  margin-top: 10px; overflow: hidden;
}
.tier-bar i {
  display: block; height: 100%; border-radius: 4px;
  background: linear-gradient(90deg, #4f9d2f, #7bc043);
  transition: width 0.8s ease;
}

/* 排名 */
.rank { display: flex; align-items: center; gap: 14px; }
.rank-main { display: flex; flex-direction: column; flex: none; }
.rank-main b { font-size: 24px; font-weight: 800; line-height: 1.05; letter-spacing: -0.6px; }
.rank-main b small { font-size: 11px; font-weight: 500; color: #a8abb2; margin-left: 1px; }
.rank-main span { font-size: 10px; color: #8a94a6; margin-top: 2px; }
.rank-tip { margin: 0; font-size: 11px; color: #606266; line-height: 1.5; }

/* 下一步 */
.next-tip { margin: 8px 0 10px; font-size: 11px; color: #606266; line-height: 1.6; }
.next-btn {
  width: 100%; height: 40px; border: none; border-radius: 11px;
  background: #f2f7ee; color: #4f9d2f; font-size: 13px; font-weight: 600;
}

/* 排行榜 */
.bd-row {
  display: flex; align-items: center; gap: 10px;
  padding: 8px 0; border-bottom: 1px solid #f7f8fa;
}
.bd-row:last-child { border-bottom: none; }
.bd-row.me { background: #f6faf3; border-radius: 10px; padding: 8px 8px; margin-top: 4px; }
.bd-rank {
  width: 18px; flex: none; text-align: center;
  font-size: 11px; font-weight: 700; color: #a8abb2;
}
.bd-row.me .bd-rank { color: #4f9d2f; }
.bd-name {
  flex: 1; min-width: 0; font-size: 12px; color: #303133;
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}
.bd-name em {
  font-style: normal; font-size: 9px; color: #4f9d2f;
  background: #eef5e8; border-radius: 4px; padding: 1px 4px; margin-left: 5px;
}
.bd-area { flex: none; font-size: 11px; color: #606266; font-variant-numeric: tabular-nums; }

.rules {
  margin: 16px 24px 0; text-align: center;
  font-size: 10px; color: #b3b8c2; line-height: 1.7;
}

/* 底部 */
.actions {
  flex: none; display: flex; gap: 10px;
  padding: 10px 12px calc(12px + env(safe-area-inset-bottom, 0));
  background: rgba(255, 255, 255, 0.96);
  backdrop-filter: blur(10px);
  border-top: 1px solid rgba(31, 45, 61, 0.06);
}
.btn {
  flex: 1; height: 46px; border: none; border-radius: 14px;
  font-size: 14px; font-weight: 600;
}
.btn.primary { background: linear-gradient(135deg, #4f9d2f, #7bc043); color: #fff; }
.btn.ghost { background: #fff; color: #606266; border: 1px solid #e4e7ed; }
</style>
