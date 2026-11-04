// 健康打卡接口（日历任务体系）
import { del, get, post } from './http'

export interface CheckInItem {
  id: number
  check_date: string
  check_type: 'diet' | 'exercise'
  task_key: string
  label: string
  status: 'pending' | 'done'
  points_awarded: number
  exercise_type: string | null
  duration_min: number | null
  calories_burned: number | null
  note: string | null
  intensity: 'light' | 'moderate' | 'intense' | null
  met_used: number | null
  burn_detail: { base?: number; factors?: { key: string; label: string; value: number; note: string }[]; steps?: string[]; low?: number; high?: number; formula?: string; source?: string } | null
  plan_day_id: number | null
  detail: any
  /** 打卡后 AI 个性化点评（无 key / 失败时为 null） */
  ai_praise?: string | null
}

/** 个性化消耗预估结果（弹窗展示"这个数怎么来的"） */
export interface BurnPreview {
  exercise_type: string
  duration_min: number
  intensity: 'light' | 'moderate' | 'intense'
  intensity_label: string
  met: number
  met_known: boolean
  weight_kg: number
  base: number
  factors: { key: string; label: string; value: number; note: string }[]
  steps: string[]
  calories_burned: number
  low: number
  high: number
  formula: string
}

export interface DayStat { date: string; total: number; done: number }

/** 单日任务列表（含待完成任务的方案餐详情） */
export const getDay = (date?: string) =>
  get<{ date: string; items: CheckInItem[]; summary: { total: number; done: number; all_done: boolean } }>(
    '/check-ins',
    date ? { date } : undefined,
  )

/** 月历打点数据 */
export const getMonth = (month: string) => get<{ month: string; days: DayStat[] }>('/check-ins', { month })

/** 方案某天导入日历（幂等） */
export const importFromPlan = (planDayId: number) =>
  post<{ created: string[]; skipped: string[]; message: string }>('/check-ins/from-plan', { plan_day_id: planDayId })

/** 完成任务打卡（按完成度给积分） */
export const complete = (id: number) =>
  post<{ points_awarded: number; bonus: number; message: string; ai_praise?: string | null }>(`/check-ins/${id}/complete`)

/** 手动打卡 */
export const manual = (data: { check_type: 'diet' | 'exercise'; exercise_type?: string; duration_min?: number; intensity?: 'light' | 'moderate' | 'intense'; calories_burned?: number; note?: string; check_date?: string }) =>
  post<CheckInItem>('/check-ins', data)

export const remove = (id: number) => del(`/check-ins/${id}`)

/** 个性化运动消耗预估：按档案（性别/年龄/体脂/体能）修正，返回区间与逐层明细 */
export const getBurnPreview = (exercise_type: string, duration_min: number, intensity: 'light' | 'moderate' | 'intense' = 'moderate') =>
  get<BurnPreview>('/check-ins/burn-preview', { exercise_type, duration_min, intensity })
