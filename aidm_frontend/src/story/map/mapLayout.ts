import type { WorldMapNode } from '../../gameSelectors'

/** The map's SVG coordinate system; node x/y (0–100) map onto it. */
export const MAP_WIDTH = 1000
export const MAP_HEIGHT = 750

const REM_PX = 16
const NARROW_WIDTH = 380
const WIDE_WIDTH = 1100
/** Nominal glyph size in its own path units (the paths are drawn about ±12 around the place). */
const GLYPH_UNITS = 24
const COMPASS_UNITS = 56
const EDGE_MARGIN_PX = 6
/** The Expand (inline) and Close (overlay) buttons sit in the top-right corner. */
const CORNER_BUTTON_PX = 52
const CENTRAL_LOW = 20
const CENTRAL_HIGH = 80
/** The caption under the current place, set a little smaller than its name. */
export const HERE_CAPTION = 'You are here'
const HERE_SIZE = 0.78

export type Box = { left: number; top: number; right: number; bottom: number }

/** Pixel sizes for a map rendered `width` CSS pixels wide, and the SVG units in one pixel. */
export type InkSizes = { unitsPerPx: number; glyphPx: number; labelPx: number; compassPx: number }

export function inkSizes(width: number): InkSizes {
  const rendered = width > 0 ? width : MAP_WIDTH
  const t = Math.max(0, Math.min(1, (rendered - NARROW_WIDTH) / (WIDE_WIDTH - NARROW_WIDTH)))
  return {
    unitsPerPx: MAP_WIDTH / rendered,
    glyphPx: 24 + 16 * t,
    labelPx: (0.72 + 0.28 * t) * REM_PX,
    compassPx: 40 + 16 * t,
  }
}

/** SVG scale factor that draws a glyph's path units at the rendered glyph size. */
export function glyphScale(sizes: InkSizes): number {
  return sizes.glyphPx / GLYPH_UNITS * sizes.unitsPerPx
}

export function compassScale(sizes: InkSizes): number {
  return sizes.compassPx / COMPASS_UNITS * sizes.unitsPerPx
}

function centralAxis(values: number[]): (value: number) => number {
  const low = Math.min(...values)
  const high = Math.max(...values)
  if (low >= CENTRAL_LOW && high <= CENTRAL_HIGH) return (value) => value
  if (high - low < 1) return () => (CENTRAL_LOW + CENTRAL_HIGH) / 2
  return (value) => CENTRAL_LOW + (value - low) / (high - low) * (CENTRAL_HIGH - CENTRAL_LOW)
}

/**
 * The server spreads any layout to the map's corners. With one or two places that reads as
 * an accident, so they are drawn in the central 60% of the map instead.
 */
export function centredNodes(nodes: WorldMapNode[]): WorldMapNode[] {
  if (!nodes.length || nodes.length >= 3) return nodes
  const toX = centralAxis(nodes.map((node) => node.x))
  const toY = centralAxis(nodes.map((node) => node.y))
  return nodes.map((node) => ({ ...node, x: toX(node.x), y: toY(node.y) }))
}

export function nodePoint(node: WorldMapNode) {
  return { x: node.x * MAP_WIDTH / 100, y: node.y * MAP_HEIGHT / 100 }
}

export function labelLines(name: string): string[] {
  if (name.length <= 19) return [name]
  const words = name.split(/\s+/)
  if (words.length === 1) return [name.slice(0, Math.ceil(name.length / 2)), name.slice(Math.ceil(name.length / 2))]
  let best = 1
  let difference = Infinity
  for (let index = 1; index < words.length; index += 1) {
    const delta = Math.abs(words.slice(0, index).join(' ').length - words.slice(index).join(' ').length)
    if (delta < difference) { difference = delta; best = index }
  }
  return [words.slice(0, best).join(' '), words.slice(best).join(' ')].filter(Boolean)
}

export type LabelPlacement = {
  lines: string[]
  /** Text position relative to the place. */
  x: number
  y: number
  anchor: 'start' | 'middle' | 'end'
  fontSize: number
  lineHeight: number
  /** "You are here" baseline relative to the place, for the current place only. */
  hereY: number | null
  hereFontSize: number
  box: Box
}

type Side = 'below' | 'above' | 'right' | 'left'

function overlap(a: Box, b: Box): number {
  const width = Math.min(a.right, b.right) - Math.max(a.left, b.left)
  const height = Math.min(a.bottom, b.bottom) - Math.max(a.top, b.top)
  return width > 0 && height > 0 ? width * height : 0
}

function outside(box: Box, margin: number): number {
  const inner = { left: margin, top: margin, right: MAP_WIDTH - margin, bottom: MAP_HEIGHT - margin }
  const area = (box.right - box.left) * (box.bottom - box.top)
  return area - overlap(box, inner)
}

function glyphBox(point: { x: number; y: number }, sizes: InkSizes): Box {
  const half = sizes.glyphPx * sizes.unitsPerPx * 0.6
  return { left: point.x - half, top: point.y - half, right: point.x + half, bottom: point.y + half }
}

/**
 * One label position. Above or below, the block is centred on the place, then shifted sideways
 * when that would cross `bounds` (the map edge, or a box the label must clear).
 */
