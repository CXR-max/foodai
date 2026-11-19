<script setup lang="ts">
// 轨迹地图（Leaflet + 高德栅格瓦片）
// - 底图：高德瓦片（免 key；失败自动降级网格）
// - 轨迹：Canvas 渲染；按配速分段着色；公里牌、起终点、精度圈
// - 实时：按 points.length 增量 addLatLng，不在每个点上重画全部
// - 跟随：用户拖动后自动关闭跟随，出现「回到当前位置」
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import L from 'leaflet'
import 'leaflet/dist/leaflet.css'
import { haversineM } from '@/utils/workout'
import type { TrackPoint } from '@/platform/location'

const props = withDefaults(defineProps<{
  points: TrackPoint[]
  /** 运动中：加当前位置蓝点、跟随 */
  live?: boolean
  height?: string
  /** 骑行看均速，跑步/快走看配速 */
  sport?: string
  /** 是否画公里牌 */
  showKm?: boolean
}>(), { live: false, height: '260px', sport: '跑步', showKm: true })

const wrap = ref<HTMLDivElement | null>(null)
const offline = ref(false)
const loading = ref(true)
const follow = ref(true)
const hasPoints = computed(() => props.points.length > 0)
const showRecenter = computed(() => props.live && !follow.value && hasPoints.value)

const TILE_URL = ((import.meta.env.VITE_MAP_TILES as string | undefined)
  ?? 'https://webrd0{s}.is.autonavi.com/appmaptile?lang=zh_cn&size=1&scale=1&style=8&x={x}&y={y}&z={z}').trim()
const USE_TILES = !!TILE_URL && TILE_URL !== 'off'
// 国内瓦片（高德/腾讯）是 GCJ-02
const USE_GCJ = /autonavi|amap|map\.qq\.com/.test(TILE_URL)

let map: L.Map | null = null
let tileLayer: L.TileLayer | null = null
let casing: L.Polyline | null = null
let segments: { color: string; line: L.Polyline }[] = []
let kmLayer: L.LayerGroup | null = null
let startMarker: L.Marker | null = null
let endMarker: L.Marker | null = null
let liveMarker: L.Marker | null = null
let accCircle: L.Circle | null = null
let ro: ResizeObserver | null = null
let lastFollow = 0
let tileOk = 0
let tileErr = 0

/* ---------------- WGS-84 → GCJ-02 ---------------- */
const PI = Math.PI
const AXIS = 6378245.0
const EE = 0.00669342162296594323

const outOfChina = (lat: number, lng: number) =>
  !(lng > 73.66 && lng < 135.05 && lat > 3.86 && lat < 53.55)

function transformLat(x: number, y: number) {
  let ret = -100.0 + 2.0 * x + 3.0 * y + 0.2 * y * y + 0.1 * x * y + 0.2 * Math.sqrt(Math.abs(x))
  ret += (20.0 * Math.sin(6.0 * x * PI) + 20.0 * Math.sin(2.0 * x * PI)) * 2.0 / 3.0
  ret += (20.0 * Math.sin(y * PI) + 40.0 * Math.sin((y / 3.0) * PI)) * 2.0 / 3.0
  ret += (160.0 * Math.sin((y / 12.0) * PI) + 320 * Math.sin((y * PI) / 30.0)) * 2.0 / 3.0
  return ret
}

function transformLng(x: number, y: number) {
  let ret = 300.0 + x + 2.0 * y + 0.1 * x * x + 0.1 * x * y + 0.1 * Math.sqrt(Math.abs(x))
  ret += (20.0 * Math.sin(6.0 * x * PI) + 20.0 * Math.sin(2.0 * x * PI)) * 2.0 / 3.0
  ret += (20.0 * Math.sin(x * PI) + 40.0 * Math.sin((x / 3.0) * PI)) * 2.0 / 3.0
  ret += (150.0 * Math.sin((x / 12.0) * PI) + 300.0 * Math.sin((x / 30.0) * PI)) * 2.0 / 3.0
  return ret
}

