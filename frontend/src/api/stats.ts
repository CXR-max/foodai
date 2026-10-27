// 数据统计接口（仪表盘 + 三张图表）
import { get } from './http'

export interface Overview {
  today_calories: number
  daily_target: number | null
  streak_days: number
  points_total: number
  rank: number
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
