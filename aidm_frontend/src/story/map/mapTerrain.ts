import type { WorldMapNode } from '../../gameSelectors'
import { distanceToRoutes, mapHash, type DrawnRoute } from './mapRoutes'

export type TerrainName = 'moor' | 'forest' | 'mountain' | 'coast' | 'plains' | 'marsh'
export type ScatterMark = { x: number; y: number; rotation: number; scale: number; opacity: number; kind: string }
export type TerrainImage = {
  width: number
  height: number
  heights: Float32Array
  pixels: Uint8ClampedArray
  fog: [Uint8ClampedArray, Uint8ClampedArray]
  scatter: ScatterMark[]
}

type Color = readonly [number, number, number]
const PALETTES: Record<TerrainName, readonly [Color, Color, Color]> = {
  moor: [[30, 27, 24], [57, 47, 51], [70, 73, 55]],
  forest: [[18, 30, 25], [35, 52, 39], [61, 74, 51]],
  mountain: [[27, 34, 39], [48, 57, 62], [79, 81, 78]],
  coast: [[15, 41, 43], [42, 65, 61], [83, 77, 60]],
  plains: [[42, 34, 25], [73, 61, 40], [95, 82, 53]],
  marsh: [[18, 32, 28], [39, 55, 39], [67, 73, 49]],
}
const TERRAINS: TerrainName[] = ['moor', 'forest', 'mountain', 'coast', 'plains', 'marsh']
const CACHE_LIMIT = 8
const terrainCache = new Map<string, TerrainImage>()

export function terrainName(value: string): TerrainName {
  return TERRAINS.find((name) => name === value) ?? 'plains'
}

function lattice(x: number, y: number, seed: number): number {
  let n = Math.imul(x, 374761393) + Math.imul(y, 668265263) + Math.imul(seed | 0, 1442695041)
  n = Math.imul(n ^ (n >>> 13), 1274126177)
  return ((n ^ (n >>> 16)) >>> 0) / 4294967295
}

function smooth(value: number): number {
  return value * value * (3 - 2 * value)
}

function valueNoise(x: number, y: number, seed: number): number {
  const x0 = Math.floor(x)
  const y0 = Math.floor(y)
  const fx = smooth(x - x0)
  const fy = smooth(y - y0)
  const top = lattice(x0, y0, seed) * (1 - fx) + lattice(x0 + 1, y0, seed) * fx
  const bottom = lattice(x0, y0 + 1, seed) * (1 - fx) + lattice(x0 + 1, y0 + 1, seed) * fx
  return top * (1 - fy) + bottom * fy
}

/** Four octaves of seeded value noise, always in the range 0–1. */
export function terrainHeight(x: number, y: number, seed: number): number {
  let height = 0
  let amplitude = 1
  let total = 0
  for (let octave = 0; octave < 4; octave += 1) {
    const frequency = 3 * 2 ** octave
    height += valueNoise(x * frequency, y * frequency, seed + octave * 1013) * amplitude
    total += amplitude
    amplitude *= 0.5
  }
  return height / total
}

function mix(a: Color, b: Color, amount: number): [number, number, number] {
  const t = Math.max(0, Math.min(1, amount))
  return [a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t, a[2] + (b[2] - a[2]) * t]
}

/** Terrain colour before the hillshade, vignette and grain are applied. */
export function terrainColor(terrain: TerrainName, height: number): [number, number, number] {
  const palette = PALETTES[terrain]
  if (terrain === 'coast' && height < 0.47) {
    const water = mix(palette[0], palette[1], height / 0.47)
    return height > 0.447 ? mix(water, [113, 124, 108], 0.28) : water
  }
  if (terrain === 'marsh' && height < 0.32) return mix([12, 28, 28], palette[0], height / 0.32)
  return height < 0.5 ? mix(palette[0], palette[1], height * 2) : mix(palette[1], palette[2], (height - 0.5) * 2)
}

function randomFor(seed: number): () => number {
  let value = seed >>> 0
  return () => {
    value = (Math.imul(value, 1664525) + 1013904223) >>> 0
    return value / 4294967296
  }
}

function scatterMarks(seed: number, terrain: TerrainName, nodes: WorldMapNode[], routes: DrawnRoute[]): ScatterMark[] {
  const random = randomFor(mapHash(`scatter:${terrain}`, seed))
  const minDistance = 26
  const cell = minDistance / Math.SQRT2
  const columns = Math.ceil(1000 / cell)
  const rows = Math.ceil(750 / cell)
  const cells: Array<ScatterMark | undefined> = new Array(columns * rows)
  const marks: ScatterMark[] = []
  for (let attempt = 0; attempt < 2400 && marks.length < 235; attempt += 1) {
    const x = 12 + random() * 976
    const y = 12 + random() * 726
    if (nodes.some((node) => {
      const dx = x - node.x * 10
      const dy = y - node.y * 7.5
      return Math.hypot(dx, dy) < 34 || (Math.abs(dx) < 70 && dy > 0 && dy < 46)
    }) || distanceToRoutes({ x, y }, routes) < 34) continue
    const gx = Math.floor(x / cell)
    const gy = Math.floor(y / cell)
    let crowded = false
    for (let iy = Math.max(0, gy - 2); iy <= Math.min(rows - 1, gy + 2) && !crowded; iy += 1) {
      for (let ix = Math.max(0, gx - 2); ix <= Math.min(columns - 1, gx + 2); ix += 1) {
        const existing = cells[iy * columns + ix]
        if (existing && Math.hypot(existing.x - x, existing.y - y) < minDistance) {
          crowded = true
          break
        }
      }
    }
    if (crowded) continue
    const height = terrainHeight(x / 1000, y / 750, seed)
    const kind = terrain === 'forest' ? 'trees' : terrain === 'mountain' ? 'ridge' : terrain === 'marsh' ? 'reeds'
      : terrain === 'plains' ? 'furrows' : terrain === 'coast' ? (height < 0.47 ? 'waves' : 'stones')
        : (height > 0.6 ? 'stones' : 'tussock')
    const mark = { x, y, rotation: (random() * 24 - 12) * Math.PI / 180, scale: 0.8 + random() * 0.4, opacity: 0.28 + random() * 0.12, kind }
    marks.push(mark)
    cells[gy * columns + gx] = mark
  }
  return marks
}

