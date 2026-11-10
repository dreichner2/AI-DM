import { describe, expect, it } from 'vitest'
import { mapFixture } from '../preview/fixtures'
import { drawRoutes } from './mapRoutes'

describe('drawRoutes', () => {
  it('draws repeatable curves with exact endpoints and removes dangling routes', () => {
    const map = mapFixture()
    const routes = drawRoutes(map.nodes, [...map.routes, { from: 'inn', to: 'missing', style: 'road', travelable: false }], map.seed)
    expect(routes).toEqual(drawRoutes(map.nodes, map.routes, map.seed))
    expect(routes[0].points[0]).toEqual({ x: 190, y: 570 })
    expect(routes[0].points.at(-1)).toEqual({ x: 370, y: 427.5 })
    expect(routes[0].path).toMatch(/^M 190\.0 570\.0 C /)
    const { points } = routes[0]
    const [first, middle, last] = [points[0], points[1], points[points.length - 1]]
    expect((middle.x - first.x) * (last.y - first.y) - (middle.y - first.y) * (last.x - first.x)).not.toBeCloseTo(0)
    expect(routes[0].current).toBe(true)
    expect(routes[2].current).toBe(false)
    expect(drawRoutes(map.nodes, map.routes, map.seed + 1)[0].points).not.toEqual(points)
  })

  it('keeps the same route shape when its endpoints are reversed', () => {
    const map = mapFixture()
    const forward = drawRoutes(map.nodes, [map.routes[0]], map.seed)[0]
    const reverse = drawRoutes(map.nodes, [{ ...map.routes[0], from: map.routes[0].to, to: map.routes[0].from }], map.seed)[0]
    forward.points.forEach((point, index) => {
      expect(point.x).toBeCloseTo(reverse.points.at(-index - 1)!.x)
      expect(point.y).toBeCloseTo(reverse.points.at(-index - 1)!.y)
    })
  })
})
