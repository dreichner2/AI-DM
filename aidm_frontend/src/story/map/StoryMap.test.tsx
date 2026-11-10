// @vitest-environment jsdom
import '@testing-library/jest-dom/vitest'
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { testBoard, testInspector } from '../dockTestFixtures'
import { MapSection } from '../drawer/MapSection'
import { mapFixture } from '../preview/fixtures'
import { StoryDrawer } from '../StoryDrawer'
import { StoryMap } from './StoryMap'

beforeEach(() => {
  const context = {
    createImageData: (width: number, height: number) => ({ data: new Uint8ClampedArray(width * height * 4) }),
    putImageData: vi.fn(), drawImage: vi.fn(), setTransform: vi.fn(), save: vi.fn(), restore: vi.fn(),
    translate: vi.fn(), rotate: vi.fn(), scale: vi.fn(), beginPath: vi.fn(), moveTo: vi.fn(),
    lineTo: vi.fn(), quadraticCurveTo: vi.fn(), closePath: vi.fn(), stroke: vi.fn(),
  }
  vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue(context as never)
  vi.stubGlobal('ResizeObserver', undefined)
  vi.stubGlobal('matchMedia', (query: string) => ({ matches: false, media: query, addEventListener: vi.fn(), removeEventListener: vi.fn() }))
  vi.spyOn(window, 'requestAnimationFrame').mockReturnValue(1)
  vi.spyOn(window, 'cancelAnimationFrame').mockImplementation(() => undefined)
})

afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals() })

