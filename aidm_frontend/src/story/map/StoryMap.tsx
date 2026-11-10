import { useEffect, useMemo, useRef, useState, type CSSProperties, type KeyboardEvent } from 'react'
import type { WorldMap, WorldMapNode } from '../../gameSelectors'
import { subscribeToMediaQueryChange } from '../../mediaQuery'
import { CompassRose, MapGlyph } from './mapGlyphs'
import {
  HERE_CAPTION, MAP_HEIGHT, MAP_WIDTH, centredNodes, compassPosition, compassScale, glyphScale, inkSizes, nodePoint, placeLabels, reservedBoxes,
} from './mapLayout'
import { drawRoutes } from './mapRoutes'
import { generateTerrain, terrainName, type ScatterMark, type TerrainImage } from './mapTerrain'

export type StoryMapProps = {
  map: WorldMap
  travelBlockedReason: string
  onTravel: (place: { id: string; name: string }) => void
}

function imageCanvas(width: number, height: number, pixels: Uint8ClampedArray): HTMLCanvasElement | null {
  const canvas = document.createElement('canvas')
  canvas.width = width
  canvas.height = height
  const context = canvas.getContext('2d')
  if (!context) return null
  const image = context.createImageData(width, height)
  image.data.set(pixels)
  context.putImageData(image, 0, 0)
  return canvas
}

function inkMark(context: CanvasRenderingContext2D, mark: ScatterMark) {
  context.save()
  context.translate(mark.x, mark.y)
  context.rotate(mark.rotation)
  context.scale(mark.scale, mark.scale)
  context.globalAlpha = mark.opacity
  context.strokeStyle = '#c2bca1'
  context.lineWidth = 1.1
  context.lineCap = 'round'
  context.lineJoin = 'round'
  context.beginPath()
  if (mark.kind === 'trees') {
    context.moveTo(-7, 6); context.lineTo(-4, -2); context.lineTo(-1, 6)
    context.moveTo(0, 6); context.lineTo(4, -6); context.lineTo(8, 6)
    context.moveTo(-4, 6); context.lineTo(-4, 8); context.moveTo(4, 6); context.lineTo(4, 8)
  } else if (mark.kind === 'ridge') {
    context.moveTo(-9, 5); context.lineTo(-2, -4); context.lineTo(2, 1); context.lineTo(5, -2); context.lineTo(10, 5)
    context.moveTo(-6, 7); context.lineTo(-1, 2); context.moveTo(4, 7); context.lineTo(8, 3)
  } else if (mark.kind === 'reeds') {
    context.moveTo(-5, 7); context.lineTo(-6, -4); context.moveTo(-5, 2); context.lineTo(-9, -2)
    context.moveTo(0, 7); context.lineTo(1, -7); context.moveTo(1, 1); context.lineTo(5, -3)
    context.moveTo(5, 7); context.lineTo(7, -3)
  } else if (mark.kind === 'furrows') {
    context.moveTo(-8, -2); context.quadraticCurveTo(0, -5, 8, -2)
    context.moveTo(-9, 2); context.quadraticCurveTo(0, -1, 9, 2)
    context.moveTo(-7, 6); context.quadraticCurveTo(0, 3, 7, 6)
  } else if (mark.kind === 'waves') {
    context.moveTo(-9, 0); context.quadraticCurveTo(-5, -3, -1, 0); context.quadraticCurveTo(3, 3, 7, 0)
    context.moveTo(-5, 5); context.quadraticCurveTo(-1, 2, 3, 5); context.quadraticCurveTo(7, 8, 10, 5)
  } else if (mark.kind === 'stones') {
    context.moveTo(-8, 7); context.lineTo(-7, -1); context.lineTo(-3, -4); context.lineTo(0, -1); context.lineTo(0, 7); context.closePath()
    context.moveTo(3, 7); context.lineTo(4, 2); context.lineTo(7, 0); context.lineTo(10, 7); context.closePath()
  } else {
    context.moveTo(-8, 7); context.quadraticCurveTo(-5, 0, -4, -2); context.lineTo(-3, 7)
    context.moveTo(-2, 7); context.quadraticCurveTo(0, -4, 1, -6); context.lineTo(2, 7)
    context.moveTo(3, 7); context.quadraticCurveTo(5, 1, 8, 0); context.lineTo(7, 7)
  }
  context.stroke()
  context.restore()
}

