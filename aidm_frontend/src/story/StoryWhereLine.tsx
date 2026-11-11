import { useState, useSyncExternalStore } from 'react'
import type { WorldZoneSummary } from '../gameSelectors'
import { subscribeToMediaQueryChange } from '../mediaQuery'
import { zonePeople, zonesInReadingOrder } from './zones'

/** Below this width the line shows the viewer's own zone, and the rest on a tap. */
const NARROW_QUERY = '(max-width: 600px)'
/** Names a zone lists before the rest are counted ("+2"). */
const NAMES_SHOWN = 3

function subscribeToNarrow(listener: () => void) {
  if (typeof window.matchMedia !== 'function') return () => undefined
  return subscribeToMediaQueryChange(window.matchMedia(NARROW_QUERY), listener)
}

function narrowMatches() {
  return typeof window.matchMedia === 'function' && window.matchMedia(NARROW_QUERY).matches
}

type StoryWhereLineProps = {
  zones: WorldZoneSummary[]
  /** The viewer's hero (`player_<id>`), named "you". */
  viewerId: string
  /** A zone next door can be stepped into now (outside a fight, nothing being sent). */
  canMove: boolean
  onMove: (zone: WorldZoneSummary) => void
}

/**
 * Who stands where in a place with rooms, under the scene's name: `THE YARD — you, Corin Ashby · THE COMMON ROOM —
 * Mara Reedwake · THE STABLE`. The viewer's zone comes first in the accent; a zone next door is a link that steps
 * into it. At phone width only the viewer's zone shows, and a tap on it opens the rest.
 */
export function StoryWhereLine({ zones, viewerId, canMove, onMove }: StoryWhereLineProps) {
  const narrow = useSyncExternalStore(subscribeToNarrow, narrowMatches, () => false)
  const [open, setOpen] = useState(false)
  const ordered = zonesInReadingOrder(zones)
  const others = ordered.filter((zone) => !zone.current).length
  return (
    <p className="story-where-line" aria-label="Where everyone stands" data-open={narrow && open ? '' : undefined}>
      {ordered.map((zone, index) => {
        const people = zonePeople(zone, viewerId)
        const hidden = people.length - NAMES_SHOWN
        const shown = hidden > 1 ? people.slice(0, NAMES_SHOWN) : people
        return (
          <span
            key={zone.id}
            className="story-where-zone"
            data-current={zone.current ? '' : undefined}
            data-adjacent={zone.adjacent ? '' : undefined}
          >
            {index ? <span className="story-where-sep" aria-hidden="true"> · </span> : null}
            {zone.current && narrow && others ? (
              <button
                type="button"
                className="story-where-name"
                aria-expanded={open}
                title={open ? 'Show only where you are' : 'Show every room'}
                onClick={() => setOpen((value) => !value)}
              >
                {zone.name}
              </button>
            ) : zone.adjacent && canMove ? (
              <button type="button" className="story-where-name story-where-move" title={zone.moveLabel} onClick={() => onMove(zone)}>
                {zone.name}
              </button>
            ) : (
              <span className="story-where-name">{zone.name}</span>
            )}
            {shown.length ? (
              <span className="story-where-people">
                {' — '}
                {shown.join(', ')}
                {hidden > 1 ? ` +${hidden}` : ''}
              </span>
            ) : null}
          </span>
        )
      })}
    </p>
  )
}
