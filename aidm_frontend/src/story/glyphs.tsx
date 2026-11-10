import { useId } from 'react'

type GlyphProps = {
  size?: number
  className?: string
}

/** A d20 seen face-on: the hexagonal silhouette with its front face and edges. */
export function D20Glyph({ size = 18, className }: GlyphProps) {
  return (
    <svg
      className={className}
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.35}
      strokeLinejoin="round"
      strokeLinecap="round"
      aria-hidden="true"
      focusable="false"
    >
      <path d="M12 2.2 20.6 7v10L12 21.8 3.4 17V7Z" />
      <path d="M12 7.4 16.8 15.4H7.2Z" />
      <path d="M12 2.2v5.2M3.4 7 12 7.4l8.6-.4M20.6 7l-3.8 8.4M20.6 17l-3.8-1.6L12 21.8l-4.8-6.4L3.4 17M3.4 7l3.8 8.4" />
    </svg>
  )
}

/** AI-DM's ember: a small banked flame with a hot core. */
export function EmberMark({ size = 22, className }: GlyphProps) {
  const id = useId()
  const flame = `${id}-flame`
  const core = `${id}-core`
  return (
    <svg className={className} width={size} height={size} viewBox="0 0 24 24" aria-hidden="true" focusable="false">
      <defs>
        <linearGradient id={flame} x1="12" y1="2" x2="12" y2="22" gradientUnits="userSpaceOnUse">
          <stop offset="0" stopColor="var(--story-ember-soft)" />
          <stop offset="0.62" stopColor="var(--story-ember)" />
          <stop offset="1" stopColor="var(--story-ember)" stopOpacity="0.75" />
        </linearGradient>
        <radialGradient id={core} cx="12" cy="16.2" r="4.2" gradientUnits="userSpaceOnUse">
          <stop offset="0" stopColor="var(--story-ink)" stopOpacity="0.95" />
          <stop offset="0.55" stopColor="var(--story-ember-soft)" stopOpacity="0.7" />
          <stop offset="1" stopColor="var(--story-ember-soft)" stopOpacity="0" />
        </radialGradient>
      </defs>
      <path
        d="M12.6 2.4c.5 2.8 2.3 4.4 3.9 6.1 1.7 1.8 3.1 3.8 3.1 6.6A7.6 7.6 0 0 1 12 22a7.6 7.6 0 0 1-7.6-7c0-2.6 1.3-4.4 2.6-5.8.2 1.7.9 2.9 2 3.5-.4-3.7.9-7.6 3.6-10.3Z"
        fill={`url(#${flame})`}
      />
      <ellipse cx="12" cy="16.2" rx="4.2" ry="4.6" fill={`url(#${core})`} />
    </svg>
  )
}

/** Chapter ornament: a lozenge between two fading flourishes. */
export function Ornament({ size = 72, className }: GlyphProps) {
  return (
    <svg
      className={className}
      width={size}
      height={(size * 14) / 72}
      viewBox="0 0 72 14"
      fill="none"
      stroke="currentColor"
      strokeLinecap="round"
      aria-hidden="true"
      focusable="false"
    >
      <path d="M4 7h18M50 7h18" strokeWidth={0.8} strokeOpacity={0.55} />
      <path d="M22 7c3.2 0 4.6-3.6 8-3.6M50 7c-3.2 0-4.6-3.6-8-3.6M22 7c3.2 0 4.6 3.6 8 3.6M50 7c-3.2 0-4.6 3.6-8 3.6" strokeWidth={0.9} />
      <path d="M36 2.4 40.6 7 36 11.6 31.4 7Z" fill="currentColor" fillOpacity={0.25} strokeWidth={1} />
      <circle cx="36" cy="7" r="1" fill="currentColor" stroke="none" />
    </svg>
  )
}