describe('StoryMap', () => {
  it('exposes known nodes and hides the identity of rumoured places', () => {
    const map = mapFixture()
    map.nodes.find((node) => node.state === 'rumoured')!.name = 'A secret ruin'
    render(<StoryMap map={map} travelBlockedReason="" onTravel={vi.fn()} />)
    expect(screen.getByRole('button', { name: 'The Drowned Lantern, current' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'The Old North Road, visited' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Reedwater Ford, known' })).toBeInTheDocument()
    expect(screen.queryByText('A secret ruin')).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /secret ruin/i })).not.toBeInTheDocument()
  })

  it('only travels along available routes, and blocks travel during combat', () => {
    const map = mapFixture()
    const onTravel = vi.fn()
    const { rerender } = render(<StoryMap map={map} travelBlockedReason="" onTravel={onTravel} />)
    fireEvent.click(screen.getByRole('button', { name: 'Hollin Cross, known' }))
    fireEvent.click(screen.getByRole('button', { name: 'The Drowned Lantern, current' }))
    expect(onTravel).not.toHaveBeenCalled()
    fireEvent.click(screen.getByRole('button', { name: 'The Old North Road, visited' }))
    expect(onTravel).toHaveBeenCalledWith({ id: 'road', name: 'The Old North Road' })
    onTravel.mockClear()
    rerender(<StoryMap map={map} travelBlockedReason="Not while combat is underway." onTravel={onTravel} />)
    const destination = screen.getByRole('button', { name: 'Reedwater Ford, known' })
    fireEvent.focus(destination)
    expect(screen.getByText('Not while combat is underway.')).toBeInTheDocument()
    expect(destination).toHaveAttribute('aria-disabled', 'true')
    fireEvent.keyDown(destination, { key: 'Enter' })
    expect(onTravel).not.toHaveBeenCalled()
  })

  it('moves keyboard focus to the nearest known node in the requested direction', () => {
    render(<StoryMap map={mapFixture()} travelBlockedReason="" onTravel={vi.fn()} />)
    const current = screen.getByRole('button', { name: 'The Drowned Lantern, current' })
    current.focus()
    fireEvent.keyDown(current, { key: 'ArrowRight' })
    expect(screen.getByRole('button', { name: 'The Old North Road, visited' })).toHaveFocus()
  })

  it('keeps edge labels inside the map and moves the compass away from a place', () => {
    vi.spyOn(HTMLCanvasElement.prototype, 'getBoundingClientRect').mockReturnValue({ width: 360 } as DOMRect)
    const map = mapFixture()
    map.nodes = [
      { id: 'east', name: 'The Lantern Post Inn', icon: 'inn', x: 92, y: 92, state: 'current' },
      { id: 'west', name: 'The Old North Road', icon: 'road', x: 8, y: 92, state: 'known' },
      { id: 'north', name: 'Hollin Cross', icon: 'town', x: 50, y: 30, state: 'known' },
    ]
    map.routes = []
    const { container } = render(<StoryMap map={map} travelBlockedReason="" onTravel={vi.fn()} />)
    const east = screen.getByRole('button', { name: 'The Lantern Post Inn, current' })
    const west = screen.getByRole('button', { name: 'The Old North Road, known' })
    expect(Number(east.querySelector('.story-map-place-label')?.getAttribute('y'))).toBeLessThan(0)
    expect(east.querySelector('.story-map-place-label')).toHaveAttribute('text-anchor', 'end')
    expect(west.querySelector('.story-map-place-label')).toHaveAttribute('text-anchor', 'start')
    const compass = container.querySelector('.story-map-compass')?.parentElement?.getAttribute('transform') ?? ''
    expect(Number(compass.match(/translate\([^ ]+ ([^)]+)\)/)?.[1])).toBeLessThan(695 - 28 * 1000 / 360)
  })

  it.each([
    [380, 24, 0.72 * 16],
    [1100, 40, 16],
  ])('draws %ipx-wide maps with %ipx glyphs and %spx labels', (width, glyphPx, labelPx) => {
    vi.spyOn(HTMLCanvasElement.prototype, 'getBoundingClientRect').mockReturnValue({ width } as DOMRect)
    const { container } = render(<StoryMap map={mapFixture()} travelBlockedReason="" onTravel={vi.fn()} />)
    const unitsPerPx = 1000 / width
    const place = screen.getByRole('button', { name: 'Hollin Cross, known' })
    const glyphTransform = place.querySelector('.story-map-glyph')?.parentElement?.getAttribute('transform') ?? ''

    expect(Number(glyphTransform.match(/scale\(([^)]+)\)/)?.[1])).toBeCloseTo(glyphPx / 24 * unitsPerPx)
    expect(parseFloat((place.querySelector('.story-map-place-label') as SVGTextElement).style.fontSize)).toBeCloseTo(labelPx * unitsPerPx)
    expect(container.querySelector('.story-map-ink')).toHaveStyle({ '--story-map-glyph-scale': String(glyphPx / 24) })
  })

  it('marks a known place without an icon with the generic place mark, keeping ? for rumours', () => {
    const map = mapFixture()
    map.nodes = map.nodes.map((node) => (node.id === 'road' ? { ...node, icon: 'unknown' } : node))
    const { container } = render(<StoryMap map={map} travelBlockedReason="" onTravel={vi.fn()} />)

    const road = screen.getByRole('button', { name: 'The Old North Road, visited' })
    expect(road.querySelector('.story-map-glyph')).toHaveAttribute('data-glyph', 'place')
    expect(screen.getByRole('button', { name: 'The Drowned Lantern, current' }).querySelector('.story-map-glyph')).toHaveAttribute('data-glyph', 'inn')
    const glyphs = [...container.querySelectorAll('.story-map-place-rumoured .story-map-glyph')]
    expect(glyphs).toHaveLength(2)
    glyphs.forEach((glyph) => expect(glyph).toHaveAttribute('data-glyph', 'rumoured'))
    expect(container.querySelectorAll('[data-glyph="rumoured"]')).toHaveLength(2)
  })

  it('draws an adventure with one or two places in the middle of the map, not its corners', () => {
    const map = mapFixture()
    map.nodes = [
      { id: 'inn', name: 'The Lantern Post Inn', icon: 'unknown', x: 92, y: 92, state: 'current' },
      { id: 'road', name: 'The Old North Road', icon: 'unknown', x: 8, y: 8, state: 'known' },
    ]
    map.routes = [{ from: 'inn', to: 'road', style: 'road', travelable: true }]
    const { container } = render(<StoryMap map={map} travelBlockedReason="" onTravel={vi.fn()} />)

    const positions = [...container.querySelectorAll('.story-map-place')].map((place) =>
      (place.getAttribute('transform')?.match(/translate\(([^ ]+) ([^)]+)\)/) ?? []).slice(1).map(Number))
    expect(positions).toHaveLength(2)
    for (const [x, y] of positions) {
      expect(x).toBeGreaterThanOrEqual(200)
      expect(x).toBeLessThanOrEqual(800)
      expect(y).toBeGreaterThanOrEqual(150)
      expect(y).toBeLessThanOrEqual(600)
    }
    expect(container.querySelector('.story-map-route-line')?.getAttribute('d')).toMatch(/^M 800\.0 600\.0/)
  })

  it('opens the overlay, closes it by Escape, close button or backdrop, and restores focus', async () => {
    render(<div className="story-shell"><MapSection inspector={testInspector()} worldMap={mapFixture()} travelBlockedReason="" onTravel={vi.fn()} /></div>)
    const expand = screen.getByRole('button', { name: 'Expand map' })
    expand.focus()
    fireEvent.click(expand)
    let dialog = screen.getByRole('dialog', { name: 'Map' })
    await waitFor(() => expect(within(dialog).getByRole('button', { name: 'Close map' })).toHaveFocus())
    fireEvent.keyDown(dialog, { key: 'Escape' })
    expect(screen.queryByRole('dialog', { name: 'Map' })).not.toBeInTheDocument()
    expect(expand).toHaveFocus()
    fireEvent.click(expand)
    dialog = screen.getByRole('dialog', { name: 'Map' })
    fireEvent.click(within(dialog).getByRole('button', { name: 'Close map' }))
    expect(screen.queryByRole('dialog', { name: 'Map' })).not.toBeInTheDocument()
    fireEvent.click(expand)
    dialog = screen.getByRole('dialog', { name: 'Map' })
    fireEvent.mouseDown(dialog.parentElement!)
    expect(screen.queryByRole('dialog', { name: 'Map' })).not.toBeInTheDocument()
  })

  it('shows the map facts line only for facts that exist', () => {
    const unknown = testInspector({ mapMeta: { explored: '—', threat: 'Unknown', threatTone: 'low', weather: 'Not recorded' } })
    const { container, rerender } = render(<MapSection inspector={unknown} worldMap={mapFixture()} travelBlockedReason="" onTravel={vi.fn()} />)
    expect(container.querySelector('.story-sheet-map > .story-sheet-meta')).toBeNull()
    expect(screen.queryByText(/Threat unknown|Not recorded|Explored —/)).not.toBeInTheDocument()

    const partial = testInspector({ mapMeta: { explored: '—', threat: 'Rising', threatTone: 'medium', weather: 'Not recorded' } })
    rerender(<MapSection inspector={partial} worldMap={mapFixture()} travelBlockedReason="" onTravel={vi.fn()} />)
    expect(screen.getByText('Threat rising')).toBeInTheDocument()

    rerender(<MapSection inspector={testInspector()} worldMap={mapFixture()} travelBlockedReason="" onTravel={vi.fn()} />)
    expect(screen.getByText('Explored 38% · Threat rising · Heavy rain')).toBeInTheDocument()
  })

  it('shows a quiet empty state while retaining ways onward', () => {
    render(<MapSection inspector={testInspector()} worldMap={null} travelBlockedReason="" onTravel={vi.fn()} />)
    expect(screen.getByText('No map of this place yet.')).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Ways onward' })).toBeInTheDocument()
  })

  it('uses the drawer typed travel action when a map destination is activated', async () => {
    const board = testBoard()
    render(<div className="story-shell"><StoryDrawer board={board} inspector={testInspector()} open tab="map" onTabChange={vi.fn()} onClose={vi.fn()} /></div>)
    fireEvent.click(await screen.findByRole('button', { name: 'The Old North Road, visited' }))
    expect(board.actionComposerProps.submitAction).toHaveBeenCalledWith('The party travels to The Old North Road.', expect.objectContaining({
      kind: 'travel', source: 'scene_panel', location: { id: 'road', name: 'The Old North Road' },
    }))
  })
})
