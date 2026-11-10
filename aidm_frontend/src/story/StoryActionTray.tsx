import { Fragment, useEffect, useId, useMemo, useRef, useState, type KeyboardEvent, type ReactNode, type RefObject } from 'react'
import {
  Backpack,
  Clapperboard,
  Coins,
  Compass,
  Dices,
  Drama,
  MessageCircle,
  Minus,
  Plus,
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
  GameplayItemControl,
  GameplayNarratedSpell,
  GameplaySpellControl,
  GameplayTarget,
  GameplayTradeGood,
} from '../gameplayControlState'
import type { WorldMerchantStockEntry } from '../gameSelectors'
import type { SessionBoardProps } from '../SessionBoard'
import type { ActivePlayer, TurnControlMode } from '../types'
import {
  buySubmission,
  capabilitySubmission,
  capabilityTarget,
  itemUseSubmission,
  objectActionSubmission,
  restSubmission,
  selectedHealingAmount,
  sellSubmission,
  spellCastSubmission,
  type ActionSubmission,
} from './actionIntents'
import { CoinGlyph } from './glyphs'
import { coinLabel, copperValue, sellOffer, tradeLineKey, tradeRefusals, type Coins as CoinAmounts } from './trade'
import { useEdgeFades } from './useEdgeFades'

export type TraySection =
  | 'speak'
  | 'roll'
  | 'magic'
  | 'abilities'
  | 'items'
  | 'trade'
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
  /** The merchant the Trade section shows (the first open one when unset). */
  tradeMerchantId: string
  onTradeMerchantChange: (merchantId: string) => void
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
  trade: { label: 'Trade', icon: Coins },
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

const CASTING_TIME_LABELS: Record<string, string> = {
  action: 'Action',
  bonus_action: 'Bonus',
  reaction: 'Reaction',
}

function ordinalLevel(level: number) {
  if (level <= 0) return 'Cantrip'
  const suffix = level === 1 ? 'st' : level === 2 ? 'nd' : level === 3 ? 'rd' : 'th'
  return `${level}${suffix}`
}

function sentenceCase(value: string) {
  return value ? value.charAt(0).toUpperCase() + value.slice(1) : value
}

function castingTimeLabel(castingTime: string) {
  return CASTING_TIME_LABELS[castingTime] ?? sentenceCase(castingTime.replace(/_/g, ' '))
}

/** What one use of a capability spends: "1 Ki", or the pool itself when the amount is chosen. */
function capabilityCost(capability: GameplayCapabilityControl) {
  if (!capability.poolName) return ''
  return capability.poolCost != null ? `${capability.poolCost} ${capability.poolName}` : `${capability.poolName} pool`
}

/** Enough hit dice to heal the missing HP at the die's average, as many as remain. */
function suggestedHitDice(hitDice: { current: number; die: string }, missingHp: number) {
  const faces = Number.parseInt(hitDice.die.replace(/^d/, ''), 10)
  if (!Number.isFinite(faces) || missingHp <= 0) return 0
  return Math.min(hitDice.current, Math.ceil(missingHp / ((faces + 1) / 2)))
}

