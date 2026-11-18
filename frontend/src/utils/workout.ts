// 运动数据格式化（三个页面共用，避免各写一套导致口径不一致）

const EARTH_R_M = 6371000

/** 两点球面距离（米），前后端同一套公式 */
export function haversineM(
  a: { lat: number; lng: number },
  b: { lat: number; lng: number },
): number {
  const p1 = (a.lat * Math.PI) / 180
  const p2 = (b.lat * Math.PI) / 180
  const dp = p2 - p1
  const dl = ((b.lng - a.lng) * Math.PI) / 180
  const h = Math.sin(dp / 2) ** 2 + Math.cos(p1) * Math.cos(p2) * Math.sin(dl / 2) ** 2
  return 2 * EARTH_R_M * Math.asin(Math.sqrt(h))
}

/** 秒 → 05:32 / 1:02:33 */
export function fmtDuration(sec: number | null | undefined): string {
  const s = Math.max(0, Math.round(sec || 0))
  const h = Math.floor(s / 3600)
  const m = Math.floor((s % 3600) / 60)
  const ss = s % 60
  const mm = String(m).padStart(h > 0 ? 2 : 1, '0')
  const pad = String(ss).padStart(2, '0')
  return h > 0 ? `${h}:${String(m).padStart(2, '0')}:${pad}` : `${mm}:${pad}`
}

/** 秒/公里 → 5'32" ；无配速返回 --'--" */
export function fmtPace(secPerKm: number | null | undefined): string {
  if (!secPerKm || secPerKm <= 0 || !isFinite(secPerKm)) return "--'--\""
  const s = Math.round(secPerKm)
  const m = Math.floor(s / 60)
  const ss = String(s % 60).padStart(2, '0')
  return `${m}'${ss}"`
}

/** 均速：km/h，保留一位小数 */
export function fmtSpeed(kmh: number | null | undefined): string {
  if (!kmh || !isFinite(kmh) || kmh <= 0) return '--'
  return kmh.toFixed(1)
}

/** 配速（秒/km）→ 均速（km/h） */
export function paceToSpeed(secPerKm: number | null | undefined): number | null {
  if (!secPerKm || secPerKm <= 0) return null
  return 3600 / secPerKm
}

/** 均速（km/h）→ 配速（秒/km） */
export function speedToPace(kmh: number | null | undefined): number | null {
  if (!kmh || kmh <= 0) return null
  return 3600 / kmh
}

/** 米 → 1.24 km / 860 m */
export function fmtDistance(m: number | null | undefined): { value: string; unit: string } {
  const v = Math.max(0, m || 0)
  if (v >= 1000) return { value: (v / 1000).toFixed(2), unit: 'km' }
  return { value: String(Math.round(v)), unit: 'm' }
}

/** 配速快慢 → 颜色（快=红 慢=绿，符合国内运动 App 习惯） */
export function paceColor(pace: number, fastest: number, slowest: number): string {
  // 分段配速差小于 3 秒时视为同一档，避免整页分段出现“红绿乱跳”
  if (!pace || slowest <= fastest || slowest - fastest < 3) return '#67c23a'
  const r = (pace - fastest) / (slowest - fastest)   // 0 最快，1 最慢
  if (r < 0.33) return '#f56c6c'
  if (r < 0.66) return '#e6a23c'
  return '#67c23a'
}

/** 相对时间：今天 10:23 / 昨天 / 11-09 */
export function fmtWhen(iso: string | null | undefined): string {
  if (!iso) return ''
  const d = new Date(iso)
  const now = new Date()
  const sameDay = d.toDateString() === now.toDateString()
  const hm = `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`
  if (sameDay) return `今天 ${hm}`
  const y = new Date(now)
  y.setDate(now.getDate() - 1)
  if (d.toDateString() === y.toDateString()) return `昨天 ${hm}`
  return `${d.getMonth() + 1}-${String(d.getDate()).padStart(2, '0')} ${hm}`
}

/** 运动类型 → 图标 */
export const SPORT_ICON: Record<string, string> = {
  跑步: '🏃', 快走: '🚶', 骑行: '🚴', 跑步机: '🏃', 跳绳: '🤸',
  瑜伽: '🧘', 拉伸: '🤸', 八段锦: '🧘', 呼吸: '🌬️',
}
export const sportIcon = (t: string) => SPORT_ICON[t] || '🏃'

/** 骑行看均速，跑步/快走看配速 */
export const isRide = (sport: string) => sport.includes('骑')
