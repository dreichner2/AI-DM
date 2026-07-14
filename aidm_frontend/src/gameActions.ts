export type ComposerMode = 'action' | 'roll' | 'ability' | 'spell' | 'item' | 'interact' | 'emote' | 'ooc' | 'admin'
export type RollMode = 'normal' | 'advantage' | 'disadvantage'
export type ResultVisibility = 'hidden_until_landed' | 'visible'
export type InteractionType = 'speak_to' | 'act_on' | 'give_to' | 'take_from'
export type InventoryAction = 'pick_up' | 'buy' | 'use' | 'equip' | 'unequip' | 'drop' | 'give' | 'sell'
export type SpellResourcePool = 'auto' | 'standard' | 'pact' | 'arcanum'
export type SceneObjectAction =
  | 'inspect'
  | 'open'
  | 'close'
  | 'lock'
  | 'unlock'
  | 'search'
  | 'break'
  | 'use'
  | 'disarm'
  | 'trigger'
  | 'reset'

export const DICE_OPTIONS = ['d4', 'd6', 'd8', 'd10', 'd12', 'd20', 'd100'] as const
export const PLAIN_ROLL_ABILITY_KEY = 'plain_roll'
export const INITIATIVE_ROLL_ABILITY_KEY = 'initiative_roll'
export const INITIATIVE_ROLL_REASON = 'initiative'
export const INTERACTION_TYPE_OPTIONS: Array<{ value: InteractionType; label: string }> = [
  { value: 'speak_to', label: 'Speak to' },
  { value: 'act_on', label: 'Act on' },
  { value: 'give_to', label: 'Give to' },
  { value: 'take_from', label: 'Take from' },
]
export const INVENTORY_ACTION_OPTIONS: Array<{ value: InventoryAction; label: string }> = [
  { value: 'pick_up', label: 'Pick up' },
  { value: 'buy', label: 'Buy' },
  { value: 'use', label: 'Use' },
  { value: 'equip', label: 'Equip' },
  { value: 'unequip', label: 'Unequip' },
  { value: 'drop', label: 'Drop' },
  { value: 'give', label: 'Give' },
  { value: 'sell', label: 'Sell' },
]

export type AbilityOption = {
  key: string
  label: string
  score: string
  modifier: string
}

export type ItemOption = {
  id?: string
  name: string
  quantity: string
  equipped?: boolean
  slot?: string
}

export function itemOptionSelectionKey(item: ItemOption, index: number) {
  return item.id || `legacy-item-${index}`
}

export type InteractionTarget = {
  kind: 'player' | 'npc'
  player_id?: number
  npc_id?: string
  character_name: string
  player_name: string
  active: boolean
}

export type RollDraft = {
  die: string
  mode: RollMode
  reason: string
  resultVisibility: ResultVisibility
  targetPendingTurnId?: number | null
}

export type PendingRollSpec = {
  die: string
  mode: RollMode
  ruleType: string
  reason: string
  resultVisibility: ResultVisibility
  ability?: {
    key: string
    label: string
  } | null
}

export type PendingRollGuidance = {
  pendingTurnId: number
  ruleType: string
  dcHint: string | null
  prompt: string
  remainingPlayerIds: number[]
  rollSpec: PendingRollSpec
}

export type RollRequiredPayload = PendingRollGuidance & {
  sessionId: number
}

export type RollResult = RollDraft & {
  modifier: number
  rolls: number[]
  kept: number
  total: number
}

export type RollResolvedPayload = {
  session_id: number
  turn_id: number
  player_id: number
  client_message_id: string | null
  pending_turn_id: number | null
  rule_type: string
  die: string
  mode: RollMode
  rolls: number[]
  kept: number
  modifier: number
  total: number
  reason: string
  result_visibility: ResultVisibility
  ability?: { key: string; label: string; score: number | string | null; modifier: number } | null
  proficiency?: { bonus: number; skills: string[]; multiplier?: number }
  modifier_breakdown?: {
    ability_modifier: number
    proficiency_bonus: number
    proficiency_multiplier?: number
    wound_penalty: number
    total: number
  }
  authoritative: true
}

