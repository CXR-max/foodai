// axios 封装（前端所有请求的咽喉）：
// - 自动带上登录 token
// - 401 自动清 token 跳登录页
// - 错误统一弹 ElMessage 提示
// - baseURL 可配置：浏览器走 Vite 代理（/api），打包成 App 时用 VITE_API_BASE 指向后端
import axios from 'axios'
import { ElMessage } from 'element-plus'
import router from '@/router'

/**
 * 接口前缀。
 * - Web / 开发：默认 '/api'，由 Vite 代理转发到后端
 * - 手机 App（Capacitor）：构建时注入 VITE_API_BASE=http://192.168.x.x:8000/api
 */
const API_BASE = (import.meta.env.VITE_API_BASE as string | undefined) || '/api'

/**
 * 后端根地址（去掉 /api 后缀）。
 * 后端返回的图片是 '/uploads/xxx.jpg' 这种相对路径，在 App 里必须拼上后端地址，
 * 否则会去请求 https://localhost/uploads/... 导致图片全部裂掉。
 */
export const API_ORIGIN = API_BASE.replace(/\/api\/?$/, '')

/** 把后端返回的相对资源路径转成可直接访问的完整地址 */
export function assetUrl(p?: string | null): string {
  if (!p) return ''
  if (/^(https?:|data:|blob:)/i.test(p)) return p
  return `${API_ORIGIN}${p.startsWith('/') ? '' : '/'}${p}`
}

const http = axios.create({
  baseURL: API_BASE,
  timeout: 30000,
})

// 请求拦截：塞 token
http.interceptors.request.use((config) => {
  const token = localStorage.getItem('token')
  if (token) config.headers.Authorization = `Bearer ${token}`
  return config
})

// 响应拦截：直接吐 data；错误统一处理
http.interceptors.response.use(
  (resp) => resp.data,
  (error) => {
    const status = error.response?.status
    const detail = error.response?.data?.detail
    if (status === 401) {
      localStorage.removeItem('token')
      ElMessage.warning('请重新登录')
      router.push('/login')
    } else {
      // detail 可能是字符串，也可能是 pydantic 校验错误数组
      const msg = typeof detail === 'string' ? detail : '请求失败，请稍后再试'
      ElMessage.error(msg)
    }
    return Promise.reject(error)
  },
)

// 类型化快捷方法
export const get = <T = any>(url: string, params?: object, config?: object) =>
  http.get<T, T>(url, { params, ...config })
export const post = <T = any>(url: string, data?: object, config?: object) =>
  http.post<T, T>(url, data, config)
export const put = <T = any>(url: string, data?: object, config?: object) =>
  http.put<T, T>(url, data, config)
export const patch = <T = any>(url: string, data?: object, config?: object) =>
  http.patch<T, T>(url, data, config)
export const del = <T = any>(url: string, config?: object) => http.delete<T, T>(url, config)

export default http
