<script setup lang="ts">
// 领地详情：独立整页（不再是半屏 sheet）
// 结构：形状剪影 + 面积 → 活力倒计时 → 交锋战绩/战书 → 最近占领 → 动态时间线 → 底部动作
// 数据全部来自 /territory/owners/:id，形状用后端返回的格子在前端还原轮廓。
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import * as territoryApi from '@/api/territory'
import type { TerritoryOwnerDetail } from '@/api/territory'
import * as friendsApi from '@/api/friends'
import { loopsToSvgPaths, outlineLoops } from '@/utils/territoryShape'

const route = useRoute()
const router = useRouter()

const ownerId = Number(route.params.id)
const detail = ref<TerritoryOwnerDetail | null>(null)
const loading = ref(true)
const failed = ref('')
const challengeLeft = ref(0)
let timer: number | null = null

/* ---------------- 展示格式化 ---------------- */
function fmtArea(m2: number | null | undefined): string {
  const v = Number(m2 || 0)
  if (!v) return '0 ㎡'
  if (v >= 10000) return `${(v / 10000).toFixed(2)} 万㎡`
  return `${Math.round(v)} ㎡`
}
function fmtWhen(iso: string | null): string {
  if (!iso) return ''
  const d = new Date(iso)
  return `${d.getMonth() + 1}-${d.getDate()} ${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`
}
function fmtPace(sec: number | null): string {
  if (!sec) return '--'
  return `${Math.floor(sec / 60)}'${String(Math.round(sec % 60)).padStart(2, '0')}"`
}
function fmtCountdown(sec: number): string {
  if (sec <= 0) return '00:00:00'
  const h = Math.floor(sec / 3600)
  const m = Math.floor((sec % 3600) / 60)
  const s = sec % 60
  return [h, m, s].map((n) => String(n).padStart(2, '0')).join(':')
}

/* ---------------- 形状剪影 ---------------- */
// 逐环渲染：和地图页保持一致（里圈也填满，不会出现空洞）
const shapePaths = computed(() => {
  const cells = detail.value?.cells || []
  if (cells.length < 3) return []
  return loopsToSvgPaths(outlineLoops(cells.map((c) => c[0])), 160, 160, 14)
})
const hasShape = computed(() => shapePaths.value.length > 0)
const accent = computed(() => detail.value?.color || '#4f9d2f')

// 面积拆成数字 + 单位，避免单位跟着数字一样大
const areaParts = computed(() => {
  const m2 = detail.value?.area_m2 || 0
  if (m2 >= 10000) return { num: (m2 / 10000).toFixed(2), unit: '万㎡' }
  return { num: String(Math.round(m2)), unit: '㎡' }
})

// 活力分布：健康 / 警戒 / 将消退
const vitSpread = computed(() => {
  const cells = detail.value?.cells || []
  let good = 0
  let warn = 0
  let low = 0
  for (const [, v] of cells) {
    if (v >= 60) good += 1
    else if (v >= 30) warn += 1
    else low += 1
  }
  return { good, warn, low }
})

const heldText = computed(() => {
  const d = detail.value?.held_days ?? 0
  if (!detail.value?.cells_total) return ''
  return d === 0 ? '今天刚拿下' : `已守 ${d} 天`
})

const vitColor = computed(() => {
  const v = detail.value?.vitality_avg ?? 0
  if (v >= 60) return '#4f9d2f'
  if (v >= 30) return '#e6a23c'
  return '#f56c6c'
})
const vitHint = computed(() => {
  const d = detail.value?.days_left ?? 0
  if (!detail.value?.cells_total) return '还没有地盘'
  if (d <= 1) return '明天就可能消退，需要尽快跑一圈'
  return `按每天 -8 活力，约 ${d} 天后消退`
})
const roleText = computed(() => {
  const d = detail.value
  if (!d) return ''
  if (d.is_me) return '这是你的地盘'
  return d.is_friend ? '好友 · 可以互抢' : '陌生人 · 只能看'
})
const lastEncounter = computed(() => {
  const d = detail.value
  if (!d) return ''
  const e = d.events.find((x) => (x.type === 'cut' || x.type === 'steal') &&
    ((x.actor_id === d.user_id) || (x.victim_id === d.user_id)))
  if (!e) return '还没有交过手'
  const theirs = e.actor_id === d.user_id
  const area = e.area_m2 ? ` ${fmtArea(e.area_m2)}` : ''
  return `${fmtWhen(e.created_at)} ${theirs ? `${e.actor} 从你这里切走` : `你从 ${e.victim} 手里切走`}${area}`
})

