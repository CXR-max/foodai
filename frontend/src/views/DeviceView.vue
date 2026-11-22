<script setup lang="ts">
// 我的设备：绑定小米手环 → 同步数据 → 手机网页直接看
// 三个数据来源，最终都落到后端同一张表，页面上不区分入口差别：
//  1) 浏览器蓝牙直连（手机 Chrome）：实时读步数/心率/电量
//  2) 导入手环 App 导出的文件（CSV/JSON/Apple Health XML）
//  3) 演示数据（source=demo，页头会标「演示」，不会冒充真实采集）
import { computed, onActivated, onBeforeUnmount, onMounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Delete, Iphone, Refresh, Upload, VideoPlay } from '@element-plus/icons-vue'
import * as deviceApi from '@/api/device'
import type { DeviceItem, DeviceListResult, DeviceMetric } from '@/api/device'
import { useMiBand } from '@/composables/useMiBand'
import { isMobile } from '@/composables/useIsMobile'
import TrendChart from '@/components/TrendChart.vue'

const band = useMiBand()

const data = ref<DeviceListResult | null>(null)
const summary = ref<deviceApi.DeviceListResult | any>(null)
const rings = ref<deviceApi.RingsResult | null>(null)
const loading = ref(false)
const chartOption = ref({})

const today = computed(() => data.value?.today || summary.value?.today || {})
const stepGoal = computed(() => data.value?.step_goal || summary.value?.step_goal || 8000)

async function load() {
  loading.value = true
  try {
    const [d, s, r] = await Promise.all([
      deviceApi.listDevices(),
      deviceApi.deviceSummary(7),
      deviceApi.deviceRings().catch(() => null),   // 三环拿不到也不挡页面其它数据
    ])
    data.value = d
    summary.value = s
    rings.value = r
    renderChart()
  } finally {
    loading.value = false
  }
}

function renderChart() {
  const series: any[] = summary.value?.series || []
  chartOption.value = {
    tooltip: { trigger: 'axis', valueFormatter: (v: number) => `${v} 步` },
    grid: { left: 8, right: 8, top: 22, bottom: 4, containLabel: true },
    xAxis: {
      type: 'category',
      data: series.map((s) => s.date.slice(5)),
      axisLabel: { fontSize: 10, color: '#909399', interval: isMobile.value ? 1 : 0 },
    },
    yAxis: { type: 'value', axisLabel: { fontSize: 10, color: '#909399' }, splitLine: { lineStyle: { color: '#f0f2f5' } } },
    series: [{
      type: 'bar',
      data: series.map((s) => s.steps || 0),
      barMaxWidth: 18,
      itemStyle: { color: '#409eff', borderRadius: [4, 4, 0, 0] },
      markLine: {
        symbol: 'none', silent: true,
        lineStyle: { color: '#e6a23c', type: 'dashed' },
        // 标签放轴内侧，避免贴右边被容器裁掉
        data: [{ yAxis: stepGoal.value, label: { formatter: '目标', fontSize: 10, position: 'insideStartTop' } }],
      },
    }],
  }
}

// ---------------------------------------------------------------- 实时数据
// 后台每 60 秒采一条样本，这里 15 秒轮询一次：数字会自己往上走，心率会跳
const live = ref<deviceApi.LiveResult | null>(null)
const autoSync = ref(true)
const switching = ref(false)
const tick = ref(Date.now())          // 只用来刷新「x 秒前」这类相对时间
let liveTimer: number | undefined
let tickTimer: number | undefined

async function loadLive() {
  try {
    const r = await deviceApi.deviceLive()
    live.value = r
    autoSync.value = r.auto_sync ?? true
    tick.value = Date.now()
  } catch {
    // 实时是增强功能，拿不到就退回普通汇总，不弹错打扰用户
  }
}

function startTimers() {
  stopTimers()
  liveTimer = window.setInterval(() => {
    if (document.visibilityState === 'visible') loadLive()
    else tick.value = Date.now()
  }, 5000)
  tickTimer = window.setInterval(() => { tick.value = Date.now() }, 5000)
}

function stopTimers() {
  if (liveTimer) { clearInterval(liveTimer); liveTimer = undefined }
  if (tickTimer) { clearInterval(tickTimer); tickTimer = undefined }
}

function onVisible() {
  if (document.visibilityState === 'visible') loadLive()
}

