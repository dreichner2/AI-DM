import { describe, expect, it } from 'vitest'
import type { WorldMapNode } from '../../gameSelectors'
import { mapFixture } from '../preview/fixtures'
import {
  MAP_HEIGHT, MAP_WIDTH, centredNodes, compassBox, compassPosition, inkSizes, placeLabels, reservedBoxes, type Box,
} from './mapLayout'

function node(id: string, x: number, y: number, overrides: Partial<WorldMapNode> = {}): WorldMapNode {
  return { id, name: `Place ${id}`, icon: 'town', x, y, state: 'known', ...overrides }
}

function intersects(a: Box, b: Box) {
  return Math.min(a.right, b.right) > Math.max(a.left, b.left) && Math.min(a.bottom, b.bottom) > Math.max(a.top, b.top)
}

describe('map layout', () => {
  it('scales glyphs and labels with the rendered width', () => {
    expect(inkSizes(380)).toMatchObject({ glyphPx: 24, labelPx: 0.72 * 16 })
    expect(inkSizes(1100)).toMatchObject({ glyphPx: 40, labelPx: 16 })
    expect(inkSizes(740).glyphPx).toBeCloseTo(32)
    expect(inkSizes(300)).toMatchObject({ glyphPx: 24, labelPx: 0.72 * 16 })
    expect(inkSizes(1100).unitsPerPx).toBeCloseTo(MAP_WIDTH / 1100)
  })

  it('draws one or two places in the central 60% of the map', () => {
    const corners = centredNodes([node('a', 8, 92), node('b', 92, 8)])
    for (const place of corners) {
      expect(place.x).toBeGreaterThanOrEqual(20)
      expect(place.x).toBeLessThanOrEqual(80)
      expect(place.y).toBeGreaterThanOrEqual(20)
      expect(place.y).toBeLessThanOrEqual(80)
    }
    expect(corners[0].x).toBeLessThan(corners[1].x)
    expect(corners[0].y).toBeGreaterThan(corners[1].y)
    expect(centredNodes([node('only', 3, 97)])[0]).toMatchObject({ x: 50, y: 50 })
    expect(centredNodes([node('a', 8, 92), node('b', 92, 92)]).map(({ y }) => y)).toEqual([50, 50])
  })

  it('leaves authored central positions and maps of three or more places alone', () => {
    const central = [node('a', 30, 40), node('b', 60, 55)]
    expect(centredNodes(central)).toEqual(central)
    const three = [node('a', 8, 92), node('b', 92, 8), node('c', 50, 50)]
    expect(centredNodes(three)).toBe(three)
  })

  it('keeps every label inside the map, clear of the corner button, the compass and other places', () => {
    const nodes = [
      node('nw', 8, 8, { name: 'The Lantern Post Inn', state: 'current' }),
      node('ne', 92, 8, { name: 'The Watchtower of Ashes' }),
      node('sw', 8, 92, { name: 'The Old North Road' }),
      node('se', 92, 92, { name: 'Reedwater Ford and Ferry' }),
      node('mid', 50, 50, { name: 'Hollin Cross' }),
      node('floor', 40, 99, { name: 'The Drowned Steps' }),
      node('wall', 1, 50, { name: 'The Saltmarsh Gate' }),
      node('rumour', 70, 30, { name: '?', icon: 'unknown', state: 'rumoured' }),
    ]
    for (const width of [380, 1100]) {
      const sizes = inkSizes(width)
      const compass = compassPosition(nodes, sizes)
      const reserved = reservedBoxes(sizes, compass)
      const labels = placeLabels(nodes, sizes, reserved)
      expect(labels.has('rumour')).toBe(false)
      for (const [id, label] of labels) {
        expect(label.box.left, id).toBeGreaterThanOrEqual(0)
        expect(label.box.top, id).toBeGreaterThanOrEqual(0)
        expect(label.box.right, id).toBeLessThanOrEqual(MAP_WIDTH)
        expect(label.box.bottom, id).toBeLessThanOrEqual(MAP_HEIGHT)
        for (const box of reserved) expect(intersects(label.box, box), `${id} at ${width}px`).toBe(false)
      }
      expect(labels.get('floor')?.y).toBeLessThan(0)
      expect(labels.get('wall')?.anchor).toBe('start')
      // Inline, the Expand button covers the corner place's usual spot, so its name moves left of it.
      expect(labels.get('ne')?.anchor).toBe(width === 380 ? 'end' : 'middle')
      expect(labels.get('mid')).toMatchObject({ anchor: 'middle', x: 0 })
      expect(labels.get('mid')?.y).toBeGreaterThan(0)
      expect(labels.get('nw')?.hereY).toBeGreaterThan(labels.get('nw')?.y ?? Infinity)
    }
  })

  it('moves the compass out of the way of a place in the bottom-right corner', () => {
    const sizes = inkSizes(1100)
    const open = compassPosition(mapFixture().nodes, sizes)
    const crowded = compassPosition([node('corner', 92, 92)], sizes)
    expect(crowded).not.toEqual(compassPosition([], sizes))
    expect(intersects(compassBox(open, sizes), { left: 900, top: 670, right: 940, bottom: 710 })).toBe(true)
  })
})
