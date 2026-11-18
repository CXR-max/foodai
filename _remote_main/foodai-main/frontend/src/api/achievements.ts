// 成就徽章接口
import { get, post } from './http'

export interface Achievement {
  id: number
  code: string
  name: string
  icon: string
  description: string
  category: 'badge' | 'title'
  condition_type: string
  threshold: number
  unlocked: boolean
  equipped: boolean
  unlocked_at: string | null
}

export const getAchievements = () =>
  get<{
    items: Achievement[]
    equipped_achievement_id: number | null
    unlocked_count: number
    newly_unlocked: string[]
  }>('/achievements')

export const equipAchievement = (achievementId: number | null) =>
  post<{ ok: boolean; equipped_achievement_id: number | null; message: string }>(
    '/achievements/equip',
    { achievement_id: achievementId },
  )