/** Fine contours make the height field read as land rather than a colour wash. */
function inkContours(context: CanvasRenderingContext2D, field: TerrainImage) {
  const step = 3
  context.save()
  context.strokeStyle = 'rgba(205, 193, 163, 0.15)'
  context.lineWidth = 0.7
  for (let level = 0.32; level <= 0.72; level += 0.055) {
    context.beginPath()
    for (let y = 0; y + step < field.height; y += step) {
      for (let x = 0; x + step < field.width; x += step) {
        const topLeft = field.heights[y * field.width + x]
        const topRight = field.heights[y * field.width + x + step]
        const bottomRight = field.heights[(y + step) * field.width + x + step]
        const bottomLeft = field.heights[(y + step) * field.width + x]
        if (level < Math.min(topLeft, topRight, bottomRight, bottomLeft) || level > Math.max(topLeft, topRight, bottomRight, bottomLeft)) continue
        const corners = [
          { x, y, value: topLeft },
          { x: x + step, y, value: topRight },
          { x: x + step, y: y + step, value: bottomRight },
          { x, y: y + step, value: bottomLeft },
        ]
        const crossings: Array<{ x: number; y: number }> = []
        for (let edge = 0; edge < 4; edge += 1) {
          const a = corners[edge]
          const b = corners[(edge + 1) % 4]
          if ((a.value < level) === (b.value < level)) continue
          const fraction = (level - a.value) / (b.value - a.value)
          crossings.push({ x: (a.x + (b.x - a.x) * fraction) / field.width * 1000, y: (a.y + (b.y - a.y) * fraction) / field.height * 750 })
        }
        for (let index = 0; index + 1 < crossings.length; index += 2) {
          context.moveTo(crossings[index].x, crossings[index].y)
          context.lineTo(crossings[index + 1].x, crossings[index + 1].y)
        }
      }
    }
    context.stroke()
  }
  context.restore()
}

function useCanvas(map: WorldMap, routes: ReturnType<typeof drawRoutes>) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null)
  const [width, setWidth] = useState(0)
  useEffect(() => {
    const canvas = canvasRef.current
    if (!canvas) return undefined
    const resize = () => {
      const next = Math.round(canvas.getBoundingClientRect().width)
      setWidth(next > 0 ? Math.min(1100, next) : 1000)
    }
    resize()
    if (typeof ResizeObserver === 'undefined') {
      window.addEventListener('resize', resize)
      return () => window.removeEventListener('resize', resize)
    }
    const observer = new ResizeObserver(resize)
    observer.observe(canvas)
    return () => observer.disconnect()
  }, [])
  useEffect(() => {
    const canvas = canvasRef.current
    if (!canvas || !width) return undefined
    const started = performance.now()
    const context = canvas.getContext('2d')
    if (!context) return undefined
    const height = Math.round(width * 0.75)
    canvas.width = width
    canvas.height = height
    const generated = generateTerrain(width, height, terrainName(map.terrain), map.seed, map.nodes, routes)
    const ground = imageCanvas(generated.width, generated.height, generated.pixels)
    const firstFog = imageCanvas(generated.width, generated.height, generated.fog[0])
    const secondFog = imageCanvas(generated.width, generated.height, generated.fog[1])
    const base = document.createElement('canvas')
    base.width = width
    base.height = height
    const baseContext = base.getContext('2d')
    if (!ground || !firstFog || !secondFog || !baseContext) return undefined
    baseContext.drawImage(ground, 0, 0, width, height)
    baseContext.setTransform(width / 1000, 0, 0, height / 750, 0, 0)
    inkContours(baseContext, generated)
    generated.scatter.forEach((mark) => inkMark(baseContext, mark))
    const motionQuery = window.matchMedia?.('(prefers-reduced-motion: reduce)')
    let animation = 0
    let lastDraw = -Infinity
    const draw = (time: number) => {
      if (time - lastDraw >= 1000 / 12) {
        lastDraw = time
        context.globalAlpha = 1
        context.drawImage(base, 0, 0)
        const blend = motionQuery?.matches ? 0.5 : (1 + Math.cos(time / 40000 * Math.PI * 2)) / 2
        context.globalAlpha = blend
        context.drawImage(firstFog, 0, 0, width, height)
        context.globalAlpha = 1 - blend
        context.drawImage(secondFog, 0, 0, width, height)
        context.globalAlpha = 1
      }
      if (!motionQuery?.matches) animation = window.requestAnimationFrame(draw)
    }
    const onMotionChange = () => {
      window.cancelAnimationFrame(animation)
      lastDraw = -Infinity
      draw(performance.now())
    }
    const unsubscribeMotion = motionQuery ? subscribeToMediaQueryChange(motionQuery, onMotionChange) : () => undefined
    draw(0)
    canvas.dataset.renderMilliseconds = (performance.now() - started).toFixed(1)
    return () => { unsubscribeMotion(); window.cancelAnimationFrame(animation) }
  }, [width, map.terrain, map.seed, map.nodes, routes])
  return { canvasRef, width }
}