function onSwitchChange(v: string | number | boolean) {
  toggleAutoSync(Boolean(v))
}

async function toggleAutoSync(v: boolean) {
  switching.value = true
  try {
    const r = await deviceApi.setAutoSync(v)
    autoSync.value = r.auto_sync
    ElMessage.success(v ? '已开启自动同步（后台每分钟更新一次）' : '已暂停自动同步')
    await loadLive()
  } catch {
    autoSync.value = !v            // 失败回滚开关，别让用户以为生效了
  } finally {
    switching.value = false
  }
}

// 展示值：有实时就优先用实时，没有就退回今日汇总
const liveSteps = computed(() => live.value?.today?.steps ?? today.value.steps ?? 0)
const liveHr = computed(() => live.value?.today?.hr ?? today.value.hr_avg ?? null)
const liveCal = computed(() => live.value?.today?.calories ?? today.value.calories ?? 0)
const anchorIsDemo = computed(() => live.value?.today?.is_demo ?? (today.value as any)?.source === 'demo')

const updatedText = computed(() => {
  if (!live.value?.updated_at) return '尚未同步'
  const at = new Date(live.value.updated_at.replace(' ', 'T')).getTime()
  if (!at) return '尚未同步'
  const sec = Math.max(0, Math.round((tick.value - at) / 1000))
  if (sec < 20) return '刚刚更新'
  if (sec < 60) return `${sec} 秒前更新`
  const min = Math.round(sec / 60)
  return min < 60 ? `${min} 分钟前更新` : `${Math.round(min / 60)} 小时前更新`
})

// 心率迷你走势：取最近 40 个样本，映射成 0~100 的 y 坐标
const hrSpark = computed(() => {
  const pts = (live.value?.samples || []).slice(-40).filter((s) => s.hr != null)
  if (pts.length < 2) return ''
  const hs = pts.map((s) => s.hr as number)
  const lo = Math.min(...hs) - 3
  const hi = Math.max(...hs) + 3
  return pts.map((s, i) => {
    const x = (i / (pts.length - 1)) * 100
    const y = 34 - ((s.hr as number) - lo) / (hi - lo) * 28
    return `${x.toFixed(1)},${y.toFixed(1)}`
  }).join(' ')
})

// ---------------------------------------------------------------- 蓝牙直连
const bleNotice = ref('')
// 环境自检：把「为什么点不动」摊开给用户看，而不是静默失败
const diag = ref(band.diagnose())
const diagVisible = ref(false)
const helpVisible = ref(false)

function refreshDiag() {
  diag.value = band.diagnose()
}

onMounted(() => {
  load()
  loadLive()
  startTimers()
  document.addEventListener('visibilitychange', onVisible)
  refreshDiag()
  if (diag.value.reason === 'INSECURE')
    bleNotice.value = '当前是 HTTP 地址，浏览器禁用了蓝牙接口。手机请用「启动手机访问.bat」打印的 https 地址；电脑想看界面用 https://localhost:5173'
  else if (!diag.value.canTry)
    bleNotice.value = diag.value.title
})

onActivated(() => {
  load()
  loadLive()
  refreshDiag()
})

onBeforeUnmount(() => {
  stopTimers()
  document.removeEventListener('visibilitychange', onVisible)
})

async function connectBand() {
  refreshDiag()
  if (!diag.value.canTry) {
    diagVisible.value = true
    return
  }
  try {
    const snap = await band.capture()
    if (!snap)
      return

    const name = /band|手环/i.test(snap.ble_name) ? snap.ble_name : `小米手环（${snap.ble_name}）`
    const bound = await deviceApi.bindDevice({
      name, model: snap.firmware ? `固件 ${snap.firmware}` : undefined,
      platform: 'xiaomi', ble_name: snap.ble_name, battery: snap.battery ?? undefined,
      bind_source: 'ble',
    })
    const metric: Partial<DeviceMetric> = {
      steps: snap.steps ?? undefined,
      hr_avg: snap.heart_rate ?? undefined,
      source: 'ble',
    }
    await deviceApi.syncMetrics(bound.device.id, { metrics: [metric], battery: snap.battery ?? undefined })

    const got: string[] = []
    if (snap.steps != null) got.push(`步数 ${snap.steps}`)
    if (snap.heart_rate != null) got.push(`心率 ${snap.heart_rate} 次/分`)
    if (snap.battery != null) got.push(`电量 ${snap.battery}%`)
    ElMessage.success({ message: `同步成功：${got.length ? got.join('，') : '设备信息（未读到运动数据）'}`, duration: 5000 })
    if (snap.unsupported.length)
      ElMessage.warning({ message: `部分数据没读到：${snap.unsupported.join('；')}，可用「导入手环数据」补齐`, duration: 8000 })
    await load()
  } catch (e: any) {
    const msg: string = e?.message || String(e)
    if (msg.includes('CANCELLED')) {
      ElMessage.info('已取消选择手环（系统弹窗里没选设备）')
      return
    }
    refreshDiag()
    if (msg.includes('INSECURE') || msg.includes('安全上下文')) {
      ElMessage.warning({ message: '手机上蓝牙需要 https 入口，点「自检详情」看具体原因', duration: 6000 })
    } else {
      ElMessage.error({ message: `蓝牙同步失败：${msg}`, duration: 8000 })
    }
    diagVisible.value = true
  }
}