function eventText(e: { type: string; actor: string; victim: string; area_m2: number }): string {
  if (e.type === 'decay') return `${e.victim || '有人的地盘'} 因久未跑而消退`
  if (e.type === 'leave') return `${e.actor || '有人'} 关闭领地跑，释放了全部地盘`
  const area = e.area_m2 ? ` ${fmtArea(e.area_m2)}` : ''
  if (e.type === 'cut') return `${e.actor} 从 ${e.victim} 手里切走${area}`
  if (e.type === 'recapture') return `${e.actor} 从 ${e.victim} 手里抢回${area}`
  if (e.type === 'steal') return `${e.actor} 抢走 ${e.victim} 的${area}`
  return `${e.actor} → ${e.victim}`
}

/* ---------------- 加载 ---------------- */
async function load() {
  loading.value = true
  failed.value = ''
  try {
    detail.value = await territoryApi.getTerritoryOwner(ownerId)
    challengeLeft.value = detail.value.challenge?.seconds_left || 0
  } catch {
    failed.value = '领地数据加载失败，请返回重试'
  } finally {
    loading.value = false
  }
}

/* ---------------- 动作 ---------------- */
function goRunWithGoal(mode: 'attack' | 'defend') {
  const d = detail.value
  if (!d) return
  router.push({
    path: '/workout/run',
    query: { sport: '跑步', revenge: String(d.user_id), revenge_name: d.nickname, mode },
  })
}

async function startChallenge() {
  const d = detail.value
  if (!d) return
  try {
    detail.value = { ...d, challenge: await territoryApi.sendChallenge(d.user_id) }
    challengeLeft.value = detail.value.challenge?.seconds_left || 0
  } catch (e: any) {
    const msg = e?.response?.data?.detail
    ElMessage.warning(typeof msg === 'string' ? msg : '下战书失败')
    return
  }
  try {
    await ElMessageBox.confirm(
      `已向 ${d.nickname} 下战书：24 小时内圈走 TA 任意一块地就算赢。现在去跑？`,
      '战书已送达',
      { confirmButtonText: '现在去跑', cancelButtonText: '晚点再跑', type: 'warning' },
    )
    goRunWithGoal('attack')
  } catch {
    /* 用户选择晚点跑，战书已经生效 */
  }
}

async function acceptChallenge() {
  const d = detail.value
  const ch = d?.challenge
  if (!ch) return
  try {
    detail.value = { ...d!, challenge: await territoryApi.acceptChallenge(ch.id) }
  } catch {
    /* 忽略：应战只是记号 */
  }
  ElMessage.success('已应战，去跑一圈巩固你的地盘')
  goRunWithGoal('defend')
}

async function addFriend() {
  const d = detail.value
  if (!d) return
  try {
    await friendsApi.addFriend(d.user_id)
    ElMessage.success('已加为好友，可以互相抢地了')
    await load()
  } catch {
    /* http 层已提示 */
  }
}

function viewOnMap() {
  router.push({ path: '/territory', query: { owner: String(ownerId) } })
}

function back() {
  if (window.history.length > 1) router.back()
  else router.replace('/territory')
}

onMounted(async () => {
  await load()
  timer = window.setInterval(() => {
    if (challengeLeft.value > 0) challengeLeft.value -= 1
  }, 1000)
})
onBeforeUnmount(() => {
  if (timer !== null) clearInterval(timer)
})
</script>

