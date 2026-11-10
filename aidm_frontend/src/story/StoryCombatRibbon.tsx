import { useCallback, useEffect, useId, useRef, useState } from 'react'
import { ChevronLeft } from 'lucide-react'
import type { ActionIntent } from '../gameActions'
import type {
  CombatLegalAction,
  CombatLegalActionBundle,
  CombatLegalTarget,
  CombatParticipantSummary,
  CombatStatePanel,
} from '../gameSelectors'
import type { ActivePlayer } from '../types'
import { combatActionSubmission } from './actionIntents'
import { useEdgeFades } from './useEdgeFades'

const RESOLVING_REASON = 'Actions are unavailable while the current turn resolves.'
const MAX_TELEGRAPHS = 2
const MAX_CONDITION_DOTS = 2

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

function combatantLabel(participant: CombatParticipantSummary) {
  const conditions = participant.conditions.length ? `, ${participant.conditions.join(', ')}` : ''
  return `${participant.name}: ${participant.health}${conditions}`
}

function Combatant({
  participant,
  portrait,
  current,
}: {
  participant: CombatParticipantSummary
  portrait: string
  current: boolean
}) {
  const ally = participant.team !== 'enemy'
  return (
    <li
      className="story-combatant"
      data-team={ally ? 'ally' : 'enemy'}
      data-health={participant.healthTone}
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
        {participant.conditions.length ? (
          <span className="story-combatant-conditions">
            {participant.conditions.slice(0, MAX_CONDITION_DOTS).map((condition) => (
              <i key={condition} title={condition} />
            ))}
          </span>
        ) : null}
      </span>
      <span className="story-combatant-name" aria-hidden="true">{participant.name}</span>
    </li>
  )
}

function EconomyPips({ bundle }: { bundle: CombatLegalActionBundle }) {
  const pips: Array<[string, number]> = [
    ['Action', bundle.actionRemaining],
    ['Bonus', bundle.bonusActionRemaining],
    ['Move', bundle.movementRemaining],
    ['Reaction', bundle.reactionRemaining],
  ]
  return (
    <ul className="story-economy" aria-label="Turn resources">
      {pips.map(([label, remaining]) => (
        <li key={label} data-spent={remaining > 0 ? undefined : true}>
          <i aria-hidden="true" />
          {label}
          <span className="story-sr-only">{remaining > 0 ? ' ready' : ' spent'}</span>
        </li>
      ))}
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
  const actionSpent = bundle.subTurnCountersTracked && bundle.actionRemaining <= 0
  const telegraphs = combat.telegraphs.slice(0, MAX_TELEGRAPHS)

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
          {combat.allies.map((participant) => (
            <Combatant
              key={participant.id || participant.name}
              participant={participant}
              portrait={portraitFor(participant)}
              current={participant.id === bundle.currentActorId}
            />
          ))}
          {combat.allies.length && combat.enemies.length ? (
            <li className="story-combatant-divider" aria-hidden="true" />
          ) : null}
          {combat.enemies.map((participant) => (
            <Combatant
              key={participant.id || participant.name}
              participant={participant}
              portrait=""
              current={participant.id === bundle.currentActorId}
            />
          ))}
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
              choices.map((action) => {
                const reason = actionBlockedReason(action)
                const key = `action-${action.id}`
                return (
                  <button
                    key={action.id}
                    type="button"
                    className="story-chip"
                    {...blockedChip(key, reason)}
                    onClick={() => chooseAction(action, reason)}
                  >
                    {action.label}
                  </button>
                )
              })
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
