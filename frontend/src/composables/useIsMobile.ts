// 全局屏幕尺寸判断：所有组件共用同一份状态，避免各自监听 resize 造成重复计算
// 用法：import { isMobile } from '@/composables/useIsMobile'
import { ref } from 'vue'

/** 小于等于这个宽度按手机处理（底部 tabBar + 卡片布局） */
export const MOBILE_MAX = 768
/** 更窄的屏幕（小屏手机），卡片可再紧凑一档 */
export const NARROW_MAX = 420

export const isMobile = ref(false)
export const isNarrow = ref(false)

/** 当前视口宽度，便于个别地方按像素微调 */
export const viewportWidth = ref(0)

function sync() {
  const w = window.innerWidth || document.documentElement.clientWidth || 0
  viewportWidth.value = w
  isMobile.value = w <= MOBILE_MAX
  isNarrow.value = w <= NARROW_MAX
}

// 模块首次加载时立即算一次，避免首屏闪一下桌面布局
if (typeof window !== 'undefined') {
  sync()
  window.addEventListener('resize', sync, { passive: true })
  window.addEventListener('orientationchange', sync, { passive: true })
}
