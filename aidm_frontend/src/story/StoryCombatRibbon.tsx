import { Fragment, useCallback, useEffect, useId, useLayoutEffect, useRef, useState } from 'react'
import { ChevronLeft, Footprints, Hand, MessageCircle, type LucideIcon } from 'lucide-react'
import type { ActionIntent } from '../gameActions'
import {
  type CombatActionCost,
  type CombatLegalAction,
  type CombatLegalActionBundle,
  type CombatLegalTarget,
  type CombatParticipantSummary,
  type CombatStatePanel,
} from '../gameSelectors'
import type {
  GameplayControlState,
  GameplayInteractableControl,
  GameplayObjectAction,
  GameplaySpellControl,
} from '../gameplayControlState'
import type { ActivePlayer } from '../types'
import { combatActionSubmission, objectActionSubmission, spellCastSubmission } from './actionIntents'
import { combatActionLabel, combatTargetLabel, distance, enemyDetail, offeredTargets } from './combatLabels'
import { useEdgeFades } from './useEdgeFades'
import { heroVitalityOf, VITALITY_CONDITIONS, type HeroVitality } from './vitalityLabels'

const RESOLVING_REASON = 'Actions are unavailable while the current turn resolves.'
const MAX_TELEGRAPHS = 2
const MAX_CONDITION_DOTS = 2
/** How high an engagement arc rises above the discs it links. */
const ARC_RISE = 11
const MAX_ATTACKS = 2
const MAX_SPELLS = 2
const MAX_ABILITIES = 3
const MAX_STORY_OBJECT_ACTIONS = 2
/** Features used only at rest (Arcane Recovery) never appear on the ribbon. */
const REST_ONLY_EFFECTS = new Set(['restore_slots'])
const SPELL_COSTS: Record<string, string> = { action: 'A', bonus_action: 'B', reaction: 'R' }
/** The fight's manoeuvres, in ribbon order, each with the glyph the dock gives its kind of deed. */
const MANEUVER_GLYPHS: ReadonlyArray<[string, LucideIcon]> = [
  ['grapple', Hand],
  ['shove', Footprints],
  ['subdue', Hand],
  ['demand_surrender', MessageCircle],
]

/** Where the action tray opens from the ribbon: Magic (at one spell) or the full Combat list. */
export type RibbonTraySection = 'magic' | 'combat'

type StoryCombatRibbonProps = {
  combat: CombatStatePanel
  playerId: number | null
  activePlayers: ActivePlayer[]
  /** The viewer's spells and scene objects. */
  controls: GameplayControlState
  /** Names of the spells the hero cast in this fight, most recent first. */
  recentSpellNames: string[]
  disabled: boolean
  /** The viewer's hero is down or out of the fight: the roster shows, never their choices. */
  down?: boolean
  submitAction: (overrideMessage?: string, overrideIntent?: ActionIntent) => boolean
  onOpenTray: (section: RibbonTraySection, spellId?: string) => void
}

/** One ribbon choice: a server combat action, a castable spell or an authored scene action. */
type RibbonChoice =
  | { kind: 'action'; key: string; label: string; cost: string; action: CombatLegalAction; glyph?: LucideIcon }
  | { kind: 'spell'; key: string; label: string; cost: string; spell: GameplaySpellControl }
  | { kind: 'object'; key: string; label: string; cost: string; object: GameplayInteractableControl; action: GameplayObjectAction }
  | { kind: 'tray'; key: string; label: string; cost: string; section: RibbonTraySection }

type Targeting =
  | { kind: 'action'; action: CombatLegalAction }
  | { kind: 'spell'; spell: GameplaySpellControl }

function isEndTurn(action: CombatLegalAction) {
  return action.type === 'end_turn' || action.id === 'combat.end_turn'
}

function isClassAbility(action: CombatLegalAction) {
  return (action.type === 'class_feature' || action.id.startsWith('class.')) &&
    !REST_ONLY_EFFECTS.has(action.capabilityEffect ?? '')
}

function isHealingPotion(action: CombatLegalAction) {
  return action.id.startsWith('item.use.') && /\bhealing\b/i.test(`${action.label} ${action.description}`)
}