// ---------------------------------------------------------------- 文件导入
const fileInput = ref<HTMLInputElement | null>(null)
const importVisible = ref(false)
const importPreviewData = ref<any>(null)
const importingFile = ref<File | null>(null)
const importing = ref(false)

function pickFile() {
  fileInput.value?.click()
}

async function onFileChange(ev: Event) {
  const file = (ev.target as HTMLInputElement).files?.[0]
  ;(ev.target as HTMLInputElement).value = ''
  if (!file) return
  try {
    importingFile.value = file
    importPreviewData.value = await deviceApi.importPreview(file)
    importVisible.value = true
  } catch {
    // 错误已由 http 拦截器弹过，这里只重置状态
    importingFile.value = null
  }
}

function closeImport() {
  importVisible.value = false
  importPreviewData.value = null
  importingFile.value = null
}

async function confirmImport() {
  if (!importingFile.value) return
  importing.value = true
  try {
    const r = await deviceApi.importFile(importingFile.value)
    ElMessage.success(`已导入 ${r.imported} 天数据到「${r.device.name}」`)
    closeImport()
    await load()
  } finally {
    importing.value = false
  }
}

// ---------------------------------------------------------------- 演示 / 打卡
async function simulate() {
  await ElMessageBox.confirm(
    '生成 14 天仿真手环数据（会标注为「演示数据」），用于没有手环时演示页面效果。继续？',
    '生成演示数据', { confirmButtonText: '生成', cancelButtonText: '取消', type: 'info' })
  const r = await deviceApi.simulateData(14)
  ElMessage.success(`已生成 ${r.generated} 天演示数据（${r.device.name}）`)
  await load()
}

async function stepsToCheckin(d: DeviceItem) {
  try {
    const r = await deviceApi.stepsToCheckin(d.id)
    ElMessage.success(`已按手环 ${r.steps} 步生成运动打卡：约 ${r.duration_min} 分钟，消耗 ${r.calories_burned} kcal（+${r.points_awarded} 分）`)
  } catch (e: any) {
    if (e?.response?.status !== 409 && e?.response?.status !== 400) throw e
  }
}

async function unbind(d: DeviceItem) {
  await ElMessageBox.confirm(
    `解绑「${d.name}」？它的历史手环数据会一并删除。`, '解绑设备',
    { confirmButtonText: '解绑', cancelButtonText: '取消', type: 'warning' })
  await deviceApi.removeDevice(d.id)
  ElMessage.success('已解绑')
  await load()
}

const fmtDate = (s?: string | null) => (s ? s.replace('T', ' ').slice(5, 16) : '从未同步')
const fmtSleep = (min?: number | null) =>
  min == null ? '--' : `${Math.floor(min / 60)} 小时 ${min % 60} 分`
const SOURCE_LABEL: Record<string, string> = {
  ble: '蓝牙直连', import: '文件导入', api: '第三方推送', manual: '手动', demo: '演示数据',
}
const percent = computed(() =>
  Math.min(100, Math.round(((liveSteps.value || 0) / stepGoal.value) * 100)))
const heroDash = computed(() => `${2 * Math.PI * 54 * percent.value / 100} ${2 * Math.PI * 54}`)

// 睡眠目标（分钟）：睡眠卡上的「目标 7 小时 30 分」用它
const sleepGoalMinutes = computed(() => rings.value?.sleep?.target || 450)

