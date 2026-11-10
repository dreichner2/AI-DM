// @vitest-environment jsdom
import '@testing-library/jest-dom/vitest'
import { useState } from 'react'
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { ActionComposerProps } from '../ActionComposer'
import type { ComposerMode, PendingRollGuidance, RollMode } from '../gameActions'
import { testComposer, testRollNotice } from './dockTestFixtures'
import { StoryRollPrompt } from './StoryRollPrompt'

/** Holds the bits of composer state that preparing a roll changes, like useComposerActions does. */
function PromptHarness({
  onPrepare,
  composer,
}: {
  onPrepare: (guidance: PendingRollGuidance) => void
  composer: ActionComposerProps
}) {
  const [composerMode, setComposerMode] = useState<ComposerMode>('action')
  const [rollTargetPendingTurnId, setRollTargetPendingTurnId] = useState('')
  const [rollMode, setRollMode] = useState<RollMode>('normal')
  return (
    <StoryRollPrompt
      notice={testRollNotice()}
      composer={{
        ...composer,
        composerMode,
        rollTargetPendingTurnId,
        rollMode,
        setRollMode: (value) => {
          composer.setRollMode(value)
          setRollMode(value)
        },
      }}
      onPrepare={(guidance) => {
        onPrepare(guidance)
        setRollMode(guidance.rollSpec.mode)
        setRollTargetPendingTurnId(String(guidance.pendingTurnId))
        setComposerMode('roll')
      }}
    />
  )
}

afterEach(() => cleanup())

describe('StoryRollPrompt', () => {
  it('is the one roll button in the Pending roll region, with the check and its numbers', () => {
    render(<StoryRollPrompt notice={testRollNotice()} composer={testComposer()} onPrepare={vi.fn()} />)

    const region = screen.getByRole('region', { name: 'Pending roll' })
    expect(within(region).getByText('Turn 12')).toBeInTheDocument()
    expect(within(region).getByRole('button', { name: 'Roll stealth check' })).toBeEnabled()
    expect(within(region).getByText('Dexterity +3 · DC 13')).toBeInTheDocument()
  })

  it('prepares the pending check, then starts the server roll once the composer reflects it', () => {
    const onPrepare = vi.fn()
    const composer = testComposer()
    render(<PromptHarness onPrepare={onPrepare} composer={composer} />)

    fireEvent.click(screen.getByRole('button', { name: 'Roll stealth check' }))

    expect(onPrepare).toHaveBeenCalledWith(testRollNotice().guidance)
    expect(composer.startDiceRoll).toHaveBeenCalledTimes(1)
    expect(composer.startDiceRoll).toHaveBeenCalledWith()
    expect(onPrepare.mock.invocationCallOrder[0]).toBeLessThan(
      vi.mocked(composer.startDiceRoll).mock.invocationCallOrder[0],
    )
  })

  it('does not start a roll when the prepared state never arrives', () => {
    const composer = testComposer()
    render(<StoryRollPrompt notice={testRollNotice()} composer={composer} onPrepare={vi.fn()} />)

    fireEvent.click(screen.getByRole('button', { name: 'Roll stealth check' }))
    expect(composer.startDiceRoll).not.toHaveBeenCalled()
  })

  it('preloads the dice roller on hover and focus', () => {
    const composer = testComposer()
    render(<StoryRollPrompt notice={testRollNotice()} composer={composer} onPrepare={vi.fn()} />)
    const button = screen.getByRole('button', { name: 'Roll stealth check' })

    fireEvent.mouseEnter(button)
    fireEvent.focus(button)
    expect(composer.preloadDiceRollDialog).toHaveBeenCalledTimes(2)
  })

  it('lets the player choose advantage and a reason before rolling', () => {
    const onPrepare = vi.fn()
    const composer = testComposer()
    render(<PromptHarness onPrepare={onPrepare} composer={composer} />)

    const toggle = screen.getByRole('button', { name: 'Roll options' })
    fireEvent.click(toggle)
    expect(toggle).toHaveAttribute('aria-expanded', 'true')
    expect(onPrepare).toHaveBeenCalledTimes(1)

    const modes = screen.getByRole('group', { name: 'Roll mode' })
    fireEvent.click(within(modes).getByRole('button', { name: 'Advantage' }))
    expect(composer.setRollMode).toHaveBeenCalledWith('advantage')
    expect(within(modes).getByRole('button', { name: 'Advantage' })).toHaveAttribute('aria-pressed', 'true')

    fireEvent.change(screen.getByLabelText('Reason'), { target: { value: 'Hide behind the wagon' } })
    expect(composer.setRollReason).toHaveBeenCalledWith('Hide behind the wagon')

    fireEvent.click(screen.getByRole('button', { name: 'Roll stealth check' }))
    expect(onPrepare).toHaveBeenCalledTimes(1)
    expect(composer.startDiceRoll).toHaveBeenCalledTimes(1)
  })

  it('cannot be pressed while a request is in flight', () => {
    render(<StoryRollPrompt notice={testRollNotice()} composer={testComposer({ sendPending: true })} onPrepare={vi.fn()} />)
    expect(screen.getByRole('button', { name: 'Roll stealth check' })).toBeDisabled()
  })
})
