<script setup lang="ts">
// 领地地图：后台 25m 网格，前端抽轮廓 + 渐变/发光，画成一块块「地盘」，不画格线。
// 真实轨迹只做「本次跑步」的高亮线；产权边界来自格子合并后的轮廓。
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import L from 'leaflet'
import 'leaflet/dist/leaflet.css'
import { ElMessage, ElMessageBox } from 'element-plus'
import * as territoryApi from '@/api/territory'
import type { TerritoryMapData, TerritoryMe, TerritoryScope } from '@/api/territory'
import * as friendsApi from '@/api/friends'
import type { FriendItem } from '@/api/friends'
import * as workoutApi from '@/api/workout'
import { buildOwnerShapes, cellIxIy, outlineLoops } from '@/utils/territoryShape'

const route = useRoute()
const router = useRouter()
const embed = computed(() => route.query.embed === '1')
/* ---------------- 网格常量（与后端一致） ---------------- */
const CELL_M = 25
const DLAT = CELL_M / 111_320
const DLNG = CELL_M / (111_320 * Math.cos((31.23 * Math.PI) / 180))
const DEFAULT_CENTER: [number, number] = [31.2304, 121.4737]

const TILE_URL = ((import.meta.env.VITE_MAP_TILES as string | undefined)
  ?? 'https://webrd0{s}.is.autonavi.com/appmaptile?lang=zh_cn&size=1&scale=1&style=8&x={x}&y={y}&z={z}').trim()
const USE_GCJ = /autonavi|amap|map\.qq\.com/.test(TILE_URL)

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
function gcj02ToWgs84(lat: number, lng: number) {
  let dLat = 0
  let dLng = 0
  for (let i = 0; i < 3; i++) {
    const g = wgs84ToGcj02(lat - dLat, lng - dLng)
    dLat = g.lat - lat
    dLng = g.lng - lng
  }
  return { lat: lat - dLat, lng: lng - dLng }
}
const toGcj = (lat: number, lng: number) => (USE_GCJ ? wgs84ToGcj02(lat, lng) : { lat, lng })

/* ---------------- 状态 ---------------- */
const mapEl = ref<HTMLDivElement | null>(null)
const me = ref<TerritoryMe | null>(null)
const data = ref<TerritoryMapData | null>(null)
const loading = ref(true)
const initialScope = String(route.query.scope || '')
const scope = ref<TerritoryScope>(
  initialScope === 'friends' ? 'friends' : initialScope === 'me' ? 'me' : 'all',
)
const onlyOwner = ref<number | null>(Number(route.query.owner) || null)
const friendsOpen = ref(false)
const friends = ref<FriendItem[]>([])
const suggestions = ref<FriendItem[]>([])
const friendsLoading = ref(false)
const legendOpen = ref(false)
const neutralHit = ref<{ key: string } | null>(null)

let map: L.Map | null = null
let shapeLayer: L.LayerGroup | null = null
let neutralLayer: L.LayerGroup | null = null
let recapLayer: L.LayerGroup | null = null
let labelLayer: L.LayerGroup | null = null
let moveTimer: number | null = null
let recapTimer: number | null = null
let trackLine: L.Polyline | null = null
let pulseTimer: number | null = null
const pulseKeys = new Set<string>()

const areaBig = computed(() => {
  const m2 = me.value?.area_m2 || 0
  return m2 >= 10000 ? (m2 / 10000).toFixed(2) : `${Math.round(m2)}`
})
const areaUnit = computed(() => ((me.value?.area_m2 || 0) >= 10000 ? '万㎡' : '㎡'))

function fmtArea(m2: number): string {
  if (!m2) return '0 ㎡'
  if (m2 >= 10000) return `${(m2 / 10000).toFixed(2)} 万㎡`
  return `${Math.round(m2)} ㎡`
}

const contestEvent = computed(() => {
  return (me.value?.recent_events || []).find(
    (e) => ['cut', 'steal', 'recapture'].includes(e.type),
  ) || null
})
const contestText = computed(() => {
  const e = contestEvent.value
  if (!e) return ''
  const area = fmtArea(Number(e.meta?.area_m2 || (e.meta?.cells || 0) * 625))
  if (e.type === 'cut') return `${e.actor} 从你这里切走了 ${area}`
  if (e.type === 'recapture') return `你从 ${e.victim || '对方'} 手里抢回了 ${area}`
  return `${e.actor} 抢走了你 ${area}`
})
const onlyOwnerName = computed(() => {
  if (!onlyOwner.value) return ''
  return data.value?.owners[String(onlyOwner.value)]?.nickname || 'TA'
})

/* ---------------- 活力 / 段位 / 街区 ---------------- */
const vitColor = computed(() => {
  const v = me.value?.vitality_avg ?? 0
  if (v >= 60) return '#4f9d2f'
  if (v >= 30) return '#e6a23c'
  return '#f56c6c'
})
/** 「约 3 天后消退」——规则要摆在台面上，玩家才有明天去跑的紧迫感 */
const decayText = computed(() => {
  const d = me.value?.days_left ?? 0
  if (!me.value?.total_cells) return '还没有地盘'
  if (d <= 1) return '明天就可能消退，尽快跑一圈'
  return `约 ${d} 天后消退`
})
const vitPct = computed(() => Math.min(100, Math.max(0, me.value?.vitality_avg ?? 0)))
const tier = computed(() => me.value?.tier || null)
const rankText = computed(() => {
  const m = me.value
  if (!m) return ''
  if (m.ahead_name && m.ahead_gap_m2) {
    return `距 ${m.ahead_name} 还差 ${fmtArea(m.ahead_gap_m2)}`
  }
  return m.rank === 1 ? '目前这片最大' : ''
})
/** 当前视野里：我占了多少格 / 一共有多少格，用来做「街区进度」 */
const viewStat = computed(() => {
  const cells = data.value?.cells || []
  const myId = me.value?.user_id
  let mine = 0
  for (const [, ownerId] of cells) if (ownerId === myId) mine += 1
  const total = cells.length + (data.value?.neutral?.length || 0)
  return { mine, total, pct: total ? Math.round((mine / total) * 100) : 0 }
})
const recapCount = computed(() => data.value?.recapture?.length || 0)
const recapHours = computed(() => {
  const list = data.value?.recapture || []
  if (!list.length) return 0
  return Math.ceil(Math.max(...list.map(([, sec]) => sec)) / 3600)
})

