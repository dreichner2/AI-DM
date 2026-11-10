import { useEffect, useId, useRef, useState, type KeyboardEvent, type ReactNode, type RefObject } from 'react'
import {
  Backpack,
  Clapperboard,
  Compass,
  Dices,
  Drama,
  MessageCircle,
  Quote,
  Sparkles,
  Users,
  Zap,
  type LucideIcon,
} from 'lucide-react'
import type { ActionComposerProps } from '../ActionComposer'
import {
  DICE_OPTIONS,
  INITIATIVE_ROLL_ABILITY_KEY,
  INTERACTION_TYPE_OPTIONS,
  PLAIN_ROLL_ABILITY_KEY,
  interactionActionText,
  interactionTargetId,
  itemActionText,
  itemOptionSelectionKey,
  stripComposerCommand,
  type ComposerMode,
  type InteractionTarget,
  type InteractionType,
  type InventoryAction,
  type ItemOption,
  type RollMode,
} from '../gameActions'
import type {
  GameplayCapabilityControl,
  GameplaySpellControl,
  GameplayTarget,
} from '../gameplayControlState'
import type { SessionBoardProps } from '../SessionBoard'
import type { ActivePlayer, TurnControlMode } from '../types'
import {
  capabilitySubmission,
  capabilityTarget,
  objectActionSubmission,
  restSubmission,
  selectedHealingAmount,
  spellCastSubmission,
  type ActionSubmission,
} from './actionIntents'
import { useEdgeFades } from './useEdgeFades'

export type TraySection =
  | 'speak'
  | 'roll'
  | 'magic'
  | 'abilities'
  | 'items'
  | 'interact'
  | 'scene'
  | 'emote'
  | 'ooc'
  | 'director'

/** Runs `run` once the composer state satisfies `ready` (see useStagedComposerAction). */
export type StageComposerAction = (
  ready: (composer: ActionComposerProps) => boolean,
  run: (composer: ActionComposerProps) => void,
) => void

type StoryActionTrayProps = {
  board: SessionBoardProps
  canUseOperatorTools: boolean
  section: TraySection
  onSectionChange: (section: TraySection) => void
  stage: StageComposerAction
  /** A choice was made: close the tray and return to the input. */
  onDone: () => void
  /** Dismissed without a choice: close the tray and return to the toggle. */
  onDismiss: () => void
  /** Records a sent action (used for the first-run hint). */
  onSent: () => void
  toggleRef: RefObject<HTMLButtonElement | null>
}

const SECTION_META: Record<TraySection, { label: string; icon: LucideIcon; mode?: ComposerMode }> = {
  speak: { label: 'Speak / act', icon: MessageCircle, mode: 'action' },
  roll: { label: 'Roll', icon: Dices },
  magic: { label: 'Magic', icon: Sparkles },
  abilities: { label: 'Abilities', icon: Zap },
  items: { label: 'Items', icon: Backpack },
  interact: { label: 'Interact', icon: Users },
  scene: { label: 'Scene', icon: Compass },
  emote: { label: 'Emote', icon: Drama, mode: 'emote' },
  ooc: { label: 'Out of character', icon: Quote, mode: 'ooc' },
  director: { label: 'Director', icon: Clapperboard },
}

const TRAY_DICE = ['d20', ...DICE_OPTIONS.filter((die) => die !== 'd20')]
const ROLL_MODES: Array<{ value: RollMode; label: string }> = [
  { value: 'normal', label: 'Normal' },
  { value: 'advantage', label: 'Advantage' },
  { value: 'disadvantage', label: 'Disadvantage' },
]
const TURN_MODES: Array<{ value: TurnControlMode; label: string }> = [
  { value: 'free', label: 'Free' },
  { value: 'spotlight', label: 'Spotlight' },
  { value: 'structured', label: 'Structured' },
]

function ordinalLevel(level: number) {
  if (level <= 0) return 'Cantrip'
  const suffix = level === 1 ? 'st' : level === 2 ? 'nd' : level === 3 ? 'rd' : 'th'
  return `${level}${suffix}`
}