export type ActionIntent = {
  kind: ComposerMode | 'message' | 'capability' | 'object' | 'travel' | 'rest' | 'combat'
  source: 'composer' | 'dice_roller' | 'combat_hud' | 'scene_panel'
  text: string
  client_message_id: string
  roll?: {
    die: string
    mode: RollMode
    result_visibility: ResultVisibility
    reason: string
    target_pending_turn_id?: number
  }
  ability?: {
    key: string
    label: string
    modifier?: number
  }
  item?: {
    id?: string
    name: string
    quantity: number
  }
  spell?: {
    name: string
    effect: string
    resource_pool?: SpellResourcePool
    cast_level?: number
    concentration?: boolean
    target_ids?: string[]
  }
  capability?: {
    id: string
    target_id?: string
    amount?: number
  }
  object?: {
    id: string
    action: SceneObjectAction
    revision?: number
  }
  inventory_action?: InventoryAction
  cost_gold?: number
  interaction?: {
    type: InteractionType
    label: string
  }
  target?: {
    kind?: 'player' | 'npc'
    player_id?: number
    npc_id?: string
    character_name: string
    player_name: string
  }
  location?: {
    id: string
    name: string
  }
  rest_type?: 'short_rest' | 'long_rest'
  combat?: {
    action_id: string
    target_id?: string
  }
}

const COMPOSER_PREFIX_PATTERNS = [
  /^\[OOC\]\s*/i,
  /^\[ADMIN\]\s*/i,
  /^\(\s*ADMIN\s*\)\s*/i,
  /^\/\s*ADMIN\s*\/\s*/i,
  /^\/ooc\s+/i,
  /^\/admin\s+/i,
  /^\/emote\s+/i,
  /^I roll for initiative(?:\s*\([^)]*\))?\s*:\s*/i,
  /^I roll a d\d{1,3}(?:\s*[+-]\s*\d+)?(?:\s+for\s+[^:\n]{1,120})?(?:\s*\([^)]*\))?\s*:\s*/i,
  /^[^:\n]{1,80}\s+attempts an ability check(?:\s*\([^)]*\))?:\s*/i,
  /^[^:\n]{1,80}\s+attempts a [^:\n]{1,40} check(?:\s*\([^)]*\))?:\s*/i,
  /^[^:\n]{1,80}\s+cast(?:s)?\s+[^:\n]{1,80}:\s*/i,
  /^[^:\n]{1,80}\s+cast(?:s)?\s+a spell:\s*/i,
  /^[^:\n]{1,80}\s+tr(?:y|ies) to pick up\s+[^:\n]{1,80}:\s*/i,
  /^[^:\n]{1,80}\s+tr(?:y|ies) to pick up\s+/i,
  /^[^:\n]{1,80}\s+tr(?:y|ies) to buy\s+[^:\n]{1,80}(?:\s+for\s+\d+\s+gold)?:\s*/i,
  /^[^:\n]{1,80}\s+tr(?:y|ies) to buy\s+/i,
  /^[^:\n]{1,80}\s+use(?:s)?\s+[^:\n]{1,80}:\s*/i,
  /^[^:\n]{1,80}\s+use(?:s)?\s+/i,
  /^[^:\n]{1,80}\s+equip(?:s)?\s+[^:\n]{1,80}:\s*/i,
  /^[^:\n]{1,80}\s+equip(?:s)?\s+/i,
  /^[^:\n]{1,80}\s+unequip(?:s)?\s+[^:\n]{1,80}:\s*/i,
  /^[^:\n]{1,80}\s+unequip(?:s)?\s+/i,
  /^[^:\n]{1,80}\s+drop(?:s)?\s+[^:\n]{1,80}:\s*/i,
  /^[^:\n]{1,80}\s+drop(?:s)?\s+/i,
  /^[^:\n]{1,80}\s+give(?:s)?\s+[^:\n]{1,80}:\s*/i,
  /^[^:\n]{1,80}\s+give(?:s)?\s+/i,
  /^[^:\n]{1,80}\s+tr(?:y|ies) to sell\s+[^:\n]{1,80}(?:\s+for\s+\d+\s+gold)?:\s*/i,
  /^[^:\n]{1,80}\s+tr(?:y|ies) to sell\s+/i,
  /^[^:\n]{1,80}\s+says to\s+[^:\n]{1,80}:\s*/i,
  /^[^:\n]{1,80}\s+directs an action at\s+[^:\n]{1,80}:\s*/i,
  /^[^:\n]{1,80}\s+gives something to\s+[^:\n]{1,80}:\s*/i,
  /^[^:\n]{1,80}\s+tries to take something from\s+[^:\n]{1,80}:\s*/i,
]

