import { useState } from 'react'
import { createRoot } from 'react-dom/client'
import '../../index.css'
import '../../App.css'
import { StoryMap } from '../map/StoryMap'
import { StoryMapOverlay } from '../map/StoryMapOverlay'
import { drawRoutes } from '../map/mapRoutes'
import { generateTerrain, terrainName } from '../map/mapTerrain'
import { mapFixture } from './fixtures'

const params = new URLSearchParams(window.location.search)
const map = mapFixture(terrainName(params.get('terrain') ?? 'moor'))
const started = performance.now()
generateTerrain(1100, 825, terrainName(map.terrain), map.seed, map.nodes, drawRoutes(map.nodes, map.routes, map.seed))
const generationMs = performance.now() - started

export function Preview() {
  const [open, setOpen] = useState(params.get('overlay') === '1')
  return (
    <main className="story-shell" data-mood="hearth" style={{ minHeight: '100vh', padding: 28 }}>
      <h1 className="story-label">Map preview · {map.terrain}</h1>
      <p className="story-sheet-meta">Terrain generation: {generationMs.toFixed(1)} ms at 1100 × 825</p>
      <div style={{ width: 'min(360px, 100%)' }}>
        <StoryMap map={map} travelBlockedReason="" onTravel={() => undefined} />
        <button type="button" className="story-link" onClick={() => setOpen(true)}>Expand map</button>
      </div>
      {open ? <StoryMapOverlay map={map} travelBlockedReason="" onTravel={() => undefined} onClose={() => setOpen(false)} /> : null}
    </main>
  )
}

createRoot(document.getElementById('root')!).render(<Preview />)
