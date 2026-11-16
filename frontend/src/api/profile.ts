// 健康档案接口
import { get, post, put } from './http'

/** 热量超限默认处理偏好 */
export type OverLimitPolicy = 'ask' | 'portion' | 'exercise'

export interface Profile {
  height_cm: number | null
  weight_kg: number | null
  body_fat_pct: number | null
  age: number | null
  gender: string | null
  activity_level: string
  goal: string
  target_weight_kg: number | null
  daily_calorie_target: number | null
  taste_preferences: string[]
  dietary_restrictions: string[]
  exercise_preferences: string[]
  sleep_time?: string | null
  wake_time?: string | null
  water_target_ml?: number | null
  /** 热量超限默认处理：ask=手动选择（默认）/ portion=缩份量 / exercise=加运动 */
  over_limit_policy?: OverLimitPolicy
  bmi: number | null
  bmi_level: string
  bmr: number | null
  nickname: string
  profile_completed?: boolean
}

export const getProfile = () => get<Profile>('/profile')

export const saveProfile = (data: Partial<Profile>) => put<Profile>('/profile', data)

export const addWeight = (data: { weight_kg: number; note?: string }) =>
  post<{ points_awarded: number }>('/profile/weights', data)

export const getWeights = (days = 90) =>
  get<{ weight_kg: number; recorded_date: string }[]>('/profile/weights', { days })
