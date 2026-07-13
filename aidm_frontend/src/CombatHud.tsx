import { useId } from 'react'
import type { ActionIntent } from './gameActions'
import type {
  CombatLegalAction,
  CombatLegalTarget,
  CombatParticipantSummary,
  CombatStatePanel,
} from './gameSelectors'

type CombatHudProps = {
  combat: CombatStatePanel
  playerId: number | null
  disabled: boolean
  submitAction: (overrideMessage?: string, overrideIntent?: ActionIntent) => boolean
}

function combatClientMessageId() {
  const randomId = globalThis.crypto?.randomUUID?.()
  return `combat-hud-${randomId ?? `${Date.now()}-${Math.random().toString(16).slice(2)}`}`
}

function economyLabel(action: CombatLegalAction) {
  const parts = []
  if (action.economy.action > 0) parts.push(`${action.economy.action} action`)
  if (action.economy.movement === 'used') parts.push('uses movement')
  if (action.economy.movement === 'optional') parts.push('movement optional')
  if (action.economy.endsTurn) parts.push('ends turn')
  return parts.join(' · ') || 'ends turn'
}

function attackMessage(actorName: string, action: CombatLegalAction, target: CombatLegalTarget) {
  return `${actorName} attacks ${target.name} with ${action.weaponName || 'their weapon'}.`
}

function playerFacingActionDescription(description: string) {
  const rollHandledAutomatically = /\bserver-rolled\b/i.test(description)
  const copy = description
    .replace(/\bserver-rolled\s+/gi, '')
    .replace(/\ba legal target\b/gi, 'an available target')
    .replace(/\blegal target\b/gi, 'available target')
  return rollHandledAutomatically
    ? `${copy.replace(/\.$/, '')}. The roll is handled automatically.`
    : copy
}

function CombatantList({
  currentActorId,
  label,
  participants,
}: {
  currentActorId: string
  label: 'Allies' | 'Enemies'
  participants: CombatParticipantSummary[]
}) {
  return (
    <section className="combat-hud-team" aria-label={`${label} in combat`}>
      <h3>{label}</h3>
      {participants.length ? (
        <ul>
          {participants.map((participant) => (
            <li
              key={participant.id || `${label}-${participant.name}`}
              className={participant.id === currentActorId ? 'is-acting' : ''}
            >
              <strong>{participant.name}</strong>
              <span className={`combat-hud-health ${participant.healthTone}`}>
                HP: {participant.health}
              </span>
              <small>
                {participant.conditions.length
                  ? `Conditions: ${participant.conditions.join(', ')}`
                  : 'No conditions'}
              </small>
            </li>
          ))}
        </ul>
      ) : (
        <p>{label === 'Allies' ? 'No allies listed' : 'No enemies visible'}</p>
      )}
    </section>
  )
}

