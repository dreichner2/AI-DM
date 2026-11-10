import { Fragment, useCallback, useEffect, useId, useLayoutEffect, useRef, useState } from 'react'
import { ChevronLeft } from 'lucide-react'
import type { ActionIntent } from '../gameActions'
import {
  characterVitality,
  type CharacterVitality,
  type CombatActionCost,
  type CombatLegalAction,
  type CombatLegalActionBundle,
  type CombatLegalTarget,
  type CombatParticipantSummary,
  type CombatStatePanel,
} from '../gameSelectors'
import type { ActivePlayer } from '../types'
import { combatActionSubmission } from './actionIntents'
import { StoryVitality } from './StoryVitality'
import { useEdgeFades } from './useEdgeFades'

const RESOLVING_REASON = 'Actions are unavailable while the current turn resolves.'
const MAX_TELEGRAPHS = 2
const MAX_CONDITION_DOTS = 2
const RANGE_BANDS = new Set(['melee', 'near', 'far', 'distant'])
/** Shown by the vitality mark, so not repeated as condition dots. */
const VITALITY_CONDITIONS = new Set(['dying', 'stable', 'dead', 'unconscious'])
const MOVEMENT_ACTION_TYPES = new Set(['advance', 'reposition', 'dash', 'disengage'])
const ACTION_GROUPS = ['attack', 'ability', 'movement', 'other'] as const
/** How high an engagement arc rises above the discs it links. */
const ARC_RISE = 11

type ActionGroup = (typeof ACTION_GROUPS)[number]

type StoryCombatRibbonProps = {
  combat: CombatStatePanel
  playerId: number | null
  activePlayers: ActivePlayer[]
  disabled: boolean
  submitAction: (overrideMessage?: string, overrideIntent?: ActionIntent) => boolean
}

function isEndTurn(action: CombatLegalAction) {
  return action.type === 'end_turn' || action.id === 'combat.end_turn'
}

/** Attacks; spells and class abilities; movement; everything else (Dodge, Help, Hide, Stabilize, items). */
function actionGroup(action: CombatLegalAction): ActionGroup {
  if (action.type === 'attack') return 'attack'
  if (action.type === 'class_feature' || action.type === 'spell' || /^(class|spell)\./.test(action.id)) return 'ability'
  if (MOVEMENT_ACTION_TYPES.has(action.type)) return 'movement'
  return 'other'
}

