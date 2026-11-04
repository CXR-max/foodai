<script setup lang="ts">
// 主布局：74px 图标轨 + 报告式顶栏（昵称/积分/退出）；打卡菜单带"今日未打卡"红点
import { computed, onBeforeUnmount, onMounted, watch } from 'vue'
import { useRoute } from 'vue-router'
import { useAuthStore } from '@/stores/auth'
import { hasPendingCheckin, refreshTodayCheckin } from '@/stores/todayCheckin'

const auth = useAuthStore()
const route = useRoute()

// 图标使用 1.6px 线性描边（与“营养研究所”视觉一致），path 为静态常量
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
    <!-- ================= 图标轨 ================= -->
    <aside class="rail">
      <router-link to="/dashboard" class="rail-logo" title="识膳智行">膳</router-link>

      <nav class="rail-nav">
        <router-link
          v-for="m in menus" :key="m.path" :to="m.path"
          class="rail-btn" :class="{ on: route.path === m.path }" :title="m.title"
        >
          <svg class="ico" viewBox="0 0 24 24">
            <path v-for="(d, i) in m.paths" :key="i" :d="d" />
          </svg>
          <span v-if="showDot(m.path)" class="dot"></span>
          <span class="rail-tip">{{ m.title }}</span>
        </router-link>
      </nav>

      <div class="rail-spacer"></div>

      <button class="rail-btn rail-quit" title="退出登录" @click="auth.logout()">
        <svg class="ico" viewBox="0 0 24 24">
          <path d="M14 4H6v16h8M10 12h10M17 8.5L20.5 12 17 15.5" />
        </svg>
        <span class="rail-tip">退出登录</span>
      </button>

      <div class="rail-avatar" :title="auth.user?.nickname || ''">{{ initial }}</div>
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
          <span class="nickname">{{ auth.user?.nickname }}</span>
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
  grid-template-columns: 74px minmax(0, 1fr);
  height: 100%;
  background: var(--paper);
}

/* ---------- 图标轨 ---------- */
.rail {
  background: var(--rail);
  display: flex;
  flex-direction: column;
  align-items: center;
  padding: 18px 0 16px;
  gap: 3px;
  position: relative;
  z-index: 20;
}

.rail-logo {
  width: 38px;
  height: 38px;
  border-radius: 10px;
  background: var(--lime);
  color: var(--ink);
  display: grid;
  place-items: center;
  font-family: var(--serif);
  font-weight: 700;
  font-size: 17px;
  margin-bottom: 20px;
  transition: transform 0.2s;
}
.rail-logo:hover { transform: translateY(-1px); }

.rail-nav {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 3px;
}

.rail-btn {
  position: relative;
  width: 44px;
  height: 44px;
  border: 0;
  background: transparent;
  color: var(--rail-text);
  border-radius: 10px;
  display: grid;
  place-items: center;
  cursor: pointer;
  transition: background 0.18s, color 0.18s;
  padding: 0;
}
.rail-btn .ico {
  width: 20px;
  height: 20px;
  fill: none;
  stroke: currentColor;
  stroke-width: 1.6;
  stroke-linecap: round;
  stroke-linejoin: round;
}
.rail-btn:hover { color: var(--rail-hover); background: var(--rail-2); }
.rail-btn.on { background: var(--rail-2); color: var(--lime); }
.rail-btn.on::before {
  content: '';
  position: absolute;
  left: -15px;
  top: 50%;
  transform: translateY(-50%);
  width: 3px;
  height: 18px;
  background: var(--lime);
  border-radius: 0 2px 2px 0;
}

.rail-btn .dot {
  position: absolute;
  top: 9px;
  right: 9px;
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--clay);
  border: 2px solid var(--rail);
}

.rail-tip {
  position: absolute;
  left: 52px;
  top: 50%;
  transform: translate(-4px, -50%);
  background: var(--rail);
  color: var(--paper);
  border: 1px solid var(--rail-line);
  font-size: 11.5px;
  letter-spacing: 0.04em;
  white-space: nowrap;
  padding: 5px 10px;
  border-radius: 4px;
  opacity: 0;
  pointer-events: none;
  transition: opacity 0.16s, transform 0.16s;
  z-index: 40;
}
.rail-btn:hover .rail-tip { opacity: 1; transform: translate(0, -50%); }

.rail-spacer { flex: 1; }

.rail-quit { margin-bottom: 14px; }

.rail-avatar {
  width: 34px;
  height: 34px;
  border-radius: 50%;
  background: var(--rail-2);
  color: var(--rail-hover);
  display: grid;
  place-items: center;
  font-size: 12px;
  font-family: var(--mono);
  letter-spacing: 0.02em;
  border: 1px solid var(--rail-line);
}

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
  font-family: var(--serif);
  font-size: 18px;
  font-weight: 600;
  letter-spacing: 0.02em;
  color: var(--ink);
}
.topbar-date {
  font-family: var(--mono);
  font-size: 11px;
  letter-spacing: 0.1em;
  color: var(--muted);
}

.topbar-r { display: flex; align-items: center; gap: 14px; }
.nickname { font-size: 13px; color: var(--ink-2); }

/* ---------- 内容区 ---------- */
.content {
  flex: 1;
  overflow-y: auto;
  overflow-x: hidden;
  min-height: 0;
}

@media (max-width: 1080px) {
  .shell { grid-template-columns: 60px minmax(0, 1fr); }
  .rail-btn { width: 40px; height: 40px; }
  .rail-btn.on::before { left: -11px; }
  .rail-logo { width: 34px; height: 34px; margin-bottom: 14px; }
  .topbar { padding: 0 18px; }
  .topbar-date { display: none; }
}
</style>
