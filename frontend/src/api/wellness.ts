// 睡眠 / 饮水 / 每日汇总接口
import { del, get, post } from './http'
import type { PlanTargets } from './plan'

export interface SleepRecord {
  id: number
  sleep_date: string
  bedtime: string | null
  wake_at: string | null
  duration_min: number | null
  duration_h: number | null
  deep_min?: number | null
  rem_min?: number | null
  quality_score?: number | null
  source: 'manual' | 'device' | 'manual_override'
  note?: string | null
}

export interface SleepSaveIn {
  sleep_date?: string
  bedtime?: string
  wake_at?: string
  duration_min?: number
  quality_score?: number
  note?: string
  source?: 'manual' | 'device'
}

export interface SleepSaveOut {
  sleep: SleepRecord
  target_min_h: number
  checkin: {
    achieved: boolean
    required_min: number
    duration_min: number
    checkin_id?: number | null
  }
}

export const getSleep = (date?: string) =>
  get<{ date: string; sleep: SleepRecord | null }>('/sleep', date ? { date } : undefined)

export const getSleepHistory = (days = 7) =>
  get<{ days: number; records: SleepRecord[] }>('/sleep', { days })

export const saveSleep = (data: SleepSaveIn) => post<SleepSaveOut>('/sleep', data)

export const deleteSleep = (id: number) => del(`/sleep/${id}`)

export interface WaterState {
  date: string
  total_ml: number
  target_ml: number
  achieved: boolean
  records?: { id: number; ml: number; created_at: string | null }[]
}

export const getWater = (date?: string) =>
  get<WaterState>('/water', date ? { date } : undefined)

export const addWater = (ml: number, date?: string) =>
  post<WaterState & { checkin_id?: number | null }>('/water', { ml, ...(date ? { log_date: date } : {}) })

export const deleteWater = (id: number) => del(`/water/${id}`)

/** 单个指标的「确切区间 + 浮动容忍 + 实际值 + 判定」，由后端 metric_bands 统一给 */
export interface MetricBand {
  key: 'calorie' | 'protein' | 'water' | 'sleep' | 'exercise' | 'steps'
  label: string
  unit: string
  min: number
  max: number
  target: number | null
  soft_min: number
  soft_max: number
  range_text: string
  actual: number | null
  status: 'in_range' | 'low' | 'high' | 'under' | 'over' | 'no_data'
  status_text: string
  hint: string
  factor: number | null
}

export interface DaySummary {
  date: string
  has_plan: boolean
  plan?: {
    id: number | null
    title: string | null
    day_index: number | null
    theme: string | null
    meals: any[]
    exercises: any[]
    sleep: any
    water_ml: number | null
  }
  targets: PlanTargets
  bands?: MetricBand[]
  band_note?: string | null
  intake: {
    calories: number
    protein_g: number
    fat_g: number
    carb_g: number
    records: number
    calorie_status: 'under' | 'in_range' | 'over' | null
  }
  burn: { exercise_kcal: number; exercise_min: number; times: number }
  water: {
    total_ml: number
    target_ml: number
    max_ml?: number | null
    pct: number | null
    achieved: boolean
  }
  sleep: SleepRecord | null
  tasks: { total: number; done: number; pct: number }
  progress: { calorie_pct: number | null; protein_pct: number | null; burn_pct: number | null }
}

export const getDaySummary = (date?: string) =>
  get<DaySummary>('/day/summary', date ? { date } : undefined)
