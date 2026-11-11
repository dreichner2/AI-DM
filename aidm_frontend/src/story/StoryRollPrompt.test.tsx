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
    expect(within(region).getByRole('button', { name: 'Roll Stealth check' })).toBeEnabled()
    expect(within(region).getByText('d20 + 3 against DC 13')).toBeInTheDocument()
  })

  it.each([
    ['intelligence', 'Roll Intelligence'],
    ['dexterity saving throw', 'Roll Dexterity saving throw'],
    ['sleight of hand', 'Roll Sleight of Hand'],
  ])('capitalises the ability in %s', (ruleLabel, name) => {
    render(<StoryRollPrompt notice={{ ...testRollNotice(), ruleLabel }} composer={testComposer()} onPrepare={vi.fn()} />)

    expect(screen.getByRole('button', { name })).toBeInTheDocument()
  })

  it('speaks plainly about the server hint and keeps the breakdown for Roll options', () => {
    const notice = testRollNotice()
    notice.guidance = {
      ...notice.guidance,
      ruleType: 'check',
      dcHint: '14 (roll mod +4, STR 15 mod +2, proficiency +2 (weapon:longsword))',
      rollSpec: { ...notice.guidance.rollSpec, ruleType: 'check', ability: { key: 'strength', label: 'STR' } },
    }
    render(<StoryRollPrompt notice={notice} composer={testComposer()} onPrepare={vi.fn()} />)

    const region = screen.getByRole('region', { name: 'Pending roll' })
    expect(within(region).getByText('d20 + 4 against DC 14')).toBeInTheDocument()
    expect(region).not.toHaveTextContent(/roll mod|STR|weapon:|proficiency/)

    fireEvent.click(screen.getByRole('button', { name: 'Roll options' }))
    expect(within(region).getByText('Strength +2 · proficiency +2')).toBeInTheDocument()
  })

  it.each([
    ['a negative modifier with a wound', 'check', '12 (roll mod -1, DEX 10 mod +0, wounded -1)', 'd20 − 1 against DC 12', 'Dexterity +0 · wounded −1'],
    ['a stated DC', 'check', '15 (roll mod +2, WIS 14 mod +2, stated DC)', 'd20 + 2 against DC 15', 'Wisdom +2'],
    ['no DC at all', 'initiative', 'initiative order (roll mod +3, DEX 17 mod +3)', 'd20 + 3', 'Dexterity +3'],
    ['an attack against a known AC', 'attack', 'AC 13 (roll mod +5, DEX 17 mod +3, proficiency +2 (weapon:longbow))', 'd20 + 5 against AC 13', 'Dexterity +3 · proficiency +2'],
    ['an attack whose number is not the target AC', 'attack', '14 (roll mod +4, STR 15 mod +2, proficiency +2 (weapon:longsword))', 'd20 + 4', 'Strength +2 · proficiency +2'],
  ])('reads %s', (_case, ruleType, dcHint, line, breakdown) => {
    const notice = testRollNotice()
    notice.guidance = { ...notice.guidance, ruleType, dcHint, rollSpec: { ...notice.guidance.rollSpec, ruleType } }
    render(<StoryRollPrompt notice={notice} composer={testComposer()} onPrepare={vi.fn()} />)

    expect(screen.getByText(line)).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Roll options' }))
    expect(screen.getByText(breakdown)).toBeInTheDocument()
  })

  it('says when the check is rolled with advantage', () => {
    const notice = testRollNotice()
    notice.guidance = { ...notice.guidance, rollSpec: { ...notice.guidance.rollSpec, mode: 'advantage' } }
    render(<StoryRollPrompt notice={notice} composer={testComposer()} onPrepare={vi.fn()} />)

    expect(screen.getByText('d20 + 3 against DC 13 with advantage')).toBeInTheDocument()
  })

  it('prepares the pending check, then starts the server roll once the composer reflects it', () => {
    const onPrepare = vi.fn()
    const composer = testComposer()
    render(<PromptHarness onPrepare={onPrepare} composer={composer} />)

    fireEvent.click(screen.getByRole('button', { name: 'Roll Stealth check' }))

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

    fireEvent.click(screen.getByRole('button', { name: 'Roll Stealth check' }))
    expect(composer.startDiceRoll).not.toHaveBeenCalled()
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

    fireEvent.click(screen.getByRole('button', { name: 'Roll Stealth check' }))
    expect(onPrepare).toHaveBeenCalledTimes(1)
    expect(composer.startDiceRoll).toHaveBeenCalledTimes(1)
  })

  it('cannot be pressed while a request is in flight', () => {
    render(<StoryRollPrompt notice={testRollNotice()} composer={testComposer({ sendPending: true })} onPrepare={vi.fn()} />)
    expect(screen.getByRole('button', { name: 'Roll Stealth check' })).toBeDisabled()
  })
})
