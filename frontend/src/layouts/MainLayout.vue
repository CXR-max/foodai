<script setup lang="ts">
// 主布局：248px 白色文字侧栏 + 顶栏（标题/日期/积分）；打卡菜单带"今日未打卡"红点
import { computed, onBeforeUnmount, onMounted, watch } from 'vue'
import { useRoute } from 'vue-router'
import { useAuthStore } from '@/stores/auth'
import { hasPendingCheckin, refreshTodayCheckin } from '@/stores/todayCheckin'

const auth = useAuthStore()
const route = useRoute()

// 图标使用 1.7px 线性描边，path 为静态常量
const menus = [
  {
    path: '/dashboard', title: '仪表盘',
    paths: ['M4 4h6v6H4zM14 4h6v6h-6zM4 14h6v6H4zM14 14h6v6h-6z'],
  },
  {
    path: '/profile', title: '健康档案',
    paths: ['M12 4.6a3.4 3.4 0 110 6.8 3.4 3.4 0 010-6.8', 'M4.8 20c1.2-4 3.8-6 7.2-6s6 2 7.2 6'],
  },
  {
    path: '/recognition', title: '食物识别',
    paths: ['M3.5 8.5h3l1.6-2.5h7.8l1.6 2.5h3v10h-17z', 'M12 9.8a3.2 3.2 0 110 6.4 3.2 3.2 0 010-6.4'],
  },
  {
    path: '/plans', title: '养生方案',
    paths: ['M12 3l1.8 5.2L19 10l-5.2 1.8L12 17l-1.8-5.2L5 10l5.2-1.8z', 'M18.5 16.5l.8 2.2 2.2.8-2.2.8-.8 2.2-.8-2.2-2.2-.8 2.2-.8z'],
  },
  {
    path: '/checkin', title: '健康打卡',
    paths: ['M5 5h14a1.5 1.5 0 011.5 1.5v12A1.5 1.5 0 0119 20H5a1.5 1.5 0 01-1.5-1.5v-12A1.5 1.5 0 015 5z', 'M3.5 9.5h17M8 3v4M16 3v4'],
  },
  {
    path: '/stats', title: '数据追踪',
    paths: ['M4 19V5M4 19h16', 'M7.5 15.5l4-5 3.2 2.6 4.3-6'],
  },
  {
    path: '/mall', title: '积分商城',
    paths: ['M5 8h14l-1.2 12H6.2z', 'M9 8V6.5a3 3 0 016 0V8'],
  },
  {
    path: '/achievements', title: '成就徽章',
    paths: ['M12 4a5 5 0 110 10 5 5 0 010-10', 'M9 13.6L8 21l4-2.4L16 21l-1-7.4'],
  },
  {
    path: '/leaderboard', title: '排行榜',
    paths: ['M6 20V11M12 20V4M18 20v-6'],
  },
]

