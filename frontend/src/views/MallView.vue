<script setup lang="ts">
// 积分商城：分类浏览 + 兑换（核销码）+ 我的兑换
import { computed, onMounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import * as rewardsApi from '@/api/rewards'
import type { Reward } from '@/api/rewards'
import { useAuthStore } from '@/stores/auth'

const auth = useAuthStore()
const rewards = ref<Reward[]>([])
const redemptions = ref<any[]>([])
const tab = ref('all')
const drawer = ref(false)

const CATEGORIES = [
  { key: 'all', label: '全部' },
  { key: 'honor', label: '🥇 虚拟荣誉' },
  { key: 'privilege', label: '⚡ 产品权益' },
  { key: 'goods', label: '🎁 实物好券' },
]

const filtered = computed(() =>
  tab.value === 'all' ? rewards.value : rewards.value.filter((r) => r.category === tab.value),
)

async function load() {
  rewards.value = await rewardsApi.getRewards()
  redemptions.value = await rewardsApi.getRedemptions()
}

async function doRedeem(r: Reward) {
  await ElMessageBox.confirm(
    `确定用 ${r.points_cost} 积分兑换「${r.name}」吗？`,
    '确认兑换',
    { confirmButtonText: '确认兑换', cancelButtonText: '再想想', type: 'warning' },
  )
  const res = await rewardsApi.redeem(r.id)
  auth.refreshPoints(res.points_total)
  ElMessage.success(res.message)
  await load()
}

onMounted(load)
</script>

<template>
  <div class="page">
    <div class="page-card">
      <div class="head">
        <el-tabs v-model="tab">
          <el-tab-pane v-for="c in CATEGORIES" :key="c.key" :name="c.key" :label="c.label" />
        </el-tabs>
        <div class="right">
          <el-tag type="warning" effect="dark" size="large" round>💰 我的积分：{{ auth.user?.points_total ?? 0 }}</el-tag>
          <el-button @click="drawer = true">我的兑换（{{ redemptions.length }}）</el-button>
        </div>
      </div>

      <el-row :gutter="14">
        <el-col v-for="r in filtered" :key="r.id" :span="6">
          <el-card shadow="hover" class="reward">
            <div class="icon">{{ r.icon }}</div>
            <div class="name">{{ r.name }}</div>
            <div class="desc">{{ r.description }}</div>
            <div class="foot">
              <span class="cost">{{ r.points_cost }} 分</span>
              <span v-if="r.stock > 0" class="stock">剩 {{ r.stock }} 件</span>
              <span v-else-if="r.stock === -1" class="stock">充足</span>
            </div>
            <el-button
              type="success" style="width:100%"
              :disabled="!r.can_afford"
              @click="doRedeem(r)"
            >
              {{ r.can_afford ? '立即兑换' : `还差 ${r.points_cost - (auth.user?.points_total ?? 0)} 分` }}
            </el-button>
          </el-card>
        </el-col>
      </el-row>
    </div>

    <!-- 我的兑换抽屉 -->
    <el-drawer v-model="drawer" title="我的兑换（凭核销码领取）" size="380px">
      <el-empty v-if="!redemptions.length" description="还没有兑换记录" />
      <div v-for="r in redemptions" :key="r.id" class="redeem-item">
        <div class="row1">
          <span>{{ r.icon }} {{ r.name }}</span>
          <el-tag size="small" :type="r.status === 'unused' ? 'success' : 'info'">
            {{ r.status === 'unused' ? '未使用' : '已使用' }}
          </el-tag>
        </div>
        <div class="code">核销码：<b>{{ r.code }}</b></div>
        <div class="time">-{{ r.points_spent }} 积分 · {{ new Date(r.created_at).toLocaleString('zh-CN') }}</div>
      </div>
    </el-drawer>
  </div>
</template>

<style scoped>
.head { display: flex; justify-content: space-between; align-items: center; }
.head :deep(.el-tabs) { flex: 1; }
.head :deep(.el-tabs__header) { margin-bottom: 0; }
.right { display: flex; gap: 10px; align-items: center; padding-bottom: 8px; }
.reward { text-align: center; margin-bottom: 14px; }
.reward .icon { font-size: 40px; margin: 6px 0; }
.reward .name { font-weight: 700; }
.reward .desc { color: #909399; font-size: 12px; height: 34px; margin: 6px 0; }
.reward .foot { display: flex; justify-content: space-between; margin-bottom: 8px; }
.cost { color: #e6a23c; font-weight: 800; font-size: 17px; }
.stock { color: #c0c4cc; font-size: 12px; }
.redeem-item { border: 1px dashed #ddd; border-radius: 8px; padding: 12px; margin-bottom: 10px; }
.redeem-item .row1 { display: flex; justify-content: space-between; font-weight: 600; }
.redeem-item .code { margin-top: 6px; color: #67c23a; letter-spacing: 1px; }
.redeem-item .time { color: #c0c4cc; font-size: 12px; margin-top: 4px; }
</style>
