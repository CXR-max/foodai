<script setup lang="ts">
// 能量天平（融合自 feat/personalized-burn，挂在主分支「我的」页今日三环下方）
// 摄入读主分支 diet_intake 口径；总消耗 = 基础代谢 + 手动运动打卡 + 手环活动，
// 后两项单独列出来，直观体现「自己打卡的」和「手环记录的」两个来源。
import { computed, onMounted, ref } from 'vue'
import { getEnergy } from '@/api/stats'
import type { EnergyResult } from '@/api/stats'

const e = ref<EnergyResult | null>(null)
const loading = ref(true)

async function load() {
  try {
    e.value = await getEnergy()
  } catch {
    e.value = null
  } finally {
    loading.value = false
  }
}
onMounted(load)

const R = 44
const C = 2 * Math.PI * R

function dash(value: number) {
  const max = Math.max(e.value?.intake.value || 0, e.value?.burn.total || 0, 1) * 1.08
  const pct = Math.min(1, Math.max(0, value / max))
  return `${(C * pct).toFixed(1)} ${C.toFixed(1)}`
}

const gapText = computed(() => {
  const g = e.value?.gap ?? 0
  return g > 0 ? `盈余 ${g}` : g < 0 ? `缺口 ${-g}` : '持平'
})
const bandSourceText = computed(() => {
  const s = e.value?.band.source
  if (!s) return '未接入'
  return { import: '导出包', ble: '蓝牙', api: '脚本推送', demo: '演示数据', manual: '手动填写' }[s] || s
})
</script>

<template>
  <section class="block">
    <h3 class="block-title">
      ⚖️ 能量天平
      <em>吃动平衡</em>
    </h3>
    <div class="card eb-card">
      <div v-if="loading" class="skeleton">加载中…</div>
      <template v-else-if="e">
        <div class="eb-rings">
          <div class="eb-ring">
            <svg viewBox="0 0 110 110" class="eb-svg">
              <circle cx="55" cy="55" :r="R" class="eb-track" />
              <circle cx="55" cy="55" :r="R" class="eb-arc intake" :stroke-dasharray="dash(e.intake.value)" />
            </svg>
            <div class="eb-center">
              <b>{{ e.intake.value }}</b>
              <span>摄入 kcal</span>
            </div>
          </div>

          <div class="eb-gap">
            <span class="eb-gap-label">今日</span>
            <b :class="{ over: e.gap > 0, under: e.gap < 0 }">{{ gapText }}</b>
            <span class="eb-gap-unit">kcal</span>
          </div>

          <div class="eb-ring">
            <svg viewBox="0 0 110 110" class="eb-svg">
              <circle cx="55" cy="55" :r="R" class="eb-track" />
              <circle cx="55" cy="55" :r="R" class="eb-arc burn" :stroke-dasharray="dash(e.burn.total)" />
            </svg>
            <div class="eb-center">
              <b>{{ e.burn.total }}</b>
              <span>消耗 kcal</span>
            </div>
          </div>
        </div>

        <!-- 消耗构成：手动打卡 + 手环记录两方面必须都看得到 -->
        <div class="eb-breakdown">
          <div class="eb-row">
            <span>基础代谢 <em>BMR</em></span>
            <b>{{ e.burn.bmr }}</b><i>kcal</i>
          </div>
          <div class="eb-row">
            <span>运动打卡 <em class="tag-manual">手动</em></span>
            <b>{{ e.burn.checkin }}</b><i>kcal</i>
          </div>
          <div class="eb-row">
            <span>手环活动 <em class="tag-band">设备</em></span>
            <b>{{ e.burn.band }}</b><i>kcal</i>
          </div>
          <div class="eb-row eb-total">
            <span>活动消耗合计</span>
            <b>{{ e.burn.activity }}</b><i>kcal</i>
          </div>
        </div>

        <p class="eb-verdict">{{ e.verdict }}</p>
        <p class="eb-foot">
          手环：{{ bandSourceText }}
          <template v-if="e.band.steps"> · 今日 {{ e.band.steps }} 步</template>
          <template v-if="e.band.sleep_h"> · 睡眠 {{ e.band.sleep_h }} h</template>
          <template v-if="!e.has_device"> · 暂无设备数据</template>
        </p>
      </template>
      <div v-else class="skeleton">暂时取不到能量数据</div>
    </div>
  </section>
