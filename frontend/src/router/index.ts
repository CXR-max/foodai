// 路由表
// - /welcome /login /register 独立页
// - 桌面端：/dashboard /profile /recognition /plans /checkin /stats /mall /achievements /leaderboard
// - 移动端：/m/plan /m/schedule /m/sport /m/community /m/me（五大模块，仅手机宽度可进）
// 两套页面共用同一批接口；桌面布局不受移动端改造影响
import { createRouter, createWebHistory } from 'vue-router'
import { useAuthStore } from '@/stores/auth'
import { isMobile } from '@/composables/useIsMobile'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/welcome', name: 'welcome', component: () => import('@/views/WelcomeView.vue') },
    { path: '/login', name: 'login', component: () => import('@/views/LoginView.vue') },
    { path: '/register', name: 'register', component: () => import('@/views/RegisterView.vue') },
    // 运动：跑步中 / 运动详情是沉浸式独立页（不吃底部 tabBar），运动首页放在布局内
    { path: '/workout/run', name: 'workout-run', component: () => import('@/views/RunTrackView.vue'), meta: { title: '运动中' } },
    { path: '/workout/:id(\\d+)', name: 'workout-detail', component: () => import('@/views/WorkoutDetailView.vue'), meta: { title: '运动报告' } },
    {
      path: '/',
      component: () => import('@/layouts/MainLayout.vue'),
      redirect: '/welcome',
      children: [
        // ---- 移动端五大模块（meta.mobile=true：桌面宽度访问会跳回仪表盘）----
        { path: 'm/plan', name: 'm-plan', component: () => import('@/views/mobile/MobilePlanView.vue'), meta: { title: '计划', mobile: true } },
        { path: 'm/schedule', name: 'm-schedule', component: () => import('@/views/mobile/MobileScheduleView.vue'), meta: { title: '日程', mobile: true } },
        { path: 'm/sport', name: 'm-sport', component: () => import('@/views/mobile/MobileSportView.vue'), meta: { title: '运动', mobile: true } },
        { path: 'm/community', name: 'm-community', component: () => import('@/views/mobile/MobileCommunityView.vue'), meta: { title: '社区', mobile: true } },
        { path: 'm/me', name: 'm-me', component: () => import('@/views/mobile/MobileMeView.vue'), meta: { title: '我的', mobile: true } },

        // ---- 桌面端原有页面（布局与行为保持不变）----
        { path: 'dashboard', name: 'dashboard', component: () => import('@/views/DashboardView.vue'), meta: { title: '仪表盘' } },
        { path: 'profile', name: 'profile', component: () => import('@/views/ProfileView.vue'), meta: { title: '健康档案' } },
        { path: 'recognition', name: 'recognition', component: () => import('@/views/RecognitionView.vue'), meta: { title: '食物识别' } },
        { path: 'plans', name: 'plans', component: () => import('@/views/PlansView.vue'), meta: { title: '养生方案' } },
        { path: 'checkin', name: 'checkin', component: () => import('@/views/CheckInView.vue'), meta: { title: '健康打卡' } },
        { path: 'workout', name: 'workout', component: () => import('@/views/WorkoutView.vue'), meta: { title: '运动' } },
        { path: 'stats', name: 'stats', component: () => import('@/views/StatsView.vue'), meta: { title: '数据追踪' } },
        { path: 'mall', name: 'mall', component: () => import('@/views/MallView.vue'), meta: { title: '积分商城' } },
        { path: 'achievements', name: 'achievements', component: () => import('@/views/AchievementsView.vue'), meta: { title: '成就徽章' } },
        { path: 'leaderboard', name: 'leaderboard', component: () => import('@/views/LeaderboardView.vue'), meta: { title: '排行榜' } },
      ],
    },
  ],
})

/** 登录后的默认落地页：移动端进日程，桌面端进仪表盘 */
export function homePath(): string {
  return isMobile.value ? '/m/schedule' : '/dashboard'
}

// 登录守卫：
// - 未登录访问根路径 → 启动页
// - 已登录访问启动页 → 按端跳对应首页
// - 移动端专属路由在桌面宽度下 → 回仪表盘（避免桌面看到手机版布局）
router.beforeEach((to) => {
  const auth = useAuthStore()

  if (to.name === 'welcome') {
    return auth.isLoggedIn ? homePath() : true
  }
  if (to.name === 'login' || to.name === 'register') {
    return true
  }
  if (!auth.isLoggedIn) {
    return { name: 'login' }
  }
  if (to.meta.mobile && !isMobile.value) {
    return { name: 'dashboard' }
  }
})

export default router