<template>
  <div class="td-page">
    <header class="td-bar">
      <button class="icon-btn" @click="back">‹</button>
      <div class="bar-title">领地详情</div>
      <button class="icon-btn map-btn" @click="viewOnMap">地图</button>
    </header>

    <div v-if="loading" class="state">加载中…</div>
    <div v-else-if="failed" class="state">
      {{ failed }}
      <button class="retry" @click="load">重试</button>
    </div>

    <div v-else-if="detail" class="td-scroll">
      <!-- 头部：形状 + 面积 -->
      <section class="hero" :style="{ '--c': accent }">
        <div v-if="hasShape" class="crest">
          <svg viewBox="0 0 160 160" preserveAspectRatio="xMidYMid meet">
            <path
              v-for="(d, i) in shapePaths"
              :key="i"
              :d="d"
              :fill="accent"
              fill-opacity="0.18"
              :stroke="accent"
              stroke-opacity="0.6"
              stroke-width="2.4"
              stroke-linejoin="round"
            />
          </svg>
        </div>
        <div v-else class="hero-empty">
          <span>还没有领地</span>
          <small>跑一个闭环，圈住的面积就归你</small>
        </div>

        <div class="hero-text">
          <div class="hero-role">{{ roleText }}</div>
          <div class="hero-name">{{ detail.nickname }}</div>
          <div class="hero-area">
            <b>{{ areaParts.num }}</b><em>{{ areaParts.unit }}</em>
          </div>
          <div v-if="detail.tier" class="hero-tier">
            <span class="tier-badge">Lv.{{ detail.tier.level }}</span>
            <b>{{ detail.tier.name }}</b>
            <span v-if="detail.tier.next_name" class="tier-next">
              还差 {{ fmtArea(detail.tier.to_next_m2) }} 到「{{ detail.tier.next_name }}」
            </span>
            <span v-else class="tier-next">已到顶</span>
          </div>
        </div>

        <div class="hero-stats">
          <div class="hs">
            <b>{{ fmtArea(detail.largest_block_area_m2) }}</b>
            <span>最大连片</span>
          </div>
          <div class="hs">
            <b>{{ detail.cells_total }}</b>
            <span>地块</span>
          </div>
          <div class="hs">
            <b>第 {{ detail.rank }} 名</b>
            <span>全站排名</span>
          </div>
        </div>
      </section>

      <!-- 活力 -->
      <section class="card">
        <div class="card-head">
          <h3>领地活力</h3>
          <b :style="{ color: vitColor }">{{ Math.round(detail.vitality_avg) }}%</b>
        </div>
        <div class="vit-bar">
          <i :style="{ width: `${Math.min(100, detail.vitality_avg)}%`, background: vitColor }" />
        </div>
        <p class="hint">{{ vitHint }}</p>
        <div class="chip-row" v-if="detail.cells_total">
          <span class="chip">首占 {{ fmtWhen(detail.first_claimed_at) }}</span>
          <span v-if="detail.held_days > 0" class="chip">{{ heldText }}</span>
        </div>
        <div v-if="detail.cells_total" class="spread">
          <div class="sp">
            <b>{{ vitSpread.good }}</b>
            <span>健康格</span>
          </div>
          <div class="sp">
            <b>{{ vitSpread.warn }}</b>
            <span>警戒</span>
          </div>
          <div class="sp">
            <b>{{ vitSpread.low }}</b>
            <span>将消退</span>
          </div>
        </div>
      </section>

      <!-- 交锋（不是自己才显示） -->
      <section v-if="!detail.is_me" class="card">
        <div class="card-head">
          <h3>交锋战绩</h3>
          <span class="muted">按场次计</span>
        </div>
        <div class="h2h">
          <div class="side">
            <b>{{ detail.record.wins }}</b>
            <span>我赢</span>
          </div>
          <div class="h2h-mid">:</div>
          <div class="side right">
            <b>{{ detail.record.losses }}</b>
            <span>TA 赢</span>
          </div>
        </div>
        <p class="hint">{{ lastEncounter }}</p>

        <div v-if="detail.challenge" class="duel" :class="detail.challenge.direction">
          <div class="duel-main">
            <b v-if="detail.challenge.direction === 'outgoing'">战书中 · 你向 TA 下了战书</b>
            <b v-else>TA 向你下了战书</b>
            <span v-if="detail.challenge.direction === 'outgoing'">24 小时内圈走对方一块地即胜</span>
            <span v-else>守住 24 小时，别让 TA 切走你的地</span>
          </div>
          <div class="duel-time">{{ fmtCountdown(challengeLeft) }}</div>
        </div>
      </section>

      <!-- 最近占领 -->
      <section class="card">
        <div class="card-head">
          <h3>最近占领</h3>
          <span class="muted">{{ detail.runs.length }} 次有效圈地</span>
        </div>
        <div v-if="!detail.runs.length" class="empty">
          {{ detail.area_m2 ? '还没有新的占领记录' : '还没有跑出过领地' }}
        </div>
        <div v-for="r in detail.runs" :key="r.id" class="run-row">
          <div class="run-when">
            <b>{{ fmtWhen(r.start_at) }}</b>
            <span>{{ r.sport_type }}</span>
          </div>
          <div class="run-meta">
            {{ (r.distance_m / 1000).toFixed(2) }} km · {{ Math.round(r.duration_sec / 60) }} 分钟
            · {{ fmtPace(r.avg_pace_sec_km) }} · {{ Math.round(r.calories) }} kcal
          </div>
          <div class="run-area">
            <b v-if="r.area_new_m2">+{{ fmtArea(r.area_new_m2) }}</b>
            <span v-if="r.area_cut_m2">切走 {{ fmtArea(r.area_cut_m2) }}</span>
          </div>
        </div>
      </section>

      <!-- 动态 -->
      <section class="card">
        <div class="card-head"><h3>领地动态</h3></div>
        <div v-if="!detail.events.length" class="empty">还没有抢夺记录</div>
        <div class="timeline">
          <div v-for="e in detail.events" :key="e.id" class="tl-row">
            <span class="tl-dot" :class="e.type" />
            <div class="tl-body">
              <p>{{ eventText(e) }}</p>
              <span>{{ fmtWhen(e.created_at) }}</span>
            </div>
          </div>
        </div>
      </section>

      <p class="rules">
        每天 -8 活力 · 跑过自己的地 +20 · 地被抢走后 48 小时内更容易拿回来
      </p>

      <div class="bottom-space" />
    </div>

    <!-- 底部动作 -->
    <footer v-if="detail" class="td-actions">
      <template v-if="detail.is_me">
        <button class="btn primary" @click="goRunWithGoal('defend')">去巩固领地</button>
        <button class="btn ghost" @click="viewOnMap">在地图上看</button>
      </template>
      <template v-else-if="detail.challenge && detail.challenge.direction === 'incoming'">
        <button class="btn danger" @click="acceptChallenge">应战</button>
        <button class="btn ghost" @click="viewOnMap">先看看 TA 的地</button>
      </template>
      <template v-else-if="detail.is_friend">
        <button class="btn danger" @click="startChallenge">
          {{ detail.challenge ? '战书进行中 · 去跑' : '下战书' }}
        </button>
        <button class="btn ghost" @click="goRunWithGoal('attack')">直接去抢</button>
      </template>
      <template v-else>
        <button class="btn primary" @click="addFriend">加为好友就能抢</button>
        <button class="btn ghost" @click="viewOnMap">只看 TA 的地</button>
      </template>
    </footer>
  </div>
