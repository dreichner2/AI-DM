/** Small, hand-inked landmarks. Coordinates are centred on each place. */
const LANDMARKS: Record<string, string> = {
  inn: 'M-12 0 L0-10 12 0 M-9-2 V10 H9 V-2 M-4 10 V3 H1 V10 M5-3 V0 M6-11 V-7 M-12 10 H12 M9-5 h4 v6 h-4 z M11-7 v2 M10-2 h2',
  town: 'M-13 10 H13 M-12 1 L-7-5 -2 1 V9 H-12 Z M-3-1 L2-10 8-1 V10 H-3 Z M7 2 L11-3 15 2 V10 M0 10 V2 H4 V10 M-9 2 h2 M5-5 h1',
  road: 'M-3 11 V-8 M3 11 V-8 M-8-8 Q0-12 8-8 V-4 Q0-6-8-4 Z M-6 11 H6 M-5-6 h10 M-8 7 q-4 1-5 4 M8 7 q4 1 5 4',
  bridge: 'M-14 3 Q-7-1 0 0 Q7-1 14 3 M-14 6 Q-7 2 0 3 Q7 2 14 6 M-10 6 Q-7-1-3 5 M3 5 Q7-1 10 6 M-13 0 V5 M-7-2 V3 M0-3 V3 M7-2 V3 M13 0 V5 M-14 10 q4-2 8 0 t8 0 t8 0',
  ruin: 'M-12 11 V-5 L-8-8 V-1 L-4-3 V-9 L0-11 V11 M2 11 V-6 L6-4 V-9 L11-7 V11 M-14 11 H14 M-9 4 h3 M4 3 h4 M2 8 h3',
  tower: 'M-8 11 V-7 L-11-8 V-12 H-6 V-9 H-2 V-12 H2 V-9 H6 V-12 H11 V-8 L8-7 V11 M-12 11 H12 M-2 11 V4 Q0 1 2 4 V11 M-2-4 h4 v3 h-4 Z',
  forest: 'M-14 10 H14 M-10 10 V4 M-14 4 L-10-5 -6 4 Z M0 10 V1 M-6 2 L0-11 6 2 Z M9 10 V5 M5 5 L9-4 13 5 Z',
  cave: 'M-14 10 L-7-5 -3-2 2-11 14 10 M-8 10 Q-7 1 0 1 Q7 1 8 10 M-11 10 H11 M-6-2 l2-2 M5-5 l2 2',
  camp: 'M-13 10 H13 M-11 9 L-4 0 3 9 Z M-8 6 H0 M5 9 Q1 4 6 1 Q5-3 9-6 Q9-2 12 0 Q15 4 11 9 M7 9 Q5 5 9 3 Q11 5 10 9 M-7 11 H0 M4 11 H13',
  shrine: 'M-12 11 H12 M-8 10 V-1 H8 V10 M-11-1 L0-10 11-1 M-3 10 V3 Q0 0 3 3 V10 M0-10 V-14 M-2-12 H2',
  cairn: 'M-13 10 Q-11 5-5 6 Q0 3 4 6 Q10 4 13 10 Z M-9 6 Q-8 1-3 2 Q2 0 6 5 M-5 2 Q-4-4 1-4 Q5-3 5 2 M-2-4 Q-3-10 2-11 Q6-10 5-4',
  valley: 'M-14-7 L-5 5 0-3 5 5 14-7 M-14 9 Q-7 2 0 7 Q7 2 14 9 M-11 11 H11 M-3 8 Q0 5 3 8',
  gate: 'M-12 11 V-10 H-7 V11 M7 11 V-10 H12 V11 M-13-10 H-6 M6-10 H13 M-7-5 Q0-12 7-5 M-5 11 V-3 M0 11 V-6 M5 11 V-3 M-7 3 H7 M-14 11 H14',
  marsh: 'M-14 10 q4-3 8 0 t8 0 t8 0 M-10 6 V-5 M-10 1 l-4-4 M-10-1 l4-5 M0 7 V-10 M0-2 l-4-4 M0-4 l4-4 M10 6 V-4 M10 0 l-3-4 M10-1 l3-4',
  peak: 'M-14 10 L-3-11 2-3 5-7 14 10 M-8 0 L-4-2 -2 0 0-4 M4 1 l2-2 3 2 M-14 10 H14 M-5 6 l2-3',
}
/** A known place without its own drawing: a small ring around a dot. */
const PLACE = 'M0-8 A8 8 0 1 1 0 8 A8 8 0 1 1 0-8 M-1.6 0 A1.6 1.6 0 1 1 1.6 0 A1.6 1.6 0 1 1-1.6 0'
/** Only a rumoured place is a question mark. */
const RUMOURED = 'M0-12 A12 12 0 1 1 0 12 A12 12 0 1 1 0-12 M-4-4 Q-4-8 0-8 Q5-8 5-4 Q5-1 1 1 V3 M1 7 v1'

export function MapGlyph({ icon, rumoured = false }: { icon: string; rumoured?: boolean }) {
  const kind = rumoured ? 'rumoured' : Object.hasOwn(LANDMARKS, icon) ? icon : 'place'
  const path = kind === 'rumoured' ? RUMOURED : kind === 'place' ? PLACE : LANDMARKS[icon]
  return (
    <g className="story-map-glyph" data-glyph={kind} fill="none" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path className="story-map-glyph-halo" d={path} />
      <path d={path} />
    </g>
  )
}

export function CompassRose() {
  return (
    <g className="story-map-compass" fill="none" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <circle r="18" />
      <path d="M0-28 L4-4 0 0 -4-4 Z M0 28 L4 4 0 0 -4 4 Z M-28 0 L-4-4 0 0 -4 4 Z M28 0 L4-4 0 0 4 4 Z" />
      <path d="M-13-13 L13 13 M13-13 L-13 13" />
      <text x="0" y="-34" textAnchor="middle">N</text>
    </g>
  )
}
