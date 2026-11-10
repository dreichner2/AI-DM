import { useState } from 'react'
import { createRoot } from 'react-dom/client'
import { Expand } from 'lucide-react'
import '../../index.css'
import '../../App.css'
import type { WorldMap } from '../../gameSelectors'
import { StoryMap } from '../map/StoryMap'
import { StoryMapOverlay } from '../map/StoryMapOverlay'
import { drawRoutes } from '../map/mapRoutes'
import { generateTerrain, terrainName } from '../map/mapTerrain'
import { mapFixture } from './fixtures'

const params = new URLSearchParams(window.location.search)

/** `?places=2` (or 1): an opening adventure, laid out by the server at its corners. */
function previewMap(): WorldMap {
  const map = mapFixture(terrainName(params.get('terrain') ?? 'moor'))
  const places = Number(params.get('places'))
  if (places !== 1 && places !== 2) return map
  const nodes = [
    { id: 'inn', name: 'The Lantern Post Inn', icon: 'unknown', x: 92, y: 92, state: 'current' as const },
    { id: 'road', name: 'The Old North Road', icon: 'unknown', x: 8, y: 92, state: 'known' as const },
  ].slice(0, places)
  return {
    ...map,
    title: 'The Road of Unremembered Kings',
    nodes,
    routes: places === 2 ? [{ from: 'inn', to: 'road', style: 'road', travelable: true }] : [],
  }
}

const map = previewMap()
const started = performance.now()
generateTerrain(1100, 825, terrainName(map.terrain), map.seed, map.nodes, drawRoutes(map.nodes, map.routes, map.seed))
const generationMs = performance.now() - started

export function Preview() {
  const [open, setOpen] = useState(params.get('overlay') === '1')
  return (
    <main className="story-shell" data-mood="hearth" style={{ minHeight: '100vh', padding: 28 }}>
      <h1 className="story-label">Map preview · {map.terrain}</h1>
      <p className="story-sheet-meta">Terrain generation: {generationMs.toFixed(1)} ms at 1100 × 825</p>
      <div className="story-map-inline" style={{ width: 'min(383px, 100%)' }}>
        <StoryMap map={map} travelBlockedReason="" onTravel={() => undefined} />
        <button type="button" className="story-icon-button story-map-expand" aria-label="Expand map" onClick={() => setOpen(true)}>
          <Expand aria-hidden="true" />
        </button>
      </div>
      {open ? <StoryMapOverlay map={map} travelBlockedReason="" onTravel={() => undefined} onClose={() => setOpen(false)} /> : null}
    </main>
  )
}

createRoot(document.getElementById('root')!).render(<Preview />)