/* ---------------- 格子 → 轮廓 ---------------- */
function cellBoundsWgs(key: string): [number, number, number, number] {
  const [ix, iy] = cellIxIy(key)
  return [iy * DLAT, ix * DLNG, (iy + 1) * DLAT, (ix + 1) * DLNG]
}
function ownerColor(ownerId: number) {
  return data.value?.owners[String(ownerId)]?.color || '#3b82f6'
}

/* ---------------- 地图 ---------------- */
function initMap() {
  if (!mapEl.value || map) return
  const c = toGcj(DEFAULT_CENTER[0], DEFAULT_CENTER[1])
  map = L.map(mapEl.value, {
    zoomControl: false,
    attributionControl: true,
    // 不用 Canvas 渲染器：部分 App 内置 WebView 上 Canvas 会掉层/只显示 HTML 标签
    preferCanvas: false,
    zoomSnap: 0.5,
  }).setView([c.lat, c.lng], 16.5)

  L.tileLayer(TILE_URL, {
    subdomains: ['1', '2', '3', '4'],
    maxZoom: 19,
    maxNativeZoom: 18,
    minZoom: 3,
    keepBuffer: 4,
    attribution: USE_GCJ ? '© 高德地图' : '',
  }).addTo(map)

  // 领地全部用 Leaflet 的 SVG 多边形画：矢量、不会掉层、手机 WebView 也稳
  const neutralPane = map.createPane('neutralPane')
  neutralPane.style.zIndex = '390'
  neutralPane.style.pointerEvents = 'none'
  const recapPane = map.createPane('recapPane')
  recapPane.style.zIndex = '470'
  recapPane.style.pointerEvents = 'none'

  neutralLayer = L.layerGroup().addTo(map)
  shapeLayer = L.layerGroup().addTo(map)
  recapLayer = L.layerGroup().addTo(map)
  const routePane = map.createPane('routePane')
  routePane.style.zIndex = '460'
  routePane.style.pointerEvents = 'none'
  labelLayer = L.layerGroup().addTo(map)

  if (!embed.value) L.control.zoom({ position: 'bottomright' }).addTo(map)
  map.on('moveend', scheduleLoad)
  map.on('zoomend', () => { scheduleLoad(); renderLabels() })
  map.on('click', onMapClick)
  window.setTimeout(() => map?.invalidateSize(), 120)
}

function scheduleDraw() {
  drawNeutral()
  drawTerritories()
  drawRecapture()
}

/** 无主地：灰虚线幽灵轮廓。空着但看得见，才有「去认领」的冲动。 */
function drawNeutral() {
  if (!map || !neutralLayer) return
  neutralLayer.clearLayers()
  const list = data.value?.neutral || []
  if (!list.length) return
  const keys = list.map(([k]) => k)
  const loops = outlineLoops(keys)
  let cx = 0
  let cy = 0
  let n = 0
  for (const loop of loops) {
    const latlngs = loop.map(([gx, gy]) => {
      const g = toGcj(gy * DLAT, gx * DLNG)
      return [g.lat, g.lng] as [number, number]
    })
    if (latlngs.length < 3) continue
    for (const p of latlngs) { cx += p[0]; cy += p[1]; n += 1 }
    L.polygon(latlngs, {
      pane: 'neutralPane',
      color: '#94a3b8', weight: 1.4, opacity: 0.55,
      dashArray: '4 5', fillColor: '#94a3b8', fillOpacity: 0.08,
      interactive: false,
    }).addTo(neutralLayer)
  }
  if (n && keys.length >= 6) {
    // latlngs 已经是 GCJ，别再转换一次（重复纠偏会把标签甩出屏幕）
    const html = `<div class="neutral-tag">无主地 ${keys.length} 格 · 跑进去就归你</div>`
    L.marker([cx / n, cy / n], {
      icon: L.divIcon({ className: 'neutral-tag-wrap', html, iconSize: [0, 0], iconAnchor: [0, 0] }),
      interactive: false,
      zIndexOffset: 120,
    }).addTo(neutralLayer)
  }
}

/**
 * 48h 内被抢走的地：橙色虚线标出范围，右上角挂一个小倒计时。
 * 只写「↺ 45h」——地图是看的地方，不是读的地方；
 * 具体发生了什么留给统计卡和图例解释。
 */
function drawRecapture() {
  if (!map || !recapLayer) return
  recapLayer.clearLayers()
  const list = data.value?.recapture || []
  if (!list.length) return
  const keys = list.map(([k]) => k)
  const loops = outlineLoops(keys)
  let maxLat = -Infinity
  let maxLng = -Infinity
  for (const loop of loops) {
    const latlngs = loop.map(([gx, gy]) => {
      const g = toGcj(gy * DLAT, gx * DLNG)
      return [g.lat, g.lng] as [number, number]
    })
    if (latlngs.length < 3) continue
    // 取东北角挂标：贴在轮廓右上外侧，不压住地块也不压住昵称
    for (const [lat, lng] of latlngs) {
      if (lat > maxLat) maxLat = lat
      if (lng > maxLng) maxLng = lng
    }
    L.polygon(latlngs, {
      pane: 'recapPane',
      color: '#f97316', weight: 2.2, opacity: 0.85,
      dashArray: '5 4', fill: false,
      interactive: false,
    }).addTo(recapLayer)
  }
  if (!Number.isFinite(maxLat)) return
  const maxLeft = Math.max(...list.map(([, sec]) => sec))
  const hours = Math.max(1, Math.ceil(maxLeft / 3600))
  const html = `<div class="recap-tag"><span>↺</span>${hours}h</div>`
  L.marker([maxLat, maxLng], {
    icon: L.divIcon({ className: 'recap-tag-wrap', html, iconSize: [0, 0], iconAnchor: [0, 0] }),
    interactive: false,
    zIndexOffset: 520,
  }).addTo(recapLayer)
}