function wgs84ToGcj02(lat: number, lng: number) {
  if (outOfChina(lat, lng)) return { lat, lng }
  let dLat = transformLat(lng - 105.0, lat - 35.0)
  let dLng = transformLng(lng - 105.0, lat - 35.0)
  const radLat = (lat / 180.0) * PI
  let magic = Math.sin(radLat)
  magic = 1 - EE * magic * magic
  const sqrtMagic = Math.sqrt(magic)
  dLat = (dLat * 180.0) / (((AXIS * (1 - EE)) / (magic * sqrtMagic)) * PI)
  dLng = (dLng * 180.0) / ((AXIS / sqrtMagic) * Math.cos(radLat) * PI)
  return { lat: lat + dLat, lng: lng + dLng }
}

function toLatLng(p: { lat: number; lng: number }) {
  if (!USE_GCJ) return L.latLng(p.lat, p.lng)
  const g = wgs84ToGcj02(p.lat, p.lng)
  return L.latLng(g.lat, g.lng)
}

/* ---------------- 图标 ---------------- */
function dotIcon(text: string, cls: string) {
  return L.divIcon({
    className: 'pt-wrap',
    html: `<div class="pt-badge ${cls}">${text}</div>`,
    iconSize: [26, 26],
    iconAnchor: [13, 13],
  })
}

function kmIcon(n: number) {
  return L.divIcon({
    className: 'km-wrap',
    html: `<div class="km-badge">${n}</div>`,
    iconSize: [26, 18],
    iconAnchor: [13, 9],
  })
}

function liveIcon() {
  return L.divIcon({
    className: 'live-wrap',
    html: '<div class="live-dot"><i></i></div>',
    iconSize: [22, 22],
    iconAnchor: [11, 11],
  })
}

/* ---------------- 配速着色 ---------------- */
function paceColorFor(a: TrackPoint, b: TrackPoint): string {
  const d = haversineM(a, b)
  const dt = b.t - a.t
  if (d < 1 || dt <= 0) return '#22c55e'
  const pace = dt / (d / 1000) // 秒/km
  if (props.sport.includes('骑')) {
    const kmh = 3600 / pace
    if (kmh >= 20) return '#f56c6c'
    if (kmh >= 13) return '#e6a23c'
    return '#67c23a'
  }
  if (props.sport.includes('走')) {
    if (pace <= 480) return '#f56c6c'
    if (pace <= 600) return '#e6a23c'
    return '#67c23a'
  }
  if (pace <= 330) return '#f56c6c'
  if (pace <= 420) return '#e6a23c'
  return '#67c23a'
}

/* ---------------- 地图初始化 ---------------- */
function addTiles() {
  if (!map) return
  tileOk = 0
  tileErr = 0
  offline.value = false
  loading.value = true
  if (tileLayer) { tileLayer.remove(); tileLayer = null }
  if (!USE_TILES) { offline.value = true; loading.value = false; return }

  tileLayer = L.tileLayer(TILE_URL, {
    subdomains: ['1', '2', '3', '4'],
    maxZoom: 19,
    maxNativeZoom: 18,
    minZoom: 3,
    keepBuffer: 4,
    updateWhenIdle: false,
    attribution: USE_GCJ ? '© 高德地图' : '',
  })
  tileLayer.on('loading', () => { loading.value = true })
  tileLayer.on('load', () => { loading.value = false })
  tileLayer.on('tileload', () => { tileOk++; loading.value = false })
  tileLayer.on('tileerror', () => {
    tileErr++
    // 连续失败且一张都没成功 → 判定底图不可用，退化为网格
    if (tileErr > 5 && tileOk === 0) {
      offline.value = true
      loading.value = false
    }
  })
  tileLayer.addTo(map)
}