const RESERVED_ADMIN_PREFIX_PATTERN = /^\s*(?:\[\s*admin\s*\]|\(\s*admin\s*\)|\/\s*admin\s*\/|\/admin(?:\s+|$))/i

export function hasReservedAdminPrefix(value: string) {
  return RESERVED_ADMIN_PREFIX_PATTERN.test(value)
}

export function stripComposerCommand(value: string) {
  let next = value.trimStart()
  COMPOSER_PREFIX_PATTERNS.forEach((pattern) => {
    next = next.replace(pattern, '')
  })
  return next
}

function normalizedWords(value: string) {
  return value.toLowerCase().replace(/[^a-z0-9]+/g, ' ').trim()
}

function includesItemName(message: string, itemName: string) {
  const itemWords = normalizedWords(itemName)
  if (!itemWords) return false
  return normalizedWords(message).includes(itemWords)
}

function itemActionRegex(inventoryAction: InventoryAction) {
  if (inventoryAction === 'pick_up') return /\b(?:pick up|picks up|picked up|grab|grabs|take|takes|collect|collects)\b/i
  if (inventoryAction === 'buy') return /\b(?:buy|buys|bought|purchase|purchases)\b/i
  if (inventoryAction === 'equip') return /\b(?:equip|equips|wield|wields|wear|wears|don|dons|ready|readies|draw|draws)\b/i
  if (inventoryAction === 'unequip') return /\b(?:unequip|unequips|doff|doffs|stow|stows|sheathe|sheathes|take off|takes off|remove|removes)\b/i
  if (inventoryAction === 'drop') return /\b(?:drop|drops|dropped|discard|discards|set down|sets down)\b/i
  if (inventoryAction === 'give') return /\b(?:give|gives|gave|hand|hands|offer|offers|pass|passes)\b/i
  if (inventoryAction === 'sell') return /\b(?:sell|sells|sold|trade|trades)\b/i
  return /\b(?:use|uses|used|attack|attacks|swing|swings|stab|stabs|slash|slashes|draw|draws|brandish|brandishes)\b/i
}

function shouldAttachItemIntent(message: string, itemName: string, inventoryAction: InventoryAction) {
  if (!itemName.trim()) return false
  if (!includesItemName(message, itemName)) return false
  return itemActionRegex(inventoryAction).test(message)
}

export function composerModeLabel(mode: ComposerMode, die: string) {
  if (mode === 'admin') return 'Admin Override'
  if (mode === 'ooc') return 'Out of Character'
  if (mode === 'roll') return `Roll ${die.toUpperCase()}`
  if (mode === 'ability') return 'Ability Check'
  if (mode === 'spell') return 'Spell'
  if (mode === 'item') return 'Item'
  if (mode === 'interact') return 'Player Interaction'
  if (mode === 'emote') return 'Emote'
  return 'In Character'
}

export function interactionTypeLabel(type: InteractionType) {
  return INTERACTION_TYPE_OPTIONS.find((option) => option.value === type)?.label ?? 'Interact with'
}

export function interactionTargetId(target: InteractionTarget) {
  if (target.kind === 'npc') return `npc:${target.npc_id ?? target.character_name}`
  return `player:${target.player_id ?? target.character_name}`
}

