/// <reference types="node" />
// @vitest-environment jsdom
import '@testing-library/jest-dom/vitest'
import { configure, fireEvent, screen, waitFor, within } from '@testing-library/react'
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'
import { appTestState, renderLoadedApp, renderStoryApp, setupAppTest, teardownAppTest } from '../../App.testHarness'

// The editor and its catalogs are lazy chunks; on a busy machine their first load can outlast the defaults.
configure({ asyncUtilTimeout: 5000 })
vi.setConfig({ testTimeout: 30000 })

/** The same edits made in either editor: renamed, a Tabaxi Gunslinger sage, male, level 3. */
const EDITED_PAYLOAD = {
  character_name: 'Ember Ashveil',
  race: 'Tabaxi',
  race_selection: { raceId: 'tabaxi', raceName: 'Tabaxi', source: 'curated', selectedOptions: {} },
  sex: 'male',
  char_class: 'Gunslinger',
  level: 3,
  background: 'sage',
}

/**
 * The dialog whose title or label reads `text`. Found by its text: on the busy
 * app screen a role query for it can outlast the wait.
 */
async function dialogTitled(text: string, selector: string) {
  return (await screen.findByText(text, { selector })).closest<HTMLElement>('[role="dialog"]')!
}

function savedPlayer() {
  return appTestState.fetchCalls.find((call) => call.method === 'PATCH' && call.path === '/api/players/30')
}

beforeAll(async () => {
  Element.prototype.scrollTo = () => {}
  // JSDOM has no layout; picking a catalog item scrolls its details into view.
  Element.prototype.scrollIntoView = () => {}
  await Promise.all([
    import('./EditCharacterSheet'),
    import('../create/AncestryCatalog'),
    import('../create/CallingCatalog'),
    import('../../PlayerEditDialog'),
  ])
})

describe('Editing a character', () => {
  beforeEach(setupAppTest)
  afterEach(teardownAppTest)

  it('opens the Story editor from the drawer and saves the edits through the classic save', async () => {
    await renderStoryApp()
    fireEvent.click(screen.getByRole('button', { name: 'Open character panel' }))
    fireEvent.click(await screen.findByRole('button', { name: 'Edit character' }))

    const sheet = await dialogTitled('Edit character', 'h2')
    expect(within(sheet).queryByRole('searchbox')).not.toBeInTheDocument()
    expect(screen.queryByText('Edit Character', { selector: 'h2' })).not.toBeInTheDocument()
    fireEvent.change(within(sheet).getByRole('textbox', { name: 'Name' }), { target: { value: 'Ember Ashveil' } })

    fireEvent.click(within(sheet).getByRole('button', { name: 'Change ancestry' }))
    const ancestries = await dialogTitled('Change ancestry', 'p')
    fireEvent.click(await within(ancestries).findByRole('button', { name: 'Tabaxi' }))
    fireEvent.click(within(ancestries).getByRole('button', { name: 'Choose Tabaxi' }))

    fireEvent.click(within(sheet).getByRole('button', { name: 'Change calling' }))
    const callings = await dialogTitled('Change calling', 'p')
    fireEvent.click(await within(callings).findByRole('tab', { name: 'All' }))
    fireEvent.click(within(callings).getByRole('button', { name: /^Gunslinger/ }))
    fireEvent.click(within(callings).getByRole('button', { name: 'Choose Gunslinger' }))

    fireEvent.click(within(sheet).getByRole('button', { name: 'Change background' }))
    fireEvent.click(within(sheet).getByRole('radio', { name: /^Sage/ }))
    fireEvent.click(within(within(sheet).getByRole('group', { name: 'Portrait' })).getByRole('button', { name: 'Male' }))
    fireEvent.click(within(sheet).getByRole('button', { name: 'Raise level' }))
    fireEvent.click(within(sheet).getByRole('button', { name: 'Save' }))

    await waitFor(() => expect(sheet).not.toBeInTheDocument())
    expect(savedPlayer()?.body).toEqual(EDITED_PAYLOAD)
  })

  it('keeps the classic editor in Table tools, saving the same payload for the same edits', async () => {
    await renderLoadedApp()
    fireEvent.click(within(screen.getByLabelText('Character actions')).getByRole('button', { name: 'Edit' }))

    const dialog = await dialogTitled('Edit Character', 'h2')
    expect(within(dialog).getByLabelText('Search races')).toBeInTheDocument()
    fireEvent.change(within(dialog).getByLabelText('Character Name'), { target: { value: 'Ember Ashveil' } })

    fireEvent.click(within(dialog).getByRole('button', { name: 'View Tabaxi details' }))
    fireEvent.click(within(dialog).getByRole('button', { name: 'Select Tabaxi' }))
    fireEvent.click(within(dialog).getByRole('button', { name: 'Browse all classes' }))
    fireEvent.click(within(dialog).getByRole('button', { name: 'All Classes' }))
    fireEvent.click(within(dialog).getByRole('button', { name: 'View Gunslinger details' }))
    fireEvent.click(within(dialog).getByRole('button', { name: 'Select Gunslinger' }))
    fireEvent.click(within(dialog).getByRole('radio', { name: /^Sage/ }))
    fireEvent.click(within(dialog).getByRole('button', { name: 'Male Tabaxi' }))
    fireEvent.change(within(dialog).getByRole('spinbutton', { name: 'Level' }), { target: { value: '3' } })
    fireEvent.click(within(dialog).getByRole('button', { name: 'Save Character' }))

    await waitFor(() => expect(dialog).not.toBeInTheDocument())
    expect(savedPlayer()?.body).toEqual(EDITED_PAYLOAD)
  })
})
