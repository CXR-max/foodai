<script setup lang="ts">
// 启动页：纸感编辑部式开场，停留在此页，点击「进入」后前往登录
import { onBeforeUnmount, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'

const router = useRouter()

const features = [
  { no: '01', text: '拍一张，识别盘中食物与克重', tag: 'VISION' },
  { no: '02', text: '按目标生成每天怎么吃的方案', tag: 'PLAN' },
  { no: '03', text: '打卡、积分、排行，坚持看得见', tag: 'HABIT' },
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
  }, 1800)
})

onBeforeUnmount(() => {
  if (featureTimer !== undefined) window.clearInterval(featureTimer)
})
</script>

<template>
  <div class="welcome">
    <header class="top">
      <span class="mono">SHISHAN ZHIXING</span>
      <span class="mono">NUTRITION LAB · 2026</span>
    </header>

    <main class="wrap">
      <section class="left">
        <div class="kicker mono">AI HEALTH MANAGEMENT</div>
        <h1 class="title">识膳<br /><em>智行</em></h1>
        <p class="sub">
          把每天吃进去的东西，变成看得懂的数字、<br />
          可执行的建议，和一条能坚持的记录。
        </p>

        <ul class="features">
          <li
            v-for="(f, i) in features" :key="f.no"
            class="feature" :class="{ active: activeFeature === i }"
          >
            <span class="no mono">{{ f.no }}</span>
            <span class="text">{{ f.text }}</span>
            <span class="tag mono">{{ f.tag }}</span>
          </li>
        </ul>

        <div class="cta-row">
          <button class="enter" type="button" @click="enter">
            进入识膳智行 <span aria-hidden="true">→</span>
          </button>
          <span class="hint mono">NO ACCOUNT? 可直接注册</span>
        </div>
      </section>

      <aside class="right" aria-hidden="true">
        <div class="sample-tag mono">SAMPLE · 示例</div>
        <div class="label">
          <div class="label-top">
            <div class="label-h">营养成分表<span>NUTRITION FACTS</span></div>
            <div class="label-serv mono">每份 1 天<br />共 3 餐</div>
          </div>
          <div class="lrow big"><span class="lk">热量 Energy</span><span class="lv">1,742</span><span class="lp">kcal</span></div>
          <div class="lrow"><span class="lk">蛋白质<small>PROTEIN</small></span><span class="lv">96 g</span><span class="lp">160%</span></div>
          <div class="lrow"><span class="lk">脂肪<small>FAT</small></span><span class="lv">54 g</span><span class="lp">90%</span></div>
          <div class="lrow"><span class="lk">碳水化合物<small>CARBS</small></span><span class="lv">208 g</span><span class="lp">69%</span></div>
          <div class="label-foot mono"><span>% 为每日参考摄入占比</span><span>LOCAL DB</span></div>
        </div>

        <div class="stamp">
          <span class="serif">吃好一点</span>
          <span class="mono">EAT WELL · LIVE WELL</span>
        </div>
      </aside>
    </main>

    <footer class="foot">
      <span class="mono">识膳 · 知味 · 养生</span>
      <span class="mono">POWERED BY QWEN-PLUS / GLM-4V</span>
    </footer>
  </div>
</template>

<style scoped>
.welcome {
  min-height: 100%;
  background: var(--paper);
  color: var(--ink);
  display: flex;
  flex-direction: column;
  padding: 26px 44px 22px;
  position: relative;
  overflow: hidden;
}

