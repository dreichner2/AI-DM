// @vitest-environment jsdom
import '@testing-library/jest-dom/vitest'
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi, type Mock } from 'vitest'
import type { SexKey } from '../../profileIcons'
import type { CharacterRaceSelection, RaceDefinition, RaceSummary } from '../../types'
import { AncestryCatalog, type AncestryView } from './AncestryCatalog'

const BASE_URL = 'http://localhost:5050'

const ELF: CharacterRaceSelection = { raceId: 'elf', raceName: 'Elf', source: 'curated', selectedOptions: {} }

const VEILKIN_SUMMARY = {
  id: 'veilkin',
  version: 1,
  name: 'Veilkin',
  source: 'custom',
  descriptionShort: 'Quiet people half seen through the mist.',
  aliases: [],
  tags: [],
  size: 'Medium',
  baseSpeed: 30,
  visual: {},
  originStory: '',
  physical: { averageHeight: '5 ft', averageWeight: '140 lb' },
  languages: ['Common'],
  commonProficiencies: [],
  friendlyWith: [],
  waryOf: [],
  traits: ['Still Water', 'Reed Step'],
  recommendedClasses: ['Druid', 'Ranger'],
  difficulty: 'Medium',
  balance: { budget: 5, spent: 3, tier: 'standard' },
} satisfies RaceSummary

function definition(name: string, overrides: Partial<RaceDefinition> = {}): RaceDefinition {
  return {
    ...VEILKIN_SUMMARY,
    id: name.toLowerCase(),
    name,
    descriptionLong: '',
    traits: [{ name: 'Ember Veins', description: 'glow faintly in the dark' }, { name: 'Flame Burst' }],
    aiNarrationHints: [],
    roleplayHooks: [],
    ...overrides,
  }
}

type FetchCall = { method: string; path: string; body: unknown }

let calls: FetchCall[]
let answers: Record<string, unknown>
let refusals: Record<string, string>

function jsonResponse(payload: unknown, status = 200) {
  return new Response(JSON.stringify(payload), { status, headers: { 'Content-Type': 'application/json' } })
}

beforeAll(() => {
  // JSDOM has no layout; picking an item scrolls its details into view.
  Element.prototype.scrollIntoView = () => {}
})

beforeEach(() => {
  calls = []
  answers = { 'GET /api/races?source=custom': { races: [VEILKIN_SUMMARY] } }
  refusals = {}
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = new URL(String(input))
      const method = init?.method ?? 'GET'
      const path = `${url.pathname}${url.search}`
      calls.push({ method, path, body: init?.body ? JSON.parse(String(init.body)) : null })
      const key = `${method} ${path}`
      if (key in refusals) return jsonResponse({ error: refusals[key] }, 422)
      if (!(key in answers)) return jsonResponse({ error: `Unhandled ${key}` }, 404)
      return jsonResponse(answers[key])
    }),
  )
})

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

function renderCatalog({
  current = ELF,
  sex = 'female',
  initialView,
}: { current?: CharacterRaceSelection | null; sex?: SexKey; initialView?: AncestryView } = {}) {
  const onChoose: Mock<(selection: CharacterRaceSelection) => void> = vi.fn()
  const onSexChange: Mock<(sex: SexKey) => void> = vi.fn()
  render(
    <AncestryCatalog
      auth=""
      baseUrl={BASE_URL}
      current={current}
      sex={sex}
      initialView={initialView}
      onChoose={onChoose}
      onSexChange={onSexChange}
    />,
  )
  return { onChoose, onSexChange, details: () => screen.getByRole('region', { name: 'Ancestry details' }) }
}

const medallions = () => within(screen.getByRole('list', { name: 'Ancestries' })).getAllByRole('button')

