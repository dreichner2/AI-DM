import { createClientMessageId, type ActionIntent } from '../gameActions'
import type {
  CombatLegalAction,
  CombatLegalTarget,
  WorldMerchantStockEntry,
  WorldMerchantSummary,
} from '../gameSelectors'
import type {
  GameplayCapabilityControl,
  GameplayInteractableControl,
  GameplayItemControl,
  GameplayObjectAction,
  GameplaySpellControl,
  GameplayTarget,
  GameplayTradeGood,
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
  const message = !target
    ? action.message
    : action.type === 'attack'
      ? `${actorName} attacks ${target.name} with ${action.weaponName || 'their weapon'}.`
      : `${actorName}: ${action.label} on ${target.name}.`
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

/**
 * A short or long rest (the classic Current Situation "Recovery" actions). A
 * short rest may say how many hit dice to spend; without it the server spends
 * as many as reach full HP.
 */
export function restSubmission(
  actorName: string,
  restType: 'short_rest' | 'long_rest',
  hitDice?: number,
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
      ...(restType === 'short_rest' && hitDice !== undefined ? { hit_dice: hitDice } : {}),
    },
  }
}

/**
 * A rules-backed consumable used on one target: through the server's combat
 * action in combat, as a typed item use otherwise.
 */
export function itemUseSubmission(
  actorName: string,
  item: GameplayItemControl,
  target: GameplayTarget,
): ActionSubmission {
  const targetText = target.name === actorName ? '' : ` on ${target.name}`
  const message = `${actorName} uses ${item.name}${targetText}.`
  if (item.actionId) {
    return {
      message,
      intent: {
        kind: 'combat',
        source: 'combat_hud',
        text: message,
        client_message_id: combatClientMessageId(),
        combat: { action_id: item.actionId, target_id: target.id },
      },
    }
  }
  return {
    message,
    intent: {
      kind: 'item',
      source: 'scene_panel',
      text: message,
      client_message_id: createClientMessageId(),
      inventory_action: 'use',
      item: { id: item.id, name: item.name, quantity: 1, target_id: target.id },
    },
  }
}

/** Buy one of a merchant's stock lines at the server's price. */
export function buySubmission(
  actorName: string,
  merchant: WorldMerchantSummary,
  entry: WorldMerchantStockEntry,
): ActionSubmission {
  const message = `${actorName} buys ${entry.name} from ${merchant.name}.`
  return {
    message,
    intent: {
      kind: 'shop',
      source: 'scene_panel',
      text: message,
      client_message_id: createClientMessageId(),
      shop: { action: 'buy', merchant_id: merchant.id, stock_id: entry.stockId, quantity: 1 },
    },
  }
}

/** Sell one of an inventory item to a merchant at the server's offer. */
export function sellSubmission(
  actorName: string,
  merchant: WorldMerchantSummary,
  good: GameplayTradeGood,
): ActionSubmission {
  const message = `${actorName} sells ${good.name} to ${merchant.name}.`
  return {
    message,
    intent: {
      kind: 'shop',
      source: 'scene_panel',
      text: message,
      client_message_id: createClientMessageId(),
      shop: { action: 'sell', merchant_id: merchant.id, item_id: good.id, quantity: 1 },
    },
  }
}
