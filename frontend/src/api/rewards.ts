// 积分商城接口
import { get, post } from './http'

export interface Reward {
  id: number
  name: string
  category: 'honor' | 'privilege' | 'goods'
  kind: 'makeup' | 'custom_plan' | 'avatar_frame' | 'physical'
  description: string
  points_cost: number
  stock: number
  icon: string
  is_active: boolean
  can_afford: boolean
}

export interface Redemption {
  id: number
  name: string
  icon: string
  category: string
  kind: string
  points_spent: number
  code: string
  status: string
  used_at: string | null
  created_at: string
}

export const getRewards = () => get<Reward[]>('/rewards')

export const redeem = (id: number) =>
  post<{ code: string; message: string; points_total: number }>(`/rewards/${id}/redeem`)

export const getRedemptions = () => get<Redemption[]>('/rewards/redemptions')

/** 使用权益券（补签卡传 check_date） */
export const useRedemption = (id: number, checkDate?: string) =>
  post<{ ok: boolean; message: string; points_awarded: number; points_total: number }>(
    `/rewards/redemptions/${id}/use`,
    checkDate ? { check_date: checkDate } : {},
  )
