<script setup lang="ts">
// 分享海报：Canvas 合成打卡天数、摄入/消耗等数据，一键下载 PNG
import { nextTick, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import * as statsApi from '@/api/stats'
import type { Overview } from '@/api/stats'
import { useAuthStore } from '@/stores/auth'

const props = defineProps<{ visible: boolean }>()
const emit = defineEmits<{ (e: 'update:visible', v: boolean): void }>()

const auth = useAuthStore()
const canvasReady = ref(false)
const loading = ref(false)
const canvasEl = ref<HTMLCanvasElement>()
const W = 640
const H = 880

watch(() => props.visible, async (v) => {
  if (v) {
    loading.value = true
    try {
      const [ov, trends] = await Promise.all([statsApi.getOverview(), statsApi.getTrends('week')])
      await nextTick()
      draw(ov, trends)
      canvasReady.value = true
    } catch {
      ElMessage.error('海报数据加载失败')
    } finally {
      loading.value = false
    }
  } else {
    canvasReady.value = false
  }
})

function draw(ov: Overview, trends: { points: { date: string; calories: number }[] }) {
  const cv = canvasEl.value!
  cv.width = W
  cv.height = H
  const ctx = cv.getContext('2d')!

  // 背景：绿色渐变
  const bg = ctx.createLinearGradient(0, 0, 0, H)
  bg.addColorStop(0, '#0f9d58')
  bg.addColorStop(1, '#087f46')
  ctx.fillStyle = bg
  ctx.fillRect(0, 0, W, H)

  // 顶部装饰圆
  ctx.globalAlpha = 0.12
  ctx.fillStyle = '#fff'
  ctx.beginPath(); ctx.arc(560, 90, 140, 0, Math.PI * 2); ctx.fill()
  ctx.beginPath(); ctx.arc(60, 800, 110, 0, Math.PI * 2); ctx.fill()
  ctx.globalAlpha = 1

  const pad = 48
  // 标题
  ctx.fillStyle = '#fff'
  ctx.font = 'bold 34px "Microsoft YaHei", sans-serif'
  ctx.fillText('🥗 识膳智行', pad, 78)
  ctx.font = '16px "Microsoft YaHei", sans-serif'
  ctx.fillStyle = 'rgba(255,255,255,0.85)'
  ctx.fillText('AI 健康膳食养生 · 我的一周成绩单', pad, 108)

  // 昵称卡
  ctx.fillStyle = 'rgba(255,255,255,0.18)'
  roundRect(ctx, pad, 150, W - pad * 2, 74, 14)
  ctx.fill()
  ctx.fillStyle = '#fff'
  ctx.font = 'bold 24px "Microsoft YaHei", sans-serif'
  ctx.fillText(`${auth.user?.nickname || '健康达人'} 的健康档案`, pad + 22, 196)

  // 核心指标
  const stats: [string, string, string][] = [
    [`📅`, `${ov.streak_days}`, '连续打卡天数'],
    [`🔥`, `${ov.today_calories}`, '今日摄入 kcal'],
    [`🏃`, `${ov.today_burn}`, '今日消耗 kcal'],
    [`💰`, `${ov.points_total}`, '累计积分'],
  ]
  stats.forEach((s, i) => {
    const x = pad + (i % 2) * ((W - pad * 2) / 2 + 8)
    const y = 256 + Math.floor(i / 2) * 132
    ctx.fillStyle = 'rgba(255,255,255,0.18)'
    roundRect(ctx, x, y, (W - pad * 2 - 8) / 2, 116, 14)
    ctx.fill()
    ctx.fillStyle = '#fff'
    ctx.font = '22px "Microsoft YaHei", sans-serif'
    ctx.fillText(s[0] + ' ' + s[2], x + 20, y + 38)
    ctx.font = 'bold 40px "Microsoft YaHei", sans-serif'
    ctx.fillText(s[1], x + 20, y + 92)
  })

  // 近 7 天摄入迷你柱状图
  const chartY = 560
  ctx.fillStyle = '#fff'
  ctx.font = 'bold 18px "Microsoft YaHei", sans-serif'
  ctx.fillText('近 7 天热量摄入（kcal）', pad, chartY - 14)
  const pts = trends.points.slice(-7)
  const maxCal = Math.max(...pts.map((p) => p.calories), 1)
  const bw = 44
  const gap = (W - pad * 2 - bw * 7) / 6
  pts.forEach((p, i) => {
    const h = Math.max((p.calories / maxCal) * 130, 4)
    const x = pad + i * (bw + gap)
    ctx.fillStyle = 'rgba(255,255,255,0.35)'
    roundRect(ctx, x, chartY + 130 - 0, bw, 0, 0)
    ctx.fillStyle = i === 6 ? '#ffe27a' : 'rgba(255,255,255,0.75)'
    roundRect(ctx, x, chartY + 148 - h, bw, h, 6)
    ctx.fill()
    ctx.fillStyle = 'rgba(255,255,255,0.9)'
    ctx.font = '12px "Microsoft YaHei", sans-serif'
    ctx.fillText(p.date.slice(5), x + 6, chartY + 168)
    ctx.fillText(String(Math.round(p.calories)), x + 6, chartY + 140 - h)
  })

  // 底部 slogan + 日期
  ctx.fillStyle = '#fff'
  ctx.font = 'bold 20px "Microsoft YaHei", sans-serif'
  ctx.fillText('吃动平衡，健康一生 🌱', pad, 800)
  ctx.font = '14px "Microsoft YaHei", sans-serif'
  ctx.fillStyle = 'rgba(255,255,255,0.75)'
  ctx.fillText(`生成于 ${new Date().toLocaleDateString('zh-CN')} · 识膳智行 AI 健康管理平台`, pad, 828)
}

function roundRect(ctx: CanvasRenderingContext2D, x: number, y: number, w: number, h: number, r: number) {
  ctx.beginPath()
  ctx.moveTo(x + r, y)
  ctx.arcTo(x + w, y, x + w, y + h, r)
  ctx.arcTo(x + w, y + h, x, y + h, r)
  ctx.arcTo(x, y + h, x, y, r)
  ctx.arcTo(x, y, x + w, y, r)
  ctx.closePath()
}

function download() {
  const url = canvasEl.value!.toDataURL('image/png')
  const a = document.createElement('a')
  a.href = url
  a.download = `识膳智行健康海报_${new Date().toISOString().slice(0, 10)}.png`
  a.click()
  ElMessage.success('海报已下载')
}
</script>

<template>
  <el-dialog :model-value="visible" title="🖼 分享海报" width="460px" @update:model-value="(v: boolean) => emit('update:visible', v)">
    <div v-loading="loading" style="min-height:420px;display:flex;justify-content:center">
      <canvas v-show="canvasReady" ref="canvasEl" style="width:330px;height:454px;border-radius:10px;box-shadow:0 4px 16px rgba(0,0,0,.15)" />
    </div>
    <template #footer>
      <el-button @click="emit('update:visible', false)">关闭</el-button>
      <el-button type="success" :disabled="!canvasReady" @click="download">⬇️ 下载 PNG</el-button>
    </template>
  </el-dialog>
</template>
