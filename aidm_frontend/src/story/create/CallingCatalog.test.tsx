// @vitest-environment jsdom
import '@testing-library/jest-dom/vitest'
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { afterEach, beforeAll, describe, expect, it, vi, type Mock } from 'vitest'
import { CallingCatalog, type CallingView } from './CallingCatalog'
import { FALLBACK_RULES } from './creationRules'
import { coreClasses } from './createHeroModel'

beforeAll(() => {
  // JSDOM has no layout; picking an item scrolls its details into view.
  Element.prototype.scrollIntoView = () => {}
})

afterEach(cleanup)

function renderCatalog({ current = 'Wizard', initialView }: { current?: string | null; initialView?: CallingView } = {}) {
  const onChoose: Mock<(calling: string) => void> = vi.fn()
  render(<CallingCatalog current={current} rules={FALLBACK_RULES} initialView={initialView} onChoose={onChoose} />)
  return { onChoose, details: () => screen.getByRole('region', { name: 'Calling details' }) }
}

const rows = () => within(screen.getByRole('list', { name: 'Callings' })).getAllByRole('button')
const rowNames = () => rows().map((row) => row.querySelector('.story-catalog-row-name')?.firstChild?.textContent)

describe('CallingCatalog', () => {
  it('lists the twelve core callings in the Calling step’s order and opens on the search field', () => {
    renderCatalog()
    expect(screen.getByRole('searchbox', { name: 'Search callings' })).toHaveFocus()
    expect(screen.getByRole('tab', { name: 'Core' })).toHaveAttribute('aria-selected', 'true')
    expect(rowNames()).toEqual(coreClasses(FALLBACK_RULES).map((entry) => entry.name))
    const wizard = screen.getByRole('button', { name: /^Wizard/ })
    expect(wizard).toHaveAttribute('aria-pressed', 'true')
    expect(wizard).toHaveTextContent('Arcane · Controller · 14 paths')
    expect(wizard).not.toHaveTextContent('Narrated')
  })

  it('shows what a core calling can do from the start, as the Calling step does', () => {
    const { details } = renderCatalog()
    expect(within(details()).getByRole('heading', { name: 'Wizard' })).toBeInTheDocument()
    expect(details()).toHaveTextContent('Hit die d6 · Saves INT, WIS')
    expect(within(details()).getByRole('heading', { name: 'What you can do from the start' })).toBeInTheDocument()
    expect(details()).toHaveTextContent('Spells 10 cantrips and 11 spells, such as Fire Bolt, Magic Missile and Shield.')
    expect(details()).toHaveTextContent('PathsAbjurer, Conjurer, Diviner, Enchanter, Evoker, Illusionist and 8 more')
    expect(details()).not.toHaveTextContent('Narrated')
  })

  it('lists every calling with the core ones first and labels the narrated ones', () => {
    const { details, onChoose } = renderCatalog({ initialView: 'all' })
    const names = rowNames()
    expect(names.slice(0, 12)).toEqual(coreClasses(FALLBACK_RULES).map((entry) => entry.name))
    expect(names.length).toBeGreaterThan(12)
    const gunslinger = screen.getByRole('button', { name: /^Gunslinger/ })
    expect(within(gunslinger).getByText('Narrated')).toBeInTheDocument()

    fireEvent.click(gunslinger)
    expect(gunslinger).toHaveAttribute('aria-pressed', 'true')
    expect(details()).toHaveTextContent('Narrated — the Dungeon Master decides how its features play.')
    expect(details()).not.toHaveTextContent('Hit die')
    fireEvent.click(within(details()).getByRole('button', { name: 'Choose Gunslinger' }))
    expect(onChoose).toHaveBeenCalledWith('Gunslinger')
  })

  it('shows the calling of a chosen path and says what to do when there is none', () => {
    renderCatalog({ current: 'Gunslinger - Sniper', initialView: 'all' })
    expect(screen.getByRole('button', { name: /^Gunslinger/ })).toHaveAttribute('aria-pressed', 'true')
    cleanup()
    const { details } = renderCatalog({ current: null })
    expect(details()).toHaveTextContent('Choose a calling to see what it can do.')
  })

  it('searches and filters, with a way back from an empty list', () => {
    renderCatalog({ initialView: 'all' })
    const search = screen.getByRole('searchbox', { name: 'Search callings' })
    fireEvent.change(search, { target: { value: 'zzzz' } })
    expect(screen.getByRole('status')).toHaveTextContent('Nothing by that name.')
    fireEvent.click(screen.getByRole('button', { name: 'Clear search' }))
    expect(search).toHaveValue('')

    const filter = screen.getByRole('button', { name: 'Filter' })
    fireEvent.click(filter)
    fireEvent.click(within(screen.getByRole('group', { name: 'Filter callings by tag' })).getByRole('button', { name: 'Mounted' }))
    expect(screen.getByRole('button', { name: /^Cavalier/ })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /^Wizard/ })).not.toBeInTheDocument()
    fireEvent.click(filter)
    expect(filter).toHaveTextContent('Filter: Mounted')

    fireEvent.click(screen.getByRole('tab', { name: 'Core' }))
    expect(screen.getByRole('status')).toHaveTextContent('Nothing tagged Mounted here.')
  })

  it('moves down the rows with the arrow keys and chooses with Enter', () => {
    const { details, onChoose } = renderCatalog()
    const wizard = screen.getByRole('button', { name: /^Wizard/ })
    expect(wizard).toHaveAttribute('tabindex', '0')
    wizard.focus()
    fireEvent.keyDown(wizard, { key: 'ArrowUp' })
    const previous = rows()[rows().length - 2]
    expect(previous).toHaveFocus()
    expect(previous).toHaveAttribute('aria-pressed', 'true')
    expect(within(details()).getByRole('heading', { name: rowNames()[rows().length - 2]! })).toBeInTheDocument()
    fireEvent.keyDown(previous, { key: 'Home' })
    expect(rows()[0]).toHaveFocus()
    fireEvent.keyDown(rows()[0], { key: 'ArrowDown' })
    expect(rows()[1]).toHaveFocus()
    fireEvent.keyDown(rows()[1], { key: 'Enter' })
    expect(onChoose).toHaveBeenCalledWith(rowNames()[1])
  })

  it('creates and chooses a calling of the player’s own', () => {
    const { onChoose } = renderCatalog({ initialView: 'own' })
    expect(screen.queryByRole('searchbox')).not.toBeInTheDocument()
    const form = screen.getByRole('form', { name: 'A calling of your own' })
    const create = within(form).getByRole('button', { name: 'Create and choose' })
    expect(create).toBeDisabled()
    fireEvent.change(within(form).getByRole('textbox', { name: 'Your calling' }), { target: { value: '  Dream Knight - Mirror Oath ' } })
    fireEvent.click(create)
    expect(onChoose).toHaveBeenCalledWith('Dream Knight - Mirror Oath')
  })
})