export function CombatHud({ combat, playerId, disabled, submitAction }: CombatHudProps) {
  const optionIdPrefix = useId()
  const bundle = combat.legalActionBundles.find((candidate) => candidate.playerId === playerId) ?? null

  if (!combat.active || !bundle) return null

  const submit = (action: CombatLegalAction, target: CombatLegalTarget | null) => {
    if (action.requiresTarget && !target) return
    const message = target ? attackMessage(bundle.actorName, action, target) : action.message
    const intent: ActionIntent = {
      kind: 'combat',
      source: 'combat_hud',
      text: message,
      client_message_id: combatClientMessageId(),
      combat: {
        action_id: action.id,
        ...(target ? { target_id: target.id } : {}),
      },
    }
    submitAction(message, intent)
  }

  const turnLabel = bundle.isCurrentActor
    ? 'Your turn'
    : bundle.currentActorName
      ? `${bundle.currentActorName} is acting`
      : 'Waiting for turn order'
  const actionStateNote = disabled
    ? 'Your combat choices are paused while the current turn finishes.'
    : bundle.isCurrentActor
      ? 'Choose an action and, when needed, a target. Unavailable choices explain what is blocking them.'
      : `Wait for ${bundle.currentActorName || 'the current actor'} to finish. Your choices remain visible below.`
  const enemyCues = combat.enemies.flatMap((enemy) =>
    enemy.telegraph
      ? [`${enemy.name}: ${enemy.telegraph}${enemy.intent ? ` Intent: ${enemy.intent}` : ''}`]
      : enemy.intent
        ? [`${enemy.name} appears ready to ${enemy.intent}.`]
        : [],
  )
  const visibleCues = enemyCues.length ? enemyCues : combat.telegraphs

  return (
    <section
      className={`combat-hud ${disabled ? 'is-disabled' : ''}`}
      aria-labelledby="combat-hud-title"
    >
      <div className="combat-hud-heading">
        <h2 id="combat-hud-title">Combat · Round {bundle.round}</h2>
        <span
          className={`combat-hud-turn ${bundle.isCurrentActor ? 'current' : ''}`}
          role="status"
          aria-live="polite"
        >
          <strong>Current actor</strong>
          {turnLabel}
        </span>
      </div>
      <p id="combat-hud-action-state" className="combat-hud-note">{actionStateNote}</p>
      <div className="combat-hud-overview">
        <div className="combat-hud-roster">
          <CombatantList
            currentActorId={bundle.currentActorId}
            label="Allies"
            participants={combat.allies}
          />
          <CombatantList
            currentActorId={bundle.currentActorId}
            label="Enemies"
            participants={combat.enemies}
          />
        </div>
        {visibleCues.length ? (
          <section className="combat-hud-cues" aria-label="Visible enemy intentions">
            <h3>Watch for</h3>
            <ul>
              {visibleCues.map((cue, index) => <li key={`${cue}-${index}`}>{cue}</li>)}
            </ul>
          </section>
        ) : null}
      </div>
      {bundle.actions.length > 2 ? (
        <span className="combat-hud-choice-count">Scroll to see all {bundle.actions.length} choices</span>
      ) : null}
      <div
        className="action-intent-panel combat-hud-actions"
        role="group"
        aria-label="Combat action choices"
        aria-describedby="combat-hud-action-state"
        tabIndex={0}
      >
        {bundle.actions.flatMap((action, actionIndex) => {
          const options: Array<CombatLegalTarget | null> = action.requiresTarget
            ? action.targets.length ? action.targets : [null]
            : [null]
          return options.map((target, targetIndex) => {
            const targetUnavailable = Boolean(target && !target.available)
            const missingTarget = action.requiresTarget && !target
            const blocked = disabled || !action.available || targetUnavailable || missingTarget
            const blockedReason = [
              !action.available
                ? action.reason || 'This action is not currently legal.'
                : '',
              targetUnavailable
                ? target?.reason || 'This target is not currently legal.'
                : '',
              missingTarget ? 'No targets are available for this action.' : '',
              disabled ? 'Actions are unavailable while the current turn resolves.' : '',
            ].filter(Boolean).join(' ')
            const detail = [
              target?.rangeBand || '',
              action.rangeClassification ? `${action.rangeClassification} range` : '',
              economyLabel(action),
            ].filter(Boolean).join(' · ')
            const optionId = `${optionIdPrefix}-${actionIndex}-${targetIndex}`
            const descriptionId = action.description ? `${optionId}-description` : ''
            const detailId = `${optionId}-detail`
            const reasonId = blockedReason ? `${optionId}-reason` : ''
            return (
              <button
                type="button"
                className={blockedReason ? 'combat-hud-option is-unavailable' : 'combat-hud-option'}
                key={`${action.id}:${target?.id ?? 'none'}`}
                disabled={blocked}
                onClick={() => submit(action, target)}
                aria-label={target ? `${action.label}, target ${target.name}` : action.label}
                aria-describedby={[descriptionId, detailId, reasonId].filter(Boolean).join(' ')}
                title={blockedReason || undefined}
              >
                <strong>
                  {action.label}
                  {target ? <span className="combat-hud-target"> · {target.name}</span> : null}
                </strong>
                {action.description ? (
                  <span id={descriptionId} className="combat-hud-option-description">
                    {playerFacingActionDescription(action.description)}
                  </span>
                ) : null}
                <small id={detailId}>{detail}</small>
                {blockedReason ? (
                  <small id={reasonId} className="combat-hud-option-reason">
                    Unavailable: {blockedReason}
                  </small>
                ) : null}
              </button>
            )
          })
        })}
      </div>
    </section>
  )
}
