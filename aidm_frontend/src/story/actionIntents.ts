import { createClientMessageId, type ActionIntent } from '../gameActions'
import type { CombatLegalAction, CombatLegalTarget } from '../gameSelectors'
import type {
  GameplayCapabilityControl,
  GameplayInteractableControl,
  GameplayObjectAction,
  GameplaySpellControl,
  GameplayTarget,
} from '../gameplayControlState'

/**
 * Typed action submissions shared by the classic console and Story mode, so the
 * same choice always reaches the server as the same message and intent.
 */
export type ActionSubmission = {
  message: string
  intent: ActionIntent
}

function combatClientMessageId() {
  const randomId = globalThis.crypto?.randomUUID?.()
  return `combat-hud-${randomId ?? `${Date.now()}-${Math.random().toString(16).slice(2)}`}`
}

/** A server-issued combat action, optionally against one of its legal targets. */
export function combatActionSubmission(
  actorName: string,
  action: CombatLegalAction,
  target: CombatLegalTarget | null,
): ActionSubmission | null {
  if (action.requiresTarget && !target) return null
  const message = target
    ? `${actorName} attacks ${target.name} with ${action.weaponName || 'their weapon'}.`
    : action.message
  return {
    message,
    intent: {
      kind: 'combat',
      source: 'combat_hud',
      text: message,
      client_message_id: combatClientMessageId(),
      combat: {
        action_id: action.id,
        ...(target ? { target_id: target.id } : {}),
      },
    },
  }
}

/** An authoritative spell cast at exact target IDs. */
export function spellCastSubmission(
  actorName: string,
  spell: GameplaySpellControl,
  targetIds: string[],
  castLevel?: number,
): ActionSubmission {
  const names = spell.targets
    .filter((target) => targetIds.includes(target.id))
    .map((target) => target.name)
  const targetText = names.length ? ` at ${names.join(', ')}` : ''
  const message = `${actorName} casts ${spell.name}${targetText}.`
  return {
    message,
    intent: {
      kind: 'spell',
      source: 'scene_panel',
      text: message,
      client_message_id: createClientMessageId(),
      spell: {
        name: spell.name,
        effect: 'Resolve the server-owned spell effect against the selected exact target IDs.',
        resource_pool: 'auto',
        concentration: spell.concentration,
        target_ids: targetIds,
        ...(castLevel === undefined ? {} : { cast_level: castLevel }),
      },
    },
  }
}

/** The chosen target when it is still legal, otherwise the first legal one. */
export function capabilityTarget(
  capability: GameplayCapabilityControl,
  selectedTargetId: string | undefined,
) {
  const selected = capability.targets.find((target) =>
    target.id === selectedTargetId && target.available,
  )
  return selected ?? capability.targets.find((target) => target.available) ?? null
}

/** Healing-pool amount clamped to the pool and the target's missing hit points. */
export function selectedHealingAmount(
  capability: GameplayCapabilityControl,
  target: GameplayTarget | null,
  rawAmount: string,
) {
  const parsed = Number.parseInt(rawAmount, 10)
  const missing = target && target.currentHp !== null && target.maxHp !== null
    ? Math.max(0, target.maxHp - target.currentHp)
    : capability.current
  const maximum = Math.max(1, Math.min(capability.current, missing))
  return {
    amount: Math.max(1, Math.min(maximum, Number.isFinite(parsed) ? parsed : 1)),
    maximum,
  }
}

/** A class capability used on one exact target. */
export function capabilitySubmission(
  actorName: string,
  capability: GameplayCapabilityControl,
  target: GameplayTarget,
  rawAmount: string,
): ActionSubmission {
  const healing = capability.effectType === 'healing_pool'
    ? selectedHealingAmount(capability, target, rawAmount)
    : null
  const targetText = capability.targetPolicy === 'self' ? '' : ` on ${target.name}`
  const amountText = healing ? ` for up to ${healing.amount} hit points` : ''
  const message = `${actorName} uses ${capability.name}${targetText}${amountText}.`
  return {
    message,
    intent: {
      kind: 'capability',
      source: 'scene_panel',
      text: message,
      client_message_id: createClientMessageId(),
      capability: {
        id: capability.id,
        target_id: target.id,
        ...(healing ? { amount: healing.amount } : {}),
      },
    },
  }
}

/** A scene object action carrying the object's current revision. */
export function objectActionSubmission(
  actorName: string,
  object: GameplayInteractableControl,
  action: GameplayObjectAction,
): ActionSubmission {
  const message = `${actorName} attempts to ${action.id} ${object.name}.`
  return {
    message,
    intent: {
      kind: 'object',
      source: 'scene_panel',
      text: message,
      client_message_id: createClientMessageId(),
      object: {
        id: object.id,
        action: action.id,
        ...(object.revision === null ? {} : { revision: object.revision }),
      },
    },
  }
}

/** Party travel to a connected location (the classic Current Situation "Travel" action). */
export function travelSubmission(exit: { id: string; name: string }): ActionSubmission {
  const message = `The party travels to ${exit.name}.`
  return {
    message,
    intent: {
      kind: 'travel',
      source: 'scene_panel',
      text: message,
      client_message_id: createClientMessageId(),
      location: { id: exit.id, name: exit.name },
    },
  }
}

/** A short or long rest (the classic Current Situation "Recovery" actions). */
export function restSubmission(
  actorName: string,
  restType: 'short_rest' | 'long_rest',
): ActionSubmission {
  const label = restType === 'short_rest' ? 'short rest' : 'long rest'
  const message = `${actorName} completes a ${label}.`
  return {
    message,
    intent: {
      kind: 'rest',
      source: 'scene_panel',
      text: message,
      client_message_id: createClientMessageId(),
      rest_type: restType,
    },
  }
}
