import { describe, expect, it } from 'vitest'
import { mapFixture } from '../preview/fixtures'
import { distanceToRoutes, drawRoutes } from './mapRoutes'
import { generateTerrain, terrainColor, terrainHeight, terrainName } from './mapTerrain'

describe('map terrain', () => {
  it('uses bounded, deterministic noise that changes with the seed', () => {
    const sample = Array.from({ length: 100 }, (_, index) => terrainHeight(index / 100, index / 120, 7))
    expect(sample.every((height) => height >= 0 && height <= 1)).toBe(true)
    expect(sample).toEqual(Array.from({ length: 100 }, (_, index) => terrainHeight(index / 100, index / 120, 7)))
    expect(sample).not.toEqual(Array.from({ length: 100 }, (_, index) => terrainHeight(index / 100, index / 120, 8)))
  })

  it('uses distinct palettes and the expected coastal and marsh thresholds', () => {
    expect(terrainColor('forest', 0.5)[1]).toBeGreaterThan(terrainColor('forest', 0.5)[0])
    expect(terrainColor('coast', 0.2)[2]).toBeGreaterThan(terrainColor('coast', 0.2)[0])
    expect(terrainColor('marsh', 0.1)).not.toEqual(terrainColor('marsh', 0.8))
    expect(new Set(['moor', 'forest', 'mountain', 'coast', 'plains', 'marsh'].map((name) => terrainColor(terrainName(name), 0.5).join(','))).size).toBe(6)
    expect(terrainName('unrecognised')).toBe('plains')
  })

  it('caches by terrain, seed, size and visible places, with scatter clear of routes and places', () => {
    const map = mapFixture()
    const routes = drawRoutes(map.nodes, map.routes, map.seed)
    const image = generateTerrain(600, 450, 'moor', map.seed, map.nodes, routes)
    expect(generateTerrain(600, 450, 'moor', map.seed, map.nodes, routes)).toBe(image)
    expect(generateTerrain(600, 450, 'moor', map.seed + 1, map.nodes, routes).pixels).not.toEqual(image.pixels)
    expect(image.width).toBe(200)
    expect(image.height).toBe(150)
    expect(image.fog[0]).not.toEqual(image.fog[1])
    expect(image.scatter.length).toBeGreaterThan(30)
    image.scatter.forEach((mark, index) => {
      expect(mark.opacity).toBeGreaterThanOrEqual(0.28)
      expect(mark.opacity).toBeLessThanOrEqual(0.4)
      expect(distanceToRoutes(mark, routes)).toBeGreaterThanOrEqual(34)
      for (const node of map.nodes) expect(Math.hypot(mark.x - node.x * 10, mark.y - node.y * 7.5)).toBeGreaterThanOrEqual(34)
      for (const other of image.scatter.slice(0, index)) expect(Math.hypot(mark.x - other.x, mark.y - other.y)).toBeGreaterThanOrEqual(26)
    })
  })
})
