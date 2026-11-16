<script setup lang="ts">
// 移动端「社区」：动态流（待接入）+ 排行榜小板块 + 奖励小板块
// 排行榜与商城走既有接口，这里只做精简展示，完整页仍可点进去
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import * as pointsApi from '@/api/points'
import type { RankItem } from '@/api/points'
import * as rewardsApi from '@/api/rewards'
import type { Reward } from '@/api/rewards'

const router = useRouter()
const ranks = ref<RankItem[]>([])
const rewards = ref<Reward[]>([])
const loading = ref(true)

async function load() {
  loading.value = true
  try {
    const [r, w] = await Promise.all([pointsApi.getLeaderboard(10), rewardsApi.getRewards()])
    ranks.value = r
    rewards.value = w.slice(0, 4)
  } catch {
    // 拦截器已提示
  } finally {
    loading.value = false
  }
}

const medal = (rank: number) => (rank === 1 ? '🥇' : rank === 2 ? '🥈' : rank === 3 ? '🥉' : `${rank}`)

onMounted(load)
</script>

<template>
  <div class="m-page">
    <!-- 动态流：待接入 -->
    <section class="block">
      <h3 class="block-title">大家的记录 <el-tag size="small" type="warning" effect="plain">待上线</el-tag></h3>
      <div class="card muted">
        <p class="hint">发布饮食 / 运动记录与心得、点赞和评论，需要新的社区表；上线后这里是信息流，你的分享也会出现在这里。</p>
      </div>
    </section>

    <!-- 排行榜小板块 -->
    <section class="block">
      <h3 class="block-title">
        排行榜
        <el-button link type="primary" size="small" style="margin-left: auto" @click="router.push('/leaderboard')">
          完整榜单 →
        </el-button>
      </h3>
      <div v-if="loading" class="skeleton">加载中…</div>
      <div v-else class="card">
        <div v-for="r in ranks" :key="r.user_id" class="rank" :class="{ me: r.is_me }">
          <span class="medal">{{ medal(r.rank) }}</span>
          <span class="name">
            {{ r.nickname }}
            <el-tag v-if="r.equipped_title" size="small" effect="plain" type="success">{{ r.equipped_title }}</el-tag>
          </span>
          <b class="pts">{{ r.points_total }}</b>
        </div>
        <el-empty v-if="!ranks.length" description="还没有人上榜" :image-size="60" />
      </div>
    </section>

    <!-- 奖励小板块 -->
    <section class="block">
      <h3 class="block-title">
        奖励
        <el-button link type="primary" size="small" style="margin-left: auto" @click="router.push('/mall')">
          去兑换 →
        </el-button>
      </h3>
      <div class="reward-grid">
        <div v-for="w in rewards" :key="w.id" class="reward" @click="router.push('/mall')">
          <div class="reward-icon">{{ w.icon }}</div>
          <div class="reward-name">{{ w.name }}</div>
          <div class="reward-cost">{{ w.points_cost }} 积分</div>
        </div>
      </div>
      <div class="card muted" style="margin-top: 8px">
        <p class="hint">成就徽章已并入奖励体系，解锁后在「我的」里可以查看和佩戴。</p>
      </div>
    </section>
  </div>
</template>

<style scoped>
.m-page { display: flex; flex-direction: column; gap: 12px; }
.block-title { font-size: 14px; font-weight: 600; color: #303133; margin: 4px 2px 8px; display: flex; align-items: center; gap: 6px; }
.card { background: #fff; border-radius: 12px; padding: 10px 14px; box-shadow: 0 1px 3px rgba(0, 0, 0, 0.05); }
.card.muted { background: #fafafa; }
.rank { display: flex; align-items: center; gap: 10px; padding: 8px 0; border-bottom: 1px dashed #f0f0f0; font-size: 13px; }
.rank:last-child { border-bottom: none; }
.rank.me { background: #f7faf4; margin: 0 -14px; padding: 8px 14px; }
.medal { width: 22px; text-align: center; color: #909399; font-size: 13px; }
.name { flex: 1; color: #303133; display: flex; align-items: center; gap: 6px; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.pts { color: #4f9d2f; }
.reward-grid { display: grid; grid-template-columns: repeat(2, 1fr); gap: 8px; }
.reward {
  background: #fff;
  border-radius: 12px;
  padding: 12px;
  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.05);
  text-align: center;
}
.reward-icon { font-size: 22px; }
.reward-name { font-size: 13px; color: #303133; margin-top: 4px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.reward-cost { font-size: 12px; color: #e6a23c; margin-top: 2px; }
.hint { font-size: 12px; color: #a8abb2; margin: 0; line-height: 1.7; }
.skeleton { font-size: 13px; color: #909399; padding: 16px; text-align: center; }
</style>
