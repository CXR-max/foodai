<script setup lang="ts">
// 仪表盘统计卡片
defineProps<{
  title: string
  value: string | number
  sub?: string
  icon: string
  color?: string
}>()
</script>

<template>
  <el-card shadow="hover" class="tile">
    <div class="row">
      <div class="icon" :style="{ background: (color || '#67c23a') + '22', color: color || '#67c23a' }">{{ icon }}</div>
      <div>
        <div class="value">{{ value }}</div>
        <div class="title">{{ title }} <span v-if="sub" class="sub">{{ sub }}</span></div>
      </div>
    </div>
  </el-card>
</template>

<style scoped>
.tile .row { display: flex; align-items: center; gap: 14px; }
.icon {
  width: 48px; height: 48px; border-radius: 10px;
  display: flex; align-items: center; justify-content: center; font-size: 24px;
}
.value { font-size: 24px; font-weight: 700; line-height: 1.2; }
.title { color: #909399; font-size: 13px; }
.sub { color: #c0c4cc; }
</style>