export function inventoryActionLabel(type: InventoryAction) {
  return INVENTORY_ACTION_OPTIONS.find((option) => option.value === type)?.label ?? 'Use'
}

export function normalizeDie(die: string) {
  const normalized = die.trim().toLowerCase()
  return DICE_OPTIONS.includes(normalized as (typeof DICE_OPTIONS)[number]) ? normalized : 'd20'
}

export function dieSides(die: string) {
  const parsed = Number(normalizeDie(die).replace(/^d/i, ''))
  return Number.isInteger(parsed) && parsed > 0 ? parsed : 20
}

export function formatModifier(value: number) {
  if (!Number.isFinite(value) || value === 0) return ''
  return value > 0 ? `+${value}` : String(value)
}

export function parseRollModifier(value: string) {
  const parsed = Number(value.replace(/\s+/g, ''))
  if (!Number.isFinite(parsed)) return 0
  return Math.max(-99, Math.min(99, Math.trunc(parsed)))
}

export function abilityModifierValue(ability: AbilityOption | null) {
  if (!ability || ability.modifier === '—') return 0
  return parseRollModifier(ability.modifier)
}

export function isInitiativeRollAbility(ability: AbilityOption | null) {
  return Boolean(ability && ability.key === 'dexterity' && ability.label.toLowerCase() === 'initiative')
}

export function isInitiativeRollReason(reason: string) {
  return reason.trim().toLowerCase() === INITIATIVE_ROLL_REASON
}

export function parsePositiveInteger(value: string, fallback = 1) {
  const parsed = Number(value.replace(/\s+/g, ''))
  if (!Number.isFinite(parsed)) return fallback
  return Math.max(1, Math.min(999, Math.trunc(parsed)))
}

export function parseGoldCost(value: string) {
  const parsed = Number(value.replace(/\s+/g, ''))
  if (!Number.isFinite(parsed)) return 0
  return Math.max(0, Math.min(99999, Math.trunc(parsed)))
}

export function createClientMessageId() {
  const cryptoSource = globalThis.crypto
  if (typeof cryptoSource?.randomUUID === 'function') {
    return cryptoSource.randomUUID()
  }
  if (typeof cryptoSource?.getRandomValues === 'function') {
    const bytes = new Uint8Array(16)
    cryptoSource.getRandomValues(bytes)
    const randomSuffix = Array.from(bytes, (byte) => byte.toString(16).padStart(2, '0')).join('')
    return `local-${randomSuffix}`
  }
  throw new Error('Secure client ID generation requires the Web Crypto API.')
}

export function diceRollMessage(roll: RollResult) {
  if (isInitiativeRollReason(roll.reason)) {
    const dexModifier = formatModifier(roll.modifier)
    const detail =
      roll.mode === 'normal'
        ? dexModifier
          ? ` (d20 ${roll.kept} ${dexModifier} DEX)`
          : ''
        : ` (${roll.mode}; rolls ${roll.rolls.join(', ')}${dexModifier ? `; ${dexModifier} DEX` : ''})`
    return `I roll for initiative: ${roll.total}${detail}`
  }
  const modifier = formatModifier(roll.modifier)
  const rollSummary =
    roll.mode === 'normal'
      ? `${roll.kept}`
      : `${roll.kept} (${roll.mode}; rolls ${roll.rolls.join(', ')})`
  const reason = roll.reason ? ` for ${roll.reason}` : ''
  return `I roll a ${roll.die}${modifier}${reason}: ${rollSummary}${roll.modifier ? ` = ${roll.total}` : ''}`
}

export function diceRollRequestMessage(roll: RollDraft) {
  if (isInitiativeRollReason(roll.reason)) return 'I roll for initiative.'
  const reason = roll.reason ? ` for ${roll.reason}` : ''
  return `I roll a ${normalizeDie(roll.die)}${reason}.`
}

export function abilityActionText(characterName: string, ability: AbilityOption | null, current: string) {
  const body = stripComposerCommand(current)
  const label = ability?.label ?? 'ability'
  const modifier = ability?.modifier && ability.modifier !== '—' ? ` (${ability.modifier})` : ''
  return `${characterName} attempts a ${label} check${modifier}: ${body}`.trim()
}