describe('AncestryCatalog', () => {
  it('opens on the search field with the hero’s ancestry chosen and shown', () => {
    const { details } = renderCatalog()
    expect(screen.getByRole('searchbox', { name: 'Search ancestries' })).toHaveFocus()
    expect(screen.getByRole('tab', { name: 'Recommended' })).toHaveAttribute('aria-selected', 'true')
    expect(screen.getByRole('button', { name: 'Elf' })).toHaveAttribute('aria-pressed', 'true')
    expect(within(details()).getByRole('heading', { name: 'Elf' })).toBeInTheDocument()
    expect(details()).toHaveTextContent('TraitsDarkvision · Keen Senses · Fey Ancestry')
    expect(details()).toHaveTextContent('Good withWizard, Ranger, Rogue')
    expect(within(details()).getByRole('button', { name: 'Female' })).toHaveAttribute('aria-pressed', 'true')
  })

  it('says what to do when nothing is chosen yet', () => {
    const { details } = renderCatalog({ current: null })
    expect(details()).toHaveTextContent('Choose an ancestry to meet them.')
    expect(within(details()).queryByRole('button')).not.toBeInTheDocument()
  })

  it('switches between the recommended and all ancestries', () => {
    renderCatalog()
    expect(screen.getByRole('button', { name: 'Tabaxi' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Aarakocra' })).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('tab', { name: 'All' }))
    expect(screen.getByRole('tab', { name: 'All' })).toHaveAttribute('aria-selected', 'true')
    expect(screen.getByRole('button', { name: 'Aarakocra' })).toBeInTheDocument()
  })

  it('searches, and offers to clear a search that finds nothing', () => {
    renderCatalog()
    const search = screen.getByRole('searchbox', { name: 'Search ancestries' })
    fireEvent.change(search, { target: { value: 'catfolk' } })
    expect(medallions().map((button) => button.textContent)).toEqual(['Tabaxi'])

    fireEvent.change(search, { target: { value: 'zzzz' } })
    expect(screen.getByRole('status')).toHaveTextContent('Nothing by that name.')
    fireEvent.click(screen.getByRole('button', { name: 'Clear search' }))
    expect(search).toHaveValue('')
    expect(medallions().length).toBeGreaterThan(1)
  })

  it('keeps the tag filter closed until asked and names the active tag once it closes', () => {
    renderCatalog({ initialView: 'all' })
    const filter = screen.getByRole('button', { name: 'Filter' })
    expect(filter).toHaveAttribute('aria-expanded', 'false')
    expect(screen.queryByRole('group', { name: 'Filter ancestries by tag' })).not.toBeInTheDocument()

    fireEvent.click(filter)
    const tags = screen.getByRole('group', { name: 'Filter ancestries by tag' })
    fireEvent.click(within(tags).getByRole('button', { name: 'Small' }))
    expect(within(tags).getByRole('button', { name: 'Small' })).toHaveAttribute('aria-pressed', 'true')
    expect(screen.getByRole('button', { name: 'Halfling' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Goliath' })).not.toBeInTheDocument()

    fireEvent.click(filter)
    expect(filter).toHaveTextContent('Filter: Small')
    fireEvent.click(filter)
    fireEvent.click(within(screen.getByRole('group', { name: 'Filter ancestries by tag' })).getByRole('button', { name: 'Clear' }))
    expect(screen.getByRole('button', { name: 'Goliath' })).toBeInTheDocument()
  })

  it('says when a tag leaves nothing in the view and clears it', () => {
    renderCatalog()
    fireEvent.click(screen.getByRole('button', { name: 'Filter' }))
    fireEvent.click(screen.getByRole('button', { name: 'Construct' }))
    expect(screen.getByRole('status')).toHaveTextContent('Nothing tagged Construct here.')
    fireEvent.click(within(screen.getByRole('status')).getByRole('button', { name: 'Clear' }))
    expect(screen.getByRole('button', { name: 'Tabaxi' })).toBeInTheDocument()
  })

  it('shows a picked ancestry and chooses it with its portrait', () => {
    const { details, onChoose, onSexChange } = renderCatalog()
    fireEvent.click(screen.getByRole('button', { name: 'Dwarf' }))
    expect(screen.getByRole('button', { name: 'Dwarf' })).toHaveAttribute('aria-pressed', 'true')
    expect(screen.getByRole('button', { name: 'Elf' })).toHaveAttribute('aria-pressed', 'false')
    expect(within(details()).getByRole('heading', { name: 'Dwarf' })).toBeInTheDocument()

    fireEvent.click(within(details()).getByRole('button', { name: 'Male' }))
    expect(onSexChange).toHaveBeenCalledWith('male')
    fireEvent.click(within(details()).getByRole('button', { name: 'Choose Dwarf' }))
    expect(onChoose).toHaveBeenCalledWith({ raceId: 'dwarf', raceName: 'Dwarf', source: 'curated', selectedOptions: {} })
  })

  it('moves through the medallions with the arrow keys and chooses with Enter', () => {
    const { details, onChoose } = renderCatalog()
    const elf = screen.getByRole('button', { name: 'Elf' })
    expect(elf).toHaveAttribute('tabindex', '0')
    expect(screen.getByRole('button', { name: 'Dwarf' })).toHaveAttribute('tabindex', '-1')
    elf.focus()

    fireEvent.keyDown(elf, { key: 'ArrowRight' })
    const gnome = screen.getByRole('button', { name: 'Gnome' })
    expect(gnome).toHaveFocus()
    expect(gnome).toHaveAttribute('aria-pressed', 'true')
    expect(within(details()).getByRole('heading', { name: 'Gnome' })).toBeInTheDocument()

    fireEvent.keyDown(gnome, { key: 'Home' })
    expect(medallions()[0]).toHaveFocus()
    fireEvent.keyDown(medallions()[0], { key: 'End' })
    const last = medallions()[medallions().length - 1]
    expect(last).toHaveFocus()
    fireEvent.keyDown(last, { key: 'Enter' })
    expect(onChoose).toHaveBeenCalledWith(expect.objectContaining({ raceName: last.textContent, source: 'curated' }))
  })

  it('lists the player’s own ancestries and chooses one with its full definition', async () => {
    const veilkin = definition('Veilkin')
    answers['GET /api/races/veilkin'] = veilkin
    const { details, onChoose } = renderCatalog({ initialView: 'own' })
    expect(screen.queryByRole('searchbox')).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Filter' })).not.toBeInTheDocument()

    const saved = await screen.findByRole('button', { name: 'Veilkin' })
    expect(saved).toHaveTextContent('V')
    expect(saved.querySelector('img')).toBeNull()
    fireEvent.click(saved)
    expect(details()).toHaveTextContent('TraitsStill Water · Reed Step')
    fireEvent.click(within(details()).getByRole('button', { name: 'Choose Veilkin' }))

    await waitFor(() =>
      expect(onChoose).toHaveBeenCalledWith({
        raceId: 'veilkin',
        raceName: 'Veilkin',
        source: 'custom',
        customRaceDefinition: veilkin,
        selectedOptions: {},
      }),
    )
    expect(calls.map((call) => `${call.method} ${call.path}`)).toEqual(['GET /api/races?source=custom', 'GET /api/races/veilkin'])
  })

  it('drafts an ancestry from an idea, lets the player rename it, then creates and chooses it', async () => {
    const draft = definition('Emberborn', { approvalStatus: 'draft' })
    const saved = definition('Cinderfolk', { id: 'cinderfolk' })
    answers['POST /api/custom-races/generate'] = { draftRace: draft, balanceAnalysis: draft.balance, warnings: [], generationSource: 'test', generationMode: 'canon' }
    answers['POST /api/custom-races'] = { race: saved, summary: { ...VEILKIN_SUMMARY, id: 'cinderfolk', name: 'Cinderfolk' } }
    const { onChoose } = renderCatalog({ initialView: 'own' })
    const form = screen.getByRole('form', { name: 'A new ancestry' })

    const draftIt = within(form).getByRole('button', { name: 'Draft it' })
    expect(draftIt).toBeDisabled()
    fireEvent.change(within(form).getByRole('textbox', { name: 'Your idea' }), { target: { value: 'People of living embers' } })
    fireEvent.click(draftIt)

    const name = await within(form).findByRole('textbox', { name: 'Name' })
    expect(form).toHaveTextContent('Ember Veins — glow faintly in the dark')
    expect(form).toHaveTextContent('Balance 3 of 5 · standard')
    fireEvent.change(name, { target: { value: 'Cinderfolk' } })
    fireEvent.click(within(form).getByRole('button', { name: 'Create and choose' }))

    await waitFor(() => expect(onChoose).toHaveBeenCalledTimes(1))
    expect(onChoose).toHaveBeenCalledWith({
      raceId: 'cinderfolk',
      raceName: 'Cinderfolk',
      source: 'custom',
      customRaceDefinition: saved,
      selectedOptions: {},
    })
    expect(calls.filter((call) => call.method === 'POST')).toEqual([
      {
        method: 'POST',
        path: '/api/custom-races/generate',
        body: { prompt: 'People of living embers', strictness: 'standard', generationMode: 'canon' },
      },
      {
        method: 'POST',
        path: '/api/custom-races',
        body: { raceDefinition: { ...draft, name: 'Cinderfolk' }, approvalStatus: 'approved_by_user' },
      },
    ])
  })

  it('shows the server’s refusal on the form', async () => {
    refusals['POST /api/custom-races/generate'] = 'Describe a people, not a single hero.'
    const { onChoose } = renderCatalog({ initialView: 'own' })
    const form = screen.getByRole('form', { name: 'A new ancestry' })
    fireEvent.change(within(form).getByRole('textbox', { name: 'Your idea' }), { target: { value: 'Moon moths' } })
    fireEvent.click(within(form).getByRole('button', { name: 'Draft it' }))
    expect(await within(form).findByRole('alert')).toHaveTextContent('Describe a people, not a single hero.')
    expect(onChoose).not.toHaveBeenCalled()
  })
})