function nearestDistance(x: number, y: number, nodes: WorldMapNode[]): number {
  let squared = 1000000
  for (const node of nodes) {
    const dx = x - node.x * 10
    const dy = y - node.y * 7.5
    squared = Math.min(squared, dx * dx + dy * dy)
  }
  return Math.sqrt(squared)
}

function fogPixel(x: number, y: number, seed: number, distance: number, rumourDistance: number): number {
  const noise = (valueNoise(x / 100, y / 100, seed) - 0.5) * 105
    + (valueNoise(x / 43, y / 43, seed + 11) - 0.5) * 24
  const edge = Math.max(0, Math.min(1, (distance - 180 + noise + 30) / 75))
  let fog = smooth(edge)
  const rumourInfluence = 1 - smooth(Math.max(0, Math.min(1, (rumourDistance - 8) / 48)))
  fog = Math.min(fog, 1 - 0.45 * rumourInfluence)
  return Math.round(fog * 215)
}

/** Build the low-resolution ground and two soft fog layers once per map and size. */
export function generateTerrain(
  width: number, height: number, terrain: TerrainName, seed: number, nodes: WorldMapNode[], routes: DrawnRoute[],
): TerrainImage {
  const w = Math.max(1, Math.ceil(width / 3))
  const h = Math.max(1, Math.ceil(height / 3))
  const key = JSON.stringify([w, h, terrain, seed, nodes.map(({ id, x, y, state }) => [id, x, y, state]), routes.map(({ from, to, path }) => [from, to, path])])
  const cached = terrainCache.get(key)
  if (cached) return cached
  const heights = new Float32Array(w * h)
  const pixels = new Uint8ClampedArray(w * h * 4)
  const fogA = new Uint8ClampedArray(w * h * 4)
  const fogB = new Uint8ClampedArray(w * h * 4)
  const visible = nodes.filter((node) => node.state !== 'rumoured')
  const rumoured = nodes.filter((node) => node.state === 'rumoured')
  for (let y = 0; y < h; y += 1) {
    for (let x = 0; x < w; x += 1) heights[y * w + x] = terrainHeight(x / w, y / h, seed)
  }
  for (let y = 0; y < h; y += 1) {
    for (let x = 0; x < w; x += 1) {
      const index = y * w + x
      const value = heights[index]
      const left = heights[y * w + Math.max(0, x - 1)]
      const right = heights[y * w + Math.min(w - 1, x + 1)]
      const up = heights[Math.max(0, y - 1) * w + x]
      const down = heights[Math.min(h - 1, y + 1) * w + x]
      const shade = 1 + Math.max(-0.35, Math.min(0.35, ((left - right) + (up - down)) * 4))
      const nx = x / Math.max(1, w - 1) * 2 - 1
      const ny = y / Math.max(1, h - 1) * 2 - 1
      const vignette = 1 - Math.min(0.32, (nx * nx + ny * ny) * 0.12)
      const grain = (lattice(x, y, seed + 817) - 0.5) * 6
      const color = terrainColor(terrain, value)
      for (let channel = 0; channel < 3; channel += 1) pixels[index * 4 + channel] = color[channel] * shade * vignette + grain
      pixels[index * 4 + 3] = 255
      const px = x / w * 1000
      const py = y / h * 750
      const distance = nearestDistance(px, py, visible)
      const rumourDistance = nearestDistance(px, py, rumoured)
      for (const [layer, fogSeed] of [[fogA, seed + 303], [fogB, seed + 907]] as const) {
        layer[index * 4] = 3
        layer[index * 4 + 1] = 8
        layer[index * 4 + 2] = 9
        layer[index * 4 + 3] = fogPixel(px, py, fogSeed, distance, rumourDistance)
      }
    }
  }
  const result = { width: w, height: h, heights, pixels, fog: [fogA, fogB] as [Uint8ClampedArray, Uint8ClampedArray], scatter: scatterMarks(seed, terrain, nodes, routes) }
  terrainCache.set(key, result)
  if (terrainCache.size > CACHE_LIMIT) terrainCache.delete(terrainCache.keys().next().value as string)
  return result
}
