<script setup lang="ts">
// 养生方案页：偏好面板 + 纯文字对话 → 合并生成 → 调整(重生成/可视化编辑) → 导入日历
import { computed, onMounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import * as planApi from '@/api/plan'
import type { Plan, PlanDay, PlanMeal } from '@/api/plan'
import * as checkinApi from '@/api/checkin'

const plans = ref<Plan[]>([])
const current = ref<Plan | null>(null)          // 当前展示的方案（含 days）
const activeDay = ref('1')                      // 当前 tab（第几天）
const generating = ref(false)
const MEAL_LABELS: Record<string, string> = { breakfast: '🌅 早餐', lunch: '☀️ 午餐', dinner: '🌙 晚餐', snack: '🍎 加餐' }
const importedDays = ref<Set<number>>(new Set()) // 已导入日历的 plan_day id

// ---- 定制向导①：偏好面板（引导回忆，可当场改，可跳过）----
const TASTES = ['清淡', '咸鲜', '香辣', '微辣', '麻辣', '酸辣', '偏甜', '偏酸', '偏咸', '蒜香', '孜然', '酱香', '烧烤', '鲜香']
const RESTRICTIONS = ['素食', '蛋奶素', '忌辣', '少油', '低糖', '低盐', '海鲜过敏', '花生过敏', '坚果过敏', '乳糖不耐', '麸质过敏', '大豆过敏', '不吃猪肉', '清真', '不吃牛肉']
const EXERCISES = ['跑步', '快走', '散步', '骑行', '游泳', '瑜伽', '普拉提', '力量训练', '健身操', '跳绳', '羽毛球', '篮球', '足球', '乒乓球', '网球', '登山', '徒步', '舞蹈', '广场舞', '太极拳', '八段锦', 'HIIT']
const prefs = ref({
  taste_preferences: [] as string[],
  dietary_restrictions: [] as string[],
  exercise_preferences: [] as string[],
})
const note = ref('')

// ---- 定制向导②：纯文字对话（支持多轮会话 + 历史存档）----
const chatMsgs = ref<planApi.ChatMsg[]>([])
const chatInput = ref('')
const chatting = ref(false)
const chatDrawer = ref(false)                    // 历史对话抽屉
const CHAT_HISTORY_KEY = 'plan_chat_history'
interface ChatSession { time: string; msgs: planApi.ChatMsg[] }
const chatSessions = ref<ChatSession[]>(loadChatHistory())

function loadChatHistory(): ChatSession[] {
  try {
    const raw = localStorage.getItem(CHAT_HISTORY_KEY)
    return raw ? (JSON.parse(raw) as ChatSession[]) : []
  } catch {
    return []
  }
}
function saveChatHistory() {
  try {
    localStorage.setItem(CHAT_HISTORY_KEY, JSON.stringify(chatSessions.value))
  } catch { /* 隐私模式等写入失败则忽略 */ }
}
// 把用户说过的话拼成 chat_text，与面板信息一起提交生成
const chatText = computed(() => chatMsgs.value.filter((m) => m.role === 'user').map((m) => m.content).join('\n'))

async function sendChat() {
  const text = chatInput.value.trim()
  if (!text || chatting.value) return
  chatMsgs.value.push({ role: 'user', content: text })
  chatInput.value = ''
  chatting.value = true
  // 先放一个 assistant 占位气泡，逐块追加文字（首字立即出现）
  const aiMsg: planApi.ChatMsg = { role: 'assistant', content: '…' }
  chatMsgs.value.push(aiMsg)
  let started = false
  try {
    await planApi.chatPlanStream(chatMsgs.value.slice(0, -1), {
      onDelta: (t) => {
        if (!started) { aiMsg.content = ''; started = true }
        aiMsg.content += t
        chatMsgs.value = [...chatMsgs.value]
      },
      onDone: () => {},
      onError: (msg) => {
        if (!started) aiMsg.content = `（对话失败：${msg}）`
        chatMsgs.value = [...chatMsgs.value]
      },
    })
  } finally {
    chatting.value = false
  }
}

const days = computed<PlanDay[]>(() => current.value?.days ?? [])

async function loadPlans() {
  plans.value = await planApi.getPlans()
  if (plans.value.length && !current.value) await openPlan(plans.value[0].id)
}

async function openPlan(id: number) {
  current.value = await planApi.getPlan(id)
  activeDay.value = '1'
  await refreshImported()
}

// 标记"已导入日历"的按钮
async function refreshImported() {
  importedDays.value = new Set()
  for (const day of days.value) {
    const r = await checkinApi.getDay(day.date)
    if (r.items.some((i) => i.plan_day_id === day.id)) importedDays.value.add(day.id)
  }
}

// 生成：偏好面板 + 聊天文本 + 备注 一起提交；逐天流式显示
const genDays = ref(7)
const genStatus = ref('')                      // 生成进度提示（第几天、已生成多少字）

async function generate() {
  generating.value = true
  genStatus.value = 'AI 正在综合你的偏好、对话与健康档案…'
  const days: PlanDay[] = []
  const temp: Plan = {
    id: 0, title: '生成中…', status: 'active',
    summary: `正在为你逐天生成方案（0/${genDays.value}）…`,
    daily_target_calories: 0, provider: '', days: [],
  }
  current.value = temp
  activeDay.value = '1'
  importedDays.value = new Set()
  try {
    await planApi.generatePlanStream(
      {
        days: genDays.value,
        note: note.value,
        // 勾选框全空则传 undefined（省略该字段），后端回退使用健康档案里的偏好
        taste_preferences: prefs.value.taste_preferences.length ? prefs.value.taste_preferences : undefined,
        dietary_restrictions: prefs.value.dietary_restrictions.length ? prefs.value.dietary_restrictions : undefined,
        exercise_preferences: prefs.value.exercise_preferences.length ? prefs.value.exercise_preferences : undefined,
        chat_text: chatText.value,
      },
      {
        onProgress: (p) => {
          const done = days.length
          genStatus.value = `正在生成第 ${p.day_index} 天…（已完成 ${done}/${genDays.value} 天）`
          current.value = { ...temp, days: [...days], summary: genStatus.value }
        },
        onDay: (d) => {
          days.push(d)
          genStatus.value = `已完成 ${days.length}/${genDays.value} 天，继续生成下一天…`
          current.value = { ...temp, days: [...days], summary: `正在生成…（${days.length}/${genDays.value} 天）` }
        },
        onDone: async (r) => {
          ElMessage.success('方案生成成功！')
          await loadPlans()
          await openPlan(r.plan_id)
        },
        onError: (msg) => {
          ElMessage.error(msg)
          current.value = null
        },
      },
    )
  } catch {
    ElMessage.error('生成失败，请稍后再试')
    current.value = null
  } finally {
    generating.value = false
  }
}

function confirmGenerate() {
  ElMessageBox.confirm(
    '将根据你的偏好、对话与健康档案生成 7 天方案，已有方案会保留在历史里。继续？',
    '生成养生方案',
    { confirmButtonText: '开始生成', cancelButtonText: '再想想', type: 'info' },
  ).then(generate).catch(() => {})
}

// 重新生成：先按当前输入生成新方案，成功后重置面板为"全新一轮"
async function regenerate() {
  await generate()
  resetForNewRound()
}

function confirmRegenerate() {
  ElMessageBox.confirm(
    '将按当前勾选与对话重新生成一份新方案；完成后会清空勾选、开启新的 AI 对话（旧对话存入历史）。继续？',
    '重新生成', { confirmButtonText: '重新生成', cancelButtonText: '取消', type: 'warning' },
  ).then(regenerate).catch(() => {})
}

// 重置为"全新一轮"：存档当前对话 → 清空对话 / 勾选 / 备注
function resetForNewRound() {
  if (chatMsgs.value.length) {
    chatSessions.value.unshift({ time: new Date().toLocaleString(), msgs: [...chatMsgs.value] })
    saveChatHistory()
  }
  chatMsgs.value = []
  chatInput.value = ''
  prefs.value = { taste_preferences: [], dietary_restrictions: [], exercise_preferences: [] }
  note.value = ''
}

// 复制某个历史对话为纯文本
async function copySession(s: ChatSession) {
  const text = s.msgs.map((m) => `${m.role === 'user' ? '我' : 'AI'}：${m.content}`).join('\n')
  try {
    await navigator.clipboard.writeText(text)
    ElMessage.success('对话已复制')
  } catch {
    ElMessage.warning('复制失败，请手动选择文本')
  }
}

function clearChatHistory() {
  ElMessageBox.confirm('确定清空全部历史对话？', '清空历史对话', {
    confirmButtonText: '清空', cancelButtonText: '取消', type: 'warning',
  }).then(() => {
    chatSessions.value = []
    saveChatHistory()
  }).catch(() => {})
}

// ---- 可视化编辑某天 ----
const editDialog = ref(false)
const editForm = ref<{ dayIndex: number; meals: PlanMeal[] } | null>(null)

function openEdit(day: PlanDay) {
  editForm.value = { dayIndex: day.day_index, meals: JSON.parse(JSON.stringify(day.meals)) }
  editDialog.value = true
}

function addItem(meal: PlanMeal) {
  meal.items.push({ name: '', portion_g: 100 })
}

async function saveEdit() {
  if (!editForm.value || !current.value) return
  const meals = editForm.value.meals
    .map((m) => ({ ...m, items: m.items.filter((i) => i.name.trim()) }))
    .filter((m) => m.items.length > 0)
  await planApi.updatePlanDay(current.value.id, editForm.value.dayIndex, { meals })
  ElMessage.success('方案已更新，热量已按营养库重算，改动会同步到日历')
  editDialog.value = false
  await openPlan(current.value.id)
  activeDay.value = String(editForm.value.dayIndex)
}

// ---- 导入日历 ----
async function importDay(day: PlanDay) {
  const r = await checkinApi.importFromPlan(day.id)
  ElMessage.success(r.message)
  importedDays.value.add(day.id)
}

onMounted(loadPlans)
</script>

<template>
  <div class="page">
    <!-- 定制向导：偏好面板 + 文字对话 -->
    <div class="page-card">
      <div>
        <h3 class="page-title" style="margin-bottom:4px">✨ AI 养生方案定制</h3>
        <span class="sub">勾选偏好 + 直接和 AI 聊 → 合并生成 · 生成后可重生成 / 手动编辑</span>
      </div>

      <el-row :gutter="16" style="margin-top:14px">
        <el-col :span="12">
          <div class="wizard-box">
            <div class="wizard-title">① 勾选偏好（帮你回忆，可跳过）</div>
            <div class="pref-label">😋 口味</div>
            <el-checkbox-group v-model="prefs.taste_preferences">
              <el-checkbox v-for="t in TASTES" :key="t" :value="t">{{ t }}</el-checkbox>
            </el-checkbox-group>
            <div class="pref-label">🚫 饮食禁忌</div>
            <el-checkbox-group v-model="prefs.dietary_restrictions">
              <el-checkbox v-for="t in RESTRICTIONS" :key="t" :value="t">{{ t }}</el-checkbox>
            </el-checkbox-group>
            <div class="pref-label">🏃 运动兴趣</div>
            <el-checkbox-group v-model="prefs.exercise_preferences">
              <el-checkbox v-for="t in EXERCISES" :key="t" :value="t">{{ t }}</el-checkbox>
            </el-checkbox-group>
          </div>
        </el-col>

        <el-col :span="12">
          <div class="wizard-box chat-box">
            <div class="wizard-title chat-title">
              <span>② 想说什么，直接和 AI 说（可选）</span>
              <el-button v-if="chatSessions.length" link type="primary" size="small" @click="chatDrawer = true">
                历史对话（{{ chatSessions.length }}）
              </el-button>
            </div>
            <div class="chat-list">
              <div v-if="!chatMsgs.length" class="chat-empty">
                例如：我下周要加班，晚饭想简单点；我爱吃辣但胃不好；不想吃外卖…
              </div>
              <div v-for="(m, i) in chatMsgs" :key="i" class="chat-row" :class="m.role">
                <div class="bubble">{{ m.content }}</div>
              </div>
            </div>
            <div class="chat-input">
              <el-input v-model="chatInput" placeholder="输入你的想法，回车发送" :disabled="chatting" @keyup.enter="sendChat" />
              <el-button :loading="chatting" @click="sendChat">发送</el-button>
            </div>
          </div>
        </el-col>
      </el-row>

      <div class="gen-actions">
        <el-radio-group v-model="genDays" :disabled="generating">
          <el-radio-button :value="3">3 天</el-radio-button>
          <el-radio-button :value="7">7 天</el-radio-button>
        </el-radio-group>
        <el-input v-model="note" placeholder="补充要求，如：下周想清淡一点" style="width:260px" maxlength="200" />
        <el-button type="success" :loading="generating" @click="confirmGenerate">
          {{ generating ? 'AI 正在逐天生成…' : `生成 ${genDays} 天方案` }}
        </el-button>
      </div>
      <el-alert v-if="generating" :title="genStatus || 'AI 正在逐天生成方案…'" type="info" :closable="false" style="margin-top:10px" />
    </div>

    <!-- 历史方案切换 -->
    <div v-if="plans.length" class="page-card">
      <span style="margin-right:8px;color:#909399">历史方案：</span>
      <el-select :model-value="current?.id" style="width:260px" @change="(v: number) => openPlan(v)">
        <el-option v-for="p in plans" :key="p.id" :value="p.id" :label="p.title" />
      </el-select>
    </div>

    <!-- 方案内容 -->
    <div v-if="current" class="page-card">
      <div class="plan-toolbar">
        <el-alert :title="current.summary" type="success" :closable="false" class="summary-alert" />
        <el-button :disabled="generating" @click="confirmRegenerate">🔄 重新生成</el-button>
      </div>

      <el-tabs v-model="activeDay">
        <el-tab-pane v-for="day in days" :key="day.day_index" :name="String(day.day_index)" :label="`第${day.day_index}天`">
          <div class="day-head">
            <div>
              <b style="font-size:16px">{{ day.theme || `第${day.day_index}天` }}</b>
              <el-tag size="small" type="info" style="margin-left:8px">{{ day.date }}</el-tag>
              <el-tag size="small" style="margin-left:8px" effect="plain">全日约 {{ day.estimated_calories }} kcal</el-tag>
            </div>
            <div style="display:flex;gap:8px">
              <el-button size="small" :disabled="generating" @click="openEdit(day)">✏️ 编辑这一天</el-button>
              <el-button size="small" type="success" :disabled="generating || importedDays.has(day.id)" @click="importDay(day)">
                {{ importedDays.has(day.id) ? '✓ 已导入日历' : '📥 导入日历' }}
              </el-button>
            </div>
          </div>

          <el-table :data="day.meals" size="default" style="margin-top:10px">
            <el-table-column label="餐次" width="110">
              <template #default="{ row }">{{ MEAL_LABELS[row.meal_type] || row.meal_type }}</template>
            </el-table-column>
            <el-table-column label="内容" min-width="300">
              <template #default="{ row }">
                <div v-for="item in row.items" :key="item.name" class="meal-item">
                  🍽 {{ item.name }} <span class="portion">约{{ item.portion_g }}g</span>
                </div>
              </template>
            </el-table-column>
            <el-table-column label="热量" width="110">
              <template #default="{ row }"><b>{{ row.calories }}</b> kcal</template>
            </el-table-column>
          </el-table>

          <el-card v-if="day.exercise" shadow="never" class="exercise-card">
            <div class="exercise-row">
              <div class="exercise-icon">🏃</div>
              <div style="flex:1">
                <b>{{ day.exercise.type }}</b>
                <span style="color:#909399;margin-left:10px">{{ day.exercise.duration_min }} 分钟 · 消耗约 {{ day.exercise.calories_burned }} kcal</span>
                <div class="tips" v-if="day.exercise.tips">💡 {{ day.exercise.tips }}</div>
              </div>
            </div>
          </el-card>
        </el-tab-pane>
      </el-tabs>
    </div>
    <el-empty v-else-if="!generating" description="还没有方案，勾选偏好或直接和 AI 聊几句，然后生成" />

    <!-- 可视化编辑弹窗 -->
    <el-dialog v-model="editDialog" title="编辑这一天（热量按营养库自动重算）" width="640px">
      <div v-for="(meal, mi) in editForm?.meals || []" :key="mi" class="edit-meal">
        <div class="edit-meal-title">{{ MEAL_LABELS[meal.meal_type] }}</div>
        <div v-for="(item, ii) in meal.items" :key="ii" class="edit-item">
          <el-input v-model="item.name" placeholder="菜名（尽量用常见菜，营养库能匹配）" style="flex:1" />
          <el-input-number v-model="item.portion_g" :min="10" :max="1500" style="width:130px" />
          <span>g</span>
          <el-button text type="danger" @click="meal.items.splice(ii, 1)">删除</el-button>
        </div>
        <el-button text type="primary" @click="addItem(meal)">+ 加一道菜</el-button>
      </div>
      <template #footer>
        <el-button @click="editDialog = false">取消</el-button>
        <el-button type="success" @click="saveEdit">保存修改</el-button>
      </template>
    </el-dialog>

    <!-- 历史对话抽屉：可查看 / 复制 -->
    <el-drawer v-model="chatDrawer" title="历史对话（可查看 / 复制）" size="460px">
      <div v-if="!chatSessions.length" class="chat-empty">暂无历史对话</div>
      <div v-for="(s, si) in chatSessions" :key="si" class="session">
        <div class="session-head">
          <span class="session-time">{{ s.time }}</span>
          <el-button link type="primary" size="small" @click="copySession(s)">复制</el-button>
        </div>
        <div v-for="(m, mi) in s.msgs" :key="mi" class="session-msg" :class="m.role">
          <b>{{ m.role === 'user' ? '我' : 'AI' }}：</b>{{ m.content }}
        </div>
      </div>
      <template #footer>
        <el-button v-if="chatSessions.length" type="danger" plain @click="clearChatHistory">清空全部</el-button>
      </template>
    </el-drawer>
  </div>
</template>

<style scoped>
.sub { color: #909399; font-size: 13px; }
.wizard-box { border: 1px solid #ebeef5; border-radius: 8px; padding: 12px 14px; height: 100%; background: #fafcff; }
.wizard-title { font-weight: 600; color: #409eff; margin-bottom: 8px; }
.chat-title { display: flex; justify-content: space-between; align-items: center; }
.pref-label { font-size: 13px; color: #606266; margin: 10px 0 4px; }

.session { border: 1px solid #ebeef5; border-radius: 8px; padding: 10px 12px; margin-bottom: 12px; }
.session-head { display: flex; justify-content: space-between; align-items: center; margin-bottom: 6px; }
.session-time { color: #909399; font-size: 12px; }
.session-msg { font-size: 13px; line-height: 1.6; margin: 4px 0; white-space: pre-wrap; }
.session-msg.user b { color: #409eff; }
.session-msg.assistant b { color: #67c23a; }

.chat-box { display: flex; flex-direction: column; }
.chat-list { flex: 1; min-height: 180px; max-height: 260px; overflow-y: auto; padding: 4px 2px; }
.chat-empty { color: #c0c4cc; font-size: 13px; line-height: 1.6; }
.chat-row { display: flex; margin-bottom: 8px; }
.chat-row.user { justify-content: flex-end; }
.chat-row.assistant { justify-content: flex-start; }
.bubble { max-width: 85%; padding: 8px 12px; border-radius: 10px; font-size: 13px; line-height: 1.5; white-space: pre-wrap; }
.chat-row.user .bubble { background: #409eff; color: #fff; }
.chat-row.assistant .bubble { background: #f0f2f5; color: #303133; }
.chat-input { display: flex; gap: 8px; margin-top: 8px; }

.gen-actions { display: flex; gap: 8px; align-items: center; justify-content: flex-end; margin-top: 14px; }

.plan-toolbar { display: flex; align-items: center; gap: 8px; margin-bottom: 14px; }
.summary-alert { flex: 1; }

.day-head { display: flex; justify-content: space-between; align-items: center; }
.meal-item { padding: 2px 0; }
.meal-item .portion { color: #909399; font-size: 12px; margin-left: 6px; }
.exercise-card { margin-top: 12px; background: #f0f9eb; border: 1px solid #e1f3d8; }
.exercise-row { display: flex; align-items: center; gap: 14px; }
.exercise-icon { font-size: 30px; }
.tips { color: #909399; font-size: 13px; margin-top: 4px; }
.edit-meal { margin-bottom: 18px; }
.edit-meal-title { font-weight: 600; margin-bottom: 8px; }
.edit-item { display: flex; gap: 8px; align-items: center; margin-bottom: 8px; }
</style>
