import type { KeyboardEvent } from 'react'

const FOCUSABLE = 'button:not([disabled]), input:not([disabled]), textarea:not([disabled]), select:not([disabled]), [tabindex]:not([tabindex="-1"])'

/**
 * Keeps Tab inside a layer drawn over inert content (a catalog overlay, the
 * discard question): from the last control it wraps to the first and back.
 * Stops the key here, because the surrounding dialog's own trap would wrap
 * focus onto the inert content.
 */
export function keepTabInside(event: KeyboardEvent<HTMLElement>, layer: HTMLElement) {
  event.stopPropagation()
  const focusable = Array.from(layer.querySelectorAll<HTMLElement>(FOCUSABLE))
  const [first, last] = [focusable[0], focusable[focusable.length - 1]]
  if (first && document.activeElement === (event.shiftKey ? first : last)) {
    event.preventDefault()
    const wrapTo = event.shiftKey ? last : first
    wrapTo.focus()
  }
}
