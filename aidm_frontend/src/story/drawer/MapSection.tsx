import { useId, useState } from 'react'
import { Expand, Footprints } from 'lucide-react'
import type { WorldMap, WorldZoneSummary } from '../../gameSelectors'
import type { InspectorPanelProps } from '../../InspectorPanel'
import { StoryMap } from '../map/StoryMap'
import { StoryMapOverlay } from '../map/StoryMapOverlay'
import { zonePeople, zonesInReadingOrder } from '../zones'

type MapSectionProps = {
  inspector: InspectorPanelProps
  worldMap: WorldMap | null
  /** Why travel is unavailable right now ('' when it is available). */
  travelBlockedReason: string
  onTravel: (exit: { id: string; name: string }) => void
  /** The viewer's hero (`player_<id>`), named "you" among who stands where. */
  viewerId?: string
  /** A zone next door can be stepped into now. */
  canMove?: boolean
  onMove?: (zone: WorldZoneSummary) => void
}

/** Placeholders the map record uses when it has no value; they are not facts worth a line. */
const UNKNOWN_META = new Set(['', '—', '-', 'unknown', 'not recorded'])

function knownMeta(value: string) {
  return UNKNOWN_META.has(value.trim().toLowerCase()) ? '' : value.trim()
}

export function MapSection({
  inspector,
  worldMap,
  travelBlockedReason,
  onTravel,
  viewerId = '',
  canMove = false,
  onMove,
}: MapSectionProps) {
  const reasonId = useId()
  const [expanded, setExpanded] = useState(false)
  const { availableExits, knownLocations } = inspector.worldStatePanel
  const zones = zonesInReadingOrder(inspector.worldStatePanel.zones ?? [])
  const explored = knownMeta(inspector.mapMeta.explored)
  const threat = knownMeta(inspector.mapMeta.threat)
  const meta = [
    explored ? `Explored ${explored}` : '',
    threat ? `Threat ${threat.toLowerCase()}` : '',
    knownMeta(inspector.mapMeta.weather),
  ].filter(Boolean)
  const exits = availableExits.filter((exit) => exit.id)
  const exitIds = new Set(exits.map((exit) => exit.id))
  const otherPlaces = knownLocations.filter((location) => !exitIds.has(location.id))

  return (
    <div className="story-sheet story-sheet-map">
      <h3 className="story-label">{inspector.mapPanelTitle}</h3>
      {inspector.mapDescription ? <p className="story-map-description">{inspector.mapDescription}</p> : null}
      {meta.length ? <p className="story-sheet-meta">{meta.join(' · ')}</p> : null}

      {worldMap?.nodes.length ? (
        <div className="story-map-inline">
          <StoryMap map={worldMap} travelBlockedReason={travelBlockedReason} onTravel={onTravel} />
          <button type="button" className="story-icon-button story-map-expand" aria-label="Expand map" onClick={() => setExpanded(true)}>
            <Expand aria-hidden="true" />
          </button>
          {expanded ? <StoryMapOverlay map={worldMap} travelBlockedReason={travelBlockedReason} onTravel={onTravel} onClose={() => setExpanded(false)} /> : null}
        </div>
      ) : <p className="story-sheet-meta">No map of this place yet.</p>}

      {zones.length ? (
        <section aria-labelledby="story-map-here">
          <h3 id="story-map-here" className="story-label">This place</h3>
          <ul className="story-journal-list story-map-zones">
            {zones.map((zone) => (
              <li key={zone.id} data-current={zone.current ? '' : undefined}>
                <span className="story-journal-name">{zone.name}</span>
                <span className="story-journal-detail"> — {zonePeople(zone, viewerId).join(', ') || 'nobody'}</span>
                {zone.adjacent && zone.moveLabel && onMove ? (
                  <button
                    type="button"
                    className="story-link story-map-zone-move"
                    disabled={!canMove}
                    onClick={() => onMove(zone)}
                  >
                    {zone.moveLabel}
                  </button>
                ) : null}
              </li>
            ))}
          </ul>
        </section>
      ) : null}

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
