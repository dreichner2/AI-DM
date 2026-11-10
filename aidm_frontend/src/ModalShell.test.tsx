// @vitest-environment jsdom
import '@testing-library/jest-dom/vitest'
import { createRef, useRef, useState } from 'react'
import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ModalShell } from './ModalShell'
import { setStoryReduceMotion } from './story/sheets/storyPreferences'
import { useModalFocusTrap } from './useModalFocusTrap'

beforeEach(() => localStorage.clear())
afterEach(cleanup)

function TrappedSheet() {
  const [open, setOpen] = useState(false)
  const dialogRef = useRef<HTMLElement | null>(null)
  const returnFocusRef = useRef<HTMLElement | null>(null)
  useModalFocusTrap({
    activeKey: open ? 'sheet' : null,
    dialogRef,
    onClose: () => setOpen(false),
    returnFocusRef,
  })
  return (
    <>
      <button type="button" onClick={() => setOpen(true)}>
        Open sheet
      </button>
      {open ? (
        <ModalShell
          closeLabel="Close sheet"
          dialogRef={dialogRef}
          labelledBy="trapped-title"
          onClose={() => setOpen(false)}
          title="Trapped"
        >
          <div className="story-dialog-body">
            <input aria-label="Name" data-autofocus />
            <footer className="story-dialog-footer">
              <button type="submit" className="story-primary">
                Begin
              </button>
            </footer>
          </div>
        </ModalShell>
      ) : null}
    </>
  )
}

describe('ModalShell', () => {
  it('renders a Story sheet: backdrop, glass panel, serif title, one-line description and a close button', () => {
    const onClose = vi.fn()
    render(
      <ModalShell
        size="narrow"
        className="share-sheet"
        closeLabel="Close share"
        description="Send this link to a friend."
        dialogRef={createRef<HTMLElement>()}
        labelledBy="share-title"
        onClose={onClose}
        title="Share"
      >
        <p>Body</p>
      </ModalShell>,
    )

    const dialog = screen.getByRole('dialog', { name: 'Share' })
    expect(dialog).toHaveAttribute('aria-modal', 'true')
    expect(dialog).toHaveAccessibleDescription('Send this link to a friend.')
    expect(dialog).toHaveClass('story-dialog-panel', 'share-sheet')
    expect(dialog).toHaveAttribute('data-size', 'narrow')
    expect(dialog.parentElement).toHaveClass('story-shell', 'story-dialog')
    expect(screen.getByRole('heading', { level: 2, name: 'Share' })).toHaveClass('story-dialog-title')
    const header = dialog.querySelector('header') as HTMLElement
    expect(header.textContent).toBe('ShareSend this link to a friend.')

    const close = within(dialog).getByRole('button', { name: 'Close share' })
    expect(close).toHaveClass('story-icon-button')
    fireEvent.click(close)
    fireEvent.mouseDown(dialog.parentElement as HTMLElement)
    expect(onClose).toHaveBeenCalledTimes(2)
    fireEvent.mouseDown(dialog)
    expect(onClose).toHaveBeenCalledTimes(2)
  })

  it('stays open while closing is disabled and defaults to the standard width', () => {
    const onClose = vi.fn()
    render(
      <ModalShell
        closeDisabled
        closeLabel="Close"
        dialogRef={createRef<HTMLElement>()}
        labelledBy="busy-title"
        onClose={onClose}
        title="Saving"
      >
        <p>Body</p>
      </ModalShell>,
    )

    const dialog = screen.getByRole('dialog', { name: 'Saving' })
    expect(dialog).toHaveAttribute('data-size', 'standard')
    expect(within(dialog).getByRole('button', { name: 'Close' })).toBeDisabled()
    fireEvent.mouseDown(dialog.parentElement as HTMLElement)
    expect(onClose).not.toHaveBeenCalled()
  })

  it('frames a dialog that renders its own header without adding another', () => {
    render(
      <ModalShell dialogRef={createRef<HTMLElement>()} labelledBy="legacy-title" onClose={vi.fn()}>
        <header>
          <h2 id="legacy-title">Edit Character</h2>
        </header>
      </ModalShell>,
    )

    const dialog = screen.getByRole('dialog', { name: 'Edit Character' })
    expect(dialog.querySelectorAll('header')).toHaveLength(1)
    expect(within(dialog).queryByRole('button')).not.toBeInTheDocument()
  })

  it('marks a busy sheet and follows the reduce-motion choice', () => {
    render(
      <ModalShell busy dialogRef={createRef<HTMLElement>()} labelledBy="loading-title" title="Opening…">
        <p>One moment.</p>
      </ModalShell>,
    )

    const dialog = screen.getByRole('dialog', { name: 'Opening…' })
    expect(dialog).toHaveAttribute('aria-busy', 'true')
    expect(within(dialog).queryByRole('button')).not.toBeInTheDocument()
    expect(dialog.parentElement).not.toHaveAttribute('data-reduce-motion')

    act(() => setStoryReduceMotion(true))
    expect(dialog.parentElement).toHaveAttribute('data-reduce-motion')
  })

  it('keeps focus inside the sheet, closes on Escape and returns focus to the opener', async () => {
    render(<TrappedSheet />)

    const opener = screen.getByRole('button', { name: 'Open sheet' })
    opener.focus()
    fireEvent.click(opener)

    const dialog = await screen.findByRole('dialog', { name: 'Trapped' })
    const nameInput = within(dialog).getByLabelText('Name')
    await waitFor(() => expect(nameInput).toHaveFocus())

    const close = within(dialog).getByRole('button', { name: 'Close sheet' })
    const begin = within(dialog).getByRole('button', { name: 'Begin' })
    begin.focus()
    fireEvent.keyDown(document, { key: 'Tab' })
    expect(close).toHaveFocus()
    fireEvent.keyDown(document, { key: 'Tab', shiftKey: true })
    expect(begin).toHaveFocus()

    fireEvent.keyDown(document, { key: 'Escape' })
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
    expect(opener).toHaveFocus()
  })
})
