<script setup lang="ts">
// 排行榜：按积分实时排序，编辑部式榜单（编号 + 发丝线 + 名次标记）
import { onMounted, onActivated, ref } from 'vue'
import * as pointsApi from '@/api/points'
import type { RankItem } from '@/api/points'

const board = ref<RankItem[]>([])
const mine = ref<{ points_total: number; rank: number; nickname: string } | null>(null)

async function load() {
  [board.value, mine.value] = await Promise.all([pointsApi.getLeaderboard(20), pointsApi.getMyRank()])
}

onMounted(load)
onActivated(load)   // 从其他页面切回来自动刷新 = 实时排名
</script>

<template>
  <div class="page">
    <div class="sec" style="margin-top: 0">
      <span class="sec-no">01</span>
      <span class="sec-title">积分排行榜</span>
      <span class="sec-line"></span>
      <span class="sec-act mono">REAL-TIME</span>
    </div>

    <div class="board">
      <div class="board-head mono">
        <span>RANK</span>
        <span>USER</span>
        <span class="right">POINTS</span>
      </div>

      <div
        v-for="row in board" :key="row.user_id"
        class="row" :class="{ top: row.rank <= 3, me: row.is_me }"
      >
        <span class="rank mono">{{ String(row.rank).padStart(2, '0') }}</span>
        <span class="who">
          <span class="name">{{ row.nickname }}</span>
          <span v-if="row.equipped_title" class="title-chip mono">
            {{ row.equipped_title }}
          </span>
          <span v-if="row.is_me" class="me-chip mono">我</span>
        </span>
        <span class="pts mono">{{ row.points_total }}</span>
      </div>

      <div v-if="!board.length" class="empty mono">NO DATA YET</div>
    </div>

    <div v-if="mine" class="mine">
      <div class="mine-block">
        <span class="mine-k mono">MY RANK</span>
        <span class="mine-v mono">No.{{ mine.rank }}</span>
      </div>
      <div class="mine-block">
        <span class="mine-k mono">MY POINTS</span>
        <span class="mine-v mono">{{ mine.points_total }}</span>
      </div>
      <div class="mine-tip mono">
        完成方案任务 +3/项 · 当日全清 +10 · 生成方案 +10 · 完善档案 +20
      </div>
    </div>
  </div>
</template>

<style scoped>
.board { border-top: 1px solid var(--line-2); }

.board-head,
.row {
  display: grid;
  grid-template-columns: 74px minmax(0, 1fr) 120px;
  align-items: center;
  gap: 12px;
}

.board-head {
  font-size: 9.5px;
  letter-spacing: 0.16em;
  color: var(--muted);
  padding: 9px 4px;
  border-bottom: 1px solid var(--line);
}

.row {
  padding: 13px 4px;
  border-bottom: 1px solid var(--line);
  transition: background 0.16s;
}
.row:hover { background: var(--paper-2); }
.row.me { background: var(--accent-soft); }

.rank {
  font-size: 15px;
  font-weight: 600;
  color: var(--muted);
}
.row.top { --medal: var(--ochre); box-shadow: inset 3px 0 0 var(--medal); }
.row.top:nth-child(3) { --medal: #8b9cb3; }
.row.top:nth-child(4) { --medal: var(--clay); }
.row.top .rank { color: var(--medal); }
.row.top:nth-child(2) .rank { font-size: 19px; }

.who { display: flex; align-items: center; gap: 8px; min-width: 0; }
.name { font-size: 13.5px; color: var(--ink); }
.row.top .name { font-weight: 600; }
.title-chip {
  font-size: 9.5px;
  letter-spacing: 0.04em;
  color: var(--accent-deep);
  border: 1px solid var(--line-2);
  border-radius: 999px;
  padding: 1px 8px;
}
.me-chip {
  font-size: 9.5px;
  background: var(--accent);
  color: #fff;
  border-radius: 999px;
  padding: 1px 8px;
}
.pts {
  text-align: right;
  font-size: 14px;
  font-weight: 600;
  font-variant-numeric: tabular-nums;
  color: var(--ink);
}
.row.top .pts { color: var(--medal); }
.right { text-align: right; }

.empty { padding: 40px 0; text-align: center; font-size: 10px; letter-spacing: 0.16em; color: var(--muted); }

.mine {
  margin-top: 22px;
  border: 1px solid var(--line);
  border-left: 3px solid var(--accent);
  background: #f6f9fd;
  border-radius: 4px;
  padding: 16px 20px;
  display: flex;
  align-items: center;
  gap: 48px;
  flex-wrap: wrap;
}
.mine-block { display: flex; align-items: baseline; gap: 10px; }
.mine-k { font-size: 9.5px; letter-spacing: 0.14em; color: var(--muted); }
.mine-v { font-size: 22px; font-weight: 600; color: var(--accent); letter-spacing: -0.01em; }
.mine-tip { margin-left: auto; font-size: 9.5px; letter-spacing: 0.04em; color: var(--muted); }
</style>