function drawTerritories() {
  if (!map || !shapeLayer) return
  shapeLayer.clearLayers()
  const cells = data.value?.cells || []
  if (!cells.length) return
  const shapes = buildOwnerShapes(cells)
  shapes.sort((a, b) => a.cells - b.cells)

  for (const shape of shapes) {
    const color = ownerColor(shape.ownerId)
    const vital = Math.min(1, Math.max(0, shape.vitality / 100))
    const alpha = 0.16 + 0.5 * vital
    const strokeAlpha = 0.32 + 0.63 * vital
    const casingAlpha = 0.26 + 0.56 * vital
    const hasPulse = [...pulseKeys].some((k) => {
      const [x, y] = cellIxIy(k)
      return cells.some(([ck, oid]) => oid === shape.ownerId && cellIxIy(ck)[0] === x && cellIxIy(ck)[1] === y)
    })
    for (const loop of shape.loops) {
      const latlngs = loop.map(([gx, gy]) => {
        const g = toGcj(gy * DLAT, gx * DLNG)
        return [g.lat, g.lng] as [number, number]
      })
      if (latlngs.length < 3) continue
      // 白色描边打底，让边界像一条发光的轨迹线；活力低时描边也变淡
      L.polygon(latlngs, {
        color: '#ffffff', weight: 6, opacity: casingAlpha, fill: false,
        interactive: false,
      }).addTo(shapeLayer)
      // 彩色主体
      L.polygon(latlngs, {
        color,
        weight: hasPulse ? 3 : 2.2,
        opacity: hasPulse ? 0.98 : strokeAlpha,
        fillColor: color,
        fillOpacity: alpha,
        dashArray: hasPulse ? '6 6' : undefined,
        interactive: false,
      }).addTo(shapeLayer)
    }
  }
}

function renderLabels() {
  if (!map || !labelLayer) return
  labelLayer.clearLayers()
  if (!data.value || map.getZoom() < 15) return
  const byOwner = new Map<number, { lats: number[]; lngs: number[]; cells: number }>()
  for (const [key, ownerId] of data.value.cells) {
    const [lat1, lng1, lat2, lng2] = cellBoundsWgs(key)
    const e = byOwner.get(ownerId) || { lats: [], lngs: [], cells: 0 }
    e.lats.push((lat1 + lat2) / 2)
    e.lngs.push((lng1 + lng2) / 2)
    e.cells += 1
    byOwner.set(ownerId, e)
  }
  const owners = [...byOwner.entries()].sort((a, b) => b[1].cells - a[1].cells).slice(0, 12)
  for (const [ownerId, e] of owners) {
    const meta = data.value.owners[String(ownerId)]
    if (!meta) continue
    // 用重心做标签点；闭环填充后重心就在地盘中心，不会像环状那样堆在操场正中
    const lat = e.lats.reduce((a, b) => a + b, 0) / e.lats.length
    const lng = e.lngs.reduce((a, b) => a + b, 0) / e.lngs.length
    const g = toGcj(lat, lng)
    const initial = (meta.nickname || '?').slice(0, 1)
    const cls = meta.is_me ? ' me' : meta.is_friend ? ' friend' : ''
    const showName = map.getZoom() >= 17
    const html = `<div class="terr-label${cls}" style="--c:${meta.color}">
      <i>${initial}</i>${showName ? `<b>${meta.nickname}</b>` : ''}</div>`
    L.marker([g.lat, g.lng], {
      icon: L.divIcon({ className: 'terr-label-wrap', html, iconSize: [0, 0], iconAnchor: [0, 0] }),
      interactive: false,
      zIndexOffset: 300,
    }).addTo(labelLayer)
  }
}

function scheduleLoad() {
  if (moveTimer !== null) clearTimeout(moveTimer)
  moveTimer = window.setTimeout(() => {
    loadMap()
    renderLabels()
  }, 220)
}

async function loadMap() {
  if (!map) return
  const b = map.getBounds()
  const sw = gcj02ToWgs84(b.getSouthWest().lat, b.getSouthWest().lng)
  const ne = gcj02ToWgs84(b.getNorthEast().lat, b.getNorthEast().lng)
  const pad = 0.004
  try {
    data.value = await territoryApi.getTerritoryMap({
      min_lat: Math.min(sw.lat, ne.lat) - pad,
      max_lat: Math.max(sw.lat, ne.lat) + pad,
      min_lng: Math.min(sw.lng, ne.lng) - pad,
      max_lng: Math.max(sw.lng, ne.lng) + pad,
      scope: onlyOwner.value ? 'all' : scope.value,
      owner_id: onlyOwner.value || undefined,
    })
    scheduleDraw()
    renderLabels()
  } catch {
    /* 保留上一帧 */
  } finally {
    loading.value = false
  }
}

function onMapClick(e: L.LeafletMouseEvent) {
  if (!map) return
  const p = map.latLngToContainerPoint(e.latlng)
  let hitId: number | null = null
  for (const [key, ownerId] of data.value?.cells || []) {
    const [lat1, lng1, lat2, lng2] = cellBoundsWgs(key)
    const a = toGcj(lat1, lng1)
    const b = toGcj(lat2, lng2)
    const p1 = map.latLngToContainerPoint([a.lat, a.lng])
    const p2 = map.latLngToContainerPoint([b.lat, b.lng])
    const x1 = Math.min(p1.x, p2.x); const x2 = Math.max(p1.x, p2.x)
    const y1 = Math.min(p1.y, p2.y); const y2 = Math.max(p1.y, p2.y)
    if (p.x >= x1 && p.x <= x2 && p.y >= y1 && p.y <= y2) {
      hitId = ownerId
      break
    }
  }
  // 点地块直接进整页详情，不再弹半屏
  if (hitId) {
    openOwnerPage(hitId)
    return
  }
  // 点到无主地：给一个「这块空着，去跑就归你」的出口
  for (const [key] of data.value?.neutral || []) {
    const [lat1, lng1, lat2, lng2] = cellBoundsWgs(key)
    const a = toGcj(lat1, lng1)
    const b = toGcj(lat2, lng2)
    const p1 = map.latLngToContainerPoint([a.lat, a.lng])
    const p2 = map.latLngToContainerPoint([b.lat, b.lng])
    const x1 = Math.min(p1.x, p2.x); const x2 = Math.max(p1.x, p2.x)
    const y1 = Math.min(p1.y, p2.y); const y2 = Math.max(p1.y, p2.y)
    if (p.x >= x1 && p.x <= x2 && p.y >= y1 && p.y <= y2) {
      neutralHit.value = { key }
      return
    }
  }
}

function openOwnerPage(ownerId: number) {
  router.push({ name: 'territory-owner', params: { id: String(ownerId) } })
}

/* ---------------- 好友 ---------------- */
async function openFriends() {
  friendsOpen.value = true
  friendsLoading.value = true
  try {
    const [f, s] = await Promise.all([friendsApi.getFriends(), friendsApi.getFriendSuggestions()])
    friends.value = f.items
    suggestions.value = s.items
  } catch {
    ElMessage.error('好友列表加载失败')
  } finally {
    friendsLoading.value = false
  }
}

