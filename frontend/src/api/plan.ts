// 养生方案接口
import { get, post, put } from './http'

export interface PlanMealItem { name: string; portion_g: number }
export interface PlanMeal { meal_type: string; name: string; items: PlanMealItem[]; calories: number }
export interface PlanExercise { type: string; duration_min: number; calories_burned: number; tips: string }
export interface PlanDay {
  id: number
  day_index: number
  date: string
  theme: string
  meals: PlanMeal[]
  exercise: PlanExercise | null
  estimated_calories: number
}
export interface Plan {
  id: number
  title: string
  status: string
  summary: string
  daily_target_calories: number
  provider: string
  days?: PlanDay[]
}

export interface ChatMsg { role: 'user' | 'assistant'; content: string }

export const getPlans = () => get<Plan[]>('/plans')

export const getPlan = (id: number) => get<Plan>(`/plans/${id}`)

/** 生成方案（LLM 较慢，单独 120s 超时）；偏好面板 + 聊天文本合并提交 */
export const generatePlan = (data: {
  days?: number
  note?: string
  taste_preferences?: string[]
  dietary_restrictions?: string[]
  exercise_preferences?: string[]
  chat_text?: string
}) => post<Plan>('/plans/generate', data, { timeout: 120000 })

export interface StreamHandlers {
  onDay: (day: PlanDay) => void
  onDone: (r: { plan_id: number; title: string; summary: string; daily_target_calories: number; points_awarded: number }) => void
  onError: (message: string) => void
}

/** 逐天流式生成方案（SSE）：每生成一天回调一次，全部完成后回调 onDone */
export async function generatePlanStream(
  data: {
    days?: number
    note?: string
    taste_preferences?: string[]
    dietary_restrictions?: string[]
    exercise_preferences?: string[]
    chat_text?: string
  },
  handlers: StreamHandlers,
): Promise<void> {
  const token = localStorage.getItem('token')
  const resp = await fetch('/api/plans/generate-stream', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    },
    body: JSON.stringify(data),
  })
  if (!resp.ok || !resp.body) {
    let msg = '生成失败，请稍后再试'
    try {
      const j = await resp.json()
      if (j?.detail) msg = typeof j.detail === 'string' ? j.detail : msg
    } catch { /* ignore */ }
    handlers.onError(msg)
    return
  }
  const reader = resp.body.getReader()
  const decoder = new TextDecoder()
  let buf = ''
  for (;;) {
    const { value, done } = await reader.read()
    if (done) break
    buf += decoder.decode(value, { stream: true })
    let idx: number
    while ((idx = buf.indexOf('\n\n')) >= 0) {
      const raw = buf.slice(0, idx)
      buf = buf.slice(idx + 2)
      let ev = 'message'
      let dataStr = ''
      for (const line of raw.split('\n')) {
        if (line.startsWith('event:')) ev = line.slice(6).trim()
        else if (line.startsWith('data:')) dataStr += line.slice(5).trim()
      }
      if (!dataStr) continue
      let payload: any
      try { payload = JSON.parse(dataStr) } catch { continue }
      if (ev === 'day') handlers.onDay(payload as PlanDay)
      else if (ev === 'done') handlers.onDone(payload)
      else if (ev === 'error') handlers.onError(payload.message || '生成失败')
    }
  }
}

/** 生成方案前的纯文字沟通（历史消息由前端携带） */
export const chatPlan = (messages: ChatMsg[]) =>
  post<{ reply: string }>('/plans/chat', { messages }, { timeout: 60000 })

/** 流式对话（SSE）：逐块回调文本，体感更快 */
export async function chatPlanStream(
  messages: ChatMsg[],
  handlers: { onDelta: (t: string) => void; onDone: () => void; onError: (m: string) => void },
): Promise<void> {
  const token = localStorage.getItem('token')
  const resp = await fetch('/api/plans/chat-stream', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    },
    body: JSON.stringify({ messages }),
  })
  if (!resp.ok || !resp.body) {
    handlers.onError('对话失败，请稍后再试')
    return
  }
  const reader = resp.body.getReader()
  const decoder = new TextDecoder()
  let buf = ''
  for (;;) {
    const { value, done } = await reader.read()
    if (done) break
    buf += decoder.decode(value, { stream: true })
    let idx: number
    while ((idx = buf.indexOf('\n\n')) >= 0) {
      const raw = buf.slice(0, idx)
      buf = buf.slice(idx + 2)
      let ev = 'message'
      let dataStr = ''
      for (const line of raw.split('\n')) {
        if (line.startsWith('event:')) ev = line.slice(6).trim()
        else if (line.startsWith('data:')) dataStr += line.slice(5).trim()
      }
      if (!dataStr) continue
      let payload: any
      try { payload = JSON.parse(dataStr) } catch { continue }
      if (ev === 'delta') handlers.onDelta(payload.t || '')
      else if (ev === 'done') handlers.onDone()
      else if (ev === 'error') handlers.onError(payload.message || '对话失败')
    }
  }
}

/** 方案"文字微调"：模型基于当前 JSON 原地改，其余天保留 */
export const revisePlan = (id: number, data: { instruction: string; day_index?: number }) =>
  post<Plan>(`/plans/${id}/revise`, data, { timeout: 120000 })

/** 编辑方案某一天（meals/exercise 可改，热量后端自动重算） */
export const updatePlanDay = (
  planId: number,
  dayIndex: number,
  data: { meals?: PlanMeal[]; exercise?: PlanExercise; theme?: string },
) => put<PlanDay>(`/plans/${planId}/days/${dayIndex}`, data)

export const archivePlan = (id: number) => put(`/plans/${id}/archive`)