/** What an action spends: A(ction), B(onus action), M(ovement), R(eaction). */
function costLetters(cost: CombatActionCost | undefined) {
  if (!cost) return ''
  return [cost.action ? 'A' : '', cost.bonusAction ? 'B' : '', cost.movement ? 'M' : '', cost.reaction ? 'R' : ''].join('')
}

/** A spell the ribbon can aim itself: one target, one cast level. Anything else opens the tray. */
function aimsInRibbon(spell: GameplaySpellControl) {
  return spell.maxTargets === 1 && spell.castLevels.length <= 1
}

/** The one target a choice can be used on now, when there is exactly one; the picker is for two or more. */
function onlyLegalTarget<Target extends { available: boolean }>(targets: Target[]) {
  const legal = targets.filter((target) => target.available)
  return legal.length === 1 ? legal[0] : null
}

/** Below maximum HP, from the live roster or else the server's exact HP for the viewer's combatant. */
function viewerHurt(player: ActivePlayer | undefined, participant: CombatParticipantSummary | undefined) {
  const hp = player?.health && player.health.maxHp > 0
    ? { current: player.health.currentHp, max: player.health.maxHp }
    : participant?.hp ?? null
  return hp ? hp.current < hp.max : false
}

function combatantLabel(participant: CombatParticipantSummary) {
  const conditions = participant.conditions.length ? `, ${participant.conditions.join(', ')}` : ''
  return `${participant.name}: ${participant.health}${conditions}`
}

/** Allies then enemies, or the initiative order when the server sends one. */
function orderedCombatants(combat: CombatStatePanel) {
  const participants = [...combat.allies, ...combat.enemies]
  const order = combat.turnOrder ?? []
  if (!order.length) return { combatants: participants, dividerAfter: combat.enemies.length ? combat.allies.length : 0 }
  const byId = new Map(participants.map((participant) => [participant.id, participant]))
  const inOrder = order.flatMap((entry) => byId.get(entry.id) ?? [])
  const placed = new Set(inOrder.map((participant) => participant.id))
  return {
    combatants: [...inOrder, ...participants.filter((participant) => !placed.has(participant.id))],
    dividerAfter: 0,
  }
}

function Combatant({
  participant,
  portrait,
  current,
  own,
  vitality,
}: {
  participant: CombatParticipantSummary
  portrait: string
  current: boolean
  /** The viewer's own marker: no range under it (a hero is never at a range from themselves). */
  own: boolean
  vitality: HeroVitality | null
}) {
  const ally = participant.team !== 'enemy'
  const dots = vitality
    ? participant.conditions.filter((condition) => !VITALITY_CONDITIONS.has(condition.toLowerCase()))
    : participant.conditions
  const detail = vitality ? (vitality.state === 'out' ? 'out' : 'down') : own ? '' : ally ? distance(participant) : enemyDetail(participant)
  return (
    <li
      className="story-combatant"
      data-combatant-id={participant.id}
      data-team={ally ? 'ally' : 'enemy'}
      data-health={participant.healthTone}
      data-vitality={vitality?.state}
      data-current={current || undefined}
      aria-label={combatantLabel(participant)}
      aria-current={current ? 'true' : undefined}
      title={participant.name}
    >
      <span className="story-combatant-disc" aria-hidden="true">
        {ally && portrait ? (
          <img src={portrait} alt="" />
        ) : (
          <span className="story-combatant-initial">{participant.name.trim().charAt(0).toUpperCase()}</span>
        )}
        {dots.length ? (
          <span className="story-combatant-conditions">
            {dots.slice(0, MAX_CONDITION_DOTS).map((condition) => (
              <i key={condition} title={condition} />
            ))}
          </span>
        ) : null}
      </span>
      <span className="story-combatant-name" aria-hidden="true">{participant.name}</span>
      {detail ? <span className="story-combatant-range" aria-hidden="true">{detail}</span> : null}
    </li>
  )
}

/**
 * Thin ember arcs from the viewer's disc to each combatant locked in melee
 * with them. Measured from the laid-out discs and redrawn when they move.
 */