function nearestNode(current: WorldMapNode, nodes: WorldMapNode[], key: string): WorldMapNode | undefined {
  const direction = key === 'ArrowLeft' ? [-1, 0] : key === 'ArrowRight' ? [1, 0] : key === 'ArrowUp' ? [0, -1] : [0, 1]
  return nodes.filter((node) => node.id !== current.id && node.state !== 'rumoured').map((node) => {
    const dx = node.x - current.x
    const dy = (node.y - current.y) * 0.75
    const forward = dx * direction[0] + dy * direction[1]
    return { node, forward, score: Math.hypot(dx, dy) }
  }).filter((entry) => entry.forward > 0).sort((a, b) => a.score - b.score || a.node.id.localeCompare(b.node.id))[0]?.node
}

/** The player-safe world map: terrain on canvas, accessible places and roads on SVG. */
export function StoryMap({ map: sourceMap, travelBlockedReason, onTravel }: StoryMapProps) {
  const map = useMemo(() => ({ ...sourceMap, nodes: centredNodes(sourceMap.nodes) }), [sourceMap])
  const routes = useMemo(() => drawRoutes(map.nodes, map.routes, map.seed), [map.nodes, map.routes, map.seed])
  const { canvasRef, width } = useCanvas(map, routes)
  const [selected, setSelected] = useState<string | null>(null)
  const nodes = useRef(new Map<string, SVGGElement>())
  const current = map.nodes.find((node) => node.state === 'current')
  const sizes = inkSizes(width)
  const scale = sizes.unitsPerPx
  const glyph = glyphScale(sizes)
  const compass = compassPosition(map.nodes, sizes)
  const labels = placeLabels(map.nodes, sizes, reservedBoxes(sizes, compass))
  const isTravelable = (node: WorldMapNode) => Boolean(current && node.id !== current.id && map.routes.some((route) =>
    route.travelable && ((route.from === current.id && route.to === node.id) || (route.to === current.id && route.from === node.id)),
  ))
  const activate = (node: WorldMapNode) => {
    if (isTravelable(node) && !travelBlockedReason) onTravel({ id: node.id, name: node.name })
  }
  const keyDown = (event: KeyboardEvent<SVGGElement>, node: WorldMapNode) => {
    if (event.key === 'Enter' || event.key === ' ') {
      event.preventDefault()
      activate(node)
    } else if (['ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown'].includes(event.key)) {
      event.preventDefault()
      const next = nearestNode(node, map.nodes, event.key)
      if (next) nodes.current.get(next.id)?.focus()
    }
  }
  const selectedNode = map.nodes.find((node) => node.id === selected && node.state !== 'rumoured')
  const selectedTravelable = selectedNode ? isTravelable(selectedNode) : false
  const tooltipWidth = selectedNode ? Math.max(120, Math.min(230, selectedNode.name.length * 7 + 30)) : 120
  const tooltipOffset = sizes.glyphPx * 0.6 + 6
  return (
    <div className="story-map" aria-label={map.title || 'World map'}>
      <canvas ref={canvasRef} className="story-map-ground" aria-hidden="true" />
      <svg
        className="story-map-ink" viewBox={`0 0 ${MAP_WIDTH} ${MAP_HEIGHT}`} role="group" aria-label={map.title || 'World map'}
        style={{ '--story-map-glyph-scale': glyph / scale } as CSSProperties}
      >
        {routes.map((route) => (
          <g key={`${route.from}:${route.to}`} className={`story-map-route story-map-route-${route.style}`} data-current={route.current || undefined}>
            <path className="story-map-route-under" d={route.path} />
            <path className="story-map-route-line" d={route.path} />
          </g>
        ))}
        {map.nodes.map((node) => {
          const { x, y } = nodePoint(node)
          const rumoured = node.state === 'rumoured'
          const label = labels.get(node.id)
          return (
            <g key={node.id} transform={`translate(${x} ${y})`} className={`story-map-place story-map-place-${node.state}`}>
              <g
                ref={(element) => { if (element) nodes.current.set(node.id, element); else nodes.current.delete(node.id) }}
                role={rumoured ? undefined : 'button'} tabIndex={rumoured ? undefined : 0}
                aria-label={rumoured ? undefined : `${node.name}, ${node.state}`}
                aria-hidden={rumoured || undefined}
                aria-disabled={!rumoured && (!isTravelable(node) || Boolean(travelBlockedReason)) ? true : undefined}
                onFocus={() => setSelected(node.id)} onBlur={() => setSelected(null)}
                onMouseEnter={() => { if (!rumoured) setSelected(node.id) }} onMouseLeave={() => setSelected(null)}
                onClick={() => { if (!rumoured) activate(node) }} onKeyDown={(event) => keyDown(event, node)}
              >
                <circle className="story-map-hit" r={22 * glyph} />
                {node.state === 'current' ? <circle className="story-map-current-ring" r={20 * glyph} /> : null}
                <g transform={`scale(${glyph})`}><MapGlyph icon={node.icon} rumoured={rumoured} /></g>
                {label ? (
                  <text className="story-map-place-label" x={label.x} y={label.y} textAnchor={label.anchor} style={{ fontSize: label.fontSize }}>
                    {label.lines.map((line, index) => <tspan key={line} x={label.x} dy={index ? label.lineHeight : 0}>{line}</tspan>)}
                  </text>
                ) : null}
                {label?.hereY != null ? (
                  <text className="story-map-here" x={label.x} y={label.hereY} textAnchor={label.anchor} style={{ fontSize: label.hereFontSize }}>{HERE_CAPTION}</text>
                ) : null}
              </g>
            </g>
          )
        })}
        <g transform={`translate(${compass.x} ${compass.y}) scale(${compassScale(sizes)})`}><CompassRose /></g>
        {selectedNode ? (
          <g className="story-map-tooltip" transform={`translate(${Math.max(10, Math.min(MAP_WIDTH - tooltipWidth * scale - 10, nodePoint(selectedNode).x + tooltipOffset * scale))} ${Math.max(10, Math.min(MAP_HEIGHT - (selectedTravelable ? 52 : 34) * scale - 10, nodePoint(selectedNode).y - (tooltipOffset + 28) * scale))}) scale(${scale})`} pointerEvents="none">
            <rect x="0" y="0" width={tooltipWidth} height={selectedTravelable ? 52 : 34} rx="5" />
            <text x="10" y="19">{selectedNode.name} · {selectedNode.state}</text>
            {selectedTravelable ? <text className="story-map-tooltip-action" x="10" y="39">{travelBlockedReason || `Travel to ${selectedNode.name}`}</text> : null}
          </g>
        ) : null}
      </svg>
      {map.title ? <div className="story-map-cartouche" aria-hidden="true" style={{ fontSize: sizes.labelPx * 1.3 }}>{map.title}</div> : null}
    </div>
  )
}
