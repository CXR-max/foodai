// 好友：单向关注，用来在地图上筛「只看好友」
import { del, get, post } from './http'

export interface FriendItem {
  user_id: number
  nickname: string
  color: string
  cells: number
  area_m2: number
}

export const getFriends = () => get<{ items: FriendItem[] }>('/friends')
export const getFriendSuggestions = () => get<{ items: FriendItem[] }>('/friends/suggest')
export const addFriend = (userId: number) => post<{ ok: boolean }>(`/friends/${userId}`)
export const removeFriend = (userId: number) => del<{ ok: boolean }>(`/friends/${userId}`)
