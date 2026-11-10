import type { WorldMapNode, WorldMapRoute } from '../../gameSelectors'

export type MapPoint = { x: number; y: number }
export type DrawnRoute = WorldMapRoute & { path: string; points: MapPoint[]; current: boolean }

/** A stable hash; the same place and map seed always produce the same ink. */
export function mapHash(value: string, seed: number): number {
  let hash = (2166136261 ^ seed) >>> 0
  for (let index = 0; index < value.length; index += 1) {
    hash = Math.imul(hash ^ value.charCodeAt(index), 16777619) >>> 0
  }
  return hash >>> 0
}

function coordinate(node: WorldMapNode): MapPoint {
  return { x: node.x * 10, y: node.y * 7.5 }
}

function pointOnCurve(points: MapPoint[]): string {
  const number = (value: number) => value.toFixed(1)
  let result = `M ${number(points[0].x)} ${number(points[0].y)}`
  for (let index = 0; index < points.length - 1; index += 1) {
    const before = points[Math.max(0, index - 1)]
    const start = points[index]
    const end = points[index + 1]
    const after = points[Math.min(points.length - 1, index + 2)]
    const first = { x: start.x + (end.x - before.x) / 6, y: start.y + (end.y - before.y) / 6 }
    const second = { x: end.x - (after.x - start.x) / 6, y: end.y - (after.y - start.y) / 6 }
    result += ` C ${number(first.x)} ${number(first.y)} ${number(second.x)} ${number(second.y)} ${number(end.x)} ${number(end.y)}`
  }
  return result
}

/** Curved, reproducible routes in the map's 1000 × 750 coordinate system. */
export function drawRoutes(nodes: WorldMapNode[], routes: WorldMapRoute[], seed: number): DrawnRoute[] {
  const byId = new Map(nodes.map((node) => [node.id, node]))
  const current = nodes.find((node) => node.state === 'current')?.id
  return routes.flatMap((route) => {
    const first = byId.get(route.from)
    const last = byId.get(route.to)
    if (!first || !last) return []
    const start = coordinate(first)
    const end = coordinate(last)
    const dx = end.x - start.x
    const dy = end.y - start.y
    const distance = Math.hypot(dx, dy) || 1
    const perpendicular = { x: -dy / distance, y: dx / distance }
    // Canonical route identity makes the same road keep its shape if its ends reverse.
    const key = [route.from, route.to].sort().join(':')
    const hash = mapHash(key, seed)
    const sign = (hash & 1 ? 1 : -1) * (route.from < route.to ? 1 : -1)
    const amplitude = distance * (0.04 + ((hash >>> 3) % 51) / 1000) * sign
    const fractions = hash & 2 ? [0.25, 0.5, 0.75] : [1 / 3, 2 / 3]
    const points = [start, ...fractions.map((fraction) => {
      const variation = Math.sin(fraction * Math.PI)
      return {
        x: start.x + dx * fraction + perpendicular.x * amplitude * variation,
        y: start.y + dy * fraction + perpendicular.y * amplitude * variation,
      }
    }), end]
    return [{ ...route, points, path: pointOnCurve(points), current: route.travelable && (route.from === current || route.to === current) }]
  })
}

function segmentDistance(point: MapPoint, start: MapPoint, end: MapPoint): number {
  const dx = end.x - start.x
  const dy = end.y - start.y
  const lengthSquared = dx * dx + dy * dy
  const t = lengthSquared ? Math.max(0, Math.min(1, ((point.x - start.x) * dx + (point.y - start.y) * dy) / lengthSquared)) : 0
  return Math.hypot(point.x - (start.x + t * dx), point.y - (start.y + t * dy))
}

/** Approximate the curved road with its control points when keeping scatter clear. */
export function distanceToRoutes(point: MapPoint, routes: DrawnRoute[]): number {
  let closest = Infinity
  for (const route of routes) {
    for (let index = 0; index < route.points.length - 1; index += 1) {
      closest = Math.min(closest, segmentDistance(point, route.points[index], route.points[index + 1]))
    }
  }
  return closest
}
