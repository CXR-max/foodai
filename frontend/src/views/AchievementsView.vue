<script setup lang="ts">
// 成就徽章页：展示全部成就（自动解锁）+ 装备/卸下头衔
import { onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import * as achApi from '@/api/achievements'
import type { Achievement } from '@/api/achievements'

const items = ref<Achievement[]>([])
const unlockedCount = ref(0)
const equippedId = ref<number | null>(null)
const loading = ref(false)

const COND_LABELS: Record<string, string> = {
  recognize_count: '累计识别',
  save_record_count: '累计保存饮食',
  streak_days: '连续打卡',
  manual_exercise_count: '累计运动',
  points_total: '累计积分',
  plan_count: '生成方案',
}
const COND_UNITS: Record<string, string> = {
  recognize_count: '次', save_record_count: '次', streak_days: '天',
  manual_exercise_count: '次', points_total: '分', plan_count: '份',
}

function condText(a: Achievement) {
  return `${COND_LABELS[a.condition_type] || a.condition_type} ${a.threshold} ${COND_UNITS[a.condition_type] || ''}`
}

async function load() {
  loading.value = true
  try {
    const r = await achApi.getAchievements()
    items.value = r.items
    unlockedCount.value = r.unlocked_count
    equippedId.value = r.equipped_achievement_id
    if (r.newly_unlocked?.length) {
      ElMessage.success(`🎉 解锁新成就：${r.newly_unlocked.join('、')}`)
    }
  } finally {
    loading.value = false
  }
}

async function toggleEquip(a: Achievement) {
  if (!a.unlocked) return
  if (a.equipped) {
    const r = await achApi.equipAchievement(null)
    ElMessage.success(r.message)
  } else {
    const r = await achApi.equipAchievement(a.id)
    ElMessage.success(r.message)
  }
  await load()
}

onMounted(load)
</script>

<template>
  <div class="page">
    <div class="page-card">
      <div class="head">
        <div>
          <h3 class="page-title">🏅 成就徽章</h3>
          <span class="sub">自动解锁 · 头衔可装备，排行榜会展示你装备的头衔</span>
        </div>
        <el-tag type="warning" effect="dark" size="large" round>已解锁 {{ unlockedCount }} / {{ items.length }}</el-tag>
      </div>

      <el-alert
        title="徽章/头衔不再用积分购买，改为「达成条件自动解锁」：连续打卡、累计识别、累计积分等达到门槛即自动点亮。头衔可自由装备/卸下。"
        type="info" :closable="false" style="margin-bottom:14px"
      />

      <el-row :gutter="14" v-loading="loading">
        <el-col v-for="a in items" :key="a.id" :span="6">
          <el-card shadow="hover" class="ach" :class="{ locked: !a.unlocked }">
            <div class="icon">{{ a.icon }}</div>
            <div class="name">{{ a.name }}</div>
            <div class="cat">
              <el-tag size="small" :type="a.category === 'title' ? 'danger' : 'primary'" effect="plain">
                {{ a.category === 'title' ? '头衔' : '徽章' }}
              </el-tag>
            </div>
            <div class="desc">{{ a.description }}</div>
            <div class="cond">{{ condText(a) }}</div>
            <div class="foot">
              <el-tag v-if="a.unlocked" type="success" size="small">已解锁</el-tag>
              <el-tag v-else type="info" size="small">未解锁</el-tag>
              <el-button
                v-if="a.unlocked"
                size="small"
                :type="a.equipped ? 'danger' : 'success'"
                plain
                @click="toggleEquip(a)"
              >
                {{ a.equipped ? '卸下' : '装备' }}
              </el-button>
            </div>
          </el-card>
        </el-col>
      </el-row>
    </div>
  </div>
</template>

<style scoped>
.head { display: flex; justify-content: space-between; align-items: flex-start; }
.sub { color: #909399; font-size: 13px; }
.ach { text-align: center; margin-bottom: 14px; height: 100%; }
.ach.locked { opacity: 0.55; }
.ach .icon { font-size: 40px; margin: 6px 0; }
.ach .name { font-weight: 700; margin-bottom: 6px; }
.ach .cat { margin-bottom: 6px; }
.ach .desc { color: #606266; font-size: 13px; min-height: 36px; }
.ach .cond { color: #c0c4cc; font-size: 12px; margin: 6px 0; }
.ach .foot { display: flex; justify-content: space-between; align-items: center; margin-top: 8px; }
</style>