</template>

<style scoped>
.td-page {
  position: fixed; inset: 0; z-index: 60;
  display: flex; flex-direction: column;
  background: #f4f6f8; color: #1f2d3d;
}

/* 顶栏 */
.td-bar {
  flex: none; display: flex; align-items: center; gap: 8px;
  padding: calc(8px + env(safe-area-inset-top, 0)) 12px 8px;
  background: rgba(255, 255, 255, 0.92);
  backdrop-filter: blur(10px);
  border-bottom: 1px solid rgba(31, 45, 61, 0.06);
}
.icon-btn {
  border: none; background: transparent; font-size: 22px; line-height: 1;
  color: #303133; padding: 2px 6px; border-radius: 10px;
}
.icon-btn.map-btn { font-size: 13px; color: #4f9d2f; font-weight: 600; }
.bar-title { flex: 1; text-align: center; font-size: 15px; font-weight: 600; }

.td-scroll { flex: 1; overflow-y: auto; -webkit-overflow-scrolling: touch; padding-bottom: 8px; }
.bottom-space { height: 8px; }
.state { padding: 40px 20px; text-align: center; font-size: 13px; color: #909399; }
.retry { margin-left: 8px; border: none; background: #eef5e8; color: #4f9d2f; border-radius: 8px; padding: 4px 10px; font-size: 12px; }

/* 头部 */
.hero {
  position: relative; margin: 12px 12px 0; padding: 16px 16px 0;
  border-radius: 20px; overflow: hidden;
  background: linear-gradient(140deg, color-mix(in srgb, var(--c) 16%, #fff), #ffffff 62%);
  box-shadow: 0 6px 22px rgba(31, 45, 61, 0.08);
}
.crest {
  position: absolute; right: 14px; top: 14px;
  width: 96px; height: 96px; padding: 6px; box-sizing: border-box;
  border-radius: 22px;
  background: color-mix(in srgb, var(--c) 9%, #fff);
  border: 1px solid color-mix(in srgb, var(--c) 16%, #fff);
  display: flex; align-items: center; justify-content: center;
}
.crest svg { width: 100%; height: 100%; display: block; }
.hero-empty {
  position: absolute; right: 14px; top: 18px; text-align: right;
  display: flex; flex-direction: column; gap: 2px;
}
.hero-empty span { font-size: 12px; color: #a8abb2; }
.hero-empty small { font-size: 10px; color: #c0c4cc; max-width: 130px; }
.hero-text { position: relative; z-index: 1; padding-right: 104px; }
.hero-role { font-size: 11px; color: #8a94a6; }
.hero-name { font-size: 20px; font-weight: 700; margin-top: 2px; letter-spacing: -0.2px; }
.hero-area { margin-top: 6px; }
.hero-area b { font-size: 32px; font-weight: 700; letter-spacing: -0.8px; }
.hero-area em { font-style: normal; font-size: 12px; color: #8a94a6; margin-left: 3px; }
.hero-tier { display: flex; align-items: center; gap: 6px; margin-top: 8px; flex-wrap: wrap; }
.tier-badge {
  font-size: 9px; font-weight: 700; color: #4f9d2f;
  background: #eef5e8; border-radius: 5px; padding: 2px 6px;
}
.hero-tier b { font-size: 11px; color: #303133; }
.tier-next { font-size: 9px; color: #a8abb2; }

.hero-stats {
  position: relative; z-index: 1;
  display: flex; margin: 14px -16px 0; padding: 12px 8px;
  background: rgba(255, 255, 255, 0.62);
  border-top: 1px solid rgba(31, 45, 61, 0.05);
}
.hs { flex: 1; text-align: center; position: relative; }
.hs + .hs::before {
  content: ''; position: absolute; left: 0; top: 4px; bottom: 4px;
  width: 1px; background: rgba(31, 45, 61, 0.07);
}
.hs b { display: block; font-size: 14px; font-weight: 600; }
.hs span { display: block; font-size: 10px; color: #8a94a6; margin-top: 2px; }

/* 卡片 */
.card {
  margin: 12px 12px 0; padding: 14px 16px;
  background: #fff; border-radius: 16px;
  box-shadow: 0 2px 10px rgba(31, 45, 61, 0.05);
}
.card-head { display: flex; align-items: baseline; justify-content: space-between; }
.card-head h3 { margin: 0; font-size: 13px; font-weight: 600; }
.card-head b { font-size: 15px; font-weight: 700; }
.muted { font-size: 10px; color: #a8abb2; }

.vit-bar { height: 8px; border-radius: 4px; background: #f0f2f5; margin-top: 10px; overflow: hidden; }
.vit-bar i { display: block; height: 100%; border-radius: 4px; transition: width 0.4s ease; }
.hint { margin: 8px 0 0; font-size: 11px; color: #8a94a6; line-height: 1.5; }
.chip-row { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 10px; }
.chip {
  font-size: 10px; color: #606266; background: #f4f6f8;
  border-radius: 6px; padding: 4px 8px;
}
.spread { display: flex; margin-top: 12px; padding-top: 10px; border-top: 1px solid #f5f6f8; }
.sp { flex: 1; text-align: center; }
.sp b { display: block; font-size: 14px; font-weight: 600; }
.sp span { display: block; font-size: 10px; color: #a8abb2; margin-top: 2px; }

/* 交锋 */
.h2h { display: flex; align-items: flex-end; justify-content: center; gap: 18px; padding: 10px 0 4px; }
.side { display: flex; flex-direction: column; align-items: center; min-width: 62px; }
.side b { font-size: 28px; font-weight: 700; line-height: 1; }
.side span { font-size: 10px; color: #8a94a6; margin-top: 4px; }
.h2h-mid { font-size: 18px; color: #c0c4cc; padding-bottom: 14px; }

.duel {
  display: flex; align-items: center; gap: 10px; margin-top: 12px;
  border-radius: 12px; padding: 10px 12px;
  background: #fff7ed; border: 1px solid #fed7aa;
}
.duel.incoming { background: #fef2f2; border-color: #fecaca; }
.duel-main { flex: 1; min-width: 0; display: flex; flex-direction: column; }
.duel-main b { font-size: 12px; color: #9a3412; }
.duel.incoming .duel-main b { color: #991b1b; }
.duel-main span { font-size: 10px; color: #c2744a; margin-top: 2px; }
.duel.incoming .duel-main span { color: #b45454; }
.duel-time {
  font-size: 13px; font-weight: 700; color: #ea580c;
  font-variant-numeric: tabular-nums; letter-spacing: 0.4px;
}

/* 占领记录 */
.run-row {
  display: flex; align-items: center; gap: 10px;
  padding: 10px 0; border-bottom: 1px solid #f5f6f8;
}
.run-row:last-child { border-bottom: none; padding-bottom: 0; }
.run-when { width: 72px; flex: none; display: flex; flex-direction: column; }
.run-when b { font-size: 11px; font-weight: 600; }
.run-when span { font-size: 10px; color: #a8abb2; margin-top: 1px; }
.run-meta { flex: 1; min-width: 0; font-size: 10px; color: #8a94a6; line-height: 1.5; }
.run-area { text-align: right; flex: none; display: flex; flex-direction: column; gap: 2px; }
.run-area b { font-size: 12px; color: #2563eb; }
.run-area span { font-size: 10px; color: #f97316; }

/* 时间线 */
.timeline { margin-top: 8px; }
.tl-row { display: flex; gap: 10px; padding: 7px 0; position: relative; }
.tl-dot {
  width: 7px; height: 7px; border-radius: 50%; background: #c0c4cc;
  margin-top: 5px; flex: none; position: relative; z-index: 1;
}
.tl-dot.cut { background: #f97316; }
.tl-dot.steal { background: #ef4444; }
.tl-dot.recapture { background: #22c55e; }
.tl-dot.decay { background: #c0c4cc; }
.tl-row:not(:last-child)::before {
  content: ''; position: absolute; left: 3px; top: 16px; bottom: -2px;
  width: 1px; background: #eef0f3;
}
.tl-body p { margin: 0; font-size: 11px; color: #303133; line-height: 1.5; }
.tl-body span { font-size: 10px; color: #a8abb2; }

.empty { font-size: 11px; color: #a8abb2; padding: 10px 0; }
.rules {
  margin: 18px 24px 0; text-align: center;
  font-size: 10px; color: #b3b8c2; line-height: 1.7;
}

/* 底部动作 */
.td-actions {
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
.btn.danger { background: linear-gradient(135deg, #ea580c, #f97316); color: #fff; }
.btn.ghost { background: #fff; color: #606266; border: 1px solid #e4e7ed; }
</style>
