// axios 封装（前端所有请求的咽喉）：
// - 自动带上登录 token
// - 401 自动清 token 跳登录页
// - 错误统一弹 ElMessage 提示
import axios from 'axios'
import { ElMessage } from 'element-plus'
import router from '@/router'

const http = axios.create({
  baseURL: '/api',
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
export const del = <T = any>(url: string, config?: object) => http.delete<T, T>(url, config)

export default http
