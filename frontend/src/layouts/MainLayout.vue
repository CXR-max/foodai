<script setup lang="ts">
// 主布局：桌面=左侧菜单；手机=顶部汉堡抽屉 + 底部 tabBar
// 两种形态共用同一份菜单数据，桌面端行为与改造前完全一致
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { Coin, Dish, DataLine, House, Medal, SwitchButton, User, Calendar, MagicStick, Trophy, Menu as MenuIcon } from '@element-plus/icons-vue'
import { useAuthStore } from '@/stores/auth'
import { hasPendingCheckin, refreshTodayCheckin } from '@/stores/todayCheckin'
import { isMobile } from '@/composables/useIsMobile'
import MobileTabBar from '@/components/MobileTabBar.vue'

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
  { path: '/achievements', title: '成就徽章', icon: Trophy },
  { path: '/leaderboard', title: '排行榜', icon: Medal },
]

// 手机端抽屉：放全部 9 个菜单，保证任何页面都进得去
const drawer = ref(false)

// 红点：仅"健康打卡"菜单 + 今日还有未完成项时显示
function showDot(path: string) {
  return path === '/checkin' && hasPendingCheckin()
}

function refreshDot() {
  if (auth.isLoggedIn) refreshTodayCheckin()
}

onMounted(() => {
  // 刷新后拉取用户信息（保持顶栏昵称/积分最新）
  if (auth.isLoggedIn) {
    auth.fetchMe().catch(() => {})
    refreshDot()
  }
  // 切页面 / 切回窗口时都同步一次，打卡完成后红点立刻消失
  window.addEventListener('focus', refreshDot)
})

watch(() => route.path, refreshDot)

// 从手机宽度拉回桌面宽度时，把抽屉关掉，避免遮住内容
watch(isMobile, (v) => {
  if (!v) drawer.value = false
})

onBeforeUnmount(() => window.removeEventListener('focus', refreshDot))
</script>

<template>
  <el-container class="layout">
    <!-- 桌面：左侧固定菜单（手机上整块不渲染） -->
    <el-aside v-if="!isMobile" width="200px" class="aside">
      <div class="logo">🥗 识膳智行</div>
      <el-menu :default-active="route.path" router background-color="#1d2530" text-color="#a3adb8" active-text-color="#67c23a">
        <el-menu-item v-for="m in menus" :key="m.path" :index="m.path">
          <el-badge is-dot :hidden="!showDot(m.path)" class="menu-badge">
            <el-icon><component :is="m.icon" /></el-icon>
          </el-badge>
          <span>{{ m.title }}</span>
        </el-menu-item>
      </el-menu>
    </el-aside>

    <el-container>
      <el-header class="header" :class="{ 'header-mobile': isMobile }">
        <!-- 手机：左上角汉堡，打开全量菜单抽屉 -->
        <el-button v-if="isMobile" :icon="MenuIcon" text class="menu-btn" @click="drawer = true" />
        <span class="title">{{ route.meta.title }}</span>
        <div class="right">
          <!-- 手机顶栏空间有限：积分和昵称收起来，只保留退出 -->
          <el-tag v-if="!isMobile" type="warning" effect="dark" round>💰 {{ auth.user?.points_total ?? 0 }} 积分</el-tag>
          <span v-if="!isMobile" class="nickname">{{ auth.user?.nickname }}</span>
          <el-button :icon="SwitchButton" circle size="small" @click="auth.logout()" title="退出登录" />
        </div>
      </el-header>
      <el-main class="main" :class="{ 'main-mobile': isMobile }">
        <router-view />
      </el-main>
    </el-container>

    <!-- 手机：抽屉（全部 9 个菜单） -->
    <el-drawer
      v-if="isMobile"
      v-model="drawer"
      direction="ltr"
      size="72%"
      :with-header="false"
      class="mobile-drawer"
    >
      <div class="drawer-logo">🥗 识膳智行</div>
      <el-menu :default-active="route.path" router @select="drawer = false">
        <el-menu-item v-for="m in menus" :key="m.path" :index="m.path">
          <el-badge is-dot :hidden="!showDot(m.path)" class="menu-badge">
            <el-icon><component :is="m.icon" /></el-icon>
          </el-badge>
          <span>{{ m.title }}</span>
        </el-menu-item>
      </el-menu>
    </el-drawer>

    <!-- 手机：底部 tabBar -->
    <MobileTabBar v-if="isMobile" />
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
.menu-badge :deep(.el-badge__content.is-dot) { top: 50%; right: -6px; transform: translateY(-50%); }
.header {
  background: #fff; display: flex; align-items: center; justify-content: space-between;
  box-shadow: 0 1px 4px rgba(0, 21, 41, 0.08);
}
.header .title { font-size: 16px; font-weight: 600; }
.header .right { display: flex; align-items: center; gap: 12px; }
.main { padding: 16px; }

/* ---- 手机端 ---- */
.header-mobile {
  height: 52px;
  padding: 0 8px;
  gap: 4px;
}
.header-mobile .title {
  flex: 1;
  font-size: 15px;
  text-align: left;
  margin-left: 2px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.menu-btn { font-size: 20px; padding: 6px; }

.main-mobile {
  padding: 10px;
  /* 给底部 tabBar 留出位置，否则页面最后一行内容会被盖住 */
  padding-bottom: calc(70px + env(safe-area-inset-bottom, 0));
}

.drawer-logo {
  color: #1d2530;
  font-size: 17px;
  font-weight: 700;
  padding: 16px 16px 12px;
  letter-spacing: 1px;
  border-bottom: 1px solid #ebeef5;
  margin-bottom: 8px;
}
</style>