// ---------------------------------------------------------------- 睡眠结构卡
const sleepDetail = computed(() => rings.value?.sleep_detail || null)
const sleepDateText = computed(() => {
  const d = sleepDetail.value?.date
  if (!d) return ''
  return `${Number(d.slice(5, 7))} 月 ${Number(d.slice(8, 10))} 日晚`
})
const SLEEP_SEGMENTS = [
  { key: 'deep', label: '深睡', color: '#4c6ef5' },
  { key: 'light', label: '浅睡', color: '#91a7ff' },
  { key: 'rem', label: '快速眼动', color: '#b197fc' },
  { key: 'awake', label: '清醒', color: '#ced4da' },
] as const
const sleepSegs = computed(() => {
  const d = sleepDetail.value
  if (!d || !d.total) return []
  return SLEEP_SEGMENTS.map((s) => {
    const min = (d as any)[s.key] || 0
    return { ...s, min, pct: Math.round((min / d.total) * 1000) / 10 }
  }).filter((s) => s.min > 0 || s.key !== 'awake')
})

// 自检项的状态 → 标签颜色
function stateType(s: string): 'success' | 'warning' | 'danger' {
  if (s === 'ok') return 'success'
  if (s === 'warn') return 'warning'
  return 'danger'
}
</script>

<template>
  <div class="page" v-loading="loading">
    <!-- 蓝牙环境提示 -->
    <el-alert v-if="bleNotice" type="warning" :closable="false" class="page-card env-tip"
              :title="bleNotice" />

    <!-- 今日手环数据（手机上的主角，接实时后数字会自己走） -->
    <div class="page-card hero">
      <div class="hero-head">
        <h3 class="page-title" style="margin:0">⌚ 今日手环数据</h3>
        <div class="hero-tags">
          <span class="live-tag" :class="{ off: !autoSync }">
            <i class="pulse" />{{ autoSync ? '实时同步中' : '已暂停' }}
          </span>
          <el-tag v-if="anchorIsDemo" type="info" size="small">模拟基线</el-tag>
          <el-switch v-model="autoSync" size="small" :loading="switching"
                     inline-prompt active-text="自动" inactive-text="手动"
                     @change="onSwitchChange" />
        </div>
      </div>

      <div class="hero-body">
        <div class="ring-wrap">
          <svg viewBox="0 0 120 120" class="ring">
            <circle cx="60" cy="60" r="54" fill="none" stroke="#f0f2f5" stroke-width="9" />
            <circle cx="60" cy="60" r="54" fill="none" stroke="#409eff" stroke-width="9"
                    stroke-linecap="round" :stroke-dasharray="heroDash"
                    transform="rotate(-90 60 60)" />
          </svg>
          <div class="ring-text">
            <b class="ring-num">{{ liveSteps.toLocaleString() }}</b>
            <span class="ring-label">步 / 目标 {{ stepGoal.toLocaleString() }}</span>
          </div>
        </div>

        <div class="metric-grid">
          <div class="metric"><span class="k">距离</span><b>{{ (today.distance_km || 0).toFixed(2) }}</b><span class="u">km</span></div>
          <div class="metric"><span class="k">活动消耗</span><b>{{ Math.round(liveCal) }}</b><span class="u">kcal</span></div>
          <div class="metric"><span class="k">活动时长</span><b>{{ today.active_minutes || 0 }}</b><span class="u">分钟</span></div>
          <div class="metric"><span class="k">实时心率</span><b>{{ liveHr ?? '--' }}</b><span class="u">次/分</span></div>
          <div class="metric"><span class="k">睡眠</span><b class="sleep">{{ fmtSleep(today.sleep_minutes) }}</b></div>
          <div class="metric"><span class="k">静息心率</span><b>{{ live?.today?.hr_resting ?? '--' }}</b><span class="u">次/分</span></div>
        </div>
      </div>

      <!-- 心率迷你走势 + 数据来源说明 -->
      <div class="live-foot">
        <svg viewBox="0 0 100 40" preserveAspectRatio="none" class="spark">
          <polyline v-if="hrSpark" :points="hrSpark" fill="none" stroke="#f56c6c"
                    stroke-width="1.4" stroke-linejoin="round" stroke-linecap="round" />
        </svg>
        <span class="live-note">
          {{ updatedText }} · 每 {{ live?.interval || 60 }} 秒自动刷新 ·
          <template v-if="anchorIsDemo">还没有真实数据，按你的档案生成模拟基线</template>
          <template v-else>锚定今日真实总量 {{ (live?.today?.anchor_steps || 0).toLocaleString() }} 步</template>
        </span>
      </div>
    </div>

    <!-- 近 7 天步数 -->
    <div class="page-card">
      <h3 class="page-title">📊 近 7 天步数</h3>
      <TrendChart :option="chartOption" height="200px" />
      <p class="sum-line">
        均值 <b>{{ summary?.avg_steps?.toLocaleString() || 0 }}</b> 步/天 ·
        累计 <b>{{ summary?.total_steps?.toLocaleString() || 0 }}</b> 步 ·
        平均心率 <b>{{ summary?.avg_hr || '--' }}</b> 次/分 ·
        平均睡眠 <b>{{ summary?.avg_sleep_minutes ? fmtSleep(summary.avg_sleep_minutes) : '--' }}</b>
      </p>
    </div>

    <!-- 睡眠记录卡（最近一晚的结构） -->
    <div class="page-card">
      <div class="sleep-head">
        <h3 class="page-title" style="margin:0">😴 睡眠记录</h3>
        <span v-if="sleepDateText" class="sleep-date">{{ sleepDateText }}</span>
      </div>

      <template v-if="sleepDetail">
        <div class="sleep-body">
          <div class="sleep-total">
            <b>{{ fmtSleep(sleepDetail.total) }}</b>
            <span>目标 {{ fmtSleep(sleepGoalMinutes) }}</span>
          </div>
          <div class="sleep-chart">
            <div class="sleep-bar">
              <i v-for="s in sleepSegs" :key="s.key"
                 :style="{ width: `${s.pct}%`, background: s.color }" />
            </div>
            <div class="sleep-legend">
              <span v-for="s in sleepSegs" :key="s.key" class="lg-item">
                <i :style="{ background: s.color }" />
                {{ s.label }} <b>{{ s.min }}</b> 分 · {{ s.pct }}%
              </span>
            </div>
          </div>
        </div>
        <p class="sleep-comment">{{ sleepDetail.comment }}</p>
      </template>
      <p v-else class="empty-tip">
        还没有睡眠数据。用「导入手环数据」上传小米运动健康导出包，或开启自动同步后这里会显示最近一晚的睡眠结构。
      </p>
    </div>

    <!-- 操作区 -->
    <div class="page-card">
      <h3 class="page-title">🔗 连接手环</h3>
      <div class="action-row">
        <el-button type="primary" size="large" :loading="band.connecting.value" @click="connectBand">
          {{ band.connecting.value ? '蓝牙同步中…' : '🥽 蓝牙连接手环' }}
        </el-button>
        <el-button size="large" :icon="Upload" @click="pickFile">导入手环数据</el-button>
        <el-button size="large" :icon="VideoPlay" @click="simulate">生成演示数据</el-button>
      </div>
      <p class="action-tip">
        蓝牙直连能读「电量 / 实时心率」，小米手环 4 以后步数加密，读不到属于正常；
        手环 10 的完整数据请用「导入手环数据」（支持小米运动健康导出的 ZIP 数据包）。
      </p>

      <!-- 蓝牙环境状态：不可用时就地说清原因，绝不静默 -->
      <div class="ble-status">
        <el-tag :type="diag.canTry ? 'success' : 'danger'" size="small" effect="plain">
          {{ diag.canTry ? '蓝牙环境可用' : '蓝牙不可用' }}
        </el-tag>
        <span class="ble-status-text">{{ diag.title }}</span>
        <el-button link type="primary" size="small" @click="diagVisible = true">自检详情</el-button>
        <el-button link type="primary" size="small" @click="helpVisible = true">手环数据怎么上传</el-button>
      </div>

      <input ref="fileInput" type="file" accept=".csv,.json,.xml,.txt,.zip" style="display:none" @change="onFileChange">
    </div>

    <!-- 已绑定设备 -->
    <div v-for="d in data?.items || []" :key="d.id" class="page-card device-card">
      <div class="device-head">
        <div>
          <b class="device-name">{{ d.name }}</b>
          <el-tag v-if="d.status === 'bound'" type="success" size="small" effect="plain">已绑定</el-tag>
          <el-tag v-else type="info" size="small" effect="plain">未连接</el-tag>
        </div>
        <div class="device-meta">
          <span v-if="d.battery != null" class="battery" :class="{ low: d.battery <= 20 }">🔋 {{ d.battery }}%</span>
          <span class="sync-time">{{ fmtDate(d.last_sync_at) }}（{{ SOURCE_LABEL[d.last_sync_source || ''] || '未同步' }}）</span>
        </div>
      </div>

      <div v-if="d.today" class="device-today">
        <span>今日 <b>{{ (d.today.steps || 0).toLocaleString() }}</b> 步</span>
        <span>心率 <b>{{ d.today.hr_avg ?? '--' }}</b></span>
        <span>消耗 <b>{{ Math.round(d.today.calories || 0) }}</b> kcal</span>
        <span>来源 <b>{{ SOURCE_LABEL[d.today.source] || d.today.source }}</b></span>
      </div>

      <div class="device-actions">
        <el-button size="small" type="primary" plain :icon="Refresh" :loading="band.connecting.value" @click="connectBand">蓝牙同步</el-button>
        <el-button size="small" type="success" plain :icon="Iphone" @click="stepsToCheckin(d)">步数转运动打卡</el-button>
        <el-button size="small" type="danger" plain :icon="Delete" @click="unbind(d)">解绑</el-button>
      </div>
    </div>

    <div v-if="data && !data.items.length" class="page-card empty-card">
      <p class="empty-tip">还没有绑定设备。戴上小米手环点上面「蓝牙连接手环」，或先用「生成演示数据」看看页面效果。</p>
    </div>

    <!-- 导入预览确认 -->
    <el-dialog v-model="importVisible" title="确认导入" width="420px">
      <template v-if="importPreviewData">
        <el-descriptions :column="1" border size="small">
          <el-descriptions-item label="文件">{{ importingFile?.name }}</el-descriptions-item>
          <el-descriptions-item label="解析到">{{ importPreviewData.days }} 天数据</el-descriptions-item>
          <el-descriptions-item label="日期范围">{{ importPreviewData.start }} ~ {{ importPreviewData.end }}</el-descriptions-item>
          <el-descriptions-item label="包含字段">{{ (importPreviewData.fields || []).join('、') || '无' }}</el-descriptions-item>
        </el-descriptions>
        <el-table :data="importPreviewData.preview" size="small" style="margin-top:10px">
          <el-table-column v-for="f in (['metric_date', ...(importPreviewData.fields || [])]).slice(0, 6)"
                           :key="f" :prop="f" :label="f === 'metric_date' ? '日期' : f" min-width="70" />
        </el-table>
      </template>
      <template #footer>
        <el-button @click="closeImport">取消</el-button>
        <el-button type="primary" :loading="importing" @click="confirmImport">确认导入</el-button>
      </template>
    </el-dialog>

    <!-- 蓝牙环境自检 -->
    <el-dialog v-model="diagVisible" title="蓝牙环境自检" width="92%" style="max-width:460px">
      <div class="diag-list">
        <div v-for="it in diag.items" :key="it.label" class="diag-item">
          <div class="diag-row">
            <span class="diag-label">{{ it.label }}</span>
            <el-tag :type="stateType(it.state)" size="small" effect="plain">
              {{ it.state === 'ok' ? '通过' : it.state === 'warn' ? '注意' : '不满足' }}
            </el-tag>
          </div>
          <p class="diag-value">{{ it.value }}</p>
          <p v-if="it.hint" class="diag-hint">{{ it.hint }}</p>
        </div>
      </div>

      <div class="diag-advice">
        <b>怎么让手环数据进到网页：</b>
        <p v-if="diag.reason === 'WECHAT'">点右上角「…」→「在浏览器打开」，用 Chrome 打开同一个 https 地址再点蓝牙。</p>
        <p v-else-if="diag.reason === 'IOS'">iPhone 上浏览器都不支持，请用电脑 Chrome 连，或在手机上用「导入手环数据」。</p>
        <p v-else-if="diag.reason === 'INSECURE'">把地址栏的 http 换成 https（手机用启动脚本打印的 <code>https://192.168.x.x:5173</code>），首次访问点「高级 → 继续前往」。</p>
        <p v-else-if="diag.reason === 'NO_BLUETOOTH'">换 Chrome / Edge 打开；小米自带浏览器、UC、夸克里可能没有这个接口。</p>
        <p>即使环境通过，小米手环 4 以后（含手环 10）的步数通道加密，浏览器只能读到电量和心率。
          <b>要看步数/睡眠，用下面的「导入手环数据」。</b></p>
      </div>

      <template #footer>
        <el-button @click="refreshDiag">重新自检</el-button>
        <el-button type="primary" @click="helpVisible = true; diagVisible = false">手环数据怎么上传</el-button>
      </template>
    </el-dialog>

    <!-- 手环 10 真实数据上传指引 -->
    <el-dialog v-model="helpVisible" title="把小米手环 10 的数据放到网页上" width="92%" style="max-width:520px">
      <ol class="help-steps">
        <li>
          <b>在「小米运动健康」App 里导出数据</b>
          <p>我的 → 设置 → 找「数据导出 / 个人信息副本 / 导出数据」（版本不同位置略有差异，也可在设置里搜「导出」）。
            App 会把数据包发到你的小米账号邮箱，或在手机文件里生成一个 <code>.zip</code>。</p>
        </li>
        <li>
          <b>把 zip 直接传上来</b>
          <p>点下面的「导入手环数据」，选这个 zip。系统会自动解开、找到里面的 CSV/JSON，按天合并成步数/心率/睡眠。</p>
        </li>
        <li>
          <b>确认预览再入库</b>
          <p>预览会显示「解析到几天、日期范围、包含哪些字段」，确认无误后入库。
            导入后仪表盘、数据追踪、运动打卡都会用这份真实数据。</p>
        </li>
      </ol>
      <el-alert type="info" :closable="false" show-icon
                title="没带手环 / 还没导出也能先看效果：点「生成演示数据」，页面会标注「演示数据」。" />
      <template #footer>
        <el-button @click="helpVisible = false">知道了</el-button>
        <el-button type="primary" @click="helpVisible = false; pickFile()">去选文件</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