</template>

<style scoped>
.block { display: flex; flex-direction: column; }
.block-title {
  font-size: 14px; font-weight: 600; color: #303133; margin: 4px 2px 8px;
  display: flex; align-items: center; gap: 6px;
}
.block-title em {
  margin-left: auto; font-style: normal; font-size: 11.5px;
  font-weight: 400; color: #909399;
}
.card { background: #fff; border-radius: 12px; padding: 14px; box-shadow: 0 1px 3px rgba(0, 0, 0, 0.05); }

.eb-rings { display: flex; align-items: center; justify-content: space-between; gap: 4px; }
.eb-ring { position: relative; width: 108px; height: 108px; flex: none; }
.eb-svg { width: 108px; height: 108px; transform: rotate(-90deg); }
.eb-track { fill: none; stroke: #eef1f6; stroke-width: 9; }
.eb-arc { fill: none; stroke-width: 9; stroke-linecap: round; transition: stroke-dasharray .6s ease; }
.eb-arc.intake { stroke: #67c23a; }
.eb-arc.burn { stroke: #4c6ef5; }
.eb-center {
  position: absolute; inset: 0; display: flex; flex-direction: column;
  align-items: center; justify-content: center; gap: 2px; pointer-events: none;
}
.eb-center b { font-size: 19px; font-weight: 700; color: #303133; }
.eb-center span { font-size: 10px; color: #909399; }
.eb-gap { flex: 1 1 auto; min-width: 0; display: flex; flex-direction: column; align-items: center; gap: 2px; }
.eb-gap-label { font-size: 11px; color: #909399; }
.eb-gap b { font-size: 15px; font-weight: 700; white-space: nowrap; }
.eb-gap b.over { color: #e6a23c; }
.eb-gap b.under { color: #67c23a; }
.eb-gap-unit { font-size: 10px; color: #b6bbc4; }

.eb-breakdown {
  margin-top: 12px; padding: 10px 12px; border-radius: 10px; background: #f7f9fc;
  display: flex; flex-direction: column; gap: 8px;
}
.eb-row { display: flex; align-items: baseline; gap: 5px; font-size: 12px; color: #606266; }
.eb-row > span { flex: 1 1 auto; }
.eb-row em {
  font-style: normal; font-size: 10px; color: #a8abb2; margin-left: 3px;
  padding: 1px 5px; border-radius: 4px; background: #eef1f6;
}
.eb-row em.tag-manual { background: #fdf3e6; color: #e6a23c; }
.eb-row em.tag-band { background: #ecf1ff; color: #4c6ef5; }
.eb-row b { font-size: 14px; font-weight: 700; color: #303133; }
.eb-row i { font-style: normal; font-size: 10px; color: #b6bbc4; }
.eb-total { border-top: 1px dashed #e4e7ed; padding-top: 8px; }
.eb-total b { color: #4c6ef5; }

.eb-verdict { margin: 10px 2px 0; font-size: 12px; color: #606266; line-height: 1.6; }
.eb-foot { margin: 4px 2px 0; font-size: 11px; color: #a8abb2; line-height: 1.5; }
.skeleton { font-size: 13px; color: #909399; text-align: center; padding: 16px; }

@media (max-width: 360px) {
  .eb-ring, .eb-svg { width: 92px; height: 92px; }
  .eb-center b { font-size: 16px; }
  .eb-gap b { font-size: 13px; }
}
</style>
