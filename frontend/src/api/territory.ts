// 领地跑接口
import { get, post } from './http'

export interface TerritoryMapOwner {
  nickname: string
  color: string
  cells: number
  is_me: boolean
  is_friend: boolean
}

export interface TerritoryMapData {
  cell_m: number
  cells: [string, number, number, number][]
  /** 无主地：[cell_key, 空置了多久(小时)] */
  neutral: [string, number][]
  /** 48h 内可夺回的地：[cell_key, 剩余秒数] */
  recapture: [string, number][]
  owners: Record<string, TerritoryMapOwner>
  count: number
}

export interface TerritoryTier {
  level: number
  name: string
  area_m2: number
  next_name: string
  next_area_m2: number
  /** 0–1，朝下一段位的进度 */
  progress: number
  to_next_m2: number
}

export interface TerritoryMe {
  user_id: number
  enabled: boolean
  anonymous: boolean
  total_cells: number
  area_m2: number
  largest_block: number
  largest_block_area_m2: number
  rank: number
  rank_of: number
  ahead_name: string
  ahead_gap_m2: number
  vitality_avg: number
  days_left: number
  tier: TerritoryTier
  recapture_cells: number
  today_new: number
  week_new: number
  center: [number, number] | null
  latest_track: [number, number][]
  color: string
  cell_m: number
  recent_events: TerritoryEvent[]
}

export interface TerritoryEvent {
  id: number
  type: string
  cell_key: string
  actor: string
  actor_id: number | null
  victim: string
  victim_id: number | null
  meta: { cells?: number; area_m2?: number; [key: string]: unknown }
  created_at: string | null
}

export interface TerritoryVictim {
  user_id: number
  nickname: string
  cells: number
  area_m2: number
}

export interface TerritoryRun {
  run_id: number
  workout_id: number
  user_id: number
  valid: boolean
  reason: string | null
  cells_new: number
  cells_filled: number
  cells_attacked: number
  cells_stolen: number
  cells_repaired: number
  cells_cut: number
  area_new_m2: number
  area_cut_m2: number
  loop: boolean
  cells: string[]
  /* 以下字段只有 /territory/runs/:id 结算接口返回 */
  area_total_m2?: number
  cells_total?: number
  victims?: TerritoryVictim[]
  tier?: TerritoryTier
  rank?: number
  rank_of?: number
  ahead_name?: string
  ahead_gap_m2?: number
  recapture_cells?: number
}

export interface TerritoryOwnerRun {
  id: number
  start_at: string | null
  sport_type: string
  distance_m: number
  duration_sec: number
  avg_pace_sec_km: number | null
  calories: number
  area_new_m2: number
  area_cut_m2: number
  loop: boolean
}

export interface TerritoryOwnerEvent {
  id: number
  type: string
  actor: string
  actor_id: number | null
  victim: string
  victim_id: number | null
  area_m2: number
  created_at: string | null
}

export interface TerritoryChallengeInfo {
  id: number
  /** outgoing = 我下的战书；incoming = TA 向我下的 */
  direction: 'outgoing' | 'incoming'
  status: 'pending' | 'win' | 'lose'
  other_id: number
  other_name: string
  seconds_left: number
  accepted: boolean
  created_at: string | null
  expires_at: string | null
}

export interface TerritoryOwnerDetail {
  user_id: number
  nickname: string
  color: string
  area_m2: number
  largest_block_area_m2: number
  rank: number
  is_me: boolean
  is_friend: boolean
  can_attack: boolean
  /** 领地格子（key + 活力），详情页用来画形状剪影 */
  cells: [string, number][]
  cells_total: number
  vitality_avg: number
  days_left: number
  first_claimed_at: string | null
  held_days: number
  record: { wins: number; losses: number }
  challenge: TerritoryChallengeInfo | null
  tier: TerritoryTier
  runs: TerritoryOwnerRun[]
  events: TerritoryOwnerEvent[]
}

export type TerritoryScope = 'all' | 'friends' | 'me'

export const getTerritoryMap = (params: {
  min_lat: number; max_lat: number; min_lng: number; max_lng: number
  scope?: TerritoryScope
  owner_id?: number
}) => get<TerritoryMapData>('/territory/map', params)

export const getTerritoryMe = () => get<TerritoryMe>('/territory/me')

export const updateTerritoryPrefs = (data: {
  enabled?: boolean
  anonymous?: boolean
  home_lat?: number
  home_lng?: number
  home_radius_m?: number
}) => post<{ ok: boolean; enabled: boolean; anonymous: boolean }>('/territory/prefs', data)

export const getTerritoryRun = (workoutId: number) =>
  get<TerritoryRun>(`/territory/runs/${workoutId}`)

export const getTerritoryOwner = (ownerId: number) =>
  get<TerritoryOwnerDetail>(`/territory/owners/${ownerId}`)

export const sendChallenge = (targetId: number) =>
  post<TerritoryChallengeInfo>('/territory/challenges', { target_id: targetId })

export const acceptChallenge = (challengeId: number) =>
  post<TerritoryChallengeInfo>(`/territory/challenges/${challengeId}/accept`, {})

export const getTerritoryLeaderboard = () =>
  get<{ items: { user_id: number; nickname: string; cells: number; area_m2: number; color: string }[] }>(
    '/territory/leaderboard',
  )
