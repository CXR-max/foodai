<script setup lang="ts">
// AI 教练：右下角悬浮入口 + 抽屉对话
// 与普通聊天机器人的区别：后端每轮都会把「今日摄入/消耗/步数/睡眠」塞进 system prompt，
// 所以它开口就带数字，问「我还能吃多少」能直接答。
import { computed, nextTick, ref, watch } from 'vue'
import { coachChat } from '@/api/coach'
import type { ChatMessage } from '@/api/coach'
import { isMobile } from '@/composables/useIsMobile'

const visible = ref(false)
// 抽屉宽度交给 el-drawer 的 size 计算，别用 CSS 硬改（会导致内容溢出被裁）
const drawerSize = computed(() => (isMobile.value ? '92%' : '420px'))
const input = ref('')
const sending = ref(false)
const listEl = ref<HTMLElement | null>(null)
const messages = ref<ChatMessage[]>([
  {
    role: 'assistant',
    content: '我是你的 AI 教练，已经看到你今天的手环和饮食数据。想问「还能吃多少」「今晚吃什么」都可以直接说。',
  },
])

const QUICK = ['我今天还能吃多少？', '看看我今天的整体状态', '晚饭吃什么比较好？', '怎么改善睡眠？']

async function scrollBottom() {
  await nextTick()
  if (listEl.value) listEl.value.scrollTop = listEl.value.scrollHeight
}

async function send(text?: string) {
  const content = (text ?? input.value).trim()
  if (!content || sending.value) return
  input.value = ''
  messages.value.push({ role: 'user', content })
  sending.value = true
  await scrollBottom()
  try {
    // 只把最近 8 条发给后端，控制上下文长度
    const history = messages.value.slice(-8)
    const r = await coachChat(history)
    messages.value.push({ role: 'assistant', content: r.reply || '我这边暂时没想出合适的回答，换个问法再试试？' })
  } catch {
    messages.value.push({ role: 'assistant', content: '刚才网络有点问题，稍后再问我一次吧。' })
  } finally {
    sending.value = false
    await scrollBottom()
  }
}

watch(visible, (v) => {
  if (v) scrollBottom()
})
</script>

<template>
  <!-- 悬浮入口 -->
  <button class="coach-fab" :class="{ open: visible }" @click="visible = !visible">
    <span class="fab-icon">{{ visible ? '✕' : '🤖' }}</span>
    <span class="fab-text">{{ visible ? '收起' : 'AI 教练' }}</span>
  </button>

  <el-drawer v-model="visible" :with-header="false" :size="drawerSize" class="coach-drawer"
             :append-to-body="true">
    <div class="coach">
      <div class="coach-head">
        <div class="coach-avatar">🤖</div>
        <div class="coach-info">
          <b>AI 教练</b>
          <span>已接入你的今日摄入 / 消耗 / 步数 / 睡眠</span>
        </div>
      </div>

      <div ref="listEl" class="coach-list">
        <div v-for="(m, i) in messages" :key="i" class="msg" :class="m.role">
          <div class="bubble">{{ m.content }}</div>
        </div>
        <div v-if="sending" class="msg assistant">
          <div class="bubble typing"><i /><i /><i /></div>
        </div>
      </div>

      <div class="coach-quick">
        <el-tag v-for="q in QUICK" :key="q" class="chip" effect="plain" @click="send(q)">{{ q }}</el-tag>
      </div>

      <div class="coach-input">
        <el-input v-model="input" placeholder="问问你的吃、动、睡…" :disabled="sending"
                  @keyup.enter="send()" />
        <el-button type="primary" :loading="sending" @click="send()">发送</el-button>
      </div>
    </div>
  </el-drawer>
</template>

<style scoped>
.coach-fab {
  position: fixed; right: 22px; bottom: 26px; z-index: 2000;
  display: flex; align-items: center; gap: 7px;
  padding: 11px 17px; border: none; border-radius: 24px; cursor: pointer;
  background: linear-gradient(135deg, #4c6ef5, #7e57c2);
  color: #fff; font-size: 14px; font-weight: 600;
  box-shadow: 0 8px 22px rgba(76, 110, 245, .35);
  transition: transform .18s ease, box-shadow .18s ease;
}
.coach-fab:hover { transform: translateY(-2px); box-shadow: 0 12px 26px rgba(76, 110, 245, .42); }
.coach-fab.open { background: linear-gradient(135deg, #868e96, #495057); }
.fab-icon { font-size: 16px; }

.coach { display: flex; flex-direction: column; height: 100%; }
.coach-head {
  display: flex; align-items: center; gap: 10px;
  padding: 4px 2px 12px; border-bottom: 1px solid #f0f2f5;
}
.coach-avatar {
  width: 38px; height: 38px; border-radius: 50%; flex: none;
  display: flex; align-items: center; justify-content: center;
  background: linear-gradient(135deg, #4c6ef5, #7e57c2); font-size: 19px;
}
.coach-info { display: flex; flex-direction: column; gap: 2px; }
.coach-info b { font-size: 15px; color: #303133; }
.coach-info span { font-size: 11px; color: #909399; }

.coach-list {
  flex: 1; overflow-y: auto; padding: 14px 2px;
  display: flex; flex-direction: column; gap: 10px;
}
.msg { display: flex; }
.msg.user { justify-content: flex-end; }
.bubble {
  max-width: 84%; padding: 9px 12px; border-radius: 12px;
  font-size: 13px; line-height: 1.65; white-space: pre-wrap; word-break: break-word;
}
.msg.assistant .bubble { background: #f4f6fb; color: #303133; border-top-left-radius: 4px; }
.msg.user .bubble { background: #4c6ef5; color: #fff; border-top-right-radius: 4px; }
.typing { display: flex; gap: 4px; align-items: center; }
.typing i {
  width: 6px; height: 6px; border-radius: 50%; background: #adb5bd;
  animation: blink 1.2s infinite ease-in-out;
}
.typing i:nth-child(2) { animation-delay: .18s; }
.typing i:nth-child(3) { animation-delay: .36s; }
@keyframes blink { 0%, 80%, 100% { opacity: .25 } 40% { opacity: 1 } }

.coach-quick { display: flex; flex-wrap: wrap; gap: 6px; padding: 8px 0 10px; }
.coach-quick .chip { cursor: pointer; font-size: 12px; }
.coach-quick .chip:hover { color: #4c6ef5; border-color: #4c6ef5; }

.coach-input { display: flex; gap: 8px; padding-top: 10px; border-top: 1px solid #f0f2f5; }

@media (max-width: 768px) {
  /* 手机底部有 tabBar（56px + 安全区），悬浮按钮必须抬到它上面，否则点不到 */
  .coach-fab {
    right: 14px;
    bottom: calc(70px + env(safe-area-inset-bottom, 0px));
    /* 手机上收成圆形图标，少挡内容 */
    padding: 0; width: 46px; height: 46px; border-radius: 50%;
    justify-content: center;
  }
  .coach-fab .fab-text { display: none; }
  .coach-fab .fab-icon { font-size: 20px; }
}
</style>
