// 运动记录接口（跑步 / 快走 / 骑行 / 手动补录）
import { del, get, post } from './http'

export interface TrackPoint {
  lat: number
  lng: number
  t: number
  acc?: number | null
}

export interface Split {
  km: number
  sec: number
  pace_sec_km: number
  partial?: boolean
}

export interface WorkoutItem {
  id: number
  sport_type: string
  source: 'gps' | 'band' | 'manual' | 'course'
  start_at: string | null
  end_at: string | null
  duration_sec: number
  distance_m: number | null
  avg_pace_sec_km: number | null
  best_pace_sec_km: number | null
  calories: number | null
  intensity: 'light' | 'moderate' | 'intense' | null
  met_used: number | null
  avg_hr: number | null
  max_hr: number | null
  splits: Split[]
  burn_detail: {
    base?: number
    factors?: { key: string; label: string; value: number; note: string }[]
    steps?: string[]
    low?: number
    high?: number
    formula?: string
    source?: string
  } | null
  note: string | null
  has_track: boolean
  checkin_id: number | null
  created_at: string | null
}

export interface WorkoutCompare {
  id: number
  start_at: string | null
  distance_m: number | null
  duration_sec: number | null
  avg_pace_sec_km: number | null
  calories: number | null
  diff_distance_m: number
  diff_duration_sec: number
  diff_pace_sec_km: number | null
}

export interface WorkoutTrackStats {
  point_count: number
  avg_accuracy: number | null
  max_gap_sec: number | null
}

export interface WorkoutDetail extends WorkoutItem {
  track: TrackPoint[]
  hr_series: { t: number; bpm: number }[]
  track_stats?: WorkoutTrackStats
  compare?: WorkoutCompare | null
}

export interface WorkoutStats {
  week: { count: number; distance_m: number; duration_sec: number; calories: number }
  month: { count: number; distance_m: number; duration_sec: number; calories: number }
  total: { count: number; distance_m: number; duration_sec: number; calories: number }
  best_pace_sec_km: number | null
  daily: { date: string; distance_m: number }[]
  streak_days: number
}

export interface CreateWorkoutIn {
  sport_type: string
  start_at: string
  end_at?: string | null
  duration_sec: number
  track?: TrackPoint[]
  distance_m?: number | null
  avg_hr?: number | null
  max_hr?: number | null
  hr_series?: { t: number; bpm: number }[] | null
  note?: string
  source?: 'gps' | 'band' | 'manual' | 'course'
  client_id?: string | null
}

/** 保存一次 GPS 运动（后端会重算距离/配速/消耗） */
export const createWorkout = (data: CreateWorkoutIn) =>
  post<WorkoutDetail & { points_awarded: number; message: string }>('/workouts', data)

/** 手动补录（跑步机 / 跳绳 / 忘带手机） */
export const createManualWorkout = (data: {
  sport_type: string
  start_at?: string | null
  duration_sec: number
  distance_m?: number | null
  intensity?: 'light' | 'moderate' | 'intense' | null
  note?: string
  plan_day_id?: number | null
  client_id?: string | null
}) => post<WorkoutDetail & { points_awarded: number; message: string }>('/workouts/manual', data)

/** 运动记录列表（不返回轨迹点） */
export const listWorkouts = (params?: { range?: 'week' | 'month' | 'all'; limit?: number; offset?: number }) =>
  get<{ total: number; items: WorkoutItem[] }>('/workouts', params)

/** 运动统计：本周/本月/累计 + 近 7 天里程 */
export const getWorkoutStats = () => get<WorkoutStats>('/workouts/stats')

/** 单次详情（含轨迹与分段） */
export const getWorkout = (id: number) => get<WorkoutDetail>(`/workouts/${id}`)

export const deleteWorkout = (id: number) => del<{ ok: boolean; message: string }>(`/workouts/${id}`)
