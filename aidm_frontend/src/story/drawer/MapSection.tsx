import { useId } from 'react'
import { Footprints } from 'lucide-react'
import type { InspectorPanelProps } from '../../InspectorPanel'

type MapSectionProps = {
  inspector: InspectorPanelProps
  /** Why travel is unavailable right now ('' when it is available). */
  travelBlockedReason: string
  onTravel: (exit: { id: string; name: string }) => void
}

export function MapSection({ inspector, travelBlockedReason, onTravel }: MapSectionProps) {
  const reasonId = useId()
  const { availableExits, knownLocations } = inspector.worldStatePanel
  const meta = [
    inspector.mapMeta.explored ? `Explored ${inspector.mapMeta.explored}` : '',
    inspector.mapMeta.threat ? `Threat ${inspector.mapMeta.threat.toLowerCase()}` : '',
    inspector.mapMeta.weather,
  ].filter(Boolean)
  const exits = availableExits.filter((exit) => exit.id)
  const exitIds = new Set(exits.map((exit) => exit.id))
  const otherPlaces = knownLocations.filter((location) => !exitIds.has(location.id))

  return (
    <div className="story-sheet story-sheet-map">
      <h3 className="story-label">{inspector.mapPanelTitle}</h3>
      {inspector.mapDescription ? <p className="story-map-description">{inspector.mapDescription}</p> : null}
      {meta.length ? <p className="story-sheet-meta">{meta.join(' · ')}</p> : null}

      <div className="story-map-canvas" hidden={!inspector.maps.length} />

      {exits.length ? (
        <section aria-labelledby="story-map-onward">
          <h3 id="story-map-onward" className="story-label">Ways onward</h3>
          <div className="story-chip-row" role="group" aria-labelledby="story-map-onward">
            {exits.map((exit) => (
              <button
                key={exit.id}
                type="button"
                className="story-chip"
                aria-disabled={travelBlockedReason ? true : undefined}
                aria-describedby={travelBlockedReason ? reasonId : undefined}
                title={travelBlockedReason || undefined}
                data-blocked={travelBlockedReason ? true : undefined}
                onClick={() => {
                  if (!travelBlockedReason) onTravel(exit)
                }}
              >
                <Footprints aria-hidden="true" />
                Go to {exit.name}
              </button>
            ))}
          </div>
          {travelBlockedReason ? (
            <p id={reasonId} className="story-sheet-meta">{travelBlockedReason}</p>
          ) : null}
        </section>
      ) : null}

      {otherPlaces.length ? (
        <section aria-labelledby="story-map-known">
          <h3 id="story-map-known" className="story-label">Known places</h3>
          <ul className="story-journal-list">
            {otherPlaces.map((location) => (
              <li key={location.id || location.name}>
                <span className="story-journal-name">{location.name}</span>
                {location.status && location.status !== 'unknown' ? (
                  <span className="story-journal-detail"> — {location.status}</span>
                ) : null}
              </li>
            ))}
          </ul>
        </section>
      ) : null}
    </div>
  )
}