function initMap() {
  if (map || !wrap.value) return
  map = L.map(wrap.value, {
    zoomControl: false,
    attributionControl: true,
    preferCanvas: true,
    inertia: true,
  }).setView([31.2304, 121.4737], 15)

  addTiles()
  L.control.zoom({ position: 'bottomright' }).addTo(map)
  L.control.scale({ imperial: false, position: 'bottomleft' }).addTo(map)

  // 用户一拖动/缩放，就关掉跟随，避免地图抢镜头
  map.on('dragstart', () => { if (props.live) follow.value = false })
  map.on('zoomstart', () => {
    // 双击/按钮缩放不算“用户想离开”，只处理拖拽
  })
}

function clearLayers() {
  casing?.remove(); casing = null
  segments.forEach((s) => s.line.remove()); segments = []
  kmLayer?.clearLayers()
  startMarker?.remove(); startMarker = null
  endMarker?.remove(); endMarker = null
  liveMarker?.remove(); liveMarker = null
  accCircle?.remove(); accCircle = null
}

function addKmMarkers(pts: TrackPoint[]) {
  if (!map || !kmLayer || !props.showKm) return
  let total = 0
  let nextKm = 1
  for (let i = 1; i < pts.length; i++) {
    total += haversineM(pts[i - 1], pts[i])
    if (total >= nextKm * 1000) {
      L.marker(toLatLng(pts[i]), { icon: kmIcon(nextKm), interactive: false }).addTo(kmLayer)
      nextKm++
    }
  }
}

function updateAccuracy(last: TrackPoint) {
  if (!map) return
  const acc = last.acc || 0
  if (acc > 10 && acc <= 50) {
    if (!accCircle) {
      accCircle = L.circle(toLatLng(last), {
        radius: acc, color: '#409eff', weight: 1,
        fillColor: '#409eff', fillOpacity: 0.12, interactive: false,
      }).addTo(map)
    } else {
      accCircle.setLatLng(toLatLng(last))
      accCircle.setRadius(acc)
    }
  } else if (accCircle) {
    accCircle.remove(); accCircle = null
  }
}

function updateMarkers(pts: TrackPoint[]) {
  if (!map || !pts.length) return
  const last = pts[pts.length - 1]
  if (props.showKm) {
    // 公里牌由 addKmMarkers 统一重画，增量时只在跨公里时追加
  }
  // 只有一个点时先不画“起”，避免和当前位置蓝点重叠
  if (pts.length > 1 && !startMarker) {
    startMarker = L.marker(toLatLng(pts[0]), { icon: dotIcon('起', 'start'), interactive: false }).addTo(map)
  }
  if (props.live) {
    if (!liveMarker) {
      liveMarker = L.marker(toLatLng(last), { icon: liveIcon(), interactive: false, zIndexOffset: 500 }).addTo(map)
    } else {
      liveMarker.setLatLng(toLatLng(last))
    }
  } else {
    if (!endMarker) {
      endMarker = L.marker(toLatLng(last), { icon: dotIcon('终', 'end'), interactive: false }).addTo(map)
    } else {
      endMarker.setLatLng(toLatLng(last))
    }
  }
}

function rebuild(fit = true) {
  if (!map) return
  clearLayers()
  const pts = props.points
  if (!pts.length) return

  const ll = pts.map(toLatLng)

  // 白色描边：轨迹在浅色底图上更清楚
  casing = L.polyline(ll, {
    color: '#ffffff', weight: 8, opacity: 0.9,
    lineJoin: 'round', lineCap: 'round', interactive: false,
  }).addTo(map)

  // 按配速分段着色
  for (let i = 1; i < pts.length; i++) {
    const color = paceColorFor(pts[i - 1], pts[i])
    const last = segments[segments.length - 1]
    if (last && last.color === color) {
      last.line.addLatLng(ll[i])
    } else {
      const line = L.polyline([ll[i - 1], ll[i]], {
        color, weight: 5, opacity: 0.95,
        lineJoin: 'round', lineCap: 'round', interactive: false,
      }).addTo(map)
      segments.push({ color, line })
    }
  }

  if (!kmLayer) {
    kmLayer = L.layerGroup().addTo(map)
  }
  addKmMarkers(pts)
  updateMarkers(pts)
  updateAccuracy(pts[pts.length - 1])

  if (fit) {
    if (pts.length === 1) {
      map.setView(ll[0], 17)
    } else {
      const b = L.latLngBounds(ll)
      if (b.isValid()) map.fitBounds(b, { padding: [28, 28], maxZoom: 17 })
    }
  }
}

