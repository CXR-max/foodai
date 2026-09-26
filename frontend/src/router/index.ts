// 路由表：/login /register 独立页；其余在 MainLayout 布局内（带侧边栏）
// 加新页面：views/ 下建 XxxView.vue，然后在这里加一行
import { createRouter, createWebHistory } from 'vue-router'
import { useAuthStore } from '@/stores/auth'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/login', name: 'login', component: () => import('@/views/LoginView.vue') },
    { path: '/register', name: 'register', component: () => import('@/views/RegisterView.vue') },
    {
      path: '/',
      component: () => import('@/layouts/MainLayout.vue'),
      redirect: '/dashboard',
      children: [
        { path: 'dashboard', name: 'dashboard', component: () => import('@/views/DashboardView.vue'), meta: { title: '仪表盘' } },
        { path: 'profile', name: 'profile', component: () => import('@/views/ProfileView.vue'), meta: { title: '健康档案' } },
        { path: 'recognition', name: 'recognition', component: () => import('@/views/RecognitionView.vue'), meta: { title: '食物识别' } },
        { path: 'plans', name: 'plans', component: () => import('@/views/PlansView.vue'), meta: { title: '养生方案' } },
        { path: 'checkin', name: 'checkin', component: () => import('@/views/CheckInView.vue'), meta: { title: '健康打卡' } },
        { path: 'stats', name: 'stats', component: () => import('@/views/StatsView.vue'), meta: { title: '数据追踪' } },
        { path: 'mall', name: 'mall', component: () => import('@/views/MallView.vue'), meta: { title: '积分商城' } },
        { path: 'leaderboard', name: 'leaderboard', component: () => import('@/views/LeaderboardView.vue'), meta: { title: '排行榜' } },
      ],
    },
  ],
})

// 登录守卫：没 token 一律踢回登录页
router.beforeEach((to) => {
  const auth = useAuthStore()
  if (to.meta.requiresAuth !== false && to.name !== 'login' && to.name !== 'register' && !auth.isLoggedIn) {
    return { name: 'login' }
  }
})

export default router