function EngagementArcs({
  viewerId,
  engagedIds,
  layoutKey,
}: {
  viewerId: string
  engagedIds: string[]
  /** Changes whenever the roster's order or current actor does. */
  layoutKey: string
}) {
  const svgRef = useRef<SVGSVGElement | null>(null)
  const engagedKey = engagedIds.join('|')

  useLayoutEffect(() => {
    const svg = svgRef.current
    // The roster list this overlay sits in (its own ref is not attached yet on first mount).
    const list = svg?.parentElement?.closest('ul') ?? null
    if (!list || !svg) return undefined
    const discFor = (id: string) =>
      [...list.querySelectorAll<HTMLElement>('[data-combatant-id]')]
        .find((element) => element.dataset.combatantId === id)
        ?.querySelector<HTMLElement>('.story-combatant-disc') ?? null
    const draw = () => {
      const box = list.getBoundingClientRect()
      // The point on a disc's rim up and towards the other disc (its "shoulder").
      const shoulder = (disc: HTMLElement, towards: number) => {
        const rect = disc.getBoundingClientRect()
        const radius = rect.width / 2
        const centreX = rect.left - box.left + radius
        return { x: centreX + Math.sign(towards - centreX) * radius * 0.6, y: rect.top - box.top + radius * 0.2 }
      }
      const centreOf = (disc: HTMLElement) => {
        const rect = disc.getBoundingClientRect()
        return rect.left - box.left + rect.width / 2
      }
      const from = discFor(viewerId)
      svg.querySelectorAll<SVGPathElement>('path[data-target]').forEach((path) => {
        const to = discFor(path.dataset.target ?? '')
        if (!from || !to) {
          path.removeAttribute('d')
          return
        }
        const start = shoulder(from, centreOf(to))
        const end = shoulder(to, centreOf(from))
        const top = Math.min(start.y, end.y) - ARC_RISE
        path.setAttribute('d', `M ${start.x} ${start.y} Q ${(start.x + end.x) / 2} ${top} ${end.x} ${end.y}`)
      })
    }
    draw()
    list.addEventListener('transitionend', draw)
    const observer = typeof ResizeObserver === 'function' ? new ResizeObserver(draw) : null
    observer?.observe(list)
    return () => {
      list.removeEventListener('transitionend', draw)
      observer?.disconnect()
    }
  }, [viewerId, engagedKey, layoutKey])

  return (
    <svg ref={svgRef} className="story-engagements" aria-hidden="true" focusable="false">
      {engagedIds.map((id) => <path key={id} data-target={id} />)}
    </svg>
  )
}

function EconomyPips({ bundle }: { bundle: CombatLegalActionBundle }) {
  const pips: Array<[string, number]> = [
    ['Action', bundle.actionRemaining],
    ['Bonus', bundle.bonusActionRemaining],
    ['Move', bundle.movementRemaining],
    ['Reaction', bundle.reactionRemaining],
  ]
  const attacks = bundle.attacksRemaining ?? 0
  return (
    <ul className="story-economy" aria-label="Turn resources">
      {pips.map(([label, remaining]) => (
        <li key={label} data-spent={remaining > 0 ? undefined : true}>
          <i aria-hidden="true" />
          {label}
          <span className="story-sr-only">{remaining > 0 ? ' ready' : ' spent'}</span>
        </li>
      ))}
      {attacks > 1 ? (
        <li data-attacks="">
          <i aria-hidden="true" />
          Attacks ×{attacks}
          <span className="story-sr-only"> left</span>
        </li>
      ) : null}
    </ul>
  )
}

/**
 * Combat in one or two quiet lines: who is fighting and how they are holding
 * up, then — on the viewer's turn — one row of what they can do now: weapon
 * attacks, their latest spells, Cast…, class abilities, a story action the
 * scene offers, Grapple, Shove down, Bind and Demand surrender, Advance, Fall
 * back, a healing potion when hurt, More… and End turn. Nothing that is not
 * legal now is shown; everything else lives in More…. A choice with exactly
 * one legal target is taken on it at once.
 */
