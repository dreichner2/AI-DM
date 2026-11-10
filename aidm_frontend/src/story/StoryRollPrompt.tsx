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

const ABILITY_NAMES: Record<string, string> = {
  str: 'Strength',
  dex: 'Dexterity',
  con: 'Constitution',
  int: 'Intelligence',
  wis: 'Wisdom',
  cha: 'Charisma',
}

type RollSummary = { line: string; breakdown: string }

function abilityName(keyOrLabel: string) {
  return ABILITY_NAMES[keyOrLabel.trim().slice(0, 3).toLowerCase()] ?? ''
}

function signed(value: number) {
  return value < 0 ? `−${Math.abs(value)}` : `+${value}`
}

function parsedNumber(text: string | undefined) {
  if (!text) return null
  const value = Number(text.replace('−', '-'))
  return Number.isFinite(value) ? value : null
}

/**
 * The server's hint reads "14 (roll mod +4, STR 15 mod +2, proficiency +2 (weapon:longsword))",
 * a stated "DC 13", or a bare "13". Ranges and "initiative order" name no target number.
 */
function rollSummary(guidance: PendingRollGuidance, composer: ActionComposerProps): RollSummary {
  const hint = guidance.dcHint?.trim() ?? ''
  const isAttack = /\battack\b/i.test(guidance.rollSpec.ruleType || guidance.ruleType)
  const armorClass = /\bAC\s*(\d{1,2})\b/i.exec(hint)?.[1]
  const difficulty = /\bDC\s*(\d{1,2})\b/i.exec(hint)?.[1] ?? /^(\d{1,2})(?!\d|\s*[-–])/.exec(hint)?.[1]
  const target = isAttack
    ? armorClass ? ` against AC ${armorClass}` : ''
    : difficulty ? ` against DC ${difficulty}` : ''

  const breakdown: string[] = []
  let modifier = parsedNumber(/\broll mod\s*([+\-−]\d+)/i.exec(hint)?.[1])
  if (modifier !== null) {
    const ability = /\b(STR|DEX|CON|INT|WIS|CHA)\s+(?:\d+|unknown)\s+mod\s*([+-]\d+)/i.exec(hint)
    const proficiency = /\bproficiency\s*\+(\d+)/i.exec(hint)
    const wounded = /\bwounded\s*-(\d+)/i.exec(hint)
    if (ability) breakdown.push(`${abilityName(ability[1])} ${signed(Number(ability[2]))}`)
    if (proficiency) breakdown.push(`proficiency +${proficiency[1]}`)
    if (wounded) breakdown.push(`wounded −${wounded[1]}`)
  } else {
    // No server breakdown: the persisted ability modifier is the best plain number to show.
    const abilityKey = guidance.ruleType === 'initiative' ? 'dexterity' : guidance.rollSpec.ability?.key
    const ability = abilityKey ? composer.abilityOptions.find((option) => option.key === abilityKey) : undefined
    modifier = parsedNumber(ability?.modifier)
    if (abilityKey && modifier !== null) breakdown.push(`${abilityName(abilityKey)} ${signed(modifier)}`)
  }

  const bonus = modifier ? ` ${modifier < 0 ? '−' : '+'} ${Math.abs(modifier)}` : ''
  const mode = guidance.rollSpec.mode === 'normal' ? '' : ` with ${guidance.rollSpec.mode}`
  return { line: `${guidance.rollSpec.die || 'd20'}${bonus}${target}${mode}`, breakdown: breakdown.join(' · ') }
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
  const summary = rollSummary(notice.guidance, composer)

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
      <p className="story-roll-prompt-detail">{summary.line}</p>
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
          {summary.breakdown ? <p className="story-roll-prompt-breakdown">{summary.breakdown}</p> : null}
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