function appendPoint(p: TrackPoint) {
  if (!map) return
  const pts = props.points
  if (pts.length <= 1) { rebuild(); return }

  const prev = pts[pts.length - 2]
  const ll = toLatLng(p)
  const prevLL = toLatLng(prev)

  casing?.addLatLng(ll)

  const color = paceColorFor(prev, p)
  const last = segments[segments.length - 1]
  if (last && last.color === color) {
    last.line.addLatLng(ll)
  } else {
    const line = L.polyline([prevLL, ll], {
      color, weight: 5, opacity: 0.95,
      lineJoin: 'round', lineCap: 'round', interactive: false,
    }).addTo(map)
    segments.push({ color, line })
  }

  updateMarkers(pts)
  updateAccuracy(p)

  // 跨整公里时补一个公里牌
  if (kmLayer && props.showKm && pts.length > 2) {
    let total = 0
    for (let i = 1; i < pts.length; i++) total += haversineM(pts[i - 1], pts[i])
    const before = total - haversineM(prev, p)
    if (Math.floor(total / 1000) > Math.floor(before / 1000)) {
      L.marker(ll, { icon: kmIcon(Math.floor(total / 1000)), interactive: false }).addTo(kmLayer)
    }
  }

  // 跟随：每 4 秒平移一次，避免每秒都跳
  if (props.live && follow.value) {
    const now = Date.now()
    if (now - lastFollow > 4000) {
      lastFollow = now
      map.panTo(ll, { animate: true, duration: 0.8 })
    }
  }
}

function recenter() {
  const pts = props.points
  if (!map || !pts.length) return
  follow.value = true
  map.setView(toLatLng(pts[pts.length - 1]), Math.max(map.getZoom(), 16), { animate: true })
}

function retryTiles() {
  addTiles()
}

onMounted(() => {
  initMap()
  rebuild(true)
  if (wrap.value && typeof ResizeObserver !== 'undefined') {
    ro = new ResizeObserver(() => map?.invalidateSize())
    ro.observe(wrap.value)
  }
})

watch(() => props.points, () => rebuild(true), { deep: false })
watch(() => props.points.length, (len, old) => {
  if (!map) return
  if (len === 0) { clearLayers(); return }
  if (len === 1 || len < old) { rebuild(true); return }
  if (len === old + 1) { appendPoint(props.points[len - 1]); return }
  rebuild(false)
})
watch(() => props.live, () => rebuild(true))

onBeforeUnmount(() => {
  ro?.disconnect()
  ro = null
  map?.remove()
  map = null
  tileLayer = null
  casing = null
  segments = []
  kmLayer = null
  startMarker = null
  endMarker = null
  liveMarker = null
  accCircle = null
})
</script>

<template>
  <div class="track-map" :style="{ height }">
    <div ref="wrap" class="map-canvas"></div>
    <div v-if="!hasPoints" class="empty">暂无轨迹</div>
    <div v-if="loading && hasPoints" class="loading">地图加载中…</div>
    <div v-if="offline" class="tip">
      底图加载失败，轨迹仍可查看
      <button @click="retryTiles">重试</button>
    </div>
    <button v-if="showRecenter" class="recenter" @click="recenter">回到当前位置</button>
  </div>