function Price({ coins }: { coins: CoinAmounts }) {
  return (
    <span className="story-price">
      <CoinGlyph />
      {coinLabel(coins)}
    </span>
  )
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

/** A class pool: its name, a pip per point (up to ten) and the count, "Ki ●●○ 2/3". */
function PoolPips({ name, current, max }: { name: string; current: number; max: number }) {
  return (
    <span className="story-pool" role="img" aria-label={`${name}: ${current} of ${max} left`}>
      <span className="story-pool-name" aria-hidden="true">{name}</span>
      <span className="story-pips" aria-hidden="true">
        {Array.from({ length: Math.min(max, 10) }, (_, index) => (
          <i key={index} data-spent={index >= current || undefined} />
        ))}
      </span>
      <span className="story-pool-count" aria-hidden="true">{current}/{max}</span>
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
  tradeMerchantId,
  onTradeMerchantChange,
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
  // Outside combat only the rules-backed spells that may be cast there are offered.
  const castableSpells = controls.inCombat
    ? controls.spells
    : controls.spells.filter((spell) => spell.outOfCombat === true)
  const narratedSpells = controls.narratedSpells ?? []
  const consumables = controls.items ?? []
  const merchants = board.worldState.merchants ?? []
  const [spellId, setSpellId] = useState(
    () => (castableSpells.find((spell) => spell.available) ?? castableSpells[0])?.id ?? '',
  )
  const [spellTargets, setSpellTargets] = useState<string[]>([])
  const [castLevels, setCastLevels] = useState<Record<string, number>>({})
  const [freeformSpell, setFreeformSpell] = useState(false)
  const freeformInputRef = useRef<HTMLInputElement | null>(null)
  const [capabilityId, setCapabilityId] = useState(
    () => (controls.capabilities.find((capability) => capability.available) ?? controls.capabilities[0])?.id ?? '',
  )
  const [capabilityTargets, setCapabilityTargets] = useState<Record<string, string>>({})
  const [capabilityAmount, setCapabilityAmount] = useState('1')
  const [consumableId, setConsumableId] = useState('')
  const [consumableTargets, setConsumableTargets] = useState<Record<string, string>>({})
  const [itemKey, setItemKey] = useState('')
  const [itemQuantity, setItemQuantityDraft] = useState(1)
  const [givingItem, setGivingItem] = useState(false)
  const [interactionType, setInteractionType] = useState<InteractionType>(composer.selectedInteractionType)
  const viewerHealth = board.activePlayers.find((player) => player.id === composer.selectedPlayerId)?.health ?? null
  const [hitDiceToSpend, setHitDiceToSpend] = useState(() =>
    controls.rest?.hitDice && viewerHealth
      ? suggestedHitDice(controls.rest.hitDice, viewerHealth.maxHp - viewerHealth.currentHp)
      : 0,
  )
  const { turnRows, currentResponseEntry } = board
  const refusals = useMemo(
    () => tradeRefusals(currentResponseEntry ? [...turnRows, currentResponseEntry] : turnRows),
    [turnRows, currentResponseEntry],
  )

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
    ...(composer.itemOptions.length || consumables.length ? ['items' as const] : []),
    ...(merchants.length ? ['trade' as const] : []),
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
  const castSpell = (spell: GameplaySpellControl, targetIds: string[], castLevel: number | undefined) => {
    if (submitTyped(spellCastSubmission(controls.actorName, spell, targetIds, castLevel))) setSpellTargets([])
  }

  const renderSpellDetail = (spell: GameplaySpellControl) => {
    const levelChoice = spell.castLevels.length > 1
    const castLevel = levelChoice && spell.castLevels.includes(castLevels[spell.id]) ? castLevels[spell.id] : spell.castLevels[0]
    // Upcasting may add targets (Magic Missile's darts, Scorching Ray's rays).
    const maxTargets = Math.min(
      20,
      spell.maxTargets + Math.max(0, (castLevel ?? spell.level) - spell.level) * (spell.upcastTargetsPerLevel ?? 0),
    )
    const eligible = new Set(spell.targets.filter((target) => target.available).map((target) => target.id))
    const chosen = spellTargets.filter((id) => eligible.has(id)).slice(0, maxTargets)
    const countValid = chosen.length >= spell.minTargets && chosen.length <= maxTargets
    const castReason = mechanicsBusyReason || spell.reason ||
      (countValid ? '' : spell.minTargets === maxTargets
        ? `Choose ${spell.minTargets} ${spell.minTargets === 1 ? 'target' : 'targets'}.`
        : `Choose ${spell.minTargets} to ${maxTargets} targets.`)
    const toggleTarget = (target: GameplayTarget) => {
      setSpellTargets((current) => {
        const valid = current.filter((id) => eligible.has(id) && id !== target.id)
        if (current.includes(target.id)) return valid
        if (maxTargets === 1) return [target.id]
        return [...valid, target.id].slice(0, maxTargets)
      })
    }
    return (
      <div className="story-tray-detail">
        {maxTargets > 0 ? (
          <div className="story-tray-group" role="group" aria-label={`${spell.name} targets`}>
            <span className="story-label" aria-hidden="true">
              {maxTargets > 1 ? `Targets · ${chosen.length} of ${maxTargets}` : 'Target'}
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
          {levelChoice ? (
            <span className="story-level-picker" role="radiogroup" aria-label="Cast level">
              {spell.castLevels.map((level, index) => (
                <Fragment key={level}>
                  {index ? <span className="story-level-dot" aria-hidden="true">·</span> : null}
                  <button
                    type="button"
                    role="radio"
                    className="story-link"
                    aria-checked={level === castLevel}
                    onClick={() => setCastLevels((current) => ({ ...current, [spell.id]: level }))}
                  >
                    {ordinalLevel(level)}
                  </button>
                </Fragment>
              ))}
            </span>
          ) : null}
          <button
            type="button"
            className="story-primary"
            disabled={mechanicsBusy || !spell.available || !countValid}
            title={castReason || undefined}
            onClick={() => castSpell(spell, chosen, levelChoice ? castLevel : undefined)}
          >
            Cast {spell.name}
          </button>
          {castReason ? <span className="story-tray-reason">{castReason}</span> : null}
        </div>
      </div>
    )
  }

  const chooseNarratedSpell = (spell: GameplayNarratedSpell) => {
    composer.applyComposerMode('spell')
    composer.updateSpellName(spell.name)
    setFreeformSpell(true)
  }

  const spellMeta = (castingTime: string, resourceLabel: string, level: number) =>
    [castingTimeLabel(castingTime), level > 0 ? resourceLabel : 'Cantrip'].filter(Boolean).join(' · ')

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
        {castableSpells.length ? (
          <ul className="story-tray-list" aria-label="Spells">
            {castableSpells.map((spell) => {
              const selected = spell.id === spellId
              const summary = spell.effectSummary || spell.effectLabel
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
                      {spellMeta(spell.castingTime, spell.resourceLabel, spell.level)}
                      {spell.concentration ? <span className="story-tag">conc.</span> : null}
                    </span>
                    {summary ? <span className="story-tray-row-summary">{summary}</span> : null}
                    {spell.reason ? <span className="story-tray-row-reason">{spell.reason}</span> : null}
                  </button>
                  {selected ? renderSpellDetail(spell) : null}
                </li>
              )
            })}
          </ul>
        ) : (
          <p className="story-tray-note">
            {controls.inCombat ? 'No spells the rules engine resolves are ready.' : 'No spells the rules engine resolves can be cast here.'}
          </p>
        )}
        {narratedSpells.length ? (
          <div className="story-tray-group story-narrated-magic" role="group" aria-label="Narrated magic">
            <span className="story-label" aria-hidden="true">Narrated magic</span>
            <p className="story-tray-note">The Dungeon Master adjudicates these.</p>
            <ul className="story-tray-list" aria-label="Narrated spells">
              {narratedSpells.map((spell) => (
                <li key={spell.id}>
                  <button
                    type="button"
                    className="story-tray-row"
                    data-unavailable={spell.available ? undefined : true}
                    onClick={() => chooseNarratedSpell(spell)}
                  >
                    <span className="story-tray-row-name">{spell.name}</span>
                    <span className="story-tray-row-meta">
                      {spellMeta(spell.castingTime, spell.resourceLabel, spell.level)}
                      {spell.concentration ? <span className="story-tag">conc.</span> : null}
                    </span>
                    {spell.effectSummary ? <span className="story-tray-row-summary">{spell.effectSummary}</span> : null}
                    {spell.reason ? <span className="story-tray-row-reason">{spell.reason}</span> : null}
                  </button>
                </li>
              ))}
            </ul>
          </div>
        ) : null}
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

  const renderAbilities = () => {
    const pools = [
      ...new Map(
        controls.capabilities
          .filter((capability) => capability.poolId && capability.poolName)
          .map((capability) => [capability.poolId, capability] as const),
      ).values(),
    ]
    return (
      <>
        {pools.length ? (
          <div className="story-pools" role="group" aria-label="Class pools">
            {pools.map((pool) => (
              <PoolPips key={pool.poolId} name={pool.poolName ?? ''} current={pool.current} max={pool.max} />
            ))}
          </div>
        ) : null}
        <ul className="story-tray-list" aria-label="Abilities">
          {controls.capabilities.map((capability) => {
            const selected = capability.id === capabilityId
            const meta = [sentenceCase(capability.actionEconomy), capabilityCost(capability)].filter(Boolean).join(' · ')
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
                  <span className="story-tray-row-meta">{meta}</span>
                  {capability.description ? (
                    <span className="story-tray-row-summary">{capability.description}</span>
                  ) : null}
                  {capability.reason ? <span className="story-tray-row-reason">{capability.reason}</span> : null}
                </button>
                {selected ? renderCapabilityDetail(capability) : null}
              </li>
            )
          })}
        </ul>
      </>
    )
  }

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

  const renderConsumable = (item: GameplayItemControl) => {
    const selected = item.id === consumableId
    const chosen = item.targets.find((target) => target.id === consumableTargets[item.id] && target.available)
    const target = chosen ?? item.targets.find((candidate) => candidate.available) ?? null
    const useReason = mechanicsBusyReason || item.reason || (target ? '' : 'No legal target is available.')
    return (
      <li key={item.id} data-selected={selected || undefined}>
        <button
          type="button"
          className="story-tray-row"
          aria-expanded={selected}
          data-unavailable={item.available ? undefined : true}
          onClick={() => setConsumableId(selected ? '' : item.id)}
        >
          <span className="story-tray-row-name">
            {item.name}
            {item.quantity > 1 ? <span className="story-tray-count"> ×{item.quantity}</span> : null}
          </span>
          <span className="story-tray-row-meta">{sentenceCase(item.cost)}</span>
          {item.effectSummary ? <span className="story-tray-row-summary">{item.effectSummary}</span> : null}
          {item.reason ? <span className="story-tray-row-reason">{item.reason}</span> : null}
        </button>
        {selected ? (
          <div className="story-tray-detail">
            {item.targets.length > 1 ? (
              <TrayGroup label={`${item.name} target`} heading="Target">
                {item.targets.map((candidate) => {
                  const reason = candidate.available ? '' : candidate.reason || 'Not a legal target.'
                  return (
                    <button
                      key={candidate.id}
                      type="button"
                      className="story-chip"
                      aria-pressed={target?.id === candidate.id}
                      {...blockedProps(reason, `item-target-${item.id}-${candidate.id}`)}
                      onClick={() => {
                        if (!reason) setConsumableTargets((current) => ({ ...current, [item.id]: candidate.id }))
                      }}
                    >
                      {targetChipLabel(candidate)}
                    </button>
                  )
                })}
              </TrayGroup>
            ) : null}
            <div className="story-tray-commit">
              <button
                type="button"
                className="story-primary"
                disabled={mechanicsBusy || !item.available || !target}
                title={useReason || undefined}
                onClick={() => {
                  if (target) submitTyped(itemUseSubmission(controls.actorName, item, target))
                }}
              >
                Use{' '}<span className="story-sr-only">{item.name}</span>
              </button>
              {useReason ? <span className="story-tray-reason">{useReason}</span> : null}
            </div>
          </div>
        ) : null}
      </li>
    )
  }

  const renderItems = () => {
    const consumableIds = new Set(consumables.map((item) => item.id))
    const otherItems = composer.itemOptions
      .map((item, index) => ({ item, index }))
      .filter(({ item }) => !item.id || !consumableIds.has(item.id))
    return (
      <>
        {consumables.length ? (
          <ul className="story-tray-list" aria-label="Consumables">
            {consumables.map(renderConsumable)}
          </ul>
        ) : null}
        {consumables.length && otherItems.length ? (
          <span className="story-label story-tray-subhead" aria-hidden="true">Other items</span>
        ) : null}
        {otherItems.length ? renderOtherItems(otherItems) : null}
      </>
    )
  }

  /** Items without a rules-engine effect: the classic item-mode path. */
  const renderOtherItems = (otherItems: Array<{ item: ItemOption; index: number }>) => {
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
      <ul className="story-tray-list" aria-label={consumables.length ? 'Other items' : 'Items'}>
        {otherItems.map(({ item, index }) => {
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
      {renderRest()}
    </>
  )

  const renderRest = () => {
    const rest = controls.rest
    const hitDice = rest?.hitDice ?? null
    const spend = hitDice ? Math.max(0, Math.min(hitDice.current, hitDiceToSpend)) : 0
    const shortReason = worldActionReason || rest?.shortReason || (rest ? '' : 'Choose a character first.')
    const longReason = worldActionReason || rest?.longReason || (rest ? '' : 'Choose a character first.')
    const actorName = composer.selectedCharacterName || 'I'
    return (
      <div className="story-tray-group story-rest" role="group" aria-label="Rest">
        <span className="story-label" aria-hidden="true">Rest</span>
        <div className="story-rest-option">
          <div className="story-rest-line">
            <button
              type="button"
              className="story-chip"
              disabled={Boolean(shortReason)}
              onClick={() => submitTyped(restSubmission(actorName, 'short_rest', hitDice ? spend : undefined))}
            >
              Short rest
            </button>
            {hitDice ? (
              <span className="story-stepper" role="group" aria-label="Hit dice to spend">
                Spend
                <button
                  type="button"
                  className="story-icon-button story-stepper-button"
                  aria-label="Spend one fewer hit die"
                  disabled={spend <= 0}
                  onClick={() => setHitDiceToSpend(spend - 1)}
                >
                  <Minus size={14} aria-hidden="true" />
                </button>
                <output className="story-stepper-value" aria-live="polite">{spend}</output>
                <button
                  type="button"
                  className="story-icon-button story-stepper-button"
                  aria-label="Spend one more hit die"
                  disabled={spend >= hitDice.current}
                  onClick={() => setHitDiceToSpend(spend + 1)}
                >
                  <Plus size={14} aria-hidden="true" />
                </button>
                of {hitDice.current} hit {hitDice.current === 1 ? 'die' : 'dice'} ({hitDice.die})
              </span>
            ) : null}
          </div>
          {shortReason ? <p className="story-tray-reason">{shortReason}</p> : null}
        </div>
        <div className="story-rest-option">
          <button
            type="button"
            className="story-chip"
            disabled={Boolean(longReason)}
            onClick={() => submitTyped(restSubmission(actorName, 'long_rest'))}
          >
            Long rest
          </button>
          {longReason ? <p className="story-tray-reason">{longReason}</p> : null}
        </div>
      </div>
    )
  }

  /* ---- Trade ---- */
  const trade = (submission: ActionSubmission) => {
    if (composer.submitAction(submission.message, submission.intent)) onSent()
  }

  const renderTrade = () => {
    const merchant = merchants.find((candidate) => candidate.id === tradeMerchantId) ?? merchants[0]
    if (!merchant) return null
    const purse = controls.currency ?? {}
    const purseValue = copperValue(purse)
    const goods = (controls.tradeGoods ?? [])
      .map((good) => ({ good, offer: sellOffer(good, merchant) }))
      .filter((entry): entry is { good: GameplayTradeGood; offer: CoinAmounts } => entry.offer !== null)
    const buyBlock = (entry: WorldMerchantStockEntry) =>
      entry.quantity === 0 ? 'Sold out.' : copperValue(entry.price) > purseValue ? 'You cannot afford this.' : ''
    return (
      <div className="story-trade">
        {merchants.length > 1 ? (
          <TrayGroup label="Merchant" heading="">
            {merchants.map((candidate) => (
              <button
                key={candidate.id}
                type="button"
                className="story-chip"
                aria-pressed={candidate.id === merchant.id}
                onClick={() => onTradeMerchantChange(candidate.id)}
              >
                {candidate.name}
              </button>
            ))}
          </TrayGroup>
        ) : null}
        <p className="story-trade-head">
          <span className="story-trade-merchant">{merchant.name}</span>
          <span className="story-trade-purse">
            <span className="story-label">Your coin</span>
            <Price coins={purse} />
          </span>
        </p>
        {mechanicsBusyReason ? <p className="story-tray-reason">{mechanicsBusyReason}</p> : null}
        {merchant.stock.length ? (
          <section className="story-trade-group" aria-label="Buy">
            <span className="story-label" aria-hidden="true">Buy</span>
            <ul className="story-trade-list">
              {merchant.stock.map((entry) => {
                const blocked = buyBlock(entry)
                const refusal = refusals.get(tradeLineKey('buy', merchant.id, entry.stockId))
                return (
                  <li key={entry.stockId}>
                    <span className="story-trade-name">{entry.name}</span>
                    <Price coins={entry.price} />
                    <span className="story-trade-stock">{entry.quantity === null ? '' : `${entry.quantity} left`}</span>
                    <button
                      type="button"
                      className="story-link story-trade-action"
                      disabled={mechanicsBusy || Boolean(blocked)}
                      onClick={() => trade(buySubmission(controls.actorName, merchant, entry))}
                    >
                      Buy{' '}<span className="story-sr-only">{entry.name}</span>
                    </button>
                    {refusal || blocked ? (
                      <p className="story-trade-note" data-refused={refusal ? '' : undefined}>{refusal || blocked}</p>
                    ) : null}
                  </li>
                )
              })}
            </ul>
          </section>
        ) : null}
        {goods.length ? (
          <section className="story-trade-group" aria-label="Sell">
            <span className="story-label" aria-hidden="true">Sell</span>
            <ul className="story-trade-list">
              {goods.map(({ good, offer }) => {
                const refusal = refusals.get(tradeLineKey('sell', merchant.id, good.id))
                return (
                  <li key={good.id}>
                    <span className="story-trade-name">
                      {good.name}
                      {good.quantity > 1 ? <span className="story-tray-count"> ×{good.quantity}</span> : null}
                    </span>
                    <Price coins={offer} />
                    <span className="story-trade-stock" />
                    <button
                      type="button"
                      className="story-link story-trade-action"
                      disabled={mechanicsBusy}
                      onClick={() => trade(sellSubmission(controls.actorName, merchant, good))}
                    >
                      Sell{' '}<span className="story-sr-only">{good.name}</span>
                    </button>
                    {refusal ? <p className="story-trade-note" data-refused="">{refusal}</p> : null}
                  </li>
                )
              })}
            </ul>
          </section>
        ) : null}
      </div>
    )
  }

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
    trade: renderTrade,
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
