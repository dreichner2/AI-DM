import { X } from 'lucide-react'
import { useEffect, useRef, type KeyboardEvent, type ReactNode } from 'react'
import { keepTabInside } from './focusLayer'

type CatalogOverlayProps = {
  label: string
  note: string
  closeLabel: string
  /** The control that opened the overlay; focus returns to it on close. */
  opener: HTMLElement | null
  /**
   * Covers the whole window (over a sheet) instead of the stage it sits in
   * (hero creation). The caller makes whatever is behind inert.
   */
  fullWindow?: boolean
  onClose: () => void
  children: ReactNode
}

/**
 * The full-screen glass overlay that holds a catalog: a label, one line of
 * note, a close button and a scrolling body. Tab stays inside it, Escape closes
 * only it, and focus returns to the opener.
 */
export function CatalogOverlay({ label, note, closeLabel, opener, fullWindow = false, onClose, children }: CatalogOverlayProps) {
  const layerRef = useRef<HTMLDivElement | null>(null)
  const closeRef = useRef<HTMLButtonElement | null>(null)
  const openerRef = useRef(opener)

  useEffect(() => {
    // The catalog focuses its search field as it mounts; until it has loaded, the close button holds focus.
    if (!layerRef.current?.contains(document.activeElement)) closeRef.current?.focus()
    const returnTo = openerRef.current
    return () => returnTo?.focus()
  }, [])

  const onKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    if (event.key === 'Escape') {
      event.preventDefault()
      event.stopPropagation()
      onClose()
      return
    }
    if (event.key === 'Tab' && layerRef.current) keepTabInside(event, layerRef.current)
  }

  const overlay = (
    <div
      ref={layerRef}
      className="story-create-overlay"
      role="dialog"
      aria-modal="true"
      aria-label={label}
      onKeyDown={onKeyDown}
    >
      <header className="story-create-overlay-head">
        <div>
          <p className="story-label">{label}</p>
          <p className="story-create-overlay-note">{note}</p>
        </div>
        <button ref={closeRef} type="button" className="story-icon-button" aria-label={closeLabel} onClick={onClose}>
          <X size={18} strokeWidth={1.6} />
        </button>
      </header>
      <div className="story-create-overlay-body">{children}</div>
    </div>
  )

  return fullWindow ? <div className="story-shell story-create-overlay-window">{overlay}</div> : overlay
}
