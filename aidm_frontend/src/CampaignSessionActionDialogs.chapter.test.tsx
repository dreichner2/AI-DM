// @vitest-environment jsdom
import '@testing-library/jest-dom/vitest'
import { createRef, type ComponentProps } from 'react'
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { StartChapterDialog } from './CampaignSessionActionDialogs'

afterEach(cleanup)

function dialogProps(
  overrides: Partial<ComponentProps<typeof StartChapterDialog>> = {},
): ComponentProps<typeof StartChapterDialog> {
  return {
    dialog: { fresh: false, force: false, unsettled: false, error: '', pending: false },
    dialogRef: createRef<HTMLElement>(),
    canForce: true,
    onClose: vi.fn(),
    onFreshChange: vi.fn(),
    onForceChange: vi.fn(),
    onSubmit: vi.fn((event) => event.preventDefault()),
    ...overrides,
  }
}

describe('StartChapterDialog', () => {
  it('explains that the story continues and starts the chapter', () => {
    const props = dialogProps()
    render(<StartChapterDialog {...props} />)

    const dialog = screen.getByRole('dialog', { name: 'Start the next chapter' })
    expect(dialog).toHaveAccessibleDescription(
      'The story continues: the world, your quests and your progress carry over. The current chapter is closed and kept for reading.',
    )
    expect(within(dialog).queryByRole('note')).not.toBeInTheDocument()
    fireEvent.click(within(dialog).getByRole('button', { name: 'Start chapter' }))
    expect(props.onSubmit).toHaveBeenCalledTimes(1)
  })

  it('offers starting over and warns what it resets', () => {
    const onFreshChange = vi.fn()
    const { rerender } = render(<StartChapterDialog {...dialogProps({ onFreshChange })} />)

    fireEvent.click(screen.getByRole('checkbox', { name: 'Start over from the beginning instead' }))
    expect(onFreshChange).toHaveBeenCalledWith(true)

    rerender(
      <StartChapterDialog
        {...dialogProps({ onFreshChange, dialog: { fresh: true, force: false, unsettled: false, error: '', pending: false } })}
      />,
    )
    expect(screen.getByRole('note')).toHaveTextContent(
      "The world resets to the adventure's opening. Your characters keep their progress.",
    )
  })

  it('shows an unsettled refusal and lets only operators drop the unfinished fight or roll', () => {
    const unsettled = {
      fresh: false,
      force: false,
      unsettled: true,
      error: 'Finish the fight or roll first.',
      pending: false,
    }
    const onForceChange = vi.fn()
    const { rerender } = render(<StartChapterDialog {...dialogProps({ dialog: unsettled, onForceChange })} />)

    expect(screen.getByRole('alert')).toHaveTextContent('Finish the fight or roll first.')
    fireEvent.click(screen.getByRole('checkbox', { name: 'Start anyway and drop the unfinished fight or roll' }))
    expect(onForceChange).toHaveBeenCalledWith(true)

    rerender(<StartChapterDialog {...dialogProps({ dialog: unsettled, canForce: false })} />)
    expect(screen.getByRole('alert')).toHaveTextContent('Finish the fight or roll first.')
    expect(
      screen.queryByRole('checkbox', { name: 'Start anyway and drop the unfinished fight or roll' }),
    ).not.toBeInTheDocument()

    rerender(<StartChapterDialog {...dialogProps({ dialog: { ...unsettled, error: '' }, canForce: true })} />)
    expect(screen.getByRole('checkbox', { name: 'Start anyway and drop the unfinished fight or roll' })).toBeInTheDocument()
  })

  it('locks the dialog while the chapter is starting', () => {
    const onClose = vi.fn()
    render(
      <StartChapterDialog
        {...dialogProps({ onClose, dialog: { fresh: false, force: false, unsettled: false, error: '', pending: true } })}
      />,
    )
    expect(screen.getByRole('button', { name: 'Starting...' })).toBeDisabled()
    fireEvent.mouseDown(screen.getByRole('dialog').parentElement as HTMLElement)
    expect(onClose).not.toHaveBeenCalled()
  })
})