async function addFriend(userId: number) {
  try {
    await friendsApi.addFriend(userId)
    ElMessage.success('已加为好友，可以互相抢地了')
    if (data.value?.owners[String(userId)]) data.value.owners[String(userId)].is_friend = true
    if (friendsOpen.value) await openFriends()
    await loadMap()
  } catch {
    /* http 层已提示 */
  }
}

async function removeFriend(userId: number) {
  await friendsApi.removeFriend(userId)
  ElMessage.success('已移除好友')
  if (onlyOwner.value === userId) {
    onlyOwner.value = null
    scope.value = 'all'
  }
  await openFriends()
  await loadMap()
}

function openFriendDetail(userId: number) {
  friendsOpen.value = false
  openOwnerPage(userId)
}

function focusFriend(f: FriendItem) {
  friendsOpen.value = false
  onlyOwner.value = f.user_id
  loadMap()
}

function focusOwner(ownerId: number | null) {
  if (!ownerId) return
  friendsOpen.value = false
  onlyOwner.value = ownerId
  loadMap()
}

function clearScope() {
  onlyOwner.value = null
  scope.value = 'all'
  loadMap()
}

function setScope(next: TerritoryScope) {
  onlyOwner.value = null
  scope.value = next
  loadMap()
}

/* ---------------- 本次圈地动画 ---------------- */
async function playRunAnimation(workoutId: number) {
  try {
    const run = await territoryApi.getTerritoryRun(workoutId)
    if (!run.valid) {
      ElMessage.info(run.reason || '本次未闭环，只记轨迹，不计领地')
      return
    }
    for (const k of run.cells || []) pulseKeys.add(k)
    scheduleDraw()
    if (pulseTimer !== null) clearTimeout(pulseTimer)
    pulseTimer = window.setTimeout(() => {
      pulseKeys.clear()
      scheduleDraw()
    }, 12000)

    const w = await workoutApi.getWorkout(workoutId)
    if (w?.track?.length && map) {
      const pts = w.track.map((p) => {
        const g = toGcj(p.lat, p.lng)
        return [g.lat, g.lng] as [number, number]
      })
      trackLine = L.polyline(pts, {
        color: '#ffffff', weight: 8, opacity: 0.7, lineJoin: 'round', pane: 'routePane',
      }).addTo(map)
      L.polyline(pts, {
        color: '#22c55e', weight: 3.5, opacity: 0.98, dashArray: '8 6', lineJoin: 'round',
        pane: 'routePane',
      }).addTo(map)
      map.fitBounds(L.latLngBounds(pts), { padding: [40, 40], maxZoom: 17 })
    }
  } catch {
    /* 没有结算记录 */
  }
}

/* ---------------- 数据 ---------------- */
async function loadMe() {
  try {
    me.value = await territoryApi.getTerritoryMe()
    const center = me.value.center || me.value.latest_track?.[0] || null
    if (center && map) {
      map.invalidateSize()
      const c = toGcj(center[0], center[1])
      map.setView([c.lat, c.lng], 16.5)
    } else if (!embed.value) {
      locateMe()
    }
    await loadMap()
  } catch {
    ElMessage.error('领地数据加载失败')
  } finally {
    loading.value = false
  }
}

async function toggle() {
  if (!me.value) return
  if (me.value.enabled) {
    try {
      await ElMessageBox.confirm('关闭后，你的地盘会立即释放，别人可以占。确定关闭？', '关闭领地跑', {
        confirmButtonText: '关闭并释放', cancelButtonText: '再想想', type: 'warning',
      })
    } catch {
      return
    }
    await territoryApi.updateTerritoryPrefs({ enabled: false })
    ElMessage.success('已关闭，地盘已释放')
  } else {
    await territoryApi.updateTerritoryPrefs({ enabled: true })
    ElMessage.success('领地跑已开启，去跑一圈吧')
  }
  await loadMe()
}

function centerMe() {
  if (!map) return
  const c = me.value?.center
  if (!c) {
    ElMessage.info('还没有地盘，去跑一圈就有了')
    return
  }
  const g = toGcj(c[0], c[1])
  map.setView([g.lat, g.lng], 17, { animate: true })
}

function locateMe() {
  if (!map || !navigator.geolocation) return
  navigator.geolocation.getCurrentPosition(
    (pos) => {
      if (!map) return
      const g = toGcj(pos.coords.latitude, pos.coords.longitude)
      map.setView([g.lat, g.lng], 16, { animate: true })
    },
    () => { /* 用户拒绝定位就保持默认视角 */ },
    { enableHighAccuracy: false, timeout: 6000, maximumAge: 60_000 },
  )
}

function back() {
  if (window.history.length > 1) router.back()
  else router.replace('/m/sport')
}

function goClaim() {
  neutralHit.value = null
  if (!me.value?.enabled) {
    ElMessage.info('先开启领地跑，跑一个闭环就能拿下这块地')
    return
  }
  router.push({ path: '/workout/run', query: { sport: '跑步' } })
}

onMounted(async () => {
  initMap()
  await loadMe()
  const runId = Number(route.query.run)
  if (runId) playRunAnimation(runId)
  // ?owner= 只做「只看 TA」的筛选，详情走独立页面，不再自动弹半屏
  if (route.query.friends === '1') openFriends()
  // 可夺回倒计时：每分钟刷新一次徽标，不用重新拉地图
  recapTimer = window.setInterval(() => {
    if (data.value?.recapture?.length) drawRecapture()
  }, 60_000)
  // 手机 WebView 首次拿到真实宽度/高度会晚一点，再补一次绘制
  window.setTimeout(() => {
    map?.invalidateSize()
    scheduleDraw()
  }, 350)
})

onBeforeUnmount(() => {
  if (moveTimer !== null) clearTimeout(moveTimer)
  if (pulseTimer !== null) clearTimeout(pulseTimer)
  if (recapTimer !== null) clearInterval(recapTimer)
  trackLine?.remove()
  labelLayer?.clearLayers()
  map?.remove()
  map = null
})
</script>