</template>

<style scoped>
.track-map {
  position: relative;
  width: 100%;
  border-radius: 14px;
  overflow: hidden;
  background-color: #eef1f5;
  background-image:
    linear-gradient(rgba(0, 0, 0, 0.045) 1px, transparent 1px),
    linear-gradient(90deg, rgba(0, 0, 0, 0.045) 1px, transparent 1px);
  background-size: 28px 28px;
}
.map-canvas { width: 100%; height: 100%; background: transparent; }

.empty {
  position: absolute; inset: 0;
  display: flex; align-items: center; justify-content: center;
  font-size: 13px; color: #9aa3af;
  pointer-events: none; z-index: 500;
}

.loading {
  position: absolute; top: 10px; left: 50%; transform: translateX(-50%);
  z-index: 600; padding: 4px 10px; border-radius: 10px;
  background: rgba(255, 255, 255, 0.92); color: #606266;
  font-size: 11px; pointer-events: none;
}

.tip {
  position: absolute; left: 10px; bottom: 10px; z-index: 600;
  display: flex; align-items: center; gap: 6px;
  padding: 5px 9px; border-radius: 8px;
  background: rgba(255, 255, 255, 0.92);
  font-size: 11px; color: #8a94a6;
}
.tip button {
  border: none; background: transparent; color: #409eff;
  font-size: 11px; padding: 0; cursor: pointer;
}

.recenter {
  position: absolute; right: 12px; bottom: 72px; z-index: 600;
  height: 36px; padding: 0 12px; border-radius: 18px;
  border: none; background: #fff; color: #409eff;
  font-size: 12px; box-shadow: 0 2px 10px rgba(0, 0, 0, 0.16);
  cursor: pointer; white-space: nowrap;
}

/* Leaflet 自绘元素（动态插入，需要 :deep） */
:deep(.pt-wrap), :deep(.km-wrap), :deep(.live-wrap) { background: transparent; border: none; }
:deep(.pt-badge) {
  width: 26px; height: 26px; border-radius: 50%;
  display: flex; align-items: center; justify-content: center;
  color: #fff; font-size: 11px; font-weight: 700;
  border: 2px solid #fff; box-shadow: 0 2px 6px rgba(0, 0, 0, 0.25);
}
:deep(.pt-badge.start) { background: #22c55e; }
:deep(.pt-badge.end) { background: #ef4444; }
:deep(.km-badge) {
  height: 18px; min-width: 26px; padding: 0 6px; border-radius: 9px;
  background: rgba(255, 255, 255, 0.95); color: #303133;
  font-size: 10px; font-weight: 700; line-height: 18px; text-align: center;
  box-shadow: 0 1px 4px rgba(0, 0, 0, 0.18);
}
:deep(.live-dot) {
  width: 22px; height: 22px; border-radius: 50%;
  background: #2f7cf6; border: 3px solid #fff;
  box-shadow: 0 2px 8px rgba(47, 124, 246, 0.5);
  position: relative;
}
:deep(.live-dot i) {
  position: absolute; inset: -6px; border-radius: 50%;
  border: 2px solid rgba(47, 124, 246, 0.5);
  animation: live-pulse 1.6s ease-out infinite;
}
@keyframes live-pulse {
  0% { transform: scale(0.6); opacity: 1; }
  100% { transform: scale(1.5); opacity: 0; }
}

/* 版权条弱化，别抢画面 */
:deep(.leaflet-control-attribution) {
  font-size: 9px; background: rgba(255, 255, 255, 0.72);
  color: #8a94a6; padding: 1px 5px;
}
:deep(.leaflet-control-attribution a) { color: #8a94a6; }
:deep(.leaflet-container) {
  font-family: inherit; background: transparent; outline: none;
}
:deep(.leaflet-control-zoom a) {
  width: 30px; height: 30px; line-height: 30px; font-size: 18px;
}
</style>