function candidate(
  node: WorldMapNode, side: Side, sizes: InkSizes, margin: number,
  bounds: { left: number; right: number } = { left: margin, right: MAP_WIDTH - margin },
): LabelPlacement {
  const point = nodePoint(node)
  const unit = sizes.unitsPerPx
  const lines = labelLines(node.name)
  const fontSize = sizes.labelPx * unit
  const lineHeight = fontSize * 1.15
  const hereFontSize = fontSize * HERE_SIZE
  const here = node.state === 'current'
  const width = Math.max(...lines.map((line) => line.length), here ? HERE_CAPTION.length * HERE_SIZE : 0) * fontSize * 0.56
  const height = lines.length * lineHeight + (here ? hereFontSize * 1.35 : 0)
  const glyphHalf = sizes.glyphPx * unit * 0.5
  const glyphSide = sizes.glyphPx * unit * 0.6
  const gap = 4 * unit
  let top: number
  let left: number
  let anchor: LabelPlacement['anchor'] = 'middle'
  let x = 0
  if (side === 'below' || side === 'above') {
    top = side === 'below' ? point.y + glyphHalf + gap : point.y - glyphHalf - gap - height
    left = point.x - width / 2
    // Shifted only as far as the edge demands, so the name stays under its place.
    if (left < bounds.left) {
      anchor = 'start'
      x = bounds.left - point.x
      left = bounds.left
    } else if (left + width > bounds.right) {
      anchor = 'end'
      x = bounds.right - point.x
      left = bounds.right - width
    }
  } else {
    top = point.y - height / 2
    anchor = side === 'right' ? 'start' : 'end'
    x = side === 'right' ? glyphSide + gap : -glyphSide - gap
    left = side === 'right' ? point.x + x : point.x + x - width
  }
  const y = top - point.y + fontSize * 0.82
  return {
    lines,
    x,
    y,
    anchor,
    fontSize,
    lineHeight,
    hereY: here ? y + (lines.length - 1) * lineHeight + hereFontSize * 1.3 : null,
    hereFontSize,
    box: { left, top, right: left + width, bottom: top + height },
  }
}

export function compassBox(position: { x: number; y: number }, sizes: InkSizes): Box {
  const half = sizes.compassPx * sizes.unitsPerPx * 0.5
  // The "N" stands above the rose.
  return { left: position.x - half, top: position.y - half * 1.3, right: position.x + half, bottom: position.y + half }
}

/** Where the compass rose sits: a bottom corner clear of every place and its usual label. */
export function compassPosition(nodes: WorldMapNode[], sizes: InkSizes): { x: number; y: number } {
  const half = sizes.compassPx * sizes.unitsPerPx * 0.5
  const inset = 56
  const right = MAP_WIDTH - inset - half
  const bottom = MAP_HEIGHT - inset - half
  const step = half * 2.6
  const spots = [
    { x: right, y: bottom }, { x: right, y: bottom - step }, { x: inset + half, y: bottom }, { x: right, y: bottom - 2 * step },
  ]
  const margin = EDGE_MARGIN_PX * sizes.unitsPerPx
  const obstacles = nodes.flatMap((node) => {
    const glyph = glyphBox(nodePoint(node), sizes)
    return node.state === 'rumoured' ? [glyph] : [glyph, candidate(node, 'below', sizes, margin).box]
  })
  return spots.find((spot) => obstacles.every((obstacle) => overlap(compassBox(spot, sizes), obstacle) === 0)) ?? spots[0]
}

/** The map's own furniture that labels must never sit under. */
export function reservedBoxes(sizes: InkSizes, compass: { x: number; y: number }): Box[] {
  const corner = CORNER_BUTTON_PX * sizes.unitsPerPx
  return [{ left: MAP_WIDTH - corner, top: 0, right: MAP_WIDTH, bottom: corner }, compassBox(compass, sizes)]
}

/**
 * Each label goes below its place, else above, right or left, pushed sideways when furniture is
 * in the way: the first spot fully inside the map that clears the reserved furniture, the other
 * places and the labels already placed, and still sits beside its own place.
 */
export function placeLabels(nodes: WorldMapNode[], sizes: InkSizes, reserved: Box[]): Map<string, LabelPlacement> {
  const margin = EDGE_MARGIN_PX * sizes.unitsPerPx
  const gap = 4 * sizes.unitsPerPx
  const reach = sizes.glyphPx * sizes.unitsPerPx * 1.6
  const placements = new Map<string, LabelPlacement>()
  const placed: Box[] = []
  for (const node of nodes) {
    if (node.state === 'rumoured') continue
    const point = nodePoint(node)
    const others = nodes.filter((other) => other.id !== node.id).map((other) => glyphBox(nodePoint(other), sizes))
    const obstacles = [...reserved, ...others, ...placed]
    const options = (['below', 'above', 'right', 'left'] as Side[]).flatMap((side) => {
      const base = candidate(node, side, sizes, margin)
      if (side === 'right' || side === 'left') return [base]
      const pushed = reserved.filter((box) => overlap(base.box, box) > 0).flatMap((box) => [
        candidate(node, side, sizes, margin, { left: margin, right: Math.min(MAP_WIDTH - margin, box.left - gap) }),
        candidate(node, side, sizes, margin, { left: Math.max(margin, box.right + gap), right: MAP_WIDTH - margin }),
      ])
      return [base, ...pushed]
    }).map((placement) => {
      const detached = Math.max(0, point.x - placement.box.right, placement.box.left - point.x) > reach
      const blocked = (detached ? MAP_WIDTH * MAP_HEIGHT : 0) + outside(placement.box, margin) * 4
        + obstacles.reduce((total, box) => total + overlap(placement.box, box), 0)
      return { placement, blocked }
    })
    const best = options.find((option) => option.blocked === 0) ?? options.reduce((a, b) => (b.blocked < a.blocked ? b : a))
    placements.set(node.id, best.placement)
    placed.push(best.placement.box)
  }
  return placements
}