.env-tip { padding: 10px 14px; }

.hero-head { display: flex; align-items: center; justify-content: space-between; gap: 10px; flex-wrap: wrap; }
.hero-tags { display: flex; align-items: center; gap: 8px; }
.live-tag {
  display: inline-flex; align-items: center; gap: 5px; font-size: 11px;
  color: #67c23a; background: #f0f9eb; border-radius: 10px; padding: 2px 8px;
}
.live-tag.off { color: #909399; background: #f4f4f5; }
.pulse {
  width: 6px; height: 6px; border-radius: 50%; background: #67c23a;
  animation: pulse 1.6s ease-in-out infinite;
}
.live-tag.off .pulse { background: #c0c4cc; animation: none; }
@keyframes pulse {
  0%, 100% { opacity: 1; transform: scale(1); }
  50% { opacity: .35; transform: scale(.7); }
}
.live-foot {
  margin-top: 12px; display: flex; align-items: center; gap: 10px;
  border-top: 1px dashed #ebeef5; padding-top: 8px;
}
.spark { width: 96px; height: 34px; flex: none; }
.live-note { font-size: 11px; color: #909399; line-height: 1.5; }

.hero-body { display: flex; gap: 18px; align-items: center; flex-wrap: wrap; }
.ring-wrap { position: relative; width: 148px; height: 148px; flex: none; }
.ring { width: 100%; height: 100%; }
.ring-text {
  position: absolute; inset: 0; display: flex; flex-direction: column;
  align-items: center; justify-content: center; gap: 2px;
}
.ring-num { font-size: 22px; font-weight: 700; }
.ring-label { font-size: 10px; color: #909399; text-align: center; }

.metric-grid {
  flex: 1; min-width: 260px;
  display: grid; grid-template-columns: repeat(3, 1fr); gap: 10px 8px;
}
.metric {
  display: flex; flex-direction: column; background: #f7f9fc;
  border-radius: 10px; padding: 10px 12px; gap: 2px;
}
.metric .k { font-size: 11px; color: #909399; }
.metric b { font-size: 17px; color: #303133; }
.metric .sleep { font-size: 14px; }
.metric .u { font-size: 11px; color: #909399; }

.sum-line { font-size: 12px; color: #909399; margin: 10px 0 0; }
.sum-line b { color: #303133; }

/* ---------------- 睡眠记录卡 ---------------- */
.sleep-head { display: flex; align-items: baseline; justify-content: space-between; gap: 10px; }
.sleep-date { font-size: 12px; color: #909399; }
.sleep-body {
  display: flex; gap: 22px; align-items: center; flex-wrap: wrap;
  margin-top: 12px;
}
.sleep-total { display: flex; flex-direction: column; gap: 4px; flex: none; }
.sleep-total b { font-size: 22px; font-weight: 700; color: #303133; }
.sleep-total span { font-size: 11px; color: #909399; }
.sleep-chart { flex: 1 1 280px; min-width: 260px; display: flex; flex-direction: column; gap: 10px; }
.sleep-bar {
  display: flex; height: 16px; border-radius: 8px; overflow: hidden;
  background: #f0f2f5;
}
.sleep-bar i { display: block; height: 100%; }
.sleep-legend { display: flex; flex-wrap: wrap; gap: 8px 16px; }
.lg-item { display: inline-flex; align-items: center; gap: 5px; font-size: 12px; color: #606266; }
.lg-item i { width: 10px; height: 10px; border-radius: 3px; }
.lg-item b { color: #303133; }
.sleep-comment {
  margin: 12px 0 0; padding: 8px 12px; border-radius: 8px;
  background: #f7f9fc; font-size: 12px; color: #606266; line-height: 1.6;
}

.action-row { display: flex; gap: 10px; flex-wrap: wrap; }
.action-tip { font-size: 12px; color: #909399; margin: 12px 0 0; line-height: 1.6; }

.ble-status {
  display: flex; align-items: center; gap: 8px; flex-wrap: wrap;
  margin-top: 10px; padding: 8px 12px; border-radius: 8px; background: #f7f9fc;
}
.ble-status-text { font-size: 12px; color: #606266; flex: 1 1 200px; }

.diag-list { display: flex; flex-direction: column; gap: 10px; }
.diag-item { border: 1px solid #ebeef5; border-radius: 8px; padding: 10px 12px; }
.diag-row { display: flex; justify-content: space-between; align-items: center; gap: 8px; }
.diag-label { font-size: 13px; font-weight: 600; color: #303133; }
.diag-value { margin: 6px 0 0; font-size: 12px; color: #606266; word-break: break-all; }
.diag-hint { margin: 4px 0 0; font-size: 12px; color: #e6a23c; line-height: 1.5; }
.diag-advice { margin-top: 14px; padding: 10px 12px; background: #f7f9fc; border-radius: 8px; font-size: 12px; color: #606266; line-height: 1.7; }
.diag-advice p { margin: 4px 0 0; }
.diag-advice code { background: #eef2f7; padding: 1px 4px; border-radius: 3px; }

.help-steps { margin: 0; padding-left: 18px; font-size: 13px; color: #303133; line-height: 1.7; }
.help-steps li { margin-bottom: 10px; }
.help-steps p { margin: 2px 0 0; font-size: 12px; color: #909399; line-height: 1.6; }
.help-steps code { background: #eef2f7; padding: 1px 4px; border-radius: 3px; }

.device-head { display: flex; justify-content: space-between; gap: 10px; flex-wrap: wrap; align-items: baseline; }
.device-name { font-size: 15px; margin-right: 8px; }
.device-meta { display: flex; gap: 12px; font-size: 12px; color: #909399; }
.battery.low { color: #f56c6c; }
.device-today {
  display: flex; gap: 16px; flex-wrap: wrap; font-size: 13px; color: #606266;
  background: #f7f9fc; border-radius: 8px; padding: 8px 12px; margin: 10px 0;
}
.device-actions { display: flex; gap: 8px; flex-wrap: wrap; }

.empty-card { text-align: center; padding: 40px 20px; }
.empty-tip { color: #909399; font-size: 13px; margin: 0; }

/* 手机上：指标网格 2 列、圆环居中 */
@media (max-width: 768px) {
  .hero-body { flex-direction: column; }
  .ring-wrap { width: 132px; height: 132px; }
  .metric-grid { grid-template-columns: repeat(2, 1fr); width: 100%; }
  .action-row :deep(.el-button) { flex: 1 1 100%; margin-left: 0; }
  .device-actions :deep(.el-button) { flex: 1 1 45%; margin-left: 0; }
  .sleep-body { flex-direction: column; align-items: flex-start; gap: 14px; }
  /* 桌面端 flex-basis:280px 是横向基准；竖排后它会变成「高度」把卡片撑高，这里必须重置 */
  .sleep-chart { flex: none; width: 100%; }
}
</style>
