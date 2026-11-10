import { describe, expect, it } from 'vitest'
import { mapFixture } from '../preview/fixtures'
import { distanceToRoutes, drawRoutes } from './mapRoutes'
import { FOG_MAX, generateTerrain, terrainColor, terrainHeight, terrainName, type TerrainName } from './mapTerrain'

/** Brightness as the eye reads an sRGB value: Rec. 709 luma on the encoded channels. */
function luma(pixels: Uint8ClampedArray, index: number) {
  return 0.2126 * pixels[index * 4] + 0.7152 * pixels[index * 4 + 1] + 0.0722 * pixels[index * 4 + 2]
}

function linear(channel: number) {
  const value = channel / 255
  return value <= 0.04045 ? value / 12.92 : ((value + 0.055) / 1.055) ** 2.4
}

/** WCAG contrast of the label ink (--story-ink #ece5d3) on a grey of the given luma. */
function inkContrast(groundLuma: number) {
  const ink = 0.2126 * linear(0xec) + 0.7152 * linear(0xe5) + 0.0722 * linear(0xd3)
  return (ink + 0.05) / (linear(groundLuma) + 0.05)
}

function isWater(terrain: TerrainName, height: number) {
  return (terrain === 'coast' && height < 0.47) || (terrain === 'marsh' && height < 0.32)
}

const TERRAINS: TerrainName[] = ['moor', 'forest', 'mountain', 'coast', 'plains', 'marsh']

describe('map terrain', () => {
  it.each(TERRAINS)('gives %s land readable relief that stays dark under labels', (terrain) => {
    const map = mapFixture(terrain)
    const image = generateTerrain(1100, 825, terrain, map.seed, map.nodes, drawRoutes(map.nodes, map.routes, map.seed))
    const land = Array.from(image.heights.keys())
      .filter((index) => !isWater(terrain, image.heights[index]))
      .map((index) => luma(image.pixels, index))
      .sort((a, b) => a - b)
    const darkest = land[Math.floor(land.length * 0.02)]
    const brightest = land[Math.floor(land.length * 0.98)]

    // About 2.2×: enough spread to read the relief at a glance.
    expect(brightest / darkest).toBeGreaterThan(1.9)
    expect(brightest / darkest).toBeLessThan(2.5)
    expect(inkContrast(brightest)).toBeGreaterThanOrEqual(4.5)
  })

  it('keeps the fog, but fogged ground keeps more than 55% of its light', () => {
    const map = mapFixture('moor')
    const image = generateTerrain(1100, 825, 'moor', map.seed, map.nodes, drawRoutes(map.nodes, map.routes, map.seed))
    let deepest = 0
    let dimmest = Infinity
    for (const layer of image.fog) {
      for (let index = 0; index < image.width * image.height; index += 1) {
        const alpha = layer[index * 4 + 3] / 255
        const ground = luma(image.pixels, index)
        deepest = Math.max(deepest, alpha)
        dimmest = Math.min(dimmest, ((1 - alpha) * ground + alpha * luma(layer, index)) / ground)
      }
    }
    expect(deepest).toBeCloseTo(FOG_MAX, 2)
    expect(deepest).toBeGreaterThan(0.3)
    // The CSS cloud shadows dim by at most 8% more, which still leaves the darkest spot above 55%.
    expect(dimmest * 0.92).toBeGreaterThan(0.55)
  })

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
