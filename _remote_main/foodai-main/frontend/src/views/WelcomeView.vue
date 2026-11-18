<script setup lang="ts">
// 启动页：登录前的动态开场，停留在此页，点击「进入」后再前往登录
import { Camera, CircleCheck, MagicStick } from '@element-plus/icons-vue'
import { onBeforeUnmount, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'

const router = useRouter()

const features = [
  { icon: Camera, text: '拍一张，认出你吃的是什么' },
  { icon: MagicStick, text: '按你的目标，算出每天怎么吃' },
  { icon: CircleCheck, text: '打卡、积分、排行榜，坚持看得见' },
]
const activeFeature = ref(0)

let featureTimer: number | undefined
let entered = false

function enter() {
  if (entered) return
  entered = true
  if (featureTimer !== undefined) window.clearInterval(featureTimer)
  router.replace({ name: 'login' })
}

onMounted(() => {
  featureTimer = window.setInterval(() => {
    activeFeature.value = (activeFeature.value + 1) % features.length
  }, 1050)
})

onBeforeUnmount(() => {
  if (featureTimer !== undefined) window.clearInterval(featureTimer)
})
</script>

<template>
  <div class="welcome">
    <div class="bg-grid" aria-hidden="true"></div>
    <div class="orb orb-a" aria-hidden="true"></div>
    <div class="orb orb-b" aria-hidden="true"></div>
    <div class="orb orb-c" aria-hidden="true"></div>

    <span class="float-food f1" aria-hidden="true">🥦</span>
    <span class="float-food f2" aria-hidden="true">🍎</span>
    <span class="float-food f3" aria-hidden="true">🥑</span>
    <span class="float-food f4" aria-hidden="true">🐟</span>
    <span class="float-food f5" aria-hidden="true">🍅</span>
    <span class="float-food f6" aria-hidden="true">🌿</span>

    <main class="hero">
      <div class="logo-wrap">
        <span class="ring ring-1" aria-hidden="true"></span>
        <span class="ring ring-2" aria-hidden="true"></span>
        <div class="logo" aria-hidden="true">🥗</div>
      </div>

      <p class="eyebrow">EAT WELL · LIVE WELL</p>
      <h1 class="title">识膳智行</h1>
      <p class="subtitle">AI 健康管理平台</p>

      <div class="feature">
        <span :key="activeFeature" class="feature-item">
          <el-icon class="feature-icon"><component :is="features[activeFeature].icon" /></el-icon>
          {{ features[activeFeature].text }}
        </span>
      </div>

      <div class="divider" aria-hidden="true"></div>
      <p class="hint">准备好开始了吗？</p>

      <button class="enter" type="button" @click="enter">
        进入识膳智行 <span aria-hidden="true">→</span>
      </button>
    </main>

    <footer class="footer">识膳 · 知味 · 养生</footer>
  </div>
</template>

<style scoped>
.welcome {
  --mint: #9df3b0;
  position: relative;
  height: 100%;
  overflow: hidden;
  display: flex;
  align-items: center;
  justify-content: center;
  color: #f2fff7;
  background:
    radial-gradient(1100px 800px at 12% 8%, rgba(103, 194, 58, 0.22), transparent 60%),
    radial-gradient(900px 700px at 88% 92%, rgba(61, 214, 181, 0.16), transparent 60%),
    linear-gradient(145deg, #07120e 0%, #0c2018 48%, #081824 100%);
}

.welcome::after {
  content: '';
  position: absolute;
  inset: 0;
  pointer-events: none;
  background: radial-gradient(circle at 50% 45%, transparent 35%, rgba(2, 8, 6, 0.45) 100%);
}

.bg-grid {
  position: absolute;
  inset: 0;
  background-image:
    linear-gradient(rgba(255, 255, 255, 0.045) 1px, transparent 1px),
    linear-gradient(90deg, rgba(255, 255, 255, 0.045) 1px, transparent 1px);
  background-size: 64px 64px;
  -webkit-mask-image: radial-gradient(circle at 50% 45%, #000 0%, transparent 72%);
  mask-image: radial-gradient(circle at 50% 45%, #000 0%, transparent 72%);
  opacity: 0.7;
}

.orb {
  position: absolute;
  border-radius: 50%;
  filter: blur(72px);
  opacity: 0.5;
  animation: drift 16s ease-in-out infinite alternate;
}

.orb-a {
  width: 440px;
  height: 440px;
  top: -140px;
  left: -100px;
  background: #1f8f5f;
}

.orb-b {
  width: 460px;
  height: 460px;
  right: -140px;
  bottom: -180px;
  background: #2f7f9e;
  animation-delay: -6s;
}

.orb-c {
  width: 260px;
  height: 260px;
  top: 58%;
  left: 72%;
  background: #67c23a;
  opacity: 0.22;
  animation-delay: -10s;
}

@keyframes drift {
  to {
    transform: translate3d(44px, -34px, 0) scale(1.12);
  }
}

.float-food {
  position: absolute;
  font-size: 34px;
  opacity: 0.32;
  filter: drop-shadow(0 12px 22px rgba(0, 0, 0, 0.3));
  animation: floaty 7s ease-in-out infinite;
  user-select: none;
}

.f1 { top: 17%; left: 11%; animation-delay: -1.2s; }
.f2 { top: 70%; left: 15%; font-size: 28px; animation-delay: -3.4s; }
.f3 { top: 23%; right: 13%; font-size: 30px; animation-delay: -2.1s; }
.f4 { top: 66%; right: 16%; animation-delay: -4.6s; }
.f5 { top: 43%; left: 6%; font-size: 24px; animation-delay: -5.2s; }
.f6 { top: 40%; right: 7%; font-size: 26px; animation-delay: -0.6s; }

@keyframes floaty {
  0%, 100% { transform: translateY(0) rotate(-4deg); }
  50% { transform: translateY(-18px) rotate(5deg); }
}

.hero {
  position: relative;
  z-index: 2;
  display: flex;
  flex-direction: column;
  align-items: center;
  text-align: center;
  padding: 32px 24px;
  max-width: 760px;
}

.logo-wrap {
  position: relative;
  width: 118px;
  height: 118px;
  display: grid;
  place-items: center;
  margin-bottom: 26px;
}

.logo {
  width: 96px;
  height: 96px;
  display: grid;
  place-items: center;
  font-size: 44px;
  border-radius: 32px;
  background:
    radial-gradient(circle at 30% 20%, rgba(157, 243, 176, 0.24), transparent 60%),
    linear-gradient(145deg, rgba(255, 255, 255, 0.1), rgba(255, 255, 255, 0.03));
  border: 1px solid rgba(255, 255, 255, 0.18);
  box-shadow:
    0 22px 60px rgba(0, 0, 0, 0.38),
    inset 0 1px 0 rgba(255, 255, 255, 0.2);
  backdrop-filter: blur(12px);
  animation: breathe 3.2s ease-in-out infinite;
}

.ring {
  position: absolute;
  inset: 0;
  border-radius: 50%;
  border: 1px solid rgba(157, 243, 176, 0.35);
  animation: ripple 3s ease-out infinite;
}

.ring-2 {
  animation-delay: 1.5s;
}

@keyframes ripple {
  0% { transform: scale(0.72); opacity: 0.8; }
  100% { transform: scale(1.38); opacity: 0; }
}

@keyframes breathe {
  0%, 100% { transform: translateY(0) scale(1); }
  50% { transform: translateY(-4px) scale(1.03); }
}

.eyebrow {
  margin: 0 0 12px;
  font-size: 11px;
  letter-spacing: 0.34em;
  color: rgba(226, 255, 236, 0.52);
}

.title {
  margin: 0;
  font-size: clamp(40px, 7vw, 64px);
  font-weight: 700;
  letter-spacing: 0.14em;
  text-indent: 0.14em;
  color: #eafff0;
  text-shadow: 0 12px 42px rgba(103, 194, 58, 0.28);
  animation: title-in 0.9s cubic-bezier(0.2, 0.8, 0.2, 1) both;
}

@keyframes title-in {
  from {
    transform: translateY(14px);
    letter-spacing: 0.32em;
  }
  to {
    transform: translateY(0);
    letter-spacing: 0.14em;
  }
}

.subtitle {
  margin: 14px 0 0;
  font-size: 14px;
  letter-spacing: 0.26em;
  text-indent: 0.26em;
  color: rgba(226, 255, 236, 0.64);
}

.feature {
  min-height: 36px;
  margin: 34px 0 0;
  display: flex;
  align-items: center;
  justify-content: center;
}

.feature-item {
  display: inline-flex;
  align-items: center;
  gap: 10px;
  padding: 9px 18px;
  border-radius: 999px;
  font-size: 14px;
  color: rgba(240, 255, 245, 0.86);
  background: rgba(255, 255, 255, 0.055);
  border: 1px solid rgba(255, 255, 255, 0.1);
  backdrop-filter: blur(8px);
  animation: feature-in 0.45s ease both;
}

@keyframes feature-in {
  from {
    transform: translateY(8px);
  }
  to {
    transform: translateY(0);
  }
}

.feature-icon {
  display: inline-flex;
  font-size: 16px;
  color: var(--mint);
}

.divider {
  position: relative;
  width: min(300px, 68vw);
  height: 1px;
  margin-top: 30px;
  overflow: hidden;
  background: linear-gradient(90deg, transparent, rgba(157, 243, 176, 0.55), transparent);
}

.divider::after {
  content: '';
  position: absolute;
  top: 0;
  left: -45%;
  width: 40%;
  height: 100%;
  background: linear-gradient(90deg, transparent, #ffffff, transparent);
  animation: shimmer 2.8s ease-in-out infinite;
}

@keyframes shimmer {
  0% { left: -45%; }
  60%, 100% { left: 110%; }
}

.hint {
  margin: 16px 0 0;
  font-size: 12px;
  letter-spacing: 0.12em;
  color: rgba(226, 255, 236, 0.52);
}

.enter {
  margin-top: 24px;
  padding: 14px 34px;
  border: 0;
  border-radius: 999px;
  font-family: inherit;
  font-size: 15px;
  font-weight: 700;
  letter-spacing: 0.08em;
  color: #062016;
  background: linear-gradient(135deg, #8ef0a8 0%, #67c23a 45%, #3dd6b5 100%);
  box-shadow:
    0 16px 40px rgba(61, 214, 181, 0.26),
    inset 0 1px 0 rgba(255, 255, 255, 0.35);
  cursor: pointer;
  transition: transform 0.25s ease, box-shadow 0.25s ease, filter 0.25s ease;
}

.enter:hover {
  transform: translateY(-2px);
  box-shadow: 0 20px 50px rgba(61, 214, 181, 0.38);
  filter: brightness(1.05);
}

.enter:active {
  transform: translateY(0);
}

.enter:focus-visible {
  outline: 2px solid rgba(157, 243, 176, 0.8);
  outline-offset: 4px;
}

.enter span {
  display: inline-block;
  transition: transform 0.25s ease;
}

.enter:hover span {
  transform: translateX(4px);
}

.footer {
  position: absolute;
  left: 0;
  right: 0;
  bottom: 26px;
  z-index: 2;
  text-align: center;
  font-size: 10px;
  letter-spacing: 0.34em;
  color: rgba(226, 255, 236, 0.26);
}

@media (max-width: 640px) {
  .hero { padding: 24px 18px; }
  .subtitle { letter-spacing: 0.16em; text-indent: 0.16em; }
  .feature { margin: 26px 0 0; }
  .feature-item { padding: 8px 14px; font-size: 13px; }
  .divider { margin-top: 24px; }
  .hint { font-size: 11px; letter-spacing: 0.06em; }
  .enter { margin-top: 20px; padding: 13px 28px; font-size: 14px; }
  .float-food { font-size: 24px; opacity: 0.2; }
  .f5, .f6 { display: none; }
  .orb { opacity: 0.32; }
}

@media (prefers-reduced-motion: reduce) {
  .orb,
  .float-food,
  .logo,
  .ring,
  .title,
  .feature-item,
  .divider::after {
    animation: none !important;
  }
  .enter {
    transition: none;
  }
}
</style>
