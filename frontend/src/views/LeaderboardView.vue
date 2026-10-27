<script setup lang="ts">
// 排行榜：实时积分排名（金银铜高亮 + 我的排名）
import { onMounted, onActivated, ref } from 'vue'
import * as pointsApi from '@/api/points'
import type { RankItem } from '@/api/points'

const board = ref<RankItem[]>([])
const mine = ref<{ points_total: number; rank: number; nickname: string } | null>(null)

async function load() {
  [board.value, mine.value] = await Promise.all([pointsApi.getLeaderboard(20), pointsApi.getMyRank()])
}

const medal = (rank: number) => (rank === 1 ? '🥇' : rank === 2 ? '🥈' : rank === 3 ? '🥉' : '')
const rowClass = ({ row }: { row: RankItem }) => (row.rank <= 3 ? 'top-row' : '')

onMounted(load)
onActivated(load)   // 从其他页面切回来自动刷新 = 实时排名
</script>

<template>
  <div class="page">
    <div class="page-card">
      <h3 class="page-title">🏆 积分排行榜（实时）</h3>
      <el-table :data="board" :row-class-name="rowClass" size="default">
        <el-table-column label="排名" width="90" align="center">
          <template #default="{ row }">
            <span v-if="row.rank <= 3" style="font-size:22px">{{ medal(row.rank) }}</span>
            <b v-else>No.{{ row.rank }}</b>
          </template>
        </el-table-column>
        <el-table-column label="用户" min-width="180">
          <template #default="{ row }">
            {{ row.nickname }}
            <el-tag v-if="row.is_me" size="small" type="success" style="margin-left:6px">我</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="总积分" width="160" align="right">
          <template #default="{ row }"><b style="color:#e6a23c">💰 {{ row.points_total }}</b></template>
        </el-table-column>
      </el-table>
    </div>

    <div class="page-card my-rank" v-if="mine">
      <div>我的排名：<b style="font-size:22px;color:#67c23a">No.{{ mine.rank }}</b></div>
      <div>我的积分：<b style="font-size:22px;color:#e6a23c">{{ mine.points_total }}</b></div>
      <div class="tip">完成方案任务 +3/项 · 当日全清 +10 · 生成方案 +10 · 完善档案 +20</div>
    </div>
  </div>
</template>

<style scoped>
:deep(.top-row) { background: #fdf6ec !important; }
.my-rank { display: flex; gap: 40px; align-items: center; }
.my-rank .tip { margin-left: auto; color: #c0c4cc; font-size: 12px; }
</style>
