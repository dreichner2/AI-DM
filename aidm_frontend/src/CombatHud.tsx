import { useId } from 'react'
import type { ActionIntent } from './gameActions'
import type {
  CombatLegalAction,
  CombatLegalTarget,
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

  return (
    <section className="combat-hud" aria-labelledby="combat-hud-title">
      <div className="combat-hud-heading">
        <h2 id="combat-hud-title">Combat actions · Round {bundle.round}</h2>
        <span
          className={`combat-hud-turn ${bundle.isCurrentActor ? 'current' : ''}`}
          role="status"
          aria-live="polite"
        >
          {turnLabel}
        </span>
      </div>
      <p className="combat-hud-note">
        Turn order and targets are server-issued. Sub-turn counters are not tracked.
      </p>
      <div className="action-intent-panel combat-hud-actions">
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
                    {action.description}
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
