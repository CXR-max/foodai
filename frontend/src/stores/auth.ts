// 登录状态管理：token 持久化到 localStorage（刷新不掉线）
import { defineStore } from 'pinia'
import { computed, ref } from 'vue'
import * as authApi from '@/api/auth'
import router from '@/router'

export const useAuthStore = defineStore('auth', () => {
  const token = ref<string | null>(localStorage.getItem('token'))
  const user = ref<authApi.User | null>(null)
  const isLoggedIn = computed(() => !!token.value)

  async function login(username: string, password: string) {
    const data = await authApi.login({ username, password })
    token.value = data.token
    user.value = data.user
    localStorage.setItem('token', data.token)
  }

  async function register(username: string, password: string, nickname?: string) {
    const data = await authApi.register({ username, password, nickname })
    token.value = data.token
    user.value = data.user
    localStorage.setItem('token', data.token)
  }

  async function fetchMe() {
    const data = await authApi.me()
    user.value = data.user
    return data
  }

  function refreshPoints(points: number) {
    if (user.value) user.value.points_total = points
  }

  /** 人脸登录等外部流程拿到 token 后，写回登录态 */
  function setSession(newToken: string, newUser: authApi.User) {
    token.value = newToken
    user.value = newUser
    localStorage.setItem('token', newToken)
  }

  function logout() {
    token.value = null
    user.value = null
    localStorage.removeItem('token')
    // 避免下一个账号看到上一个账号的本地对话缓存
    for (const key of ['plan_chat_current', 'plan_chat_history', 'plan_chat_owner']) {
      localStorage.removeItem(key)
    }
    router.push('/login')
  }

  return { token, user, isLoggedIn, login, register, setSession, fetchMe, refreshPoints, logout }
})
