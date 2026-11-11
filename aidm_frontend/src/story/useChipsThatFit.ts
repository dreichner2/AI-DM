import { useEffect, useLayoutEffect, useRef } from 'react'

/** Which of a row's chips are shown, and whether `More…` follows them. */
export type ChipFit = { shown: boolean[]; more: boolean }

/**
 * The chips that fit one line `available` pixels wide, in order, with room
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
): ChipFit {
  const total = widths.reduce((sum, width) => sum + width, 0) + (keepMore ? moreWidth : 0)
  const parts = widths.length + (keepMore ? 1 : 0)
  if (available <= 0 || total + Math.max(0, parts - 1) * gap <= available) {
    return { shown: widths.map(() => true), more: keepMore }
  }
  let used = moreWidth
  const shown = widths.map((width) => {
    if (used + gap + width > available) return false
    used += gap + width
    return true
  })
  return { shown, more: true }
}

function width(element: Element) {
  return element.getBoundingClientRect().width
}

/**
 * Keeps a one-line row of chips from clipping. The row renders every chip
 * (`data-fit="chip"`) and a `More…` chip (`data-fit="more"`); before each
 * paint they are all shown, measured, and those that do not fit are hidden,
 * and `More…` too unless something was left out or `keepMore`. The row is
 * fitted again when its width changes or the web fonts finish loading.
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
    const fonts = (document as Document & { fonts?: FontFaceSet }).fonts
    let live = true
    if (fonts && fonts.status !== 'loaded') {
      void fonts.ready.then(() => {
        if (live) refit()
      })
    }
    return () => {
      live = false
      observer?.disconnect()
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
  )
  chips.forEach((chip, index) => {
    chip.hidden = !fit.shown[index]
  })
  if (more) more.hidden = !fit.more
}
