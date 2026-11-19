// 智能设备（手环）相关接口
import { get, post, patch, del } from './http'

/** 单日指标 */
export interface DeviceMetric {
  metric_date: string
  steps: number | null
  distance_km: number | null
  calories: number | null
  active_minutes: number | null
  floors: number | null
  hr_avg: number | null
  hr_min: number | null
  hr_max: number | null
  hr_resting: number | null
  sleep_minutes: number | null
  deep_sleep_minutes: number | null
  light_sleep_minutes: number | null
  rem_sleep_minutes: number | null
  awake_minutes: number | null
  spo2: number | null
  stress: number | null
  source: string
}

export interface DeviceItem {
  id: number
  name: string
  model: string | null
  platform: string
  mac: string | null
  ble_name: string | null
  battery: number | null
  firmware: string | null
  status: string
  bind_source: string
  last_sync_at: string | null
  last_sync_source: string | null
  created_at: string | null
}

export interface DeviceListResult {
  items: (DeviceItem & { today: DeviceMetric | null })[]
  today: { steps: number; distance_km: number; calories: number; active_minutes: number; hr_avg: number | null; sleep_minutes: number | null; source?: string; has_data: boolean }
  step_goal: number
  last_metric_date: string | null
}

/** 设备列表（含今日数据） */
export const listDevices = () => get<DeviceListResult>('/devices')

/** 绑定设备（蓝牙连上后登记；同 MAC 幂等） */
export const bindDevice = (data: { name: string; model?: string; platform?: string; mac?: string; ble_name?: string; battery?: number; firmware?: string; bind_source?: string }) =>
  post<{ device: DeviceItem; reused: boolean }>('/devices', data)

/** 修改设备信息 */
export const updateDevice = (id: number, data: { name?: string; battery?: number }) =>
  patch<DeviceItem>(`/devices/${id}`, data)

/** 解绑设备（同时删除它的指标） */
export const removeDevice = (id: number) => del<{ ok: boolean }>(`/devices/${id}`)

/** 上报指标：单条对象或 { metrics: [...] } 批量 */
export const syncMetrics = (id: number, payload: Partial<DeviceMetric> | { metrics: Partial<DeviceMetric>[]; battery?: number }) =>
  post<{ synced: number; dates: string[] }>(`/devices/${id}/sync`, payload as any)

/** 近 N 天每日汇总（仪表盘/设备页头部） */
export const deviceSummary = (days = 7) =>
  get<{
    today: Record<string, any>
    series: ({ date: string; steps: number; distance_km: number; calories: number; active_minutes: number; hr_avg: number | null; sleep_minutes: number | null; has_data: boolean })[]
    step_goal: number
    days_with_data: number
    avg_steps: number
    total_steps: number
    avg_sleep_minutes: number | null
    avg_hr: number | null
  }>('/devices/summary', { days })

// ---------------------------------------------------------------- 今日概览三环
export interface SleepDetail {
  date: string
  total: number
  deep: number
  light: number
  rem: number
  awake: number
  deep_pct: number
  comment: string
}

export interface RingsResult {
  date: string
  intake: { value: number; target: number; unit: string }
  burn: { value: number; target: number; bmr: number | null; unit: string }
  sleep: { value: number | null; target: number; unit: string }
  sleep_detail: SleepDetail | null
}

/** 今日概览三环（摄入/消耗/睡眠）+ 最近一晚睡眠结构 */
export const deviceRings = () => get<RingsResult>('/devices/rings')

/** 某设备历史指标 */
export const deviceMetrics = (id: number, days = 30) =>
  get<{ device: DeviceItem; items: DeviceMetric[] }>(`/devices/${id}/metrics`, { days })

/** 上传导出文件解析预览（不入库） */
export const importPreview = (file: File) => {
  const fd = new FormData()
  fd.append('file', file)
  return post<{ days: number; start: string | null; end: string | null; fields: string[]; preview: any[]; rows: number }>(
    '/devices/import/preview', fd)
}

/** 上传导出文件并入库 */
export const importFile = (file: File, deviceId?: number) => {
  const fd = new FormData()
  fd.append('file', file)
  return post<{ imported: number; device: DeviceItem; start: string; end: string }>(
    '/devices/import', fd, { params: deviceId ? { device_id: deviceId } : undefined })
}

/** 生成演示手环数据（source=demo，界面会标注） */
export const simulateData = (days = 14, deviceId?: number) =>
  post<{ generated: number; device: DeviceItem }>('/devices/simulate', {},
    { params: { days, ...(deviceId ? { device_id: deviceId } : {}) } })

/** 把当天步数转成运动打卡 */
export const stepsToCheckin = (id: number, data?: { metric_date?: string; duration_min?: number; exercise_type?: string }) =>
  post<{ checkin_id: number; calories_burned: number; duration_min: number; steps: number; points_awarded: number }>(
    `/devices/${id}/to-checkin`, data || {})

// ---------------------------------------------------------------- 实时
export interface LiveSample { t: string; steps: number; hr: number | null }

export interface LiveToday {
  date: string
  steps: number
  steps_goal: number
  hr: number | null
  calories: number
  sleep_minutes: number | null
  hr_resting: number | null
  anchor_steps: number
  anchor_source: string
  is_demo: boolean
}

export interface LiveResult {
  enabled: boolean
  interval: number
  device: DeviceItem | null
  today: LiveToday | null
  samples: LiveSample[]
  updated_at: string | null
  mode: string
  auto_sync?: boolean
}

/** 当前设备的实时数据（后台每 N 秒采一条，页面轮询这个接口） */
export const deviceLive = (deviceId?: number) =>
  get<LiveResult>('/devices/live', deviceId ? { device_id: deviceId } : undefined)

/** 自动同步开关（只影响这一台设备） */
export const setAutoSync = (enabled: boolean, deviceId?: number) =>
  post<{ device_id: number; auto_sync: boolean }>('/devices/auto-sync',
    { enabled, device_id: deviceId })

/** 后台同步的运行状态（间隔、投递目录） */
export const liveStatus = () => get<Record<string, any>>('/devices/live/status')

/** 立即采一条，不用等后台那一分钟 */
export const liveTick = () => post<{ sampled: number; at: string }>('/devices/live/tick', {})
