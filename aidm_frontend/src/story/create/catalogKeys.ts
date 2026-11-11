import type { KeyboardEvent } from 'react'

/** How many of the items share the first one's row (1 for a single column or no layout). */
function rowLength(items: HTMLElement[]) {
  const top = items[0]?.getBoundingClientRect().top
  const count = items.filter((item) => item.getBoundingClientRect().top === top).length
  // Without layout (or in a single row) every item reports the same top: step one at a time.
  return count === items.length ? 1 : Math.max(count, 1)
}

/**
 * Arrow keys, Home and End move focus between the item buttons inside the list
 * the event reached; in a grid, Up and Down move by a row.
 */
export function moveListFocus(event: KeyboardEvent<HTMLElement>, layout: 'grid' | 'column') {
  const items = Array.from(event.currentTarget.querySelectorAll<HTMLButtonElement>('button[data-catalog-item]'))
  const index = items.findIndex((item) => item === document.activeElement)
  if (index < 0) return
  const row = layout === 'grid' ? rowLength(items) : 1
  const steps: Record<string, number> = {
    ArrowRight: 1,
    ArrowLeft: -1,
    ArrowDown: row,
    ArrowUp: -row,
    Home: -index,
    End: items.length - 1 - index,
  }
  if (!Object.hasOwn(steps, event.key)) return
  event.preventDefault()
  items[index + steps[event.key]]?.focus()
}
