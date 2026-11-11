// @vitest-environment jsdom
import '@testing-library/jest-dom/vitest'
import { cleanup, configure, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { createRef, useState } from 'react'
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'
import type { Player } from '../../types'
import type { PlayerEditDialogState } from '../../usePlayerProfileActions'
import { EditCharacterSheet } from './EditCharacterSheet'

// The catalogs are lazy chunks; on a busy machine their first load can outlast the defaults.
configure({ asyncUtilTimeout: 5000 })
vi.setConfig({ testTimeout: 20000 })

const PLAYER: Player = {
  player_id: 30,
  workspace_id: 'owner',
  account_id: null,
  username: null,
  campaign_id: 10,
  name: 'Danny',
  character_name: 'Arden Vale',
  race: 'Human',
  sex: 'male',
  profile_image: '',
  class_: 'Fighter',
  char_class: 'Fighter',
  level: 3,
  created_at: null,
  updated_at: null,
}

function editState(change: Partial<NonNullable<PlayerEditDialogState>> = {}): NonNullable<PlayerEditDialogState> {
  return {
    player: PLAYER,
    characterName: 'Arden Vale',
    race: 'Human',
    raceSelection: null,
    sex: 'male',
    charClass: 'Fighter',
    backgroundId: 'soldier',
    level: '3',
    sheet: undefined,
    ties: null,
    error: '',
    pending: false,
    ...change,
  }
}

let latest: PlayerEditDialogState = null

function renderSheet(initial = editState()) {
  const onSubmit = vi.fn((event: { preventDefault: () => void }) => event.preventDefault())
  const onClose = vi.fn()
  function Harness() {
    const [dialog, setDialog] = useState<PlayerEditDialogState>(initial)
    latest = dialog
    if (!dialog) return null
    return (
      <EditCharacterSheet
        auth=""
        baseUrl="http://localhost:5050"
        dialog={dialog}
        dialogRef={createRef<HTMLElement>()}
        onClose={onClose}
        onSubmit={onSubmit}
        setDialog={setDialog}
      />
    )
  }
  render(<Harness />)
  return { onSubmit, onClose, sheet: screen.getByRole('dialog', { name: 'Edit character' }) }
}

function row(sheet: HTMLElement, label: string) {
  return within(sheet).getByText(label, { selector: 'dt' }).parentElement!
}

beforeAll(async () => {
  // JSDOM has no layout; picking a catalog item scrolls its details into view.
  Element.prototype.scrollIntoView = () => {}
  await Promise.all([import('../create/AncestryCatalog'), import('../create/CallingCatalog')])
})

beforeEach(() => {
  // Offline: the calling catalog plays by the built-in rules.
  vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('Failed to fetch')))
})

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

