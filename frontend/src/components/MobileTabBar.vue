<script setup lang="ts">
// 手机底部导航：只放 5 个最高频入口，其余（数据追踪/商城/成就/排行）收进「我的」页
// 桌面端不渲染这个组件（由 MainLayout 用 v-if="isMobile" 控制）
import { useRoute } from 'vue-router'
import { hasPendingCheckin } from '@/stores/todayCheckin'

const route = useRoute()

const tabs = [
  { path: '/recognition', title: '识别', icon: '🍽️' },
  { path: '/plans', title: '方案', icon: '📋' },
  { path: '/checkin', title: '打卡', icon: '✅' },
  { path: '/dashboard', title: '仪表盘', icon: '📊' },
  { path: '/profile', title: '我的', icon: '👤' },
]

// 打卡未完成时显示红点（复用全局打卡状态，和侧边栏红点同源）
function showDot(path: string) {
  return path === '/checkin' && hasPendingCheckin()
}
</script>

<template>
  <nav class="tabbar">
    <router-link
      v-for="t in tabs"
      :key="t.path"
      :to="t.path"
      class="tab"
      :class="{ active: route.path === t.path }"
    >
      <span class="icon">
        {{ t.icon }}
        <i v-if="showDot(t.path)" class="dot" />
      </span>
      <span class="label">{{ t.title }}</span>
    </router-link>
  </nav>
</template>

<style scoped>
.tabbar {
  display: flex;
  background: #fff;
  border-top: 1px solid #ebeef5;
  /* 刘海屏底部安全区：iPhone 底部有横条，不加会被盖住 */
  padding-bottom: env(safe-area-inset-bottom, 0);
  height: calc(56px + env(safe-area-inset-bottom, 0));
  position: fixed;
  left: 0;
  right: 0;
  bottom: 0;
  z-index: 1000;
  box-shadow: 0 -1px 4px rgba(0, 0, 0, 0.04);
}

.tab {
  flex: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  text-decoration: none;
  color: #909399;
  font-size: 11px;
  transition: color 0.15s;
}

.tab.active {
  color: #67c23a;
  font-weight: 600;
}

.icon {
  font-size: 20px;
  line-height: 1;
  position: relative;
}

.label {
  margin-top: 3px;
}

.dot {
  position: absolute;
  top: -2px;
  right: -8px;
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: #f56c6c;
}
</style>