const WEEK = ['周日', '周一', '周二', '周三', '周四', '周五', '周六']
const todayLabel = computed(() => {
  const d = new Date()
  const p = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}.${p(d.getMonth() + 1)}.${p(d.getDate())} · ${WEEK[d.getDay()]}`
})

const initial = computed(() => (auth.user?.nickname || 'U').slice(0, 1).toUpperCase())

// 红点：仅"健康打卡"菜单 + 今日还有未完成项时显示
function showDot(path: string) {
  return path === '/checkin' && hasPendingCheckin()
}

function refreshDot() {
  if (auth.isLoggedIn) refreshTodayCheckin()
}

onMounted(() => {
  if (auth.isLoggedIn) {
    auth.fetchMe().catch(() => {})
    refreshDot()
  }
  window.addEventListener('focus', refreshDot)
})

watch(() => route.path, refreshDot)

onBeforeUnmount(() => window.removeEventListener('focus', refreshDot))
</script>

<template>
  <div class="shell">
    <!-- ================= 白色文字侧栏 ================= -->
    <aside class="sidebar">
      <router-link to="/dashboard" class="brand">
        <span class="brand-mark">膳</span>
        <span class="brand-text">
          <span class="brand-name">识膳智行</span>
          <span class="brand-scope">AI 健康管理</span>
        </span>
      </router-link>

      <nav class="nav">
        <router-link
          v-for="m in menus" :key="m.path" :to="m.path"
          class="nav-link" :class="{ on: route.path === m.path }"
        >
          <svg class="ico" viewBox="0 0 24 24">
            <path v-for="(d, i) in m.paths" :key="i" :d="d" />
          </svg>
          <span class="nav-title">{{ m.title }}</span>
          <span v-if="showDot(m.path)" class="dot"></span>
        </router-link>
      </nav>

      <div class="sidebar-foot">
        <div class="user-row" :title="auth.user?.nickname || ''">
          <span class="avatar">{{ initial }}</span>
          <span class="who">{{ auth.user?.nickname }}</span>
        </div>
        <button class="quit" @click="auth.logout()">退出登录</button>
      </div>
    </aside>

    <!-- ================= 主区 ================= -->
    <div class="main">
      <header class="topbar">
        <div class="topbar-l">
          <span class="topbar-title">{{ route.meta.title }}</span>
          <span class="topbar-date">{{ todayLabel }}</span>
        </div>
        <div class="topbar-r">
          <span class="chip chip-accent">积分 <b>{{ auth.user?.points_total ?? 0 }}</b></span>
        </div>
      </header>

      <main class="content">
        <router-view />
      </main>
    </div>
  </div>
</template>

<style scoped>
.shell {
  display: grid;
  grid-template-columns: 248px minmax(0, 1fr);
  height: 100%;
  background: var(--paper);
}

/* ---------- 白色文字侧栏 ---------- */
.sidebar {
  background: var(--surface);
  border-right: 1px solid var(--line);
  display: flex;
  flex-direction: column;
  padding: 20px 14px 16px;
  position: relative;
  z-index: 20;
  overflow-y: auto;
}

.brand {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 0 8px 20px;
  text-decoration: none;
}
.brand-mark {
  width: 36px;
  height: 36px;
  flex: 0 0 36px;
  border-radius: 11px;
  background: var(--accent);
  color: #fff;
  display: grid;
  place-items: center;
  font-family: var(--sans);
  font-weight: 800;
  font-size: 17px;
}
.brand-name {
  display: block;
  font-size: 16px;
  font-weight: 800;
  letter-spacing: -0.01em;
  line-height: 1.1;
  color: var(--ink);
}
.brand-scope {
  display: block;
  font-size: 11px;
  color: var(--muted);
  margin-top: 3px;
}

.nav {
  display: flex;
  flex-direction: column;
  gap: 2px;
}
.nav-link {
  position: relative;
  display: flex;
  align-items: center;
  gap: 10px;
  height: 40px;
  padding: 0 12px;
  border-radius: 12px;
  color: #47504f;
  font-size: 13.5px;
  text-decoration: none;
  transition: background 0.14s, color 0.14s;
}
.nav-link .ico {
  width: 18px;
  height: 18px;
  flex: 0 0 18px;
  fill: none;
  stroke: currentColor;
  stroke-width: 1.7;
  stroke-linecap: round;
  stroke-linejoin: round;
}
.nav-link:hover { background: #eef6f5; color: var(--ink); }
.nav-link.on { background: var(--accent-soft); color: var(--accent-deep); font-weight: 600; }
.nav-link.on .ico { color: var(--accent); }
.nav-link .dot {
  margin-left: auto;
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: var(--clay);
}

.sidebar-foot {
  margin-top: auto;
  padding-top: 16px;
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.user-row {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 8px 10px;
  border-radius: 12px;
  min-width: 0;
}
.avatar {
  width: 32px;
  height: 32px;
  flex: 0 0 32px;
  border-radius: 50%;
  display: grid;
  place-items: center;
  font-size: 13px;
  font-weight: 700;
  background: var(--accent-soft);
  color: var(--accent-deep);
  font-family: var(--mono);
}
.user-row .who {
  font-size: 13px;
  color: var(--ink);
  line-height: 1.2;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.quit {
  border: 1px solid var(--line-2);
  background: transparent;
  color: var(--ink-2);
  font-size: 12.5px;
  height: 36px;
  border-radius: 10px;
  cursor: pointer;
  transition: background 0.15s, color 0.15s, border-color 0.15s;
}
.quit:hover { background: var(--clay-soft); color: var(--clay-deep); border-color: var(--clay); }

/* ---------- 顶栏 ---------- */
.main {
  display: flex;
  flex-direction: column;
  min-width: 0;
  height: 100%;
}

.topbar {
  height: 70px;
  flex: 0 0 70px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0 32px;
  border-bottom: 1px solid var(--line);
  background: var(--paper);
}

.topbar-l { display: flex; align-items: baseline; gap: 14px; min-width: 0; }
.topbar-title {
  font-size: 18px;
  font-weight: 800;
  letter-spacing: -0.01em;
  color: var(--ink);
}
.topbar-date {
  font-family: var(--mono);
  font-size: 11px;
  letter-spacing: 0.1em;
  color: var(--muted);
}

.topbar-r { display: flex; align-items: center; gap: 14px; }

/* ---------- 内容区 ---------- */
.content {
  flex: 1;
  overflow-y: auto;
  overflow-x: hidden;
  min-height: 0;
}

@media (max-width: 1080px) {
  .shell { grid-template-columns: 200px minmax(0, 1fr); }
  .topbar { padding: 0 18px; }
  .topbar-date { display: none; }
}
</style>
