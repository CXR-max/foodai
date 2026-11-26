// 数据统计接口（仪表盘 + 三张图表）
import { get } from './http'

export interface Overview {
  today_calories: number
  daily_target: number | null
  streak_days: number
  points_total: number
  rank: number
  today_burn: number
  week_avg_intake: number
  week_burn: number
  today_tasks: { total: number; done: number }
  today_checked: { diet: boolean; exercise: boolean }
}

export const getOverview = () => get<Overview>('/stats/overview')

export const getCaloriesTrend = (days = 14) =>
  get<{ date: string; calories: number }[]>('/stats/calories', { days })

export const getWeightTrend = (days = 90) =>
  get<{ date: string; weight_kg: number }[]>('/stats/weight', { days })

export const getNutrition = (days = 7) =>
  get<{ days_with_data: number; avg_protein_g: number; avg_fat_g: number; avg_carb_g: number }>('/stats/nutrition', { days })

/** 趋势聚合：range=week（近7天）| month（近30天） */
export interface TrendPoint {
  date: string
  calories: number
  target: number | null
  protein_g: number
  fat_g: number
  carb_g: number
  burn: number
  weight_kg: number | null
}
export interface TrendsResult {
  range: 'week' | 'month'
  days: number
  daily_target: number | null
  recorded_days: number
  points: TrendPoint[]
}
export const getTrends = (range: 'week' | 'month' = 'week') =>
  get<TrendsResult>('/stats/trends', { range })

/** AI 健康周报 */
export interface WeeklyReport {
  summary: string
  score: number
  highlights: string[]
  suggestions: string[]
  source: string
  week_stats: {
    start: string; end: string; days_with_records: number
    avg_intake: number; target: number | null
    total_burn: number; exercise_times: number
    checkin_days: number; streak_days: number
    weight_change: number | null
  }
}
export const getWeeklyReport = () => get<WeeklyReport>('/stats/weekly-report')

/** 能量天平（融合）：摄入 vs 总消耗；消耗 = 基础代谢 + 手动运动打卡 + 手环活动 */
export interface EnergyResult {
  date: string
  intake: { value: number; target: number | null; unit: string }
  burn: {
    total: number; bmr: number; checkin: number; band: number
    activity: number; unit: string
  }
  gap: number
  verdict: string
  has_intake: boolean
  has_checkin: boolean
  has_device: boolean
  band: { source: string | null; steps: number; sleep_h: number | null }
}
export const getEnergy = () => get<EnergyResult>('/stats/energy')