<template>
  <div class="territory-page" :class="{ embed }">
    <div ref="mapEl" class="map" />

    <div class="hud">
      <header class="topbar">
        <button class="icon-btn back" @click="back">‹</button>
        <div class="title">
          领地
          <small v-if="me">{{ me.enabled ? 'LIVE' : '未开启' }}</small>
        </div>
        <button class="icon-btn" @click="openFriends" title="好友">👥</button>
        <button class="toggle" :class="{ on: me?.enabled }" @click="toggle">
          {{ me?.enabled ? '关闭' : '开启' }}
        </button>
      </header>

      <div class="filters">
        <button :class="{ on: !onlyOwner && scope === 'all' }" @click="setScope('all')">全部</button>
        <button :class="{ on: !onlyOwner && scope === 'friends' }" @click="setScope('friends')">好友</button>
        <button :class="{ on: !onlyOwner && scope === 'me' }" @click="setScope('me')">我的</button>
        <button v-if="onlyOwner" class="only" @click="clearScope">
          只看 {{ onlyOwnerName }} ✕
        </button>
      </div>

      <div v-if="me?.enabled" class="stats">
        <div class="stat-top">
          <div class="stat-main"><b>{{ areaBig }}</b><span>{{ areaUnit }}</span></div>
          <span v-if="tier" class="tier-chip">Lv.{{ tier.level }} · {{ tier.name }}</span>
        </div>

        <!-- 活力：规则要看得见，才有「明天不去就没了」的紧迫感 -->
        <div class="vit-row">
          <div class="vit-track">
            <i :style="{ width: `${vitPct}%`, background: vitColor }" />
          </div>
          <span class="vit-num" :style="{ color: vitColor }">{{ Math.round(me.vitality_avg) }}%</span>
        </div>
        <div class="stat-sub">{{ decayText }} · 连片 {{ fmtArea(me.largest_block_area_m2) }}</div>
        <div class="stat-sub dim">
          第 {{ me.rank }}/{{ me.rank_of }} 名<template v-if="rankText"> · {{ rankText }}</template>
        </div>

        <div class="chip-row">
          <span class="view-chip">这片你占 {{ viewStat.pct }}%</span>
          <span v-if="recapCount" class="recap-chip">
            被抢走 {{ recapCount }} 格 · {{ recapHours }}h 内能拿回来
          </span>
        </div>
      </div>

      <div v-if="contestEvent" class="contest-banner" @click="focusOwner(contestEvent.actor_id)">
        <span class="contest-dot" />
        <div class="contest-main">
          <b>{{ contestText }}</b>
          <span>点一下看 TA 的地</span>
        </div>
        <span class="contest-arrow">›</span>
      </div>
    </div>

    <button class="legend" :class="{ on: legendOpen }" @click="legendOpen = true">
      <span class="legend-grad" />
      <span>图例</span>
    </button>

    <button class="locate" @click="centerMe">◎ 我的地</button>

    <div v-if="me && !me.enabled" class="onboard">
      <h2>把跑过的路，变成你的地盘</h2>
      <p>跑出一个闭环，圈住的面积就归你；开环只记轨迹，不算领地。</p>
      <ul>
        <li>跑进好友的地，绕一圈能切走一块</li>
        <li>每天 -8 活力，颜色会慢慢变淡，最后消失</li>
        <li>地被抢走后 48 小时内跑回去，更容易拿回来</li>
        <li>只认 GPS 跑步/快走；骑行、手动补录不算</li>
      </ul>
      <button class="primary" @click="toggle">开启领地跑</button>
      <p class="privacy">地图形状实时更新；别人只看得到颜色和昵称，看不到你的轨迹。</p>
    </div>

    <!-- 图例：颜色深浅和身份都有解释，否则地图只是一堆色块 -->
    <div v-if="legendOpen" class="sheet" @click.self="legendOpen = false">
      <div class="sheet-card legend-card">
        <div class="sheet-handle" />
        <div class="sheet-head">
          <b>图例</b>
          <span class="close" @click="legendOpen = false">✕</span>
        </div>

        <div class="lg-row">
          <span class="lg-grad" />
          <div class="lg-main">
            <b>颜色深浅 = 活力</b>
            <span>深：活力足，守得住；浅：快消退，跑一趟就能续上</span>
          </div>
        </div>
        <div class="lg-row">
          <span class="lg-swatch" :style="{ background: me?.color || '#4f9d2f' }" />
          <div class="lg-main">
            <b>我的地</b>
            <span>{{ decayText }}</span>
          </div>
        </div>
        <div class="lg-row">
          <span class="lg-swatch" style="background: #3b82f6" />
          <div class="lg-main">
            <b>好友</b>
            <span>可以互相切地；点地块看详情、下战书</span>
          </div>
        </div>
        <div class="lg-row">
          <span class="lg-swatch" style="background: #94a3b8" />
          <div class="lg-main">
            <b>附近的人</b>
            <span>只能看，加为好友后才能抢</span>
          </div>
        </div>
        <div class="lg-row">
          <span class="lg-recap">
            <i class="lg-dash orange" />
            <em>↺45h</em>
          </span>
          <div class="lg-main">
            <b>被抢走的地</b>
            <span>橙色虚线 + 剩余小时数 · 48 小时内跑回去，比平时更容易拿回来</span>
          </div>
        </div>
        <div class="lg-row">
          <span class="lg-dash gray" />
          <div class="lg-main">
            <b>无主地</b>
            <span>曾经有人占、现在空着；跑进去就归你</span>
          </div>
        </div>
        <p class="lg-foot">每天 -8 活力 · 跑到自己的地 +20 · 闭环内场直接切走</p>
      </div>
    </div>

    <!-- 无主地：地图上点到灰虚线区域时弹这个 -->
    <div v-if="neutralHit" class="sheet" @click.self="neutralHit = null">
      <div class="sheet-card neutral-card">
        <div class="sheet-handle" />
        <div class="sheet-head">
          <b>这块地空着</b>
          <span class="close" @click="neutralHit = null">✕</span>
        </div>
        <p class="neutral-tip">
          曾经有人占过，久没人跑就退回了无主。跑一个闭环，圈住的面积就归你。
        </p>
        <div class="neutral-actions">
          <button class="primary small" @click="goClaim">去跑一圈</button>
          <button class="ghost small" @click="neutralHit = null">先看看</button>
        </div>
      </div>
    </div>

    <!-- 好友 -->
    <div v-if="friendsOpen" class="sheet" @click.self="friendsOpen = false">
      <div class="sheet-card friends-card">
        <div class="sheet-handle" />
        <div class="sheet-head">
          <b>好友 · {{ friends.length }}</b>
          <span class="close" @click="friendsOpen = false">✕</span>
        </div>
        <div v-if="friendsLoading" class="muted">加载中…</div>
        <div v-else-if="!friends.length" class="muted">还没有好友，从下面「附近的人」加一个。</div>
        <div v-for="f in friends" :key="f.user_id" class="friend-row" @click="openFriendDetail(f.user_id)">
          <span class="avatar" :style="{ background: f.color }">{{ f.nickname.slice(0, 1) }}</span>
          <div class="friend-main">
            <b>{{ f.nickname }}</b>
            <span>{{ f.area_m2 ? `${(f.area_m2 / 10000).toFixed(2)} 万㎡` : '还没有领地' }}</span>
          </div>
          <div class="friend-actions">
            <button class="mini" @click.stop="focusFriend(f)">只看 TA</button>
            <button class="mini ghost" @click.stop="removeFriend(f.user_id)">移除</button>
          </div>
        </div>
        <div class="suggest-title">附近的人</div>
        <div v-if="!suggestions.length" class="muted">附近暂时没有其他人。</div>
        <div v-for="f in suggestions" :key="f.user_id" class="friend-row" @click="openFriendDetail(f.user_id)">
          <span class="avatar" :style="{ background: f.color }">{{ f.nickname.slice(0, 1) }}</span>
          <div class="friend-main">
            <b>{{ f.nickname }}</b>
            <span>{{ f.area_m2 ? `${(f.area_m2 / 10000).toFixed(2)} 万㎡` : '还没有领地' }}</span>
          </div>
          <div class="friend-actions">
            <button class="mini primary" @click.stop="addFriend(f.user_id)">加好友</button>
          </div>
        </div>
      </div>
    </div>

    <div v-if="loading" class="loading">地图加载中…</div>
  </div>
