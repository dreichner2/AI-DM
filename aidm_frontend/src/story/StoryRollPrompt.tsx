import { useId, useState } from 'react'
import type { ActionComposerProps } from '../ActionComposer'
import type { PendingRollGuidance, RollMode } from '../gameActions'
import type { PendingRollNotice } from '../gameSelectors'
import { D20Glyph } from './glyphs'
import { useStagedComposerAction } from './useStagedComposerAction'

const ROLL_MODES: Array<{ value: RollMode; label: string }> = [
  { value: 'normal', label: 'Normal' },
  { value: 'advantage', label: 'Advantage' },
  { value: 'disadvantage', label: 'Disadvantage' },
]

type StoryRollPromptProps = {
  notice: PendingRollNotice
  composer: ActionComposerProps
  onPrepare: (guidance: PendingRollGuidance) => void
}

function dcLabel(dcHint: string | null) {
  if (!dcHint) return ''
  return /^\d+$/.test(dcHint.trim()) ? `DC ${dcHint.trim()}` : dcHint
}

/** "DEX +3 · DC 13 · advantage" from whatever the pending check provides. */
function rollGuidanceDetail(guidance: PendingRollGuidance, composer: ActionComposerProps) {
  const abilityKey = guidance.ruleType === 'initiative' ? 'dexterity' : guidance.rollSpec.ability?.key
  const ability = abilityKey ? composer.abilityOptions.find((option) => option.key === abilityKey) : undefined
  const modifier = ability?.modifier && ability.modifier !== '—' ? ability.modifier : ''
  const abilityLabel = guidance.rollSpec.ability?.label || ability?.label || ''
  return [
    abilityLabel && modifier ? `${abilityLabel} ${modifier}` : abilityLabel,
    dcLabel(guidance.dcHint),
    guidance.rollSpec.mode === 'normal' ? '' : guidance.rollSpec.mode,
  ].filter(Boolean).join(' · ')
}

/**
 * The one thing to do when the table is waiting on the viewer's dice: a single
 * button that prepares the pending check and asks the server to roll it.
 */
export function StoryRollPrompt({ notice, composer, onPrepare }: StoryRollPromptProps) {
  const optionsId = useId()
  const [optionsOpen, setOptionsOpen] = useState(false)
  const stage = useStagedComposerAction(composer)
  const turnId = String(notice.guidance.pendingTurnId)
  const preparedForThisRoll = composer.composerMode === 'roll' && composer.rollTargetPendingTurnId === turnId
  const detail = rollGuidanceDetail(notice.guidance, composer)

  const roll = () => {
    if (optionsOpen && preparedForThisRoll) {
      composer.startDiceRoll()
      return
    }
    onPrepare(notice.guidance)
    stage(
      (current) => current.composerMode === 'roll' && current.rollTargetPendingTurnId === turnId,
      (current) => current.startDiceRoll(),
    )
  }

  const toggleOptions = () => {
    if (!optionsOpen && !preparedForThisRoll) onPrepare(notice.guidance)
    setOptionsOpen((open) => !open)
  }

  return (
    <section className="story-roll-prompt" aria-label="Pending roll">
      <span className="story-label">{notice.turnLabel}</span>
      <button
        type="button"
        className="story-primary story-roll-prompt-button"
        onClick={roll}
        onMouseEnter={composer.preloadDiceRollDialog}
        onFocus={composer.preloadDiceRollDialog}
        disabled={composer.sendPending}
      >
        <D20Glyph size={26} className="story-roll-prompt-glyph" />
        Roll {notice.ruleLabel}
      </button>
      {detail ? <p className="story-roll-prompt-detail">{detail}</p> : null}
      <button
        type="button"
        className="story-link story-roll-prompt-toggle"
        aria-expanded={optionsOpen}
        aria-controls={optionsId}
        onClick={toggleOptions}
      >
        Roll options
      </button>
      {optionsOpen ? (
        <div id={optionsId} className="story-roll-prompt-options">
          <div className="story-segmented" role="group" aria-label="Roll mode">
            {ROLL_MODES.map((option) => (
              <button
                key={option.value}
                type="button"
                className="story-link"
                aria-pressed={composer.rollMode === option.value}
                onClick={() => composer.setRollMode(option.value)}
              >
                {option.label}
              </button>
            ))}
          </div>
          <label className="story-field">
            <span className="story-label">Reason</span>
            <input
              type="text"
              value={composer.rollReason}
              maxLength={120}
              placeholder="What the roll is for"
              onChange={(event) => composer.setRollReason(event.target.value)}
            />
          </label>
        </div>
      ) : null}
    </section>
  )
}
