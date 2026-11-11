// @vitest-environment jsdom
import '@testing-library/jest-dom/vitest'
import { act, cleanup, configure, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi, type Mock } from 'vitest'
import { CreateHero } from './CreateHero'
import recorded from './creationOptions.fixture.json'
import { CREATION_OPTIONS_PATH } from './creationRules'
import {
  ancestryById,
  emptyDraft,
  withAncestry,
  withCalling,
  type CreateHeroPayload,
  type CreateStep,
  type HeroDraft,
} from './createHeroModel'

type OnCreate = (payload: CreateHeroPayload) => Promise<void>

// The full selectors are lazy chunks; on a busy machine their first load can outlast the defaults.
configure({ asyncUtilTimeout: 5000 })
vi.setConfig({ testTimeout: 20000 })

function renderCreator({
  draft,
  step,
  onCreate = vi.fn<OnCreate>(async () => {}),
  onClose = vi.fn(),
}: {
  draft?: HeroDraft
  step?: CreateStep
  onCreate?: Mock<OnCreate>
  onClose?: () => void
} = {}) {
  render(
    <CreateHero
      auth=""
      baseUrl="http://localhost:5050"
      campaignTitle="The Lantern Road"
      initialDraft={draft}
      initialStep={step}
      onCreate={onCreate}
      onClose={onClose}
    />,
  )
  return { onCreate, onClose, dialog: screen.getByRole('dialog', { name: 'A new hero · The Lantern Road' }) }
}

function drafted(calling: string, ancestry = 'human') {
  return withCalling(withAncestry({ ...emptyDraft(), name: 'Wren' }, ancestryById(ancestry)!), calling)
}

const continueButton = () => screen.getByRole('button', { name: 'Continue' })

beforeAll(async () => {
  await Promise.all([import('../../RaceSelector'), import('../../ClassSelector'), import('../../raceCatalog')])
})

let fetchMock: Mock<typeof fetch>

beforeEach(() => {
  // JSDOM has no canvas; the ember motes are decorative.
  vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue(null)
  // Offline unless a test answers: the creator plays by its built-in tables.
  fetchMock = vi.fn<typeof fetch>().mockRejectedValue(new TypeError('Failed to fetch'))
  vi.stubGlobal('fetch', fetchMock)
})

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
})

