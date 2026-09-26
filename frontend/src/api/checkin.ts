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
  plan_day_id: number | null
  detail: any
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
  post<{ points_awarded: number; bonus: number; message: string }>(`/check-ins/${id}/complete`)

/** 手动打卡 */
export const manual = (data: { check_type: 'diet' | 'exercise'; exercise_type?: string; duration_min?: number; calories_burned?: number; note?: string; check_date?: string }) =>
  post<CheckInItem>('/check-ins', data)

export const remove = (id: number) => del(`/check-ins/${id}`)
