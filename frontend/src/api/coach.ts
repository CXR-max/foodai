// AI 教练对话：每次请求都带上当天最新数据（后端组 prompt）
import { post } from './http'

export interface ChatMessage {
  role: 'user' | 'assistant'
  content: string
}

export const coachChat = (messages: ChatMessage[]) =>
  post<{ reply: string; source: string; context_date: string }>('/coach/chat', { messages })
