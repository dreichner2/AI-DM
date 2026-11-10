import { useCallback, useRef } from 'react'

/**
 * For a horizontally scrolling row: marks which edges have hidden content
 * (`data-fade-start` / `data-fade-end`, used by CSS masks) and lets a vertical
 * mouse wheel scroll the row sideways.
 */
export function useEdgeFades<T extends HTMLElement>() {
  const cleanupRef = useRef<(() => void) | null>(null)

  return useCallback((element: T | null) => {
    cleanupRef.current?.()
    cleanupRef.current = null
    if (!element) return

    const update = () => {
      const maxScroll = element.scrollWidth - element.clientWidth
      element.toggleAttribute('data-fade-start', element.scrollLeft > 1)
      element.toggleAttribute('data-fade-end', element.scrollLeft < maxScroll - 1)
    }
    const handleWheel = (event: WheelEvent) => {
      if (Math.abs(event.deltaY) <= Math.abs(event.deltaX)) return
      if (element.scrollWidth <= element.clientWidth) return
      element.scrollLeft += event.deltaY
      event.preventDefault()
    }

    update()
    element.addEventListener('scroll', update, { passive: true })
    element.addEventListener('wheel', handleWheel, { passive: false })
    const resizeObserver = typeof ResizeObserver === 'function' ? new ResizeObserver(update) : null
    resizeObserver?.observe(element)
    const mutationObserver = new MutationObserver(update)
    mutationObserver.observe(element, { childList: true, subtree: true, characterData: true })
    cleanupRef.current = () => {
      element.removeEventListener('scroll', update)
      element.removeEventListener('wheel', handleWheel)
      resizeObserver?.disconnect()
      mutationObserver.disconnect()
    }
  }, [])
}
