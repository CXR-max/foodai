// 领地格子 → 轮廓
// 地图页画在 Leaflet 上，详情页画成静态 SVG 剪影，两处必须用同一套算法，
// 否则「同一块地」在两个页面上形状会不一样。
export type GridPoint = [number, number]

export interface OwnerShape {
  ownerId: number
  vitality: number
  cells: number
  loops: GridPoint[][]
}

/** t25_123_456 → [123, 456] */
export function cellIxIy(key: string): [number, number] {
  const p = key.split('_')
  return [Number(p[1]), Number(p[2])]
}

/** Chaikin 平滑：把锯齿状的格子边界磨成一条顺滑的轮廓线 */
export function chaikin(pts: GridPoint[], iterations = 2): GridPoint[] {
  let out = pts
  for (let k = 0; k < iterations; k++) {
    if (out.length < 3) break
    const next: GridPoint[] = []
    for (let i = 0; i < out.length; i++) {
      const p = out[i]
      const q = out[(i + 1) % out.length]
      next.push([p[0] * 0.75 + q[0] * 0.25, p[1] * 0.75 + q[1] * 0.25])
      next.push([p[0] * 0.25 + q[0] * 0.75, p[1] * 0.25 + q[1] * 0.75])
    }
    out = next
  }
  return out
}

/**
 * 把同一批格子合并成外轮廓（可能有多段闭环，比如两块不连的地）。
 * cells 是 [key, ownerId, vitality, flags]，与地图接口保持一致。
 */
export function buildOwnerShapes(cells: [string, number, number, number][]): OwnerShape[] {
  const byOwner = new Map<number, { set: Set<string>; vit: number[] }>()
  for (const [key, ownerId, vitality] of cells) {
    const entry = byOwner.get(ownerId) || { set: new Set<string>(), vit: [] }
    entry.set.add(key)
    entry.vit.push(vitality)
    byOwner.set(ownerId, entry)
  }
  const out: OwnerShape[] = []
  for (const [ownerId, entry] of byOwner) {
    const occupied = new Set<string>()
    for (const key of entry.set) {
      const [x, y] = cellIxIy(key)
      occupied.add(`${x},${y}`)
    }
    // 只保留「暴露在外」的边，再把边串成环
    const edges = new Map<string, string[]>()
    const edgeKey = (a: string, b: string) => `${a}|${b}`
    const addEdge = (a: string, b: string) => {
      const arr = edges.get(a) || []
      arr.push(b)
      edges.set(a, arr)
    }
    for (const cell of occupied) {
      const [xs, ys] = cell.split(',')
      const x = Number(xs)
      const y = Number(ys)
      if (!occupied.has(`${x},${y - 1}`)) addEdge(`${x},${y}`, `${x + 1},${y}`)
      if (!occupied.has(`${x + 1},${y}`)) addEdge(`${x + 1},${y}`, `${x + 1},${y + 1}`)
      if (!occupied.has(`${x},${y + 1}`)) addEdge(`${x + 1},${y + 1}`, `${x},${y + 1}`)
      if (!occupied.has(`${x - 1},${y}`)) addEdge(`${x},${y + 1}`, `${x},${y}`)
    }
    const used = new Set<string>()
    const loops: GridPoint[][] = []
    for (const [start, ends] of edges) {
      for (const first of ends) {
        if (used.has(edgeKey(start, first))) continue
        const loop: GridPoint[] = []
        let a = start
        let b = first
        let guard = 0
        while (guard++ < 20000) {
          used.add(edgeKey(a, b))
          const [ax, ay] = a.split(',').map(Number)
          loop.push([ax, ay])
          if (b === start) break
          const nexts = edges.get(b) || []
          const next = nexts.find((n) => !used.has(edgeKey(b, n)))
          if (!next) break
          a = b
          b = next
        }
        if (loop.length >= 3) loops.push(chaikin(loop, 3))
      }
    }
    if (!loops.length) continue
    out.push({
      ownerId,
      vitality: entry.vit.reduce((a, b) => a + b, 0) / entry.vit.length,
      cells: occupied.size,
      loops,
    })
  }
  return out
}

function loopArea(loop: GridPoint[]): number {
  let sum = 0
  for (let i = 0; i < loop.length; i++) {
    const [x1, y1] = loop[i]
    const [x2, y2] = loop[(i + 1) % loop.length]
    sum += x1 * y2 - x2 * y1
  }
  return Math.abs(sum) / 2
}

function pointInLoop(p: GridPoint, loop: GridPoint[]): boolean {
  let inside = false
  for (let i = 0, j = loop.length - 1; i < loop.length; j = i++) {
    const [xi, yi] = loop[i]
    const [xj, yj] = loop[j]
    if ((yi > p[1]) !== (yj > p[1]) &&
        p[0] < ((xj - xi) * (p[1] - yi)) / (yj - yi || 1e-9) + xi) {
      inside = !inside
    }
  }
  return inside
}

/** 剪影用：外圈把内圈（跑道中间的洞）填掉，否则看起来像破了 */
function dropHoles(loops: GridPoint[][]): GridPoint[][] {
  if (loops.length < 2) return loops
  const areas = loops.map(loopArea)
  return loops.filter((loop, i) => {
    const cx = loop.reduce((s, p) => s + p[0], 0) / loop.length
    const cy = loop.reduce((s, p) => s + p[1], 0) / loop.length
    for (let j = 0; j < loops.length; j++) {
      if (j === i || areas[j] <= areas[i]) continue
      if (pointInLoop([cx, cy], loops[j])) return false
    }
    return true
  })
}

/** 只关心「一块地」时用这个：传格子 key，拿到平滑后的闭环 */
export function outlineLoops(keys: string[], fillHoles = true): GridPoint[][] {
  const shape = buildOwnerShapes(keys.map((k) => [k, 1, 100, 0] as [string, number, number, number]))[0]
  if (!shape) return []
  return fillHoles ? dropHoles(shape.loops) : shape.loops
}

/**
 * 把网格坐标的闭环缩放到 width×height 的 SVG 视口，返回每一段闭环的 path d。
 * 逐环返回（而不是拼成一条复合路径）是为了和地图一致：
 * 地图上每个环都是一块独立填充的多边形，复合路径反而会把里圈挖成空洞。
 */
export function loopsToSvgPaths(
  loops: GridPoint[][],
  width: number,
  height: number,
  pad = 8,
): string[] {
  if (!loops.length) return []
  let minX = Infinity
  let minY = Infinity
  let maxX = -Infinity
  let maxY = -Infinity
  for (const loop of loops) {
    for (const [x, y] of loop) {
      if (x < minX) minX = x
      if (x > maxX) maxX = x
      if (y < minY) minY = y
      if (y > maxY) maxY = y
    }
  }
  const bw = Math.max(1, maxX - minX)
  const bh = Math.max(1, maxY - minY)
  const scale = Math.min((width - pad * 2) / bw, (height - pad * 2) / bh)
  const ox = (width - bw * scale) / 2
  const oy = (height - bh * scale) / 2
  const tx = (x: number) => ox + (x - minX) * scale
  const ty = (y: number) => oy + (maxY - y) * scale
  return loops.map((loop) => {
    const d = loop
      .map(([x, y], i) => `${i ? 'L' : 'M'}${tx(x).toFixed(1)} ${ty(y).toFixed(1)}`)
      .join(' ')
    return `${d}Z`
  })
}