/** What an action spends: A(ction), B(onus action), M(ovement), R(eaction). */
function costLetters(cost: CombatActionCost | undefined) {
  if (!cost) return ''
  return [cost.action ? 'A' : '', cost.bonusAction ? 'B' : '', cost.movement ? 'M' : '', cost.reaction ? 'R' : ''].join('')
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
  vitality,
}: {
  participant: CombatParticipantSummary
  portrait: string
  current: boolean
  vitality: CharacterVitality | null
}) {
  const ally = participant.team !== 'enemy'
  const dots = vitality
    ? participant.conditions.filter((condition) => !VITALITY_CONDITIONS.has(condition.toLowerCase()))
    : participant.conditions
  const band = participant.rangeBand && RANGE_BANDS.has(participant.rangeBand) ? participant.rangeBand : ''
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
      {vitality ? (
        <StoryVitality vitality={vitality} className="story-combatant-vitality" />
      ) : band ? (
        <span className="story-combatant-range" aria-hidden="true">{band}</span>
      ) : null}
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
 * up, then — on the viewer's turn — what they can still spend and do.
 */
export function StoryCombatRibbon({
  combat,
  playerId,
  activePlayers,
  disabled,
  submitAction,
}: StoryCombatRibbonProps) {
  const hintPrefix = useId()
  const [targetingActionId, setTargetingActionId] = useState('')
  const [blockedNote, setBlockedNote] = useState('')
  const choicesRef = useRef<HTMLDivElement | null>(null)
  const fadeChoicesRef = useEdgeFades<HTMLDivElement>()
  const rosterRef = useEdgeFades<HTMLDivElement>()
  const setChoicesElement = useCallback((element: HTMLDivElement | null) => {
    choicesRef.current = element
    fadeChoicesRef(element)
  }, [fadeChoicesRef])
  const bundle = combat.legalActionBundles.find((candidate) => candidate.playerId === playerId) ?? null
  const targetingAction = bundle?.actions.find((action) => action.id === targetingActionId) ?? null

  useEffect(() => {
    if (targetingActionId) choicesRef.current?.querySelector<HTMLElement>('button')?.focus()
  }, [targetingActionId])

  if (!combat.active || !bundle) return null

  const portraitFor = (participant: CombatParticipantSummary) =>
    activePlayers.find((player) => player.character_name === participant.name)?.profile_image ?? ''
  const viewersTurn = bundle.isCurrentActor
  const endTurn = bundle.actions.find(isEndTurn) ?? null
  const choices = bundle.actions.filter((action) => !isEndTurn(action))
  const groupedChoices = ACTION_GROUPS
    .map((group) => choices.filter((action) => actionGroup(action) === group))
    .filter((group) => group.length)
  const actionSpent = bundle.subTurnCountersTracked && bundle.actionRemaining <= 0
  const telegraphs = combat.telegraphs.slice(0, MAX_TELEGRAPHS)
  const { combatants, dividerAfter } = orderedCombatants(combat)
  const viewer = combatants.find((participant) => participant.id === bundle.actorId)
  const engagedIds = combatants
    .filter((participant) =>
      participant.id !== bundle.actorId &&
      (viewer?.engagedWith?.includes(participant.id) || participant.engagedWith?.includes(bundle.actorId)),
    )
    .map((participant) => participant.id)
  const layoutKey = `${combatants.map((participant) => participant.id).join('|')}@${bundle.currentActorId}`

  const actionBlockedReason = (action: CombatLegalAction) => [
    action.available ? '' : action.reason || 'This action is not currently legal.',
    action.requiresTarget && !action.targets.length ? 'No targets are available for this action.' : '',
    disabled ? RESOLVING_REASON : '',
  ].filter(Boolean).join(' ')
  const endTurnReason = endTurn ? actionBlockedReason(endTurn) : ''

  const submit = (action: CombatLegalAction, target: CombatLegalTarget | null) => {
    const submission = combatActionSubmission(bundle.actorName, action, target)
    if (!submission) return
    submitAction(submission.message, submission.intent)
    setTargetingActionId('')
    setBlockedNote('')
  }

  const chooseAction = (action: CombatLegalAction, blockedReason: string) => {
    if (blockedReason) {
      setBlockedNote(blockedReason)
      return
    }
    setBlockedNote('')
    if (action.requiresTarget) {
      setTargetingActionId(action.id)
      return
    }
    submit(action, null)
  }

  const chooseTarget = (action: CombatLegalAction, target: CombatLegalTarget, blockedReason: string) => {
    if (blockedReason) {
      setBlockedNote(blockedReason)
      return
    }
    submit(action, target)
  }

  // Reasons for blocked chips live outside the buttons so they describe, not rename, them.
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
                current={participant.id === bundle.currentActorId}
                vitality={participant.team === 'enemy' ? null : characterVitality(participant.conditions, participant.deathSaves)}
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
            aria-label={targetingAction ? `Targets for ${targetingAction.label}` : 'Combat actions'}
          >
            {targetingAction ? (
              <>
                {targetingAction.targets.map((target) => {
                  const reason = [
                    target.available ? '' : target.reason || 'This target is not currently legal.',
                    disabled ? RESOLVING_REASON : '',
                  ].filter(Boolean).join(' ')
                  const key = `target-${target.id}`
                  return (
                    <button
                      key={target.id}
                      type="button"
                      className="story-chip"
                      {...blockedChip(key, reason)}
                      onClick={() => chooseTarget(targetingAction, target, reason)}
                    >
                      {target.name} · {target.rangeBand}
                    </button>
                  )
                })}
                <button
                  type="button"
                  className="story-link story-ribbon-back"
                  onClick={() => {
                    setTargetingActionId('')
                    setBlockedNote('')
                  }}
                >
                  <ChevronLeft size={14} aria-hidden="true" />
                  Back
                </button>
              </>
            ) : (
              groupedChoices.map((group, groupIndex) => (
                <Fragment key={actionGroup(group[0])}>
                  {groupIndex ? <span className="story-ribbon-gap" aria-hidden="true" /> : null}
                  {group.map((action) => {
                    const reason = actionBlockedReason(action)
                    const key = `action-${action.id}`
                    const cost = costLetters(action.cost)
                    return (
                      <button
                        key={action.id}
                        type="button"
                        className="story-chip"
                        data-group={actionGroup(action)}
                        {...blockedChip(key, reason)}
                        onClick={() => chooseAction(action, reason)}
                      >
                        {action.label}
                        {cost ? <span className="story-chip-cost" aria-hidden="true">{cost}</span> : null}
                      </button>
                    )
                  })}
                </Fragment>
              ))
            )}
          </div>
          {endTurn && !targetingAction ? (
            <button
              type="button"
              className={actionSpent ? 'story-primary story-end-turn' : 'story-chip story-end-turn'}
              {...blockedChip('end-turn', endTurnReason)}
              onClick={() => chooseAction(endTurn, endTurnReason)}
            >
              {endTurn.label || 'End turn'}
            </button>
          ) : null}
        </div>
      ) : (
        <p className="story-ribbon-waiting">
          {bundle.currentActorName ? `${bundle.currentActorName} is acting` : 'Waiting for the turn order'}
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