export function spellActionText(characterName: string, spellName: string, current: string) {
  const body = stripComposerCommand(current)
  const cleanSpellName = spellName.trim() || 'a spell'
  const verb = characterName.trim().toLowerCase() === 'i' ? 'cast' : 'casts'
  return `${characterName} ${verb} ${cleanSpellName}: ${body}`.trim()
}

export function rollActionText(
  die: string,
  ability: AbilityOption | null,
  current: string,
  modifierOverride: number | null = null,
) {
  const body = stripComposerCommand(current)
  if (isInitiativeRollAbility(ability)) return `I roll for initiative: ${body}`.trim()
  const modifier =
    typeof modifierOverride === 'number'
      ? formatModifier(modifierOverride)
      : ability
        ? formatModifier(abilityModifierValue(ability))
        : ''
  const reason = ability ? ` for ${ability.label} check` : ''
  return `I roll a ${normalizeDie(die)}${modifier}${reason}: ${body}`.trim()
}

export function itemActionText(
  characterName: string,
  inventoryAction: InventoryAction,
  itemName: string,
  current: string,
  costGold = '',
) {
  const body = stripComposerCommand(current)
  const cleanItemName = itemName.trim() || 'item'
  const cost = parseGoldCost(costGold)
  const suffix = body ? `: ${body}` : ''
  const firstPerson = characterName.trim().toLowerCase() === 'i'
  const tries = firstPerson ? 'try' : 'tries'
  const uses = firstPerson ? 'use' : 'uses'
  const drops = firstPerson ? 'drop' : 'drops'
  const gives = firstPerson ? 'give' : 'gives'
  const equips = firstPerson ? 'equip' : 'equips'
  const unequips = firstPerson ? 'unequip' : 'unequips'
  if (inventoryAction === 'pick_up') return `${characterName} ${tries} to pick up ${cleanItemName}${suffix}`.trim()
  if (inventoryAction === 'buy') {
    return `${characterName} ${tries} to buy ${cleanItemName}${cost ? ` for ${cost} gold` : ''}${suffix}`.trim()
  }
  if (inventoryAction === 'equip') return `${characterName} ${equips} ${cleanItemName}${suffix}`.trim()
  if (inventoryAction === 'unequip') return `${characterName} ${unequips} ${cleanItemName}${suffix}`.trim()
  if (inventoryAction === 'drop') return `${characterName} ${drops} ${cleanItemName}${suffix}`.trim()
  if (inventoryAction === 'give') return `${characterName} ${gives} ${cleanItemName}${suffix}`.trim()
  if (inventoryAction === 'sell') {
    return `${characterName} ${tries} to sell ${cleanItemName}${cost ? ` for ${cost} gold` : ''}${suffix}`.trim()
  }
  return `${characterName} ${uses} ${cleanItemName}${suffix}`.trim()
}

export function interactionActionText(
  characterName: string,
  target: InteractionTarget | null,
  interactionType: InteractionType,
  current: string,
) {
  const body = stripComposerCommand(current)
  const targetName = target?.character_name || 'another player'
  if (interactionType === 'speak_to') {
    return `${characterName} says to ${targetName}: ${body}`.trim()
  }
  if (interactionType === 'give_to') {
    return `${characterName} gives something to ${targetName}: ${body}`.trim()
  }
  if (interactionType === 'take_from') {
    return `${characterName} tries to take something from ${targetName}: ${body}`.trim()
  }
  return `${characterName} directs an action at ${targetName}: ${body}`.trim()
}

