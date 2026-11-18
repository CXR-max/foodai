// 认证相关接口
import { get, post } from './http'

export interface User {
  id: number
  username: string
  nickname: string
  points_total: number
}

export interface AuthResp {
  token: string
  user: User
}

export const register = (data: { username: string; password: string; nickname?: string }) =>
  post<AuthResp>('/auth/register', data)

export const login = (data: { username: string; password: string }) => post<AuthResp>('/auth/login', data)

export const me = () => get<{ user: User; profile_completed: boolean }>('/auth/me')