/* 极淡的稿纸网格 */
.welcome::before {
  content: '';
  position: absolute;
  inset: 0;
  background-image:
    linear-gradient(rgba(28, 26, 23, 0.04) 1px, transparent 1px),
    linear-gradient(90deg, rgba(28, 26, 23, 0.04) 1px, transparent 1px);
  background-size: 88px 88px;
  -webkit-mask-image: radial-gradient(circle at 30% 40%, #000 0%, transparent 78%);
  mask-image: radial-gradient(circle at 30% 40%, #000 0%, transparent 78%);
  pointer-events: none;
}

.top {
  display: flex;
  justify-content: space-between;
  font-size: 10px;
  letter-spacing: 0.18em;
  color: var(--muted);
  border-bottom: 1px solid var(--line-2);
  padding-bottom: 14px;
  position: relative;
  z-index: 1;
}

.wrap {
  flex: 1;
  display: grid;
  grid-template-columns: minmax(0, 1.15fr) minmax(0, 0.85fr);
  align-items: center;
  gap: 64px;
  padding: 40px 0;
  position: relative;
  z-index: 1;
}

.kicker {
  font-size: 10.5px;
  letter-spacing: 0.24em;
  color: var(--muted);
  margin-bottom: 18px;
}

.title {
  font-family: var(--serif);
  font-size: clamp(64px, 8.4vw, 104px);
  line-height: 1.02;
  font-weight: 600;
  letter-spacing: 0.04em;
  margin: 0;
}
.title em {
  font-style: normal;
  color: var(--accent);
}

.sub {
  margin: 26px 0 0;
  font-size: 13.5px;
  line-height: 1.95;
  color: var(--ink-2);
}

.features {
  list-style: none;
  padding: 0;
  margin: 38px 0 0;
  border-top: 1px solid var(--line-2);
  max-width: 520px;
}
.feature {
  display: grid;
  grid-template-columns: 40px minmax(0, 1fr) auto;
  align-items: center;
  gap: 14px;
  padding: 13px 2px;
  border-bottom: 1px solid var(--line);
  color: var(--muted);
  transition: color 0.3s, padding 0.3s;
}
.feature.active { color: var(--ink); padding-left: 8px; }
.feature .no { font-size: 10px; letter-spacing: 0.12em; color: var(--muted); }
.feature.active .no { color: var(--accent); }
.feature .text { font-size: 13.5px; }
.feature .tag { font-size: 9px; letter-spacing: 0.16em; color: var(--muted); }
.feature.active .tag { color: var(--accent); }

.cta-row { display: flex; align-items: center; gap: 18px; margin-top: 34px; flex-wrap: wrap; }
.enter {
  border: 0;
  background: var(--ink);
  color: var(--paper);
  font-family: var(--sans);
  font-size: 14px;
  letter-spacing: 0.06em;
  padding: 14px 30px;
  border-radius: 999px;
  cursor: pointer;
  transition: transform 0.2s, background 0.2s;
}
.enter:hover { transform: translateY(-2px); background: #000; }
.enter span { display: inline-block; margin-left: 6px; transition: transform 0.2s; }
.enter:hover span { transform: translateX(4px); }
.hint { font-size: 9px; letter-spacing: 0.14em; color: var(--muted); }

/* 右侧示例营养标签 */
.right {
  position: relative;
  max-width: 400px;
  justify-self: end;
  width: 100%;
}
.sample-tag {
  text-align: right;
  font-size: 9px;
  letter-spacing: 0.18em;
  color: var(--muted);
  margin-bottom: 10px;
}
.label {
  border: 1px solid var(--ink);
  background: var(--surface);
  padding: 18px 20px 16px;
  transform: rotate(-1.2deg);
  box-shadow: 14px 18px 0 -10px var(--paper-2);
}
.label-top {
  display: flex;
  justify-content: space-between;
  align-items: flex-end;
  border-bottom: 7px solid var(--ink);
  padding-bottom: 8px;
  margin-bottom: 10px;
  gap: 12px;
}
.label-h { font-family: var(--serif); font-size: 19px; font-weight: 700; }
.label-h span {
  display: block;
  font-family: var(--mono);
  font-size: 8px;
  letter-spacing: 0.18em;
  color: var(--muted);
  font-weight: 400;
  margin-top: 4px;
}
.label-serv { font-family: var(--mono); font-size: 9px; color: var(--muted); text-align: right; line-height: 1.8; }
.lrow {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 74px 44px;
  align-items: baseline;
  gap: 8px;
  padding: 7px 0;
  border-bottom: 1px solid var(--line);
}
.lrow.big { border-bottom: 8px solid var(--ink); padding: 5px 0 7px; }
.lrow.big .lk { font-size: 14px; font-weight: 700; }
.lrow.big .lv { font-size: 20px; font-weight: 700; }
.lk { font-size: 12px; color: var(--ink-2); }
.lk small { display: block; font-family: var(--mono); font-size: 8px; letter-spacing: 0.1em; color: var(--muted); margin-top: 2px; }
.lv { font-family: var(--mono); font-variant-numeric: tabular-nums; font-size: 12.5px; font-weight: 600; text-align: right; }
.lp { font-family: var(--mono); font-size: 9px; color: var(--muted); text-align: right; }
.label-foot { display: flex; justify-content: space-between; margin-top: 10px; font-size: 8px; letter-spacing: 0.06em; color: var(--muted); }

.stamp {
  position: absolute;
  right: -6px;
  bottom: -58px;
  display: flex;
  flex-direction: column;
  align-items: flex-end;
  gap: 4px;
}
.stamp .serif { font-size: 26px; color: var(--accent); letter-spacing: 0.08em; }
.stamp .mono { font-size: 8.5px; letter-spacing: 0.2em; color: var(--muted); }

.foot {
  display: flex;
  justify-content: space-between;
  border-top: 1px solid var(--line-2);
  padding-top: 12px;
  font-size: 9px;
  letter-spacing: 0.16em;
  color: var(--muted);
  position: relative;
  z-index: 1;
}

@media (max-width: 980px) {
  .welcome { padding: 20px 22px 16px; }
  .wrap { grid-template-columns: 1fr; gap: 44px; padding: 30px 0 50px; }
  .right { justify-self: start; max-width: 340px; }
  .stamp { display: none; }
  .title { font-size: clamp(56px, 16vw, 84px); }
  .foot span:last-child { display: none; }
}
</style>