export function StoryCombatRibbon({
  combat,
  playerId,
  activePlayers,
  controls,
  recentSpellNames: recentSpells,
  disabled,
  down = false,
  submitAction,
  onOpenTray,
}: StoryCombatRibbonProps) {
  const hintPrefix = useId()
  const [targeting, setTargeting] = useState<Targeting | null>(null)
  const [blockedNote, setBlockedNote] = useState('')
  const choicesRef = useRef<HTMLDivElement | null>(null)
  const fadeChoicesRef = useEdgeFades<HTMLDivElement>()
  const rosterRef = useEdgeFades<HTMLDivElement>()
  const setChoicesElement = useCallback((element: HTMLDivElement | null) => {
    choicesRef.current = element
    fadeChoicesRef(element)
  }, [fadeChoicesRef])
  const bundle = combat.legalActionBundles.find((candidate) => candidate.playerId === playerId) ?? null
  const targetingKey = targeting ? `${targeting.kind}:${targeting.kind === 'action' ? targeting.action.id : targeting.spell.id}` : ''

  useEffect(() => {
    if (targetingKey) choicesRef.current?.querySelector<HTMLElement>('button')?.focus()
  }, [targetingKey])

  if (!combat.active || !bundle) return null

  const portraitFor = (participant: CombatParticipantSummary) =>
    activePlayers.find((player) => player.character_name === participant.name)?.profile_image ?? ''
  const { combatants, dividerAfter } = orderedCombatants(combat)
  const viewer = combatants.find((participant) => participant.id === bundle.actorId)
  // Who acts is `flags.activeActorId` when the server sends it, else its turn pointer.
  const actingId = combat.activeActorId || bundle.currentActorId
  const actingName =
    combatants.find((participant) => participant.id === actingId)?.name ??
    (actingId === bundle.currentActorId ? bundle.currentActorName : '')
  const endTurn = bundle.actions.find(isEndTurn) ?? null
  // The viewer's choices show on their turn, or whenever the server offers them anything legal.
  const viewersTurn = !down && (actingId === bundle.actorId || bundle.actions.some((action) => action.available))
  const legal = bundle.actions.filter((action) => action.available && !isEndTurn(action))
  const firstOfType = (type: string) => legal.filter((action) => action.type === type).slice(0, 1)
  const castable = controls.spells.filter((spell) => spell.available)
  const latestSpells = recentSpells
    .map((name) => castable.find((spell) => spell.name.toLowerCase() === name.toLowerCase()))
    .filter((spell): spell is GameplaySpellControl => Boolean(spell))
    .slice(0, MAX_SPELLS)
  const storyActions = controls.interactables
    .filter((object) => object.offered && object.available)
    .flatMap((object) => object.actions.filter((action) => action.authored && action.available).map((action) => ({ object, action })))
    .slice(0, MAX_STORY_OBJECT_ACTIONS)
  const actionChoice = (action: CombatLegalAction): RibbonChoice => ({
    kind: 'action',
    key: `action-${action.id}`,
    label: combatActionLabel(action),
    cost: costLetters(action.cost),
    action,
  })
  const maneuverChoice = (action: CombatLegalAction, glyph: LucideIcon): RibbonChoice => ({
    kind: 'action',
    key: `action-${action.id}`,
    label: combatActionLabel(action),
    cost: costLetters(action.cost),
    action,
    glyph,
  })
  const hurt = viewerHurt(activePlayers.find((player) => player.id === playerId), viewer)
  const choices: RibbonChoice[] = [
    ...legal.filter((action) => action.type === 'attack').slice(0, MAX_ATTACKS).map(actionChoice),
    // Inspiration is spent before the roll it helps, so it sits beside the attacks.
    ...firstOfType('take_heart').map(actionChoice),
    ...latestSpells.map((spell): RibbonChoice => ({
      kind: 'spell',
      key: `spell-${spell.id}`,
      label: spell.name,
      cost: SPELL_COSTS[spell.castingTime] ?? '',
      spell,
    })),
    ...(castable.length ? [{ kind: 'tray', key: 'cast', label: 'Cast…', cost: '', section: 'magic' } as const] : []),
    ...legal.filter(isClassAbility).slice(0, MAX_ABILITIES).map(actionChoice),
    ...storyActions.map(({ object, action }): RibbonChoice => ({
      kind: 'object',
      key: `object-${object.id}-${action.id}`,
      label: action.label,
      cost: '',
      object,
      action,
    })),
    ...MANEUVER_GLYPHS.flatMap(([type, glyph]) => firstOfType(type).map((action) => maneuverChoice(action, glyph))),
    ...firstOfType('advance').map(actionChoice),
    ...firstOfType('reposition').map(actionChoice),
    ...(hurt ? legal.filter(isHealingPotion).slice(0, 1).map(actionChoice) : []),
    { kind: 'tray', key: 'more', label: 'More…', cost: '', section: 'combat' },
  ]
  const actionSpent = bundle.subTurnCountersTracked && bundle.actionRemaining <= 0
  const telegraphs = combat.telegraphs.slice(0, MAX_TELEGRAPHS)
  const engagedIds = combatants
    .filter((participant) =>
      participant.id !== bundle.actorId &&
      (viewer?.engagedWith?.includes(participant.id) || participant.engagedWith?.includes(bundle.actorId)),
    )
    .map((participant) => participant.id)
  const layoutKey = `${combatants.map((participant) => participant.id).join('|')}@${actingId}`
  const endTurnReason = endTurn
    ? [endTurn.available ? '' : endTurn.reason || 'This action is not currently legal.', disabled ? RESOLVING_REASON : '']
        .filter(Boolean)
        .join(' ')
    : ''

  const finish = (message: string, intent: ActionIntent) => {
    submitAction(message, intent)
    setTargeting(null)
    setBlockedNote('')
  }

  const act = (action: CombatLegalAction, target: CombatLegalTarget | null) => {
    const submission = combatActionSubmission(bundle.actorName, action, target)
    if (submission) finish(submission.message, submission.intent)
  }

  const castAt = (spell: GameplaySpellControl, targetId: string) => {
    const { message, intent } = spellCastSubmission(controls.actorName, spell, [targetId])
    finish(message, intent)
  }

  const choose = (choice: RibbonChoice) => {
    if (choice.kind === 'tray') {
      onOpenTray(choice.section)
      return
    }
    if (disabled) {
      setBlockedNote(RESOLVING_REASON)
      return
    }
    setBlockedNote('')
    if (choice.kind === 'object') {
      const { message, intent } = objectActionSubmission(controls.actorName, choice.object, choice.action)
      finish(message, intent)
    } else if (choice.kind === 'spell') {
      const only = onlyLegalTarget(offeredTargets(combat, choice.spell.targets))
      if (!aimsInRibbon(choice.spell)) onOpenTray('magic', choice.spell.id)
      else if (only) castAt(choice.spell, only.id)
      else setTargeting({ kind: 'spell', spell: choice.spell })
    } else if (choice.action.requiresTarget) {
      const only = onlyLegalTarget(offeredTargets(combat, choice.action.targets))
      if (only) act(choice.action, only)
      else setTargeting({ kind: 'action', action: choice.action })
    } else {
      act(choice.action, null)
    }
  }

  const chooseEndTurn = () => {
    if (!endTurn) return
    if (endTurnReason) {
      setBlockedNote(endTurnReason)
      return
    }
    act(endTurn, null)
  }

  // Reasons for blocked targets live outside the buttons so they describe, not rename, them.
  const hints: Array<[string, string]> = []
  const blockedChip = (key: string, reason: string) => {
    const hintId = `${hintPrefix}-${key}`
    if (reason) hints.push([hintId, reason])
    return {
      'aria-disabled': reason ? true : undefined,
      'aria-describedby': reason ? hintId : undefined,
      title: reason || undefined,
      'data-blocked': reason ? true : undefined,
    }
  }

  const targetChoices = () => {
    if (!targeting) return []
    if (targeting.kind === 'action') {
      return offeredTargets(combat, targeting.action.targets).map((target) => ({
        id: target.id,
        label: combatTargetLabel(combat, target),
        reason: target.available ? '' : target.reason || 'This target is not currently legal.',
        submit: () => act(targeting.action, target),
      }))
    }
    return offeredTargets(combat, targeting.spell.targets).map((target) => ({
      id: target.id,
      label: combatTargetLabel(combat, target),
      reason: target.available ? '' : target.reason || 'This target is not currently legal.',
      submit: () => castAt(targeting.spell, target.id),
    }))
  }
  const targetingLabel = targeting
    ? (targeting.kind === 'action' ? combatActionLabel(targeting.action) : targeting.spell.name)
    : ''

  return (
    <section className="story-ribbon" aria-label={`Combat, round ${bundle.round}`}>
      <div ref={rosterRef} className="story-ribbon-roster story-scroll-row">
        <span className="story-label story-ribbon-round">Round {bundle.round}</span>
        <ul className="story-combatants" aria-label="Combatants">
          {combatants.map((participant, index) => (
            <Fragment key={participant.id || participant.name}>
              {dividerAfter && index === dividerAfter ? (
                <li className="story-combatant-divider" aria-hidden="true" />
              ) : null}
              <Combatant
                participant={participant}
                portrait={participant.team === 'enemy' ? '' : portraitFor(participant)}
                current={participant.id === actingId}
                own={participant.id === bundle.actorId}
                vitality={participant.team === 'enemy' ? null : heroVitalityOf(participant.conditions, participant.deathSaves)}
              />
            </Fragment>
          ))}
          {engagedIds.length ? (
            <li className="story-engagements-slot" aria-hidden="true">
              <EngagementArcs
                viewerId={bundle.actorId}
                engagedIds={engagedIds}
                layoutKey={layoutKey}
              />
            </li>
          ) : null}
        </ul>
      </div>

      {viewersTurn ? (
        <div className="story-ribbon-turn">
          {bundle.subTurnCountersTracked ? <EconomyPips bundle={bundle} /> : null}
          <div
            ref={setChoicesElement}
            className="story-ribbon-choices story-scroll-row"
            role="group"
            aria-label={targeting ? `Targets for ${targetingLabel}` : 'Combat actions'}
          >
            {targeting ? (
              <>
                {targetChoices().map((target) => {
                  const reason = [target.reason, disabled ? RESOLVING_REASON : ''].filter(Boolean).join(' ')
                  return (
                    <button
                      key={target.id}
                      type="button"
                      className="story-chip"
                      {...blockedChip(`target-${target.id}`, reason)}
                      onClick={() => {
                        if (reason) setBlockedNote(reason)
                        else target.submit()
                      }}
                    >
                      {target.label}
                    </button>
                  )
                })}
                <button
                  type="button"
                  className="story-link story-ribbon-back"
                  onClick={() => {
                    setTargeting(null)
                    setBlockedNote('')
                  }}
                >
                  <ChevronLeft size={14} aria-hidden="true" />
                  Back
                </button>
              </>
            ) : (
              choices.map((choice) => {
                const Glyph = choice.kind === 'action' ? choice.glyph : undefined
                return (
                  <button
                    key={choice.key}
                    type="button"
                    className="story-chip"
                    data-choice={choice.kind}
                    onClick={() => choose(choice)}
                  >
                    {Glyph ? <Glyph aria-hidden="true" /> : null}
                    {choice.label}
                    {choice.cost ? <span className="story-chip-cost" aria-hidden="true">{choice.cost}</span> : null}
                  </button>
                )
              })
            )}
          </div>
          {endTurn && !targeting ? (
            <button
              type="button"
              className={actionSpent ? 'story-primary story-end-turn' : 'story-chip story-end-turn'}
              {...blockedChip('end-turn', endTurnReason)}
              onClick={chooseEndTurn}
            >
              {endTurn.label || 'End turn'}
            </button>
          ) : null}
        </div>
      ) : (
        <p className="story-ribbon-waiting">
          {actingName ? `${actingName} is acting` : 'Waiting for the turn order'}
          <span className="story-dots" aria-hidden="true"><i /><i /><i /></span>
        </p>
      )}

      <div hidden>
        {hints.map(([id, reason]) => <span key={id} id={id}>{reason}</span>)}
      </div>

      {blockedNote ? (
        <p className="story-ribbon-note" role="status">{blockedNote}</p>
      ) : telegraphs.length ? (
        <p className="story-ribbon-note">Watch for: {telegraphs.join(' · ')}</p>
      ) : null}
    </section>
  )
}
