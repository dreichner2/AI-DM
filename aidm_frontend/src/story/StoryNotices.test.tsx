// @vitest-environment jsdom
import '@testing-library/jest-dom/vitest'
import { createRef } from 'react'
import { act, cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { ModalShell } from '../ModalShell'
import { NOTICE_FADE_MS, StoryNoticeContext, useStoryNoticeLine, type StoryNotice } from './storyNoticeLine'
import { StoryNotices } from './StoryNotices'

function Line({ notices }: { notices: StoryNotice[] }) {
  return <StoryNotices line={useStoryNoticeLine(notices)} />
}

const roll: StoryNotice = { key: 'roll-1', text: 'Brannoc: I roll a d20: 12' }
const chapter: StoryNotice = { key: 'chapter-1', text: 'That chapter has ended — you are now in the current chapter.' }
const copied: StoryNotice = { key: 'notice-1', text: 'Session link copied.', fades: true }
const failed: StoryNotice = { key: 'notice-2', text: 'Could not start the next chapter: timeout', tone: 'fail' }

afterEach(() => {
  cleanup()
  vi.useRealTimers()
})

describe('StoryNotices', () => {
  it('shows one line, the most recent notice to arrive', () => {
    const onSelect = vi.fn()
    const error: StoryNotice = {
      key: 'error-1',
      text: 'Socket connection failed: timeout',
      tone: 'fail',
      action: { label: 'Refresh and reconnect', onSelect },
    }
    const { container, rerender } = render(<Line notices={[roll]} />)
    const status = screen.getByRole('status')
    const alert = screen.getByRole('alert')
    expect(status).toHaveTextContent('Brannoc: I roll a d20: 12')

    rerender(<Line notices={[roll, chapter]} />)
    expect(status).toHaveTextContent(chapter.text)
    expect(container.querySelectorAll('.story-notice')).toHaveLength(1)

    // Newest wins whatever order the notices are listed in; an error is an alert.
    rerender(<Line notices={[error, roll, chapter]} />)
    expect(status).toBeEmptyDOMElement()
    expect(alert).toHaveTextContent('Socket connection failed: timeout')
    expect(container.querySelector('.story-notice')).toHaveAttribute('data-tone', 'fail')
    fireEvent.click(within(alert).getByRole('button', { name: 'Refresh and reconnect' }))
    expect(onSelect).toHaveBeenCalledTimes(1)
    // Acting on the notice dismisses it; the chapter notice, still current, returns.
    expect(alert).toBeEmptyDOMElement()
    expect(status).toHaveTextContent(chapter.text)

    rerender(<Line notices={[{ key: 'roll-2', text: 'Wren: I roll a d20: 3' }, chapter]} />)
    expect(status).toHaveTextContent('Wren: I roll a d20: 3')

    rerender(<Line notices={[]} />)
    expect(status).toBeEmptyDOMElement()
  })

  it('fades a notice after six seconds but keeps an error until it is dismissed or replaced', () => {
    vi.useFakeTimers()
    const { rerender } = render(<Line notices={[copied]} />)
    const status = screen.getByRole('status')
    expect(status).toHaveTextContent('Session link copied.')
    act(() => vi.advanceTimersByTime(NOTICE_FADE_MS - 1))
    expect(status).toHaveTextContent('Session link copied.')
    act(() => vi.advanceTimersByTime(1))
    expect(status).toBeEmptyDOMElement()

    rerender(<Line notices={[copied, failed]} />)
    const alert = screen.getByRole('alert')
    act(() => vi.advanceTimersByTime(NOTICE_FADE_MS * 5))
    expect(alert).toHaveTextContent('Could not start the next chapter: timeout')

    const refused: StoryNotice = { key: 'notice-3', text: 'Choose a campaign, session, and player before sending.', fades: true }
    rerender(<Line notices={[refused]} />)
    expect(alert).toBeEmptyDOMElement()
    expect(status).toHaveTextContent(refused.text)
  })

  it('dismisses the notice it is showing when it is clicked', () => {
    render(<Line notices={[roll, failed]} />)

    fireEvent.click(screen.getByRole('button', { name: failed.text }))
    expect(screen.getByRole('alert')).toBeEmptyDOMElement()
    expect(screen.getByRole('status')).toHaveTextContent(roll.text)

    fireEvent.click(screen.getByRole('button', { name: roll.text }))
    expect(screen.getByRole('status')).toBeEmptyDOMElement()
  })

  it('shows the shared line at the foot of an open sheet, and nothing outside the Story view', () => {
    const dismiss = vi.fn()
    const sheet = (
      <ModalShell dialogRef={createRef<HTMLElement>()} labelledBy="sheet-title" title="Settings">
        <p>Body</p>
      </ModalShell>
    )
    const { rerender } = render(
      <StoryNoticeContext.Provider value={{ notice: copied, dismiss }}>{sheet}</StoryNoticeContext.Provider>,
    )
    const dialog = screen.getByRole('dialog', { name: 'Settings' })
    const line = dialog.lastElementChild as HTMLElement
    expect(line).toHaveClass('story-notices', 'story-dialog-notice')
    expect(within(line).getByRole('status')).toHaveTextContent('Session link copied.')
    fireEvent.click(within(line).getByRole('button', { name: 'Session link copied.' }))
    expect(dismiss).toHaveBeenCalledTimes(1)

    rerender(sheet)
    expect(screen.queryByRole('status')).not.toBeInTheDocument()
  })
})