</template>

<style scoped>
.territory-page { position: fixed; inset: 0; background: #eef1f5; overflow: hidden; }
.territory-page.embed .hud,
.territory-page.embed .legend,
.territory-page.embed .locate,
.territory-page.embed .onboard,
.territory-page.embed .sheet,
.territory-page.embed .loading { display: none !important; }
.territory-page.embed { position: absolute; }
.map { position: absolute; inset: 0; z-index: 0; }
:deep(.leaflet-container) { font-family: inherit; background: #eef1f5; }
.map :deep(.leaflet-tile-pane) { filter: saturate(0.55) brightness(1.04); }

/* 顶部浮层改成流式布局：原来每个卡片都写死 top，统计卡一变高就会盖住事件条 */
.hud {
  position: absolute; z-index: 30; left: 0; right: 0; top: 0;
  display: flex; flex-direction: column;
  pointer-events: none;
}
.hud > * { pointer-events: auto; }

.topbar {
  display: flex; align-items: center; gap: 6px;
  padding: calc(8px + env(safe-area-inset-top, 0)) 12px 8px;
  background: linear-gradient(to bottom, rgba(255, 255, 255, 0.96), rgba(255, 255, 255, 0));
}
.icon-btn {
  border: none; background: rgba(255, 255, 255, 0.94);
  border-radius: 12px; height: 36px; min-width: 36px; padding: 0 10px;
  box-shadow: 0 2px 10px rgba(0, 0, 0, 0.08); font-size: 15px; color: #303133;
}
.icon-btn.back { font-size: 24px; line-height: 30px; }
.title { font-size: 16px; font-weight: 700; color: #1f2d3d; }
.title small {
  font-size: 9px; color: #fff; background: #22c55e; border-radius: 4px;
  padding: 1px 5px; margin-left: 5px; vertical-align: 2px; letter-spacing: 0.5px;
}
.toggle {
  margin-left: auto; border: none; background: rgba(255, 255, 255, 0.94);
  border-radius: 12px; height: 34px; padding: 0 12px; color: #4f9d2f;
  font-size: 12px; font-weight: 600; box-shadow: 0 2px 10px rgba(0, 0, 0, 0.08);
}
.toggle.on { color: #8a94a6; }

.filters {
  display: flex; gap: 6px; margin: 2px 12px 0;
  overflow-x: auto; scrollbar-width: none;
}
.filters::-webkit-scrollbar { display: none; }
.filters button {
  border: none; background: rgba(255, 255, 255, 0.92); color: #606266;
  border-radius: 16px; height: 30px; padding: 0 12px; font-size: 11px;
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.06); white-space: nowrap;
}
.filters button.on { background: #1f2d3d; color: #fff; font-weight: 600; }
.filters button.only { background: #e8f1ff; color: #3b82f6; font-weight: 600; }

.stats {
  margin: 8px 12px 0;
  background: rgba(255, 255, 255, 0.94);
  border-radius: 14px; padding: 10px 14px 11px;
  box-shadow: 0 4px 18px rgba(31, 45, 61, 0.1);
  backdrop-filter: blur(8px);
}
.stat-top { display: flex; align-items: center; gap: 8px; }
.stat-main { display: flex; align-items: baseline; gap: 4px; flex: 1; min-width: 0; }
.stat-main b { font-size: 24px; line-height: 1; color: #1f2d3d; }
.stat-main span { font-size: 12px; color: #8a94a6; }
.tier-chip {
  flex: none; font-size: 9px; font-weight: 600; color: #4f9d2f;
  background: #eef5e8; border-radius: 6px; padding: 3px 7px;
}
.vit-row { display: flex; align-items: center; gap: 8px; margin-top: 8px; }
.vit-track {
  flex: 1; height: 6px; border-radius: 3px;
  background: #eef1f5; overflow: hidden;
}
.vit-track i { display: block; height: 100%; border-radius: 3px; transition: width 0.5s ease; }
.vit-num { font-size: 10px; font-weight: 700; font-variant-numeric: tabular-nums; }
.stat-sub { font-size: 11px; color: #606266; margin-top: 5px; }
.stat-sub.dim { color: #8a94a6; }
.chip-row { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 8px; }
.view-chip {
  font-size: 9px; color: #4f9d2f; background: #eef5e8;
  border-radius: 6px; padding: 3px 7px;
}
.recap-chip {
  font-size: 9px; color: #c2410c; background: #fff2e8;
  border-radius: 6px; padding: 3px 7px; font-weight: 600;
}

.contest-banner {
  margin: 8px 12px 0;
  display: flex; align-items: center; gap: 8px;
  background: rgba(255, 247, 237, 0.97);
  border: 1px solid #fed7aa; border-radius: 12px;
  padding: 8px 10px; box-shadow: 0 4px 16px rgba(234, 88, 12, 0.12);
}
.contest-dot {
  width: 8px; height: 8px; border-radius: 50%; background: #f97316;
  box-shadow: 0 0 0 4px rgba(249, 115, 22, 0.16);
}
.contest-main { flex: 1; min-width: 0; display: flex; flex-direction: column; }
.contest-main b { font-size: 11px; color: #9a3412; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.contest-main span { font-size: 9px; color: #c2744a; margin-top: 2px; }
.contest-arrow { color: #f97316; font-size: 18px; }

.legend {
  position: absolute; z-index: 15; left: 12px;
  bottom: calc(18px + env(safe-area-inset-bottom, 0));
  display: flex; align-items: center; gap: 7px;
  border: none; background: rgba(255, 255, 255, 0.94);
  border-radius: 20px; padding: 6px 11px;
  font-size: 10px; color: #606266;
  box-shadow: 0 2px 10px rgba(0, 0, 0, 0.08);
}
.legend-grad {
  width: 36px; height: 8px; border-radius: 4px;
  background: linear-gradient(90deg, #4f9d2f, rgba(79, 157, 47, 0.14));
  box-shadow: inset 0 0 0 1px rgba(31, 45, 61, 0.06);
}

/* 图例卡 */
.legend-card { max-height: 76vh; }
.lg-row {
  display: flex; align-items: center; gap: 12px;
  padding: 9px 0; border-bottom: 1px solid #f5f6f8;
}
.lg-row:last-of-type { border-bottom: none; }
.lg-swatch {
  flex: none; width: 22px; height: 22px; border-radius: 7px;
  box-shadow: 0 0 0 1px rgba(31, 45, 61, 0.06) inset;
}
.lg-grad {
  flex: none; width: 22px; height: 22px; border-radius: 7px;
  background: linear-gradient(180deg, #4f9d2f, rgba(79, 157, 47, 0.12));
}
.lg-dash {
  flex: none; width: 22px; height: 22px; border-radius: 7px;
  border: 2px dashed #94a3b8; box-sizing: border-box;
}
.lg-dash.orange { border-color: #f97316; }
.lg-dash.gray { border-color: #94a3b8; }
/* 图例里带一个和地图上一样的 ↺45h 小标，让符号能对上号 */
.lg-recap { position: relative; flex: none; width: 40px; height: 24px; display: block; }
.lg-recap .lg-dash { display: block; width: 22px; height: 22px; border-radius: 6px; }
.lg-recap em {
  position: absolute; left: 12px; top: 15px; font-style: normal;
  font-size: 8px; font-weight: 600; color: #c2410c;
  background: #fff; border: 1px solid rgba(249, 115, 22, 0.55);
  border-radius: 5px; padding: 0 3px; line-height: 1.4;
  font-variant-numeric: tabular-nums;
}
.lg-main { display: flex; flex-direction: column; min-width: 0; }
.lg-main b { font-size: 12px; color: #1f2d3d; }
.lg-main span { font-size: 10px; color: #8a94a6; margin-top: 2px; line-height: 1.5; }
.lg-foot {
  margin: 12px 0 0; font-size: 10px; color: #a8abb2;
  text-align: center; line-height: 1.6;
}

/* 无主地 */
.neutral-card { padding-bottom: calc(20px + env(safe-area-inset-bottom, 0)); }
.neutral-tip { margin: 10px 0 14px; font-size: 12px; color: #606266; line-height: 1.6; }
.neutral-actions { display: flex; gap: 8px; }

.locate {
  position: absolute; z-index: 15; right: 12px;
  bottom: calc(74px + env(safe-area-inset-bottom, 0));
  border: none; height: 38px; padding: 0 14px; border-radius: 19px;
  background: #fff; color: #4f9d2f; font-size: 12px; font-weight: 600;
  box-shadow: 0 3px 12px rgba(0, 0, 0, 0.14);
}

.onboard {
  position: absolute; z-index: 35; left: 16px; right: 16px;
  bottom: calc(24px + env(safe-area-inset-bottom, 0));
  background: rgba(255, 255, 255, 0.98);
  border-radius: 18px; padding: 18px 18px 14px;
  box-shadow: 0 10px 40px rgba(31, 45, 61, 0.22);
}
.onboard h2 { margin: 0 0 6px; font-size: 18px; color: #1f2d3d; }
.onboard p { margin: 0 0 8px; font-size: 12px; color: #606266; line-height: 1.6; }
.onboard ul { margin: 0 0 12px; padding-left: 18px; font-size: 12px; color: #606266; line-height: 1.8; }
.onboard .primary {
  width: 100%; height: 44px; border: none; border-radius: 12px;
  background: linear-gradient(135deg, #4f9d2f, #7bc043); color: #fff;
  font-size: 15px; font-weight: 600;
}
.onboard .privacy { font-size: 10px; color: #a8abb2; margin: 10px 0 0; line-height: 1.5; }

.sheet {
  position: absolute; z-index: 40; inset: 0;
  background: rgba(15, 23, 42, 0.26);
  display: flex; align-items: flex-end;
}
.sheet-card {
  box-sizing: border-box;
  width: 100%; max-height: 78vh; overflow-y: auto;
  background: #fff; border-radius: 20px 20px 0 0;
  padding: 10px 18px calc(20px + env(safe-area-inset-bottom, 0));
  box-shadow: 0 -8px 30px rgba(15, 23, 42, 0.16);
}
.sheet-handle { width: 36px; height: 4px; border-radius: 2px; background: #e4e7ed; margin: 0 auto 12px; }
.sheet-head { display: flex; align-items: center; gap: 8px; }
.owner-dot { width: 12px; height: 12px; border-radius: 50%; }
.sheet-head b { font-size: 15px; color: #1f2d3d; }
.sheet-head .close { margin-left: auto; color: #a8abb2; font-size: 16px; padding: 4px 8px; }
.vitality { margin-top: 12px; font-size: 12px; color: #606266; }
.vitality-bar { height: 8px; border-radius: 4px; background: #f0f2f5; margin-top: 6px; overflow: hidden; }
.vitality-bar i { display: block; height: 100%; border-radius: 4px; }
.sheet-tip { margin-top: 10px; font-size: 11px; color: #8a94a6; line-height: 1.6; }
.sheet-actions { display: flex; gap: 8px; margin-top: 12px; align-items: center; }
.primary {
  border: none; background: linear-gradient(135deg, #4f9d2f, #7bc043);
  color: #fff; border-radius: 12px; font-weight: 600;
}
.primary.small, .ghost.small { height: 38px; padding: 0 14px; font-size: 12px; }
.ghost { border: 1px solid #e4e7ed; background: #fff; color: #606266; border-radius: 12px; }
.friend-tag { font-size: 11px; color: #22c55e; }

.friends-card { max-height: 82vh; }
.owner-card { max-height: 80vh; }
.owner-head { display: flex; align-items: center; gap: 10px; }
.owner-avatar {
  width: 42px; height: 42px; border-radius: 50%; color: #fff;
  display: flex; align-items: center; justify-content: center;
  font-size: 16px; font-weight: 700; flex-shrink: 0;
}
.owner-title { flex: 1; min-width: 0; display: flex; flex-direction: column; }
.owner-title b { font-size: 16px; color: #1f2d3d; }
.owner-title span { font-size: 10px; color: #8a94a6; margin-top: 2px; }
.owner-area { text-align: right; display: flex; flex-direction: column; flex-shrink: 0; }
.owner-area b { font-size: 16px; color: #1f2d3d; }
.owner-area span { font-size: 9px; color: #8a94a6; }
.owner-meta { font-size: 10px; color: #8a94a6; margin-top: 6px; }
.record-title {
  font-size: 11px; font-weight: 600; color: #606266;
  margin: 14px 0 2px; padding-top: 10px; border-top: 1px solid #f3f4f6;
}
.record-row {
  display: flex; align-items: flex-start; gap: 8px;
  padding: 8px 0; border-bottom: 1px solid #f7f8fa;
}
.record-main { flex: 1; min-width: 0; display: flex; flex-direction: column; }
.record-main b { font-size: 11px; color: #1f2d3d; }
.record-main span { font-size: 9px; color: #8a94a6; margin-top: 2px; }
.record-area { text-align: right; display: flex; flex-direction: column; flex-shrink: 0; }
.record-area b { font-size: 12px; color: #2563eb; }
.record-area span { font-size: 9px; color: #f97316; margin-top: 2px; }
.event-row { display: flex; align-items: center; gap: 8px; padding: 7px 0; }
.event-dot { width: 7px; height: 7px; border-radius: 50%; background: #94a3b8; flex-shrink: 0; }
.event-dot.cut { background: #f97316; }
.event-dot.recapture { background: #22c55e; }
.event-main { display: flex; flex-direction: column; }
.event-main b { font-size: 10px; color: #303133; }
.event-main span { font-size: 9px; color: #8a94a6; margin-top: 1px; }
.friend-row { display: flex; align-items: center; gap: 8px; padding: 9px 0; border-bottom: 1px solid #f3f4f6; flex-wrap: wrap; }
.avatar {
  width: 30px; height: 30px; border-radius: 50%; color: #fff;
  display: flex; align-items: center; justify-content: center; font-size: 12px; font-weight: 700;
}
.friend-main { flex: 1 1 120px; min-width: 0; display: flex; flex-direction: column; }
.friend-main b { font-size: 13px; color: #1f2d3d; }
.friend-main span { font-size: 10px; color: #8a94a6; margin-top: 2px; }
.friend-actions { display: flex; gap: 6px; width: 100%; margin-left: 38px; }
.mini { border: none; background: #eef5ff; color: #3b82f6; border-radius: 10px; height: 28px; padding: 0 9px; font-size: 11px; }
.mini.primary { background: #3b82f6; color: #fff; }
.mini.ghost { background: #f5f6f8; color: #8a94a6; }
.suggest-title { font-size: 11px; font-weight: 600; color: #909399; margin: 14px 0 2px; }
.muted { font-size: 11px; color: #a8abb2; padding: 8px 0; }
.loading {
  position: absolute; z-index: 50; left: 50%; top: 50%; transform: translate(-50%, -50%);
  background: rgba(15, 23, 42, 0.75); color: #fff; font-size: 12px;
  padding: 8px 14px; border-radius: 10px;
}

:deep(.leaflet-control-attribution) {
  font-size: 9px; background: rgba(255, 255, 255, 0.72); color: #8a94a6;
}

/* Leaflet divIcon 是动态插入的，scoped 样式需要 :global */
:global(.terr-label-wrap) { background: transparent; border: none; }
:global(.terr-label) {
  display: inline-flex; align-items: center; gap: 3px;
  transform: translate(-50%, -50%);
  background: rgba(255, 255, 255, 0.86);
  border: 1px solid rgba(255, 255, 255, 0.85);
  box-shadow: 0 2px 6px rgba(15, 23, 42, 0.12);
  border-radius: 10px; padding: 1px 5px 1px 1px;
  font-size: 9px; color: #1f2d3d; white-space: nowrap;
}
:global(.terr-label i) {
  width: 16px; height: 16px; border-radius: 50%;
  background: var(--c); color: #fff; font-style: normal;
  display: flex; align-items: center; justify-content: center;
  font-size: 8px; font-weight: 700;
}
:global(.terr-label b) { font-size: 9px; font-weight: 500; max-width: 56px; overflow: hidden; text-overflow: ellipsis; }
:global(.terr-label span) { display: none; }
:global(.terr-label.me) { border-color: var(--c); box-shadow: 0 2px 8px rgba(0, 0, 0, 0.18); }
:global(.terr-label.friend) { border-color: rgba(34, 197, 94, 0.45); }

/* 无主地标签 */
:global(.neutral-tag-wrap) { background: transparent; border: none; }
:global(.neutral-tag) {
  /* 往下放：避免和无主地自己的虚线框角标叠在一起 */
  transform: translate(-50%, 14px);
  display: inline-block; white-space: nowrap;
  font-size: 9px; color: #56606e;
  background: rgba(255, 255, 255, 0.78);
  border: 1px dashed #94a3b8; border-radius: 10px;
  padding: 2px 7px;
}

/* 48h 倒计时小标：挂在轮廓右上角外侧，一像素都不压住地块 */
:global(.recap-tag-wrap) { background: transparent; border: none; }
:global(.recap-tag) {
  transform: translate(3px, calc(-100% - 3px));
  display: inline-flex; align-items: center; gap: 2px;
  white-space: nowrap;
  background: rgba(255, 255, 255, 0.9);
  border: 1px solid rgba(249, 115, 22, 0.55);
  border-radius: 7px; padding: 1px 5px;
  font-size: 9px; font-weight: 600; color: #c2410c;
  font-variant-numeric: tabular-nums;
  line-height: 1.5;
}
:global(.recap-tag span) { font-size: 10px; opacity: 0.75; }
</style>
