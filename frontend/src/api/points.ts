// 积分接口：流水 / 排行榜 / 我的排名
import { get } from './http'

export interface PointsLog { id: number; action: string; points: number; description: string; created_at: string }
export interface RankItem { rank: number; user_id: number; nickname: string; points_total: number; is_me: boolean }

export const getLogs = (page = 1) => get<{ total: number; items: PointsLog[] }>('/points/logs', { page })

export const getLeaderboard = (limit = 20) => get<RankItem[]>('/points/leaderboard', { limit })

export const getMyRank = () => get<{ points_total: number; rank: number; nickname: string }>('/points/me')