export function composerTextForMode(
  mode: ComposerMode,
  current: string,
  characterName: string,
  die: string,
  ability: AbilityOption | null = null,
  item: ItemOption | null = null,
  interactionTarget: InteractionTarget | null = null,
  interactionType: InteractionType = 'speak_to',
  inventoryAction: InventoryAction = 'use',
  itemName = item?.name ?? '',
  costGold = '',
  spellName = '',
  rollModifierOverride: number | null = null,
) {
  const body = stripComposerCommand(current)
  if (mode === 'roll') return rollActionText(die, ability, current, rollModifierOverride)
  if (mode === 'admin') return `[ADMIN] ${body}`
  if (mode === 'ooc') return `[OOC] ${body}`
  if (mode === 'ability') return abilityActionText(characterName, ability, current)
  if (mode === 'spell') return spellActionText(characterName, spellName, current)
  if (mode === 'item') {
    const resolvedItemName =
      inventoryAction === 'pick_up' || inventoryAction === 'buy'
        ? itemName || 'item'
        : itemName || item?.name || 'item'
    return itemActionText(characterName, inventoryAction, resolvedItemName, current, costGold)
  }
  if (mode === 'interact') {
    return interactionActionText(characterName, interactionTarget, interactionType, current)
  }
  if (mode === 'emote') return `/emote ${body}`
  return body
}

export function buildActionIntent({
  mode,
  message,
  clientMessageId,
  source = 'composer',
  roll,
  ability,
  item,
  inventoryAction = 'use',
  itemName,
  itemQuantity = '1',
  costGold = '0',
  spellName = '',
  interactionType,
  interactionTarget,
}: {
  mode: ComposerMode
  message: string
  clientMessageId: string
  source?: ActionIntent['source']
  roll?: RollDraft | null
  ability?: AbilityOption | null
  item?: ItemOption | null
  inventoryAction?: InventoryAction
  itemName?: string
  itemQuantity?: string
  costGold?: string
  spellName?: string
  interactionType?: InteractionType
  interactionTarget?: InteractionTarget | null
}): ActionIntent {
  const kind = mode === 'action' ? 'message' : mode
  const intent: ActionIntent = {
    kind,
    source,
    text: message,
    client_message_id: clientMessageId,
  }
  if (mode === 'roll' && roll) {
    intent.roll = {
      die: normalizeDie(roll.die),
      mode: roll.mode,
      result_visibility: roll.resultVisibility,
      reason: roll.reason.trim().slice(0, 240),
    }
    if (roll.targetPendingTurnId) {
      intent.roll.target_pending_turn_id = roll.targetPendingTurnId
    }
  }
  if ((mode === 'roll' || mode === 'ability') && ability) {
    intent.ability = {
      key: ability.key,
      label: ability.label,
    }
    if (mode !== 'roll') intent.ability.modifier = abilityModifierValue(ability)
  }
  if (mode === 'spell') {
    const effect = stripComposerCommand(message)
    const name = spellName.trim() || 'spell'
    intent.spell = {
      name,
      effect,
    }
    if (ability) {
      intent.ability = {
        key: ability.key,
        label: ability.label,
        modifier: abilityModifierValue(ability),
      }
    }
  }
  if (mode === 'item') {
    const resolvedItemName = (itemName || item?.name || '').trim()
    if (!shouldAttachItemIntent(message, resolvedItemName, inventoryAction)) {
      intent.kind = 'message'
      return intent
    }
    intent.item = {
      ...(item?.id ? { id: item.id } : {}),
      name: resolvedItemName,
      quantity: parsePositiveInteger(itemQuantity || item?.quantity || '1'),
    }
    intent.inventory_action = inventoryAction
    intent.cost_gold = parseGoldCost(costGold)
  }
  if (mode === 'interact' && interactionTarget) {
    const normalizedInteractionType = interactionType ?? 'speak_to'
    intent.interaction = {
      type: normalizedInteractionType,
      label: interactionTypeLabel(normalizedInteractionType),
    }
    intent.target = {
      kind: interactionTarget.kind,
      character_name: interactionTarget.character_name,
      player_name: interactionTarget.player_name,
    }
    if (interactionTarget.kind === 'npc') {
      intent.target.npc_id = interactionTarget.npc_id
    } else {
      intent.target.player_id = interactionTarget.player_id
    }
  }
  return intent
}
