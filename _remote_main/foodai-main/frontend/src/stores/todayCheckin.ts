// 今日打卡状态（全局共享）
// 菜单红点、仪表盘横幅、打卡页共用同一份状态，避免各页面各拉一次导致"打完卡红点还在"
import { reactive } from 'vue'
import * as statsApi from '@/api/stats'

export const todayCheckin = reactive({
  diet: false,
  exercise: false,
  loaded: false, // 首次数据是否已拿到（没拿到时不显示红点，避免误闪）
})

let inflight: Promise<void> | null = null

/** 重新拉取今日打卡状态；并发调用会复用同一个请求 */
export function refreshTodayCheckin(): Promise<void> {
  if (inflight) return inflight
  inflight = statsApi
    .getOverview()
    .then((ov) => {
      todayCheckin.diet = ov.today_checked.diet
      todayCheckin.exercise = ov.today_checked.exercise
      todayCheckin.loaded = true
    })
    .catch(() => {})
    .finally(() => {
      inflight = null
    })
  return inflight
}

/** 今日是否还有未完成的打卡项 */
export function hasPendingCheckin() {
  return todayCheckin.loaded && !(todayCheckin.diet && todayCheckin.exercise)
}
