import { useEffect, useLayoutEffect, useRef } from 'react'

/** Which of a row's chips are shown, and whether `More…` follows them. */
export type ChipFit = { shown: boolean[]; more: boolean }

/** How many lines `widths` take when they wrap at `available` pixels; Infinity when one is too wide for a line. */
function wrappedLines(widths: number[], gap: number, available: number) {
  let lines = 1
  let used = 0
  for (const width of widths) {
    if (width > available) return Infinity
    if (used === 0) used = width
    else if (used + gap + width <= available) used += gap + width
    else {
      lines += 1
      used = width
    }
  }
  return lines
}

/**
 * The chips that fit `rows` lines `available` pixels wide, in order and
 * wrapping as the browser wraps them, with room at the end of the last line
 * for a trailing `More…` chip whenever one is shown: always when `keepMore`,
 * and whenever a chip is left out. A chip too wide for what is left waits
 * behind `More…`; a later one that fits is still shown. A row with no width
 * yet (not laid out) shows everything.
 */
export function chipsThatFit(
  widths: number[],
  moreWidth: number,
  gap: number,
  available: number,
  keepMore: boolean,
  rows = 1,
): ChipFit {
  const all = keepMore ? [...widths, moreWidth] : widths
  if (available <= 0 || wrappedLines(all, gap, available) <= rows) {
    return { shown: widths.map(() => true), more: keepMore }
  }
  // The last line keeps room for More… at its end.
  const lineStart = (line: number) => (line === rows ? moreWidth + gap : 0)
  let line = 1
  let used = lineStart(line)
  const fitsAt = (start: number, width: number) => start + width <= available
  const shown = widths.map((width) => {
    if (!fitsAt(used, width) && line < rows && fitsAt(lineStart(line + 1), width)) {
      line += 1
      used = lineStart(line)
    }
    if (!fitsAt(used, width)) return false
    used += width + gap
    return true
  })
  return { shown, more: true }
}

function width(element: Element) {
  return element.getBoundingClientRect().width
}

/**
 * Keeps a row of chips from clipping. The row renders every chip
 * (`data-fit="chip"`) and a `More…` chip (`data-fit="more"`); before each
 * paint they are all shown, measured, and those that do not fit the row's
 * lines (`--story-chip-rows`, one unless the stylesheet allows more) are
 * hidden, and `More…` too unless something was left out or `keepMore`. The
 * row is fitted again when its width changes or the web fonts finish loading.
 */
export function useChipsThatFit<T extends HTMLElement>(keepMore: boolean) {
  const rowRef = useRef<T | null>(null)
  const keepMoreRef = useRef(keepMore)

  useLayoutEffect(() => {
    keepMoreRef.current = keepMore
    const row = rowRef.current
    if (row) fitRow(row, keepMore)
  })

  useEffect(() => {
    const row = rowRef.current
    if (!row) return undefined
    let fittedWidth = width(row)
    const refit = () => {
      fittedWidth = width(row)
      fitRow(row, keepMoreRef.current)
    }
    const observer = typeof ResizeObserver === 'function'
      ? new ResizeObserver(() => {
          if (Math.abs(width(row) - fittedWidth) >= 0.5) refit()
        })
      : null
    observer?.observe(row)
    // Web fonts can start loading after the row is first fitted (their stylesheet arrives late), so
    // every finished load refits it, not only one already under way.
    const fonts = (document as Document & { fonts?: FontFaceSet }).fonts
    fonts?.addEventListener?.('loadingdone', refit)
    return () => {
      observer?.disconnect()
      fonts?.removeEventListener?.('loadingdone', refit)
    }
  }, [])

  return rowRef
}

function fitRow(row: HTMLElement, keepMore: boolean) {
  const chips = [...row.querySelectorAll<HTMLElement>('[data-fit="chip"]')]
  const more = row.querySelector<HTMLElement>('[data-fit="more"]')
  ;[...chips, ...(more ? [more] : [])].forEach((chip) => {
    chip.hidden = false
  })
  const style = window.getComputedStyle(row)
  const available = width(row) - (Number.parseFloat(style.paddingLeft) || 0) - (Number.parseFloat(style.paddingRight) || 0)
  const fit = chipsThatFit(
    chips.map(width),
    more ? width(more) : 0,
    Number.parseFloat(style.columnGap) || 0,
    available,
    keepMore,
    Number.parseInt(style.getPropertyValue('--story-chip-rows'), 10) || 1,
  )
  chips.forEach((chip, index) => {
    chip.hidden = !fit.shown[index]
  })
  if (more) more.hidden = !fit.more
}