describe('CreateHero', () => {
  it('walks Who, Calling, Shape and Review and begins with the finished hero', async () => {
    const { onCreate } = renderCreator()
    const steps = screen.getByRole('navigation', { name: 'Hero creation steps' })
    expect(within(steps).getByRole('button', { name: 'Who' })).toHaveAttribute('aria-current', 'step')
    expect(within(steps).getByRole('button', { name: 'Review' })).toBeDisabled()

    fireEvent.change(screen.getByLabelText('Hero name'), { target: { value: 'Tamsin' } })
    const ancestry = screen.getByRole('group', { name: 'Ancestry' })
    fireEvent.click(within(ancestry).getByRole('radio', { name: 'Gnome' }))
    expect(screen.getByRole('list', { name: 'Gnome traits in play' })).toHaveTextContent(
      'Inventive: proficient in Arcana and Investigation',
    )
    fireEvent.click(screen.getByRole('radio', { name: 'Female Gnome' }))
    fireEvent.keyDown(screen.getByLabelText('Hero name'), { key: 'Enter' })

    expect(screen.getByRole('heading', { name: 'Calling' })).toHaveFocus()
    fireEvent.click(screen.getByRole('radio', { name: /^Wizard/ }))
    const details = screen.getByRole('region', { name: 'Calling details' })
    expect(details).toHaveTextContent('Hit die d6 · Saves INT, WIS')
    expect(details).toHaveTextContent('10 cantrips and 11 spells, such as Fire Bolt, Magic Missile and Shield.')
    expect(details).toHaveTextContent('Level 5')
    fireEvent.click(continueButton())

    expect(screen.getByRole('radio', { name: /^Sage/ })).toBeChecked()
    expect(screen.queryByRole('checkbox', { name: 'Arcana' })).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('checkbox', { name: 'Medicine' }))
    fireEvent.click(screen.getByRole('checkbox', { name: 'Insight' }))
    fireEvent.click(continueButton())

    expect(screen.getByRole('heading', { name: 'Tamsin' })).toHaveFocus()
    expect(screen.getByText('Level 1 Gnome Wizard')).toBeInTheDocument()
    expect(screen.getByText(/^A sage gnome wizard who left the library/)).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Begin as Tamsin' }))

    await waitFor(() => expect(onCreate).toHaveBeenCalledTimes(1))
    expect(onCreate.mock.calls[0][0]).toMatchObject({
      character_name: 'Tamsin',
      race: 'Gnome',
      sex: 'female',
      char_class: 'Wizard',
      level: 1,
      background: 'sage',
      skill_choices: ['medicine', 'insight'],
    })
  })

  it('asks the server for the creation rules once and plays by its answer', async () => {
    const answer = structuredClone(recorded)
    const wizard = answer.classes.find((entry) => entry.id === 'wizard')!
    wizard.skillChoices.count = 3
    fetchMock.mockResolvedValue(
      new Response(JSON.stringify(answer), { status: 200, headers: { 'Content-Type': 'application/json' } }),
    )
    renderCreator({ draft: drafted('Wizard'), step: 'calling' })
    const details = screen.getByRole('region', { name: 'Calling details' })

    expect(await within(details).findByText(/recover spell slots totalling up to half your wizard level/)).toBeInTheDocument()
    expect(fetchMock).toHaveBeenCalledTimes(1)
    expect(fetchMock.mock.calls[0]?.[0]).toBe(`http://localhost:5050${CREATION_OPTIONS_PATH}`)
    fireEvent.click(continueButton())
    expect(screen.getByRole('group', { name: /Class skills/ })).toHaveTextContent('Choose 3')
  })

  it('creates offline from the built-in tables when the rules cannot be fetched', async () => {
    renderCreator({ draft: drafted('Wizard'), step: 'calling' })
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1))
    await act(async () => {
      await fetchMock.mock.results[0]?.value.catch(() => undefined)
    })
    const details = screen.getByRole('region', { name: 'Calling details' })
    expect(details).toHaveTextContent('Arcane Recovery Once per long rest, after a short rest, recover spent spell slots.')
    fireEvent.click(continueButton())
    expect(screen.getByRole('group', { name: /Class skills/ })).toHaveTextContent('Choose 2')
  })

  it('uses real radio groups and checkboxes so the keyboard works as everywhere else', () => {
    renderCreator({ draft: drafted('Rogue'), step: 'calling' })
    const callings = screen.getAllByRole('radio').filter((radio) => radio.getAttribute('name') === 'story-create-calling')
    expect(callings).toHaveLength(12)
    expect(screen.getByRole('radio', { name: /^Rogue/ })).toBeChecked()
    expect(screen.getByText('You mark two of your skills for Expertise in the next step.')).toBeInTheDocument()

    fireEvent.click(continueButton())
    const skills = screen.getByRole('group', { name: /Class skills/ })
    expect(within(skills).getAllByRole('checkbox')).toHaveLength(9)
    expect(screen.getByRole('heading', { name: 'Shape' })).toHaveFocus()
  })

  it('keeps Continue disabled until each step is complete and says what is missing', () => {
    renderCreator()
    expect(continueButton()).toBeDisabled()
    expect(screen.getByText('Choose an ancestry to continue.')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('radio', { name: 'Dwarf' }))
    expect(screen.getByText('Name your hero to continue.')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Suggest' }))
    expect((screen.getByLabelText('Hero name') as HTMLInputElement).value).not.toBe('')
    fireEvent.click(continueButton())

    expect(continueButton()).toBeDisabled()
    fireEvent.click(screen.getByRole('radio', { name: /^Rogue/ }))
    fireEvent.click(continueButton())

    expect(screen.getByText('Choose 4 class skills to continue.')).toBeInTheDocument()
    for (const skill of ['Acrobatics', 'Athletics', 'Insight', 'Perception']) {
      fireEvent.click(screen.getByRole('checkbox', { name: skill }))
    }
    expect(screen.getByRole('checkbox', { name: 'Persuasion' })).toBeDisabled()
    expect(screen.getByText('Mark two skills for expertise to continue.')).toBeInTheDocument()
    const expertise = screen.getByRole('group', { name: /Expertise/ })
    fireEvent.click(within(expertise).getByRole('checkbox', { name: 'Stealth' }))
    fireEvent.click(within(expertise).getByRole('checkbox', { name: 'History' }))
    expect(continueButton()).toBeEnabled()

    fireEvent.click(screen.getByRole('button', { name: 'Adjust' }))
    fireEvent.click(screen.getByRole('button', { name: 'Raise Wisdom' }))
    expect(screen.getByText('2 points over the 27-point budget')).toBeInTheDocument()
    expect(continueButton()).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Raise Dexterity' })).toBeDisabled()
    fireEvent.click(screen.getByRole('button', { name: 'Lower Wisdom' }))
    expect(screen.getByText('0 of 27 points left')).toBeInTheDocument()
    expect(continueButton()).toBeEnabled()
  })

  it('shows class choices only when they apply and updates the live numbers', () => {
    renderCreator({ draft: drafted('Fighter'), step: 'calling' })
    const styles = screen.getByRole('group', { name: 'Fighting style' })
    expect(within(styles).getByRole('radio', { name: /^Dueling/ })).toBeChecked()
    fireEvent.click(within(styles).getByRole('radio', { name: /^Defense/ }))
    fireEvent.click(continueButton())
    expect(screen.getByText('AC').nextElementSibling).toHaveTextContent('19')
    expect(screen.getByText('Longsword').nextElementSibling).toHaveTextContent('+4')

    fireEvent.click(screen.getByRole('button', { name: 'Calling' }))
    fireEvent.click(screen.getByRole('radio', { name: /^Paladin/ }))
    expect(screen.getByRole('group', { name: 'Fighting style' })).toHaveTextContent('Applies from level 2.')
    expect(screen.getByRole('region', { name: 'Calling details' })).toHaveTextContent(
      'Spells 1 cantrip and 2 spells, such as Amber Knot, Obsidian Lash and Silver Charm.',
    )
    fireEvent.click(screen.getByRole('radio', { name: /^Druid/ }))
    expect(screen.queryByRole('group', { name: 'Fighting style' })).not.toBeInTheDocument()
    expect(screen.getByText('Wild Shape is narrated by the Dungeon Master.')).toBeInTheDocument()
  })

  it('opens the full class catalog in a glass overlay with narrated classes labelled', async () => {
    renderCreator({ draft: drafted('Wizard'), step: 'calling' })
    const more = screen.getByRole('button', { name: 'More callings' })
    fireEvent.click(more)

    const overlay = await screen.findByRole('dialog', { name: 'More callings' })
    const gunslinger = await within(overlay).findByRole('button', { name: 'View Gunslinger details' })
    expect(gunslinger).toHaveTextContent('Narrated class — the Dungeon Master adjudicates its features.')
    expect(within(overlay).getByRole('button', { name: 'View Fighter details' })).not.toHaveTextContent('Narrated class')

    fireEvent.keyDown(overlay, { key: 'Escape' })
    expect(screen.queryByRole('dialog', { name: 'More callings' })).not.toBeInTheDocument()
    expect(screen.queryByRole('alertdialog')).not.toBeInTheDocument()
    expect(more).toHaveFocus()

    fireEvent.click(more)
    fireEvent.click(await screen.findByRole('button', { name: 'View Gunslinger details' }))
    const details = await screen.findByRole('dialog', { name: 'Gunslinger' })
    expect(details).toHaveTextContent('Narrated class — the Dungeon Master adjudicates its features.')
    fireEvent.click(within(details).getByRole('button', { name: 'Select Gunslinger - Sniper' }))

    expect(screen.queryByRole('dialog', { name: 'More callings' })).not.toBeInTheDocument()
    expect(screen.getByRole('radio', { name: /^Gunslinger \(Sniper\)/ })).toBeChecked()
    expect(screen.getByRole('region', { name: 'Calling details' })).toHaveTextContent('Narrated class')
    fireEvent.click(continueButton())
    expect(screen.getByText(/A narrated calling has no class skill list/)).toBeInTheDocument()
  })

  it('opens the full race selector in a glass overlay and adds the pick to the portrait row', async () => {
    renderCreator({ draft: drafted('Wizard'), step: 'who' })
    fireEvent.click(screen.getByRole('button', { name: 'More ancestries' }))

    const overlay = await screen.findByRole('dialog', { name: 'More ancestries' })
    fireEvent.click(await within(overlay).findByRole('button', { name: 'View Tabaxi details' }))
    fireEvent.click(await screen.findByRole('button', { name: 'Select Tabaxi' }))

    await waitFor(() => expect(screen.queryByRole('dialog', { name: 'More ancestries' })).not.toBeInTheDocument())
    expect(screen.getByRole('radio', { name: 'Tabaxi' })).toBeChecked()
    expect(screen.getByRole('heading', { name: 'Tabaxi' })).toBeInTheDocument()
  })

  it('asks before discarding on Escape and keeps the hero when told to', () => {
    const { onClose, dialog } = renderCreator({ draft: drafted('Bard'), step: 'calling' })
    fireEvent.keyDown(dialog, { key: 'Escape' })
    const confirm = screen.getByRole('alertdialog', { name: 'Discard this hero?' })
    expect(confirm).toHaveAccessibleDescription('Wren and every choice so far will be lost.')
    const keep = within(confirm).getByRole('button', { name: 'Keep shaping' })
    expect(keep).toHaveFocus()
    // Tab stays inside the question while the stage behind is inert.
    within(confirm).getByRole('button', { name: 'Discard' }).focus()
    fireEvent.keyDown(document.activeElement!, { key: 'Tab' })
    expect(keep).toHaveFocus()
    fireEvent.keyDown(keep, { key: 'Tab', shiftKey: true })
    expect(within(confirm).getByRole('button', { name: 'Discard' })).toHaveFocus()

    fireEvent.keyDown(confirm, { key: 'Escape' })
    expect(screen.queryByRole('alertdialog')).not.toBeInTheDocument()
    expect(onClose).not.toHaveBeenCalled()

    const leave = screen.getByRole('button', { name: 'Leave hero creation' })
    leave.focus()
    fireEvent.click(leave)
    fireEvent.keyDown(screen.getByRole('alertdialog'), { key: 'Escape' })
    expect(leave).toHaveFocus()

    fireEvent.click(screen.getByRole('button', { name: 'Leave hero creation' }))
    fireEvent.click(screen.getByRole('button', { name: 'Discard' }))
    expect(onClose).toHaveBeenCalledTimes(1)
  })

  it('shows a server refusal as one line above the Begin button and lets the player try again', async () => {
    const onCreate = vi.fn<OnCreate>(async () => {
      throw new Error('A Rogue chooses exactly 4 class skills.')
    })
    let draft = drafted('Fighter')
    draft = { ...draft, backgroundId: 'soldier', skills: ['perception', 'survival'] }
    renderCreator({ draft, step: 'review', onCreate })

    fireEvent.click(screen.getByRole('button', { name: 'Begin as Wren' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('A Rogue chooses exactly 4 class skills.')
    expect(screen.getByRole('button', { name: 'Begin as Wren' })).toBeEnabled()
    expect(onCreate.mock.calls[0][0]).toMatchObject({ fighting_style: 'dueling', skill_choices: ['perception', 'survival'] })
  })
})
