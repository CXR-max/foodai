<script setup lang="ts">
// 主布局：左侧菜单 + 顶栏（昵称/积分/退出）
import { onMounted } from 'vue'
import { useRoute } from 'vue-router'
import { Coin, Dish, DataLine, House, Medal, SwitchButton, User, Calendar, MagicStick } from '@element-plus/icons-vue'
import { useAuthStore } from '@/stores/auth'

const auth = useAuthStore()
const route = useRoute()

const menus = [
  { path: '/dashboard', title: '仪表盘', icon: House },
  { path: '/profile', title: '健康档案', icon: User },
  { path: '/recognition', title: '食物识别', icon: Dish },
  { path: '/plans', title: '养生方案', icon: MagicStick },
  { path: '/checkin', title: '健康打卡', icon: Calendar },
  { path: '/stats', title: '数据追踪', icon: DataLine },
  { path: '/mall', title: '积分商城', icon: Coin },
  { path: '/leaderboard', title: '排行榜', icon: Medal },
]

onMounted(() => {
  // 刷新后拉取用户信息（保持顶栏昵称/积分最新）
  if (auth.isLoggedIn) auth.fetchMe().catch(() => {})
})
</script>

<template>
  <el-container class="layout">
    <el-aside width="200px" class="aside">
      <div class="logo">🥗 识膳智行</div>
      <el-menu :default-active="route.path" router background-color="#1d2530" text-color="#a3adb8" active-text-color="#67c23a">
        <el-menu-item v-for="m in menus" :key="m.path" :index="m.path">
          <el-icon><component :is="m.icon" /></el-icon>
          <span>{{ m.title }}</span>
        </el-menu-item>
      </el-menu>
    </el-aside>

    <el-container>
      <el-header class="header">
        <span class="title">{{ route.meta.title }}</span>
        <div class="right">
          <el-tag type="warning" effect="dark" round>💰 {{ auth.user?.points_total ?? 0 }} 积分</el-tag>
          <span class="nickname">{{ auth.user?.nickname }}</span>
          <el-button :icon="SwitchButton" circle size="small" @click="auth.logout()" title="退出登录" />
        </div>
      </el-header>
      <el-main class="main">
        <router-view />
      </el-main>
    </el-container>
  </el-container>
</template>

<style scoped>
.layout { height: 100%; }
.aside { background: #1d2530; }
.logo {
  color: #fff; font-size: 18px; font-weight: 700;
  padding: 20px 16px; letter-spacing: 1px;
}
.aside :deep(.el-menu) { border-right: none; }
.header {
  background: #fff; display: flex; align-items: center; justify-content: space-between;
  box-shadow: 0 1px 4px rgba(0, 21, 41, 0.08);
}
.header .title { font-size: 16px; font-weight: 600; }
.header .right { display: flex; align-items: center; gap: 12px; }
.main { padding: 16px; }
</style>
