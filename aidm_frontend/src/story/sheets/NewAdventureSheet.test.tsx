// @vitest-environment jsdom
import '@testing-library/jest-dom/vitest'
import { createRef, useState } from 'react'
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { World } from '../../types'
import type { CampaignPackExample, CreateCampaignForm } from '../../useCampaignActions'
import { NewAdventureSheet } from './NewAdventureSheet'

const PACKS: CampaignPackExample[] = [
  {
    pack_id: 'roadlands.kings',
    title: 'The Road of Unremembered Kings',
    description: 'A long description of the caravan.',
    short_description: 'An empty caravan rolls into a roadside inn at dusk.',
  },
]
const WORLDS: World[] = [
  { world_id: 5, name: 'The Western Roadlands', description: 'Old roads.', created_at: null },
]

function Harness({
  onSubmit,
  worlds = WORLDS,
}: {
  onSubmit: (form: CreateCampaignForm) => void
  worlds?: World[]
}) {
  // useCampaignActions opens the sheet with the current world chosen.
  const [form, setForm] = useState<CreateCampaignForm>({
    title: '',
    description: '',
    worldId: '5',
    worldName: '',
    packId: '',
  })
  return (
    <NewAdventureSheet
      dialogRef={createRef<HTMLElement>()}
      error=""
      form={form}
      onClose={vi.fn()}
      onSubmit={() => onSubmit(form)}
      packOptions={PACKS}
      packOptionsPending={false}
      pending={false}
      setForm={setForm}
      worlds={worlds}
    />
  )
}

afterEach(cleanup)

describe('NewAdventureSheet', () => {
  it('begins a bundled adventure straight from its row', () => {
    const onSubmit = vi.fn()
    render(<Harness onSubmit={onSubmit} />)

    const dialog = screen.getByRole('dialog', { name: 'New adventure' })
    expect(within(dialog).getByRole('heading', { name: 'Choose a beginning' })).toBeInTheDocument()
    const rows = within(within(dialog).getByRole('list', { name: 'Beginnings' })).getAllByRole('listitem')
    expect(rows[0]).toHaveTextContent('The Road of Unremembered Kings')
    expect(rows[0]).toHaveTextContent('An empty caravan rolls into a roadside inn at dusk.')
    expect(rows.at(-1)).toHaveTextContent('A blank page')
    expect(rows.at(-1)).toHaveTextContent('You and the Dungeon Master make it up.')

    fireEvent.click(within(rows[0]).getByRole('button', { name: 'Begin The Road of Unremembered Kings' }))
    expect(onSubmit).toHaveBeenCalledOnce()
    expect(onSubmit).toHaveBeenCalledWith(
      expect.objectContaining({ packId: 'roadlands.kings', title: 'The Road of Unremembered Kings', worldId: '' }),
    )
    expect(within(dialog).queryByLabelText('Name')).not.toBeInTheDocument()

    // Had it failed to begin, a blank page still starts empty.
    fireEvent.click(within(dialog).getByRole('button', { name: 'Begin with a blank page' }))
    expect(within(dialog).getByLabelText('Name')).toHaveValue('')
    expect(within(dialog).getByLabelText('What is it about?')).toHaveValue('')
  })

  it('names a blank page, in a new world named after it by default', async () => {
    const onSubmit = vi.fn()
    render(<Harness onSubmit={onSubmit} />)

    fireEvent.click(screen.getByRole('button', { name: 'Begin with a blank page' }))
    const dialog = screen.getByRole('dialog', { name: 'New adventure' })
    const name = within(dialog).getByLabelText('Name')
    await waitFor(() => expect(name).toHaveFocus())
    const begin = within(dialog).getByRole('button', { name: 'Begin the adventure' })
    expect(begin).toBeDisabled()

    const world = within(dialog).getByRole('radiogroup', { name: 'World' })
    expect(within(world).getByRole('radio', { name: /A new world/ })).toBeChecked()

    fireEvent.change(name, { target: { value: 'Crystal Road' } })
    fireEvent.change(within(dialog).getByLabelText('What is it about?'), { target: { value: 'Find the lantern city.' } })
    fireEvent.click(begin)
    expect(onSubmit).toHaveBeenLastCalledWith({
      title: 'Crystal Road',
      description: 'Find the lantern city.',
      worldId: '',
      worldName: 'Crystal Road',
      packId: '',
    })

    fireEvent.click(within(world).getByRole('radio', { name: /The Western Roadlands/ }))
    fireEvent.click(begin)
    expect(onSubmit).toHaveBeenLastCalledWith(expect.objectContaining({ worldId: '5', worldName: '' }))
  })

  it('asks about the world only when worlds exist, and goes back to the beginnings', () => {
    render(<Harness onSubmit={vi.fn()} worlds={[]} />)

    fireEvent.click(screen.getByRole('button', { name: 'Begin with a blank page' }))
    const dialog = screen.getByRole('dialog', { name: 'New adventure' })
    expect(within(dialog).queryByRole('radiogroup', { name: 'World' })).not.toBeInTheDocument()

    fireEvent.click(within(dialog).getByRole('button', { name: 'Back' }))
    expect(within(dialog).getByRole('heading', { name: 'Choose a beginning' })).toBeInTheDocument()
    expect(within(dialog).getByRole('button', { name: 'Begin with a blank page' })).toHaveFocus()
  })
})
