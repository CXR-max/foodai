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
export interface NutritionTotals {
  calories: number
  protein_g: number
  fat_g: number
  carb_g: number
  fiber_g: number
  sodium_mg: number
  sugar_g: number
}

export interface DietItem {
  key: 'calorie' | 'protein' | 'fat' | 'carb' | 'fiber' | 'sodium' | 'sugar'
  label: string
  unit: string
  value: number | null
  actual?: number | null
  min: number | null
  max: number | null
  range_text: string
  status: 'in_range' | 'under' | 'over' | 'unknown' | 'no_data'
  status_text: string
  delta?: number | null
}

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
  status: 'in_range' | 'low' | 'high' | 'under' | 'over' | 'no_data' | 'in_progress'
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
  /** 饮食合并判定：热量 + 蛋白一行结论（蛋白不单独打卡、不弹窗） */
  diet?: {
    status:
      | 'ok' | 'in_progress' | 'protein_low' | 'protein_over'
      | 'calorie_over' | 'calorie_under' | 'plan_over' | 'extra_over' | 'no_data'
    status_text: string
    factor: number | null
    ok: boolean
    line: string
    hint: string
    /** 归因：plan=方案排错了 / portion=份量问题 / extra=额外加餐 / intake=没吃完 / user=用户选择 */
    attribution?: 'plan' | 'portion' | 'extra' | 'intake' | 'user' | null
    attribution_text?: string
    /** 份量偏离：方案内实际 vs 方案基线。份量吃多要让下一餐紧一点，不是改方案 */
    portion?: {
      delta_kcal: number | null
      baseline_kcal: number | null
      plan_kcal: number | null
      significant: boolean
      direction: 'over' | 'under' | 'none'
    } | null
    /** 七项逐项（热量/蛋白/脂肪/碳水/纤维/钠/糖） */
    items?: DietItem[]
    /** 结构项折叠展示，不卡打卡 */
    structure?: DietItem[]
    calorie?: MetricBand | null
    protein?: MetricBand | null
  } | null
  intake: {
    calories: number
    protein_g: number
    fat_g: number
    carb_g: number
    records: number
    calorie_status: 'under' | 'in_range' | 'over' | null
    /** 已打卡但没记食物的餐次，按方案菜品折算 */
    estimated?: boolean
    estimated_meals?: string[]
    estimated_labels?: string[]
    note?: string | null
    /** 方案内摄入（打卡折算 + 归属为 plan 的记录） */
    plan?: NutritionTotals | null
    /** 方案外摄入（标了「额外」的记录） */
    extra?: NutritionTotals | null
    extra_records?: number
    /** 有记录没写餐次，分不清归属 */
    attribution_uncertain?: boolean
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
  /** 动态调整卡片：系统这轮自动改了什么、为什么（每条可单独撤销） */
  adapt?: {
    applied: { day: string; reasons: { field: string; action: string; reason: string }[] }[]
    state?: Record<string, any>
  }
}

/** 撤销某天的动态调整（恢复调整前快照） */
export const revertAdapt = (date: string) =>
  post<{ ok: boolean; day: string; reverted: boolean }>('/day/adapt/revert', { date })

export const getDaySummary = (date?: string) =>
  get<DaySummary>('/day/summary', date ? { date } : undefined)
