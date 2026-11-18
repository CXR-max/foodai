<script setup lang="ts">
// 排行榜：实时积分排名（金银铜高亮 + 我的排名）
// 桌面用表格，手机用卡片列表
import { onMounted, onActivated, ref } from 'vue'
import * as pointsApi from '@/api/points'
import type { RankItem } from '@/api/points'
import { isMobile } from '@/composables/useIsMobile'

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

      <!-- 手机：卡片列表 -->
      <div v-if="isMobile" class="rank-cards">
        <div
          v-for="row in board" :key="row.rank"
          class="rank-card" :class="{ 'rank-top': row.rank <= 3, 'rank-me': row.is_me }"
        >
          <span class="rank-no">
            <template v-if="row.rank <= 3">{{ medal(row.rank) }}</template>
            <b v-else>No.{{ row.rank }}</b>
          </span>
          <span class="rank-user">
            <span class="rank-name">{{ row.nickname }}</span>
            <span class="rank-tags">
              <el-tag v-if="row.equipped_title" size="small" type="warning" effect="plain">
                {{ row.equipped_icon }} {{ row.equipped_title }}
              </el-tag>
              <el-tag v-if="row.is_me" size="small" type="success">我</el-tag>
            </span>
          </span>
          <span class="rank-points">💰 {{ row.points_total }}</span>
        </div>
        <el-empty v-if="!board.length" description="还没有排名数据" :image-size="70" />
      </div>

      <!-- 桌面：原表格 -->
      <el-table v-else :data="board" :row-class-name="rowClass" size="default">
        <el-table-column label="排名" width="90" align="center">
          <template #default="{ row }">
            <span v-if="row.rank <= 3" style="font-size:22px">{{ medal(row.rank) }}</span>
            <b v-else>No.{{ row.rank }}</b>
          </template>
        </el-table-column>
        <el-table-column label="用户" min-width="200">
          <template #default="{ row }">
            {{ row.nickname }}
            <el-tag v-if="row.equipped_title" size="small" type="warning" effect="plain" style="margin-left:6px">
              {{ row.equipped_icon }} {{ row.equipped_title }}
            </el-tag>
            <el-tag v-if="row.is_me" size="small" type="success" style="margin-left:6px">我</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="总积分" width="160" align="right">
          <template #default="{ row }"><b style="color:#e6a23c">💰 {{ row.points_total }}</b></template>
        </el-table-column>
      </el-table>
    </div>

    <div class="page-card my-rank" :class="{ 'my-rank-mobile': isMobile }" v-if="mine">
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

/* 手机端卡片 */
.rank-cards { display: flex; flex-direction: column; gap: 8px; }
.rank-card {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 11px 12px;
  border: 1px solid #ebeef5;
  border-radius: 10px;
  background: #fcfcfd;
}
.rank-top { background: #fdf6ec; border-color: #f5dab1; }
.rank-me { border-color: #67c23a; box-shadow: 0 0 0 1px #67c23a inset; }
.rank-no { width: 40px; flex-shrink: 0; text-align: center; font-size: 15px; }
.rank-user { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 3px; }
.rank-name { font-size: 14px; color: #303133; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.rank-tags { display: flex; flex-wrap: wrap; gap: 4px; }
.rank-points { font-size: 13px; color: #e6a23c; font-weight: 600; white-space: nowrap; }

@media (max-width: 768px) {
  .my-rank-mobile { flex-direction: column; align-items: flex-start; gap: 8px; }
  .my-rank-mobile .tip { margin-left: 0; }
}
</style>
