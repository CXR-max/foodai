// 偏离处置 / 当日模式 / 自学闭环接口
//
// 这一层的意义：判断「今天该不该下结论、能做点什么」全部由后端算，
// 前端只负责把选项画出来、把用户点的那个送回去。规则不写在前端，
// 否则同一天在不同页面会给出不同建议。
import { get, post } from './http'

/** 处置选项：已经是算好的动作（"把晚餐少排 380 kcal"而不是"建议减少摄入"） */
export interface DispositionOption {
  kind: string
  label: string
  detail: string
  reason: string
  recommended: boolean
  safe: boolean
  safety: string[]
  payload: Record<string, any>
  window?: string
  window_label?: string
}

export interface Deviation {
  id: number
  kind: 'over' | 'under'
  nutrient: string
  label: string
  actual: number | null
  limit: number | null
  delta: number | null
  attribution: string | null
  severity: 'soft' | 'hard' | null
  status: string
}

/** 结算时点：前端据此决定文案（上午不判、深夜只调明天） */
export interface Settlement {
  point: 'pending' | 'open' | 'midday' | 'evening' | 'final'
  label: string
  settled: boolean
  can_adjust_today: boolean
  judge_under: boolean
  message: string
}

export interface DayModeEffect {
  mode: string
  label: string
  note_text: string
  calorie_max_pct: number
  relaxed_keys: string[]
  pause_scoring: boolean
  no_disposition: boolean
  recorded: boolean
}

export interface DayState {
  date: string
  point: Settlement
  mode: DayModeEffect
  deviations: { deviation: Deviation; options: DispositionOption[] }[]
  has_open: boolean
  targets_note: string | null
}

export interface SelfReview {
  trend: { direction: string; streak: number; days: string[]; nutrients: string[] }
  proposal: {
    old_target: number
    new_target: number
    bmr: number | null
    reason: string
    message: string
    estimate: { method: string; tdee: number; avg_intake: number; weight_delta_kg?: number }
  } | null
  adoption: {
    days: number
    proposed: number
    applied: number
    declined: number
    rate: number | null
    by_kind: Record<string, { proposed: number; applied: number; rate: number | null }>
    note: string | null
  }
  exceptions: { days: number; count: number; ratio: number; note: string | null }
  kinds: Record<string, { tried: number; resolved: number; rate: number | null }>
  lines: string[]
  ok: boolean
}

/** 当天完整状态：结算时点 + 例外模式 + 待处置偏离 + 每条能怎么做 */
export const getDayState = (date?: string) => get<DayState>('/day/state', date ? { date } : undefined)

/** 设置当天模式：social / training / period / sick / travel / normal */
export const setDayMode = (mode: string, opts: { date?: string; note?: string } = {}) =>
  post<{ ok: boolean; effects: DayModeEffect; message: string }>('/day/mode', {
    mode,
    date: opts.date,
    note: opts.note,
  })

/** 执行一条处置 */
export const applyDisposition = (
  deviationId: number,
  kind: string,
  payload: Record<string, any> = {},
) =>
  post<{ ok: boolean; kind: string; detail: string[]; adjustment_id: number }>('/day/disposition', {
    deviation_id: deviationId,
    kind,
    payload,
  })

/** 系统自省：趋势 / 采纳率 / 例外占比 / 哪些手段对这个人有效 */
export const getSelfReview = () => get<SelfReview>('/day/self-review')

/** 连续偏离后重算目标（不再压摄入） */
export const applyRecalc = () =>
  post<{ ok: boolean; message: string; updated_days?: any[] }>('/day/recalc')

export const MODE_OPTIONS = [
  { value: 'social', label: '聚餐', desc: '热量上限放宽 15%，钠糖脂肪只提示' },
  { value: 'training', label: '加练', desc: '热量上限放宽 10%，蛋白下限照旧' },
  { value: 'period', label: '生理期', desc: '不评等级、不推处置，想吃甜的正常' },
  { value: 'sick', label: '生病', desc: '只看吃没吃、喝够水，配比不判' },
  { value: 'travel', label: '出差', desc: '纤维/钠只提示，热量蛋白照看' },
]
