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
        <span className={`combat-hud-turn ${bundle.isCurrentActor ? 'current' : ''}`}>{turnLabel}</span>
      </div>
      <p className="combat-hud-note">
        Turn order and targets are server-issued. Sub-turn counters are not tracked.
      </p>
      <div className="action-intent-panel combat-hud-actions">
        {bundle.actions.flatMap((action) => {
          const availableTargets = action.targets.filter((target) => target.available)
          const options: Array<CombatLegalTarget | null> = action.requiresTarget
            ? availableTargets.length ? availableTargets : [null]
            : [null]
          return options.map((target) => {
            const blocked = disabled || !action.available || (action.requiresTarget && !target)
            const detail = [
              target ? `${target.name} · ${target.rangeBand}` : '',
              action.rangeClassification ? `${action.rangeClassification} range` : '',
              economyLabel(action),
              action.available ? '' : action.reason,
            ].filter(Boolean).join(' · ')
            return (
              <button
                type="button"
                className="combat-hud-option"
                key={`${action.id}:${target?.id ?? 'none'}`}
                disabled={blocked}
                onClick={() => submit(action, target)}
              >
                <strong>{action.label}</strong>
                {' '}
                <small>{detail}</small>
              </button>
            )
          })
        })}
      </div>
    </section>
  )
}
