// 人脸登录接口
import { del, get, post } from './http'

export interface FaceStatus {
  registered: boolean
  provider: string
  mock: boolean
}

/** 登录页：查这个用户名有没有绑过人脸 */
export const faceStatus = (username: string) => post<FaceStatus>('/auth/face/status', { username })

/** 现场照 1:1 比对，通过后拿到一次性 ticket */
export const faceVerify = (username: string, images: string[]) =>
  post<{ ticket: string; score: number; expires_in: number }>('/auth/face/verify', { username, images })

/** 用 ticket 换正式 token */
export const faceLogin = (ticket: string) =>
  post<{ token: string; user: any }>('/auth/face/login', { ticket })

/** 个人中心：当前账号是否已绑人脸 */
export const myFace = () => get<{ registered: boolean; provider: string }>('/auth/face/me')

/** 绑定/更新人脸（需登录） */
export const enrollFace = (image: string) => post<{ ok: boolean; provider: string }>('/auth/face/enroll', { image })

/** 解绑人脸 */
export const unbindFace = () => del<{ ok: boolean }>('/auth/face/enroll')