describe('EditCharacterSheet', () => {
  it('shows each choice as a row and writes back what each Change picks', async () => {
    const { sheet, onSubmit } = renderSheet()
    expect(within(sheet).getByRole('textbox', { name: 'Name' })).toHaveValue('Arden Vale')
    expect(row(sheet, 'Ancestry')).toHaveTextContent('Human')
    expect(row(sheet, 'Calling')).toHaveTextContent('Fighter')
    expect(row(sheet, 'Background')).toHaveTextContent('Soldier')
    expect(within(sheet).queryByRole('searchbox')).not.toBeInTheDocument()

    const changeAncestry = within(sheet).getByRole('button', { name: 'Change ancestry' })
    fireEvent.click(changeAncestry)
    const ancestries = await screen.findByRole('dialog', { name: 'Change ancestry' })
    expect(sheet.querySelector('form')).toHaveAttribute('inert')
    await waitFor(() => expect(within(ancestries).getByRole('searchbox', { name: 'Search ancestries' })).toHaveFocus())
    expect(within(ancestries).getByRole('button', { name: 'Human' })).toHaveAttribute('aria-pressed', 'true')
    fireEvent.click(within(ancestries).getByRole('button', { name: 'Tabaxi' }))
    fireEvent.click(within(ancestries).getByRole('button', { name: 'Choose Tabaxi' }))
    expect(screen.queryByRole('dialog', { name: 'Change ancestry' })).not.toBeInTheDocument()
    expect(row(sheet, 'Ancestry')).toHaveTextContent('Tabaxi')
    expect(changeAncestry).toHaveFocus()

    fireEvent.click(within(sheet).getByRole('button', { name: 'Change calling' }))
    const callings = await screen.findByRole('dialog', { name: 'Change calling' })
    fireEvent.click(await within(callings).findByRole('tab', { name: 'All' }))
    fireEvent.click(within(callings).getByRole('button', { name: /^Gunslinger/ }))
    fireEvent.click(within(callings).getByRole('button', { name: 'Choose Gunslinger' }))
    expect(row(sheet, 'Calling')).toHaveTextContent('Gunslinger')

    const changeBackground = within(sheet).getByRole('button', { name: 'Change background' })
    expect(changeBackground).toHaveAttribute('aria-expanded', 'false')
    fireEvent.click(changeBackground)
    const backgrounds = within(sheet).getByRole('group', { name: 'Background' })
    expect(within(backgrounds).getAllByRole('radio')).toHaveLength(6)
    fireEvent.click(within(backgrounds).getByRole('radio', { name: /^Sage/ }))
    expect(row(sheet, 'Background')).toHaveTextContent('Sage')

    fireEvent.click(within(row(sheet, 'Portrait')).getByRole('button', { name: 'Female' }))
    fireEvent.click(within(sheet).getByRole('button', { name: 'Raise level' }))
    fireEvent.change(within(sheet).getByRole('textbox', { name: 'Name' }), { target: { value: 'Arden the Bold' } })

    expect(latest).toMatchObject({
      characterName: 'Arden the Bold',
      race: 'Tabaxi',
      raceSelection: { raceId: 'tabaxi', raceName: 'Tabaxi', source: 'curated', selectedOptions: {} },
      sex: 'female',
      charClass: 'Gunslinger',
      backgroundId: 'sage',
      level: '4',
    })
    fireEvent.click(within(sheet).getByRole('button', { name: 'Save' }))
    expect(onSubmit).toHaveBeenCalledTimes(1)
  })

  it('keeps Tab inside an open catalog and closes only the catalog on Escape', async () => {
    const { sheet, onClose } = renderSheet()
    const changeCalling = within(sheet).getByRole('button', { name: 'Change calling' })
    fireEvent.click(changeCalling)
    const callings = await screen.findByRole('dialog', { name: 'Change calling' })
    await within(callings).findByRole('searchbox', { name: 'Search callings' })

    const close = within(callings).getByRole('button', { name: 'Close callings' })
    close.focus()
    fireEvent.keyDown(close, { key: 'Tab', shiftKey: true })
    expect(within(callings).getByRole('button', { name: 'Choose Fighter' })).toHaveFocus()
    fireEvent.keyDown(document.activeElement!, { key: 'Tab' })
    expect(close).toHaveFocus()

    fireEvent.keyDown(callings, { key: 'Escape' })
    expect(screen.queryByRole('dialog', { name: 'Change calling' })).not.toBeInTheDocument()
    expect(screen.getByRole('dialog', { name: 'Edit character' })).toBeInTheDocument()
    expect(onClose).not.toHaveBeenCalled()
    expect(changeCalling).toHaveFocus()
  })

  it('steps the level within 1 to 20 and shows the save’s refusal', () => {
    const { sheet } = renderSheet(editState({ level: '1', error: 'Level must be 1 through 20.' }))
    expect(within(sheet).getByRole('alert')).toHaveTextContent('Level must be 1 through 20.')
    expect(within(sheet).getByRole('spinbutton', { name: 'Level' })).toHaveValue(1)
    expect(within(sheet).getByRole('button', { name: 'Lower level' })).toBeDisabled()
    fireEvent.change(within(sheet).getByRole('spinbutton', { name: 'Level' }), { target: { value: '20' } })
    expect(within(sheet).getByRole('button', { name: 'Raise level' })).toBeDisabled()
    fireEvent.click(within(sheet).getByRole('button', { name: 'Lower level' }))
    expect(latest?.level).toBe('19')
  })

  it("shows the hero's own threads from their sheet and records an edit with what it would save", () => {
    const { sheet } = renderSheet(
      editState({ sheet: { ties: { desire: { kind: 'own', words: 'To see the sea' }, attachment: { name: 'Edda Vale', relation: 'sister' } } } }),
    )
    const threads = within(sheet).getByRole('region', { name: 'Your own threads' })
    expect(within(threads).getByRole('button', { name: 'in your own words' })).toHaveAttribute('aria-pressed', 'true')
    expect(within(threads).getByRole('textbox', { name: 'What you want, in your own words' })).toHaveValue('To see the sea')
    expect(latest?.ties).toBeNull()

    fireEvent.change(within(threads).getByRole('textbox', { name: 'Who or what you would not abandon' }), { target: { value: 'E' } })
    expect(latest?.ties?.problem).toBe('A name of 2 to 40 letters, or none.')
    fireEvent.change(within(threads).getByRole('textbox', { name: 'Who or what you would not abandon' }), { target: { value: '' } })
    expect(latest?.ties).toMatchObject({ problem: '', payload: { desire: { kind: 'own', words: 'To see the sea' } } })
    expect(latest?.ties?.payload).not.toHaveProperty('attachment')
  })

  it('locks the sheet while it saves', () => {
    const { sheet } = renderSheet(editState({ pending: true }))
    expect(within(sheet).getByRole('button', { name: 'Saving…' })).toBeDisabled()
    expect(within(sheet).getByRole('button', { name: 'Cancel' })).toBeDisabled()
    expect(within(sheet).getByRole('button', { name: 'Change ancestry' })).toBeDisabled()
    expect(within(sheet).getByRole('textbox', { name: 'Name' })).toBeDisabled()
  })
})
