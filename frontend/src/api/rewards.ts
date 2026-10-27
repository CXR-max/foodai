// 积分商城接口
import { get, post } from './http'

export interface Reward {
  id: number
  name: string
  category: 'honor' | 'privilege' | 'goods'
  description: string
  points_cost: number
  stock: number
  icon: string
  can_afford: boolean
}

export interface Redemption { id: number; name: string; icon: string; points_spent: number; code: string; status: string; created_at: string }

export const getRewards = () => get<Reward[]>('/rewards')

export const redeem = (id: number) =>
  post<{ code: string; message: string; points_total: number }>(`/rewards/${id}/redeem`)

export const getRedemptions = () => get<Redemption[]>('/rewards/redemptions')