function sentenceCase(value: string) {
  return value ? value.charAt(0).toUpperCase() + value.slice(1) : value
}

function targetChipLabel(target: GameplayTarget) {
  return target.currentHp !== null && target.maxHp !== null
    ? `${target.name} · ${target.currentHp}/${target.maxHp}`
    : target.name
}

function playerName(player: ActivePlayer) {
  return player.character_name || player.name
}

/** A labelled row of chips; `heading` overrides the visible label ('' hides it). */
function TrayGroup({ label, heading = label, children }: { label: string; heading?: string; children: ReactNode }) {
  return (
    <div className="story-tray-group" role="group" aria-label={label}>
      {heading ? <span className="story-label" aria-hidden="true">{heading}</span> : null}
      <div className="story-chip-row">{children}</div>
    </div>
  )
}

function UsePips({ current, max }: { current: number; max: number }) {
  return (
    <span className="story-pips" aria-label={`${current} of ${max} uses left`} role="img">
      {Array.from({ length: Math.min(max, 10) }, (_, index) => (
        <i key={index} data-spent={index >= current || undefined} />
      ))}
    </span>
  )
}

/**
 * The single action picker. Every submit uses the same typed intents and the
 * same composer state the classic console uses.
 */
export function StoryActionTray({
  board,
  canUseOperatorTools,
  section,
  onSectionChange,
  stage,
  onDone,
  onDismiss,
  onSent,
  toggleRef,
}: StoryActionTrayProps) {
  const composer = board.actionComposerProps
  const controls = board.gameplayControls
  const hintPrefix = useId()
  const trayRef = useRef<HTMLDivElement | null>(null)
  const navRef = useEdgeFades<HTMLElement>()
  const [spellId, setSpellId] = useState(
    () => (controls.spells.find((spell) => spell.available) ?? controls.spells[0])?.id ?? '',
  )
  const [spellTargets, setSpellTargets] = useState<string[]>([])
  const [freeformSpell, setFreeformSpell] = useState(false)
  const freeformInputRef = useRef<HTMLInputElement | null>(null)
  const [capabilityId, setCapabilityId] = useState(
    () => (controls.capabilities.find((capability) => capability.available) ?? controls.capabilities[0])?.id ?? '',
  )
  const [capabilityTargets, setCapabilityTargets] = useState<Record<string, string>>({})
  const [capabilityAmount, setCapabilityAmount] = useState('1')
  const [itemKey, setItemKey] = useState('')
  const [itemQuantity, setItemQuantityDraft] = useState(1)
  const [givingItem, setGivingItem] = useState(false)
  const [interactionType, setInteractionType] = useState<InteractionType>(composer.selectedInteractionType)

  const characterName = composer.selectedCharacterName ?? 'I'
  const sendPending = board.sendPending
  const mechanicsBusy =
    board.sendPending ||
    board.streamingTurnActive ||
    Boolean(board.pendingRollNotice) ||
    !composer.selectedPlayerHasTurn ||
    !controls.actorId
  const mechanicsBusyReason = !controls.actorId
    ? 'Choose a character to use these actions.'
    : board.sendPending || board.streamingTurnActive
      ? 'Wait for the Dungeon Master to finish.'
      : board.pendingRollNotice
        ? 'Finish the pending roll first.'
        : !composer.selectedPlayerHasTurn
          ? 'It is not your turn yet.'
          : ''
  const worldActionReason = board.sendPending || board.streamingTurnActive
    ? 'Wait for the Dungeon Master to finish.'
    : board.worldState.combat.active
      ? 'Not while combat is underway.'
      : !composer.selectedPlayerId || !composer.selectedCharacterName
        ? 'Choose a character first.'
        : ''

  const sections: TraySection[] = [
    'speak',
    'roll',
    'magic',
    ...(controls.capabilities.length ? ['abilities' as const] : []),
    ...(composer.itemOptions.length ? ['items' as const] : []),
    ...(composer.interactionTargets.length ? ['interact' as const] : []),
    'scene',
    'emote',
    'ooc',
    ...(canUseOperatorTools ? ['director' as const] : []),
  ]
  const contentSections = sections.filter((candidate) => !SECTION_META[candidate].mode)
  const activeSection = contentSections.includes(section) ? section : contentSections[0]
  const preloadDice = composer.preloadDiceRollDialog

  useEffect(() => {
    trayRef.current?.querySelector<HTMLElement>('[data-active-section="true"]')?.focus()
    // Focus moves into the tray once, when it opens.
  }, [])

  useEffect(() => {
    const handlePointerDown = (event: PointerEvent) => {
      const target = event.target as Node | null
      if (!target || trayRef.current?.contains(target) || toggleRef.current?.contains(target)) return
      onDismiss()
    }
    document.addEventListener('pointerdown', handlePointerDown)
    return () => document.removeEventListener('pointerdown', handlePointerDown)
  }, [onDismiss, toggleRef])

  useEffect(() => {
    if (activeSection === 'roll') preloadDice()
  }, [activeSection, preloadDice])

  useEffect(() => {
    if (freeformSpell) freeformInputRef.current?.focus()
  }, [freeformSpell])

  const handleKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    if (event.key !== 'Escape') return
    event.preventDefault()
    event.stopPropagation()
    onDismiss()
  }

  const submitTyped = (submission: ActionSubmission) => {
    if (!composer.submitAction(submission.message, submission.intent)) return false
    onSent()
    onDone()
    return true
  }

  const chooseSection = (next: TraySection) => {
    const mode = SECTION_META[next].mode
    if (mode) {
      composer.applyComposerMode(mode)
      onDone()
      return
    }
    onSectionChange(next)
  }

  // aria-disabled (not `disabled`) keeps a blocked choice focusable; its reason
  // is kept outside the button so it describes the choice rather than renaming it.
  const hints: Array<[string, string]> = []
  const blockedProps = (reason: string, key: string) => {
    const hintId = `${hintPrefix}-${key}`
    if (reason) hints.push([hintId, reason])
    return {
      'aria-disabled': reason ? true : undefined,
      'aria-describedby': reason ? hintId : undefined,
      title: reason || undefined,
      'data-blocked': reason ? true : undefined,
    }
  }

  /* ---- Roll ---- */
  const renderRoll = () => {
    const dexterity = composer.abilityOptions.find((ability) => ability.key === 'dexterity')
    const initiativeModifier = dexterity?.modifier && dexterity.modifier !== '—' ? ` ${dexterity.modifier}` : ''
    return (
      <>
        <TrayGroup label="Die">
          {TRAY_DICE.map((die) => (
            <button
              key={die}
              type="button"
              className="story-chip"
              aria-pressed={composer.selectedDie === die}
              onClick={() => composer.updateSelectedDie(die)}
            >
              {die.toUpperCase()}
            </button>
          ))}
        </TrayGroup>
        <TrayGroup label="Check">
          <button
            type="button"
            className="story-chip"
            aria-pressed={composer.selectedAbilityKey === PLAIN_ROLL_ABILITY_KEY}
            onClick={() => composer.updateRollAbilityKey(PLAIN_ROLL_ABILITY_KEY)}
          >
            Plain
          </button>
          <button
            type="button"
            className="story-chip"
            aria-pressed={composer.selectedAbilityKey === INITIATIVE_ROLL_ABILITY_KEY}
            onClick={() => composer.updateRollAbilityKey(INITIATIVE_ROLL_ABILITY_KEY)}
          >
            Initiative{initiativeModifier}
          </button>
          {composer.abilityOptions.map((ability) => (
            <button
              key={ability.key}
              type="button"
              className="story-chip"
              aria-pressed={composer.selectedAbilityKey === ability.key}
              onClick={() => composer.updateRollAbilityKey(ability.key)}
            >
              {ability.label}
              {ability.modifier && ability.modifier !== '—' ? ` ${ability.modifier}` : ''}
            </button>
          ))}
        </TrayGroup>
        <TrayGroup label="Mode">
          {ROLL_MODES.map((option) => (
            <button
              key={option.value}
              type="button"
              className="story-chip"
              aria-pressed={composer.rollMode === option.value}
              onClick={() => composer.setRollMode(option.value)}
            >
              {option.label}
            </button>
          ))}
        </TrayGroup>
        <label className="story-field">
          <span className="story-label">Reason</span>
          <input
            type="text"
            value={composer.rollReason}
            maxLength={120}
            placeholder="Stealth check"
            onChange={(event) => composer.setRollReason(event.target.value)}
          />
        </label>
        <div className="story-tray-commit">
          <button
            type="button"
            className="story-primary"
            disabled={sendPending}
            onMouseEnter={composer.preloadDiceRollDialog}
            onClick={() => {
              composer.startDiceRoll()
              onDone()
            }}
          >
            <Dices size={18} aria-hidden="true" />
            Roll dice
          </button>
        </div>
      </>
    )
  }

  /* ---- Magic ---- */
  const castSpell = (spell: GameplaySpellControl, targetIds: string[]) => {
    if (submitTyped(spellCastSubmission(controls.actorName, spell, targetIds))) setSpellTargets([])
  }

  const renderSpellDetail = (spell: GameplaySpellControl) => {
    const eligible = new Set(spell.targets.filter((target) => target.available).map((target) => target.id))
    const chosen = spellTargets.filter((id) => eligible.has(id)).slice(0, spell.maxTargets)
    const countValid = chosen.length >= spell.minTargets && chosen.length <= spell.maxTargets
    const castReason = mechanicsBusyReason || spell.reason ||
      (countValid ? '' : spell.minTargets === spell.maxTargets
        ? `Choose ${spell.minTargets} ${spell.minTargets === 1 ? 'target' : 'targets'}.`
        : `Choose ${spell.minTargets} to ${spell.maxTargets} targets.`)
    const toggleTarget = (target: GameplayTarget) => {
      setSpellTargets((current) => {
        const valid = current.filter((id) => eligible.has(id) && id !== target.id)
        if (current.includes(target.id)) return valid
        if (spell.maxTargets === 1) return [target.id]
        return [...valid, target.id].slice(0, spell.maxTargets)
      })
    }
    return (
      <div className="story-tray-detail">
        {spell.maxTargets > 0 ? (
          <div className="story-tray-group" role="group" aria-label={`${spell.name} targets`}>
            <span className="story-label" aria-hidden="true">
              {spell.maxTargets > 1 ? `Targets · ${chosen.length} of ${spell.maxTargets}` : 'Target'}
            </span>
            <div className="story-chip-row">
              {spell.targets.map((target) => {
                const reason = target.available ? '' : target.reason || 'Not a legal target.'
                const key = `spell-target-${target.id}`
                return (
                  <button
                    key={target.id}
                    type="button"
                    className="story-chip"
                    aria-pressed={chosen.includes(target.id)}
                    {...blockedProps(reason, key)}
                    onClick={() => {
                      if (!reason) toggleTarget(target)
                    }}
                  >
                    {targetChipLabel(target)}
                  </button>
                )
              })}
            </div>
          </div>
        ) : null}
        <div className="story-tray-commit">
          <button
            type="button"
            className="story-primary"
            disabled={mechanicsBusy || !spell.available || !countValid}
            title={castReason || undefined}
            onClick={() => castSpell(spell, chosen)}
          >
            Cast {spell.name}
          </button>
          {castReason ? <span className="story-tray-reason">{castReason}</span> : null}
        </div>
      </div>
    )
  }

  const renderMagic = () => {
    if (freeformSpell) {
      return (
        <div className="story-tray-freeform">
          <label className="story-field">
            <span className="story-label">Spell</span>
            <input
              type="text"
              value={composer.spellName}
              maxLength={80}
              placeholder="Name the spell"
              ref={freeformInputRef}
              onChange={(event) => composer.updateSpellName(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === 'Enter') {
                  event.preventDefault()
                  onDone()
                }
              }}
            />
          </label>
          <p className="story-tray-note">The Dungeon Master will adjudicate this one.</p>
          <div className="story-tray-commit">
            <button type="button" className="story-primary" onClick={onDone}>
              Describe the casting
            </button>
            <button type="button" className="story-link" onClick={() => setFreeformSpell(false)}>
              Back to known spells
            </button>
          </div>
        </div>
      )
    }
    return (
      <>
        {controls.spells.length ? (
          <ul className="story-tray-list" aria-label="Spells">
            {controls.spells.map((spell) => {
              const selected = spell.id === spellId
              const meta = [ordinalLevel(spell.level), spell.resourceLabel].filter(Boolean).join(' · ')
              return (
                <li key={spell.id} data-selected={selected || undefined}>
                  <button
                    type="button"
                    className="story-tray-row"
                    aria-expanded={selected}
                    data-unavailable={spell.available ? undefined : true}
                    onClick={() => {
                      setSpellId(selected ? '' : spell.id)
                      setSpellTargets([])
                    }}
                  >
                    <span className="story-tray-row-name">{spell.name}</span>
                    <span className="story-tray-row-meta">
                      {meta}
                      {spell.concentration ? <span className="story-tag">conc.</span> : null}
                    </span>
                    {spell.reason ? <span className="story-tray-row-reason">{spell.reason}</span> : null}
                  </button>
                  {selected ? renderSpellDetail(spell) : null}
                </li>
              )
            })}
          </ul>
        ) : (
          <p className="story-tray-note">No spells the rules engine resolves are ready.</p>
        )}
        <button
          type="button"
          className="story-link story-tray-aside"
          onClick={() => {
            composer.applyComposerMode('spell')
            setFreeformSpell(true)
          }}
        >
          Cast something else…
        </button>
      </>
    )
  }

  /* ---- Abilities ---- */
  const renderCapabilityDetail = (capability: GameplayCapabilityControl) => {
    const target = capabilityTarget(capability, capabilityTargets[capability.id])
    const healing = capability.effectType === 'healing_pool'
      ? selectedHealingAmount(capability, target, capabilityAmount)
      : null
    const useReason = mechanicsBusyReason || capability.reason || (target ? '' : 'No legal target is available.')
    return (
      <div className="story-tray-detail">
        {capability.targetPolicy !== 'self' && capability.targets.length ? (
          <div className="story-tray-group" role="group" aria-label={`${capability.name} target`}>
            <span className="story-label" aria-hidden="true">Target</span>
            <div className="story-chip-row">
              {capability.targets.map((candidate) => {
                const reason = candidate.available ? '' : candidate.reason || 'Not a legal target.'
                const key = `capability-target-${capability.id}-${candidate.id}`
                return (
                  <button
                    key={candidate.id}
                    type="button"
                    className="story-chip"
                    aria-pressed={target?.id === candidate.id}
                    {...blockedProps(reason, key)}
                    onClick={() => {
                      if (!reason) {
                        setCapabilityTargets((current) => ({ ...current, [capability.id]: candidate.id }))
                      }
                    }}
                  >
                    {targetChipLabel(candidate)}
                  </button>
                )
              })}
            </div>
          </div>
        ) : null}
        {healing ? (
          <label className="story-field story-field-number">
            <span className="story-label">Hit points</span>
            <input
              type="number"
              min={1}
              max={healing.maximum}
              value={capabilityAmount}
              onChange={(event) => setCapabilityAmount(event.target.value)}
            />
          </label>
        ) : null}
        <div className="story-tray-commit">
          <button
            type="button"
            className="story-primary"
            disabled={mechanicsBusy || !capability.available || !target}
            title={useReason || undefined}
            onClick={() => {
              if (target) {
                submitTyped(capabilitySubmission(controls.actorName, capability, target, capabilityAmount))
              }
            }}
          >
            Use {capability.name}
          </button>
          {useReason ? <span className="story-tray-reason">{useReason}</span> : null}
        </div>
      </div>
    )
  }

  const renderAbilities = () => (
    <ul className="story-tray-list" aria-label="Abilities">
      {controls.capabilities.map((capability) => {
        const selected = capability.id === capabilityId
        const meta = [
          sentenceCase(capability.actionEconomy),
          capability.refreshesOn || 'long rest',
        ].filter(Boolean).join(' · ')
        return (
          <li key={capability.id} data-selected={selected || undefined}>
            <button
              type="button"
              className="story-tray-row"
              aria-expanded={selected}
              data-unavailable={capability.available ? undefined : true}
              onClick={() => {
                setCapabilityId(selected ? '' : capability.id)
                setCapabilityAmount('1')
              }}
            >
              <span className="story-tray-row-name">{capability.name}</span>
              <span className="story-tray-row-meta">
                <UsePips current={capability.current} max={capability.max} />
                {meta}
              </span>
              {capability.reason ? <span className="story-tray-row-reason">{capability.reason}</span> : null}
            </button>
            {selected ? renderCapabilityDetail(capability) : null}
          </li>
        )
      })}
    </ul>
  )

  /* ---- Items ---- */
  const selectedItemIndex = composer.itemOptions.findIndex(
    (item, index) => itemOptionSelectionKey(item, index) === itemKey,
  )
  const selectedItem = selectedItemIndex >= 0 ? composer.itemOptions[selectedItemIndex] : null
  const recipients = composer.activePlayers.filter((player) => player.id !== composer.selectedPlayerId)

  const submitItem = (item: ItemOption, index: number, action: InventoryAction, recipient?: ActivePlayer) => {
    const key = itemOptionSelectionKey(item, index)
    const quantity = String(itemQuantity)
    const draft = stripComposerCommand(composer.actionText).trim()
    const text = recipient
      ? `${itemActionText(characterName, action, item.name, '')} to ${playerName(recipient)}${draft ? `: ${draft}` : '.'}`
      : itemActionText(characterName, action, item.name, draft)
    composer.applyComposerMode('item')
    composer.updateSelectedInventoryAction(action)
    composer.setSelectedItemId(key)
    composer.setItemQuantity(quantity)
    composer.setActionText(text)
    stage(
      (current) => {
        const currentIndex = current.selectedItem ? current.itemOptions.indexOf(current.selectedItem) : -1
        return (
          current.composerMode === 'item' &&
          current.selectedInventoryAction === action &&
          currentIndex >= 0 &&
          itemOptionSelectionKey(current.itemOptions[currentIndex], currentIndex) === key &&
          current.itemQuantity === quantity &&
          current.actionText === text
        )
      },
      (current) => {
        if (current.submitAction()) onSent()
      },
    )
    onDone()
  }

  const renderItems = () => {
    const quantityMax = selectedItem ? Math.max(1, Number.parseInt(selectedItem.quantity, 10) || 1) : 1
    const itemActions: Array<{ action: InventoryAction; label: string }> = selectedItem
      ? [
          { action: 'use', label: 'Use' },
          ...(recipients.length ? [{ action: 'give' as const, label: 'Give' }] : []),
          { action: 'drop', label: 'Drop' },
          selectedItem.equipped ? { action: 'unequip', label: 'Unequip' } : { action: 'equip', label: 'Equip' },
        ]
      : []
    return (
      <ul className="story-tray-list" aria-label="Items">
        {composer.itemOptions.map((item, index) => {
          const key = itemOptionSelectionKey(item, index)
          const selected = key === itemKey
          const quantity = Number.parseInt(item.quantity, 10) || 1
          return (
            <li key={key} data-selected={selected || undefined}>
              <button
                type="button"
                className="story-tray-row"
                aria-expanded={selected}
                onClick={() => {
                  setItemKey(selected ? '' : key)
                  setItemQuantityDraft(1)
                  setGivingItem(false)
                }}
              >
                <span className="story-tray-row-name">
                  {item.name}
                  {quantity > 1 ? <span className="story-tray-count"> ×{quantity}</span> : null}
                </span>
                {item.equipped ? <span className="story-tray-row-meta">Equipped</span> : null}
              </button>
              {selected ? (
                <div className="story-tray-detail">
                  {quantityMax > 1 ? (
                    <label className="story-field story-field-number">
                      <span className="story-label">How many</span>
                      <input
                        type="number"
                        min={1}
                        max={quantityMax}
                        value={itemQuantity}
                        onChange={(event) => {
                          const next = Number.parseInt(event.target.value, 10)
                          setItemQuantityDraft(Number.isFinite(next) ? Math.max(1, Math.min(quantityMax, next)) : 1)
                        }}
                      />
                    </label>
                  ) : null}
                  {givingItem ? (
                    <TrayGroup label={`Give ${item.name} to`} heading="Give to">
                      {recipients.map((player) => (
                        <button
                          key={player.id}
                          type="button"
                          className="story-chip"
                          disabled={sendPending}
                          onClick={() => submitItem(item, index, 'give', player)}
                        >
                          {playerName(player)}
                        </button>
                      ))}
                      <button type="button" className="story-link" onClick={() => setGivingItem(false)}>
                        Back
                      </button>
                    </TrayGroup>
                  ) : (
                    <TrayGroup label={`${item.name} actions`} heading="">
                      {itemActions.map(({ action, label }) => (
                        <button
                          key={action}
                          type="button"
                          className="story-chip"
                          disabled={sendPending}
                          onClick={() => {
                            if (action === 'give') setGivingItem(true)
                            else submitItem(item, index, action)
                          }}
                        >
                          {label}
                        </button>
                      ))}
                    </TrayGroup>
                  )}
                </div>
              ) : null}
            </li>
          )
        })}
      </ul>
    )
  }

  /* ---- Interact ---- */
  const chooseInteraction = (target: InteractionTarget) => {
    const draft = stripComposerCommand(composer.actionText)
    composer.applyComposerMode('interact')
    composer.setSelectedInteractionType(interactionType)
    composer.setSelectedInteractionTargetId(interactionTargetId(target))
    composer.setActionText(interactionActionText(characterName, target, interactionType, draft))
    onDone()
  }

  const renderInteract = () => (
    <>
      <TrayGroup label="How">
        {INTERACTION_TYPE_OPTIONS.map((option) => (
          <button
            key={option.value}
            type="button"
            className="story-chip"
            aria-pressed={interactionType === option.value}
            onClick={() => setInteractionType(option.value)}
          >
            {option.label}
          </button>
        ))}
      </TrayGroup>
      <TrayGroup label="Who">
        {composer.interactionTargets.map((target) => (
          <button
            key={interactionTargetId(target)}
            type="button"
            className="story-chip"
            onClick={() => chooseInteraction(target)}
          >
            {target.character_name}
          </button>
        ))}
      </TrayGroup>
    </>
  )

  /* ---- Scene ---- */
  const renderScene = () => (
    <>
      {controls.interactables.map((object) => (
        <div key={object.id} className="story-tray-object">
          <div className="story-tray-object-heading">
            <span className="story-tray-row-name">{object.name}</span>
            {object.states.length ? <span className="story-tray-row-meta">{object.states.join(' · ')}</span> : null}
          </div>
          <div className="story-chip-row" role="group" aria-label={`${object.name} actions`}>
            {object.actions.map((action) => {
              const blocked = mechanicsBusy || !object.available || !action.available
              const reason = blocked
                ? mechanicsBusyReason || object.reason || action.reason || 'Not available right now.'
                : ''
              const key = `object-${object.id}-${action.id}`
              return (
                <button
                  key={action.id}
                  type="button"
                  className="story-chip"
                  {...blockedProps(reason, key)}
                  onClick={() => {
                    if (!reason) submitTyped(objectActionSubmission(controls.actorName, object, action))
                  }}
                >
                  {action.label}
                </button>
              )
            })}
          </div>
        </div>
      ))}
      <TrayGroup label="Rest">
        {(['short_rest', 'long_rest'] as const).map((restType) => {
          const key = `rest-${restType}`
          return (
            <button
              key={restType}
              type="button"
              className="story-chip"
              {...blockedProps(worldActionReason, key)}
              onClick={() => {
                if (!worldActionReason) {
                  submitTyped(restSubmission(composer.selectedCharacterName || 'I', restType))
                }
              }}
            >
              {restType === 'short_rest' ? 'Short rest' : 'Long rest'}
            </button>
          )
        })}
      </TrayGroup>
    </>
  )

  /* ---- Director ---- */
  const renderDirector = () => {
    const turnControl = composer.turnControl
    const activeTurnPlayerId =
      turnControl.activePlayerId ?? composer.selectedPlayerId ?? composer.activePlayers[0]?.id ?? null
    const conductorControlled = turnControl.source === 'auto' || turnControl.source === 'ai'
    const manualOverride = turnControl.source === 'manual' || turnControl.source === 'admin'
    const noPlayer = !composer.selectedPlayerId
    return (
      <>
        <TrayGroup label="Turn flow">
          <button
            type="button"
            className="story-chip"
            aria-pressed={conductorControlled}
            disabled={noPlayer}
            onClick={() => composer.updateTurnControl('free', null, 'auto')}
          >
            Auto
          </button>
          {TURN_MODES.map((option) => (
            <button
              key={option.value}
              type="button"
              className="story-chip"
              aria-pressed={manualOverride && turnControl.mode === option.value}
              disabled={noPlayer}
              onClick={() =>
                composer.updateTurnControl(
                  option.value,
                  option.value === 'free' ? null : activeTurnPlayerId,
                  'manual',
                )
              }
            >
              {option.label}
            </button>
          ))}
        </TrayGroup>
        {turnControl.mode !== 'free' && composer.activePlayers.length ? (
          <TrayGroup label="Active player">
            {composer.activePlayers.map((player) => (
              <button
                key={player.id}
                type="button"
                className="story-chip"
                aria-pressed={activeTurnPlayerId === player.id}
                disabled={noPlayer}
                onClick={() => composer.updateTurnControl(turnControl.mode, player.id)}
              >
                {playerName(player)}
              </button>
            ))}
          </TrayGroup>
        ) : null}
        {composer.adminToolsUnlocked ? (
          <div className="story-tray-admin">
            <label className="story-field">
              <span className="story-label">Admin passcode</span>
              <input
                type="password"
                value={composer.adminPasscode}
                autoComplete="off"
                onChange={(event) => composer.setAdminPasscode(event.target.value)}
              />
            </label>
            <button
              type="button"
              className="story-chip"
              aria-pressed={composer.composerMode === 'admin'}
              onClick={() => {
                composer.applyComposerMode('admin')
                onDone()
              }}
            >
              Admin mode
            </button>
          </div>
        ) : null}
        <button type="button" className="story-link story-tray-aside" onClick={composer.toggleAdminTools}>
          {composer.adminToolsUnlocked ? 'Lock admin tools' : 'Unlock admin tools'}
        </button>
      </>
    )
  }

  const content: Partial<Record<TraySection, () => ReactNode>> = {
    roll: renderRoll,
    magic: renderMagic,
    abilities: renderAbilities,
    items: renderItems,
    interact: renderInteract,
    scene: renderScene,
    director: renderDirector,
  }
  const renderContent = content[activeSection]

  return (
    <div
      ref={trayRef}
      className="story-tray"
      role="dialog"
      aria-label="Actions"
      onKeyDown={handleKeyDown}
    >
      <nav ref={navRef} className="story-tray-nav" aria-label="Kinds of action">
        {sections.map((candidate) => {
          const meta = SECTION_META[candidate]
          const Icon = meta.icon
          const isActive = candidate === activeSection
          const changesMode = Boolean(meta.mode)
          return (
            <button
              key={candidate}
              type="button"
              className="story-link story-tray-nav-link"
              aria-pressed={changesMode ? meta.mode !== 'action' && composer.composerMode === meta.mode : isActive}
              data-active-section={isActive || undefined}
              onClick={() => chooseSection(candidate)}
            >
              <Icon size={16} aria-hidden="true" />
              {meta.label}
            </button>
          )
        })}
      </nav>
      <div className="story-tray-content" aria-label={SECTION_META[activeSection].label} role="region">
        {renderContent ? renderContent() : null}
      </div>
      <div hidden>
        {hints.map(([id, reason]) => <span key={id} id={id}>{reason}</span>)}
      </div>
    </div>
  )
}
