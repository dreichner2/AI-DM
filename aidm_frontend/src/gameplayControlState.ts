import type { SceneObjectAction } from './gameActions'
import { isRecord, numberValue, stringValue } from './gameSelectors'
import type { JsonRecord } from './types'

export type GameplayTarget = {
  id: string
  name: string
  team: string
  rangeBand: string
  currentHp: number | null
  maxHp: number | null
  conditions: string[]
  available: boolean
  reason: string
}

export type GameplaySpellControl = {
  id: string
  name: string
  level: number
  description: string
  delivery: string
  castingTime: string
  castLevels: number[]
  effectLabel: string
  relation: string
  rangeBands: string[]
  minTargets: number
  maxTargets: number
  concentration: boolean
  prepared: boolean
  resourceLabel: string
  available: boolean
  reason: string
  targets: GameplayTarget[]
}

export type GameplayCapabilityControl = {
  id: string
  name: string
  description: string
  actionEconomy: string
  targetPolicy: string
  effectType: string
  current: number
  max: number
  refreshesOn: string
  available: boolean
  reason: string
  targets: GameplayTarget[]
}

export type GameplayObjectAction = {
  id: SceneObjectAction
  label: string
  available: boolean
  reason: string
}

export type GameplayInteractableControl = {
  id: string
  name: string
  kind: string
  description: string
  revision: number | null
  states: string[]
  available: boolean
  reason: string
  actions: GameplayObjectAction[]
}

export type GameplayItemControl = {
  id: string
  name: string
  quantity: number
  effectSummary: string
  cost: string
  available: boolean
  reason: string
  targets: GameplayTarget[]
}

export type GameplayRestControl = {
  shortAvailable: boolean
  longAvailable: boolean
  reason: string
  hitDice: { current: number; max: number; die: string } | null
}

export type GameplayControlState = {
  actorId: string
  actorName: string
  inCombat: boolean
  activeActorId: string
  isActorTurn: boolean
  actionRemaining: number | null
  bonusActionRemaining: number | null
  concentration: string
  spells: GameplaySpellControl[]
  capabilities: GameplayCapabilityControl[]
  interactables: GameplayInteractableControl[]
  /** Usable items: the server's combat bundle in combat, the server's item effects otherwise. */
  items?: GameplayItemControl[]
  rest?: GameplayRestControl
}

const BLOCKING_CAST_CONDITIONS = new Set([
  'dead',
  'fled',
  'escaped',
  'retreated',
  'withdrawn',
  'surrendered',
  'yielded',
  'unconscious',
  'incapacitated',
  'paralyzed',
  'petrified',
  'stunned',
  'absent',
])

const OBJECT_ACTION_LABELS: Record<SceneObjectAction, string> = {
  inspect: 'Inspect',
  open: 'Open',
  close: 'Close',
  lock: 'Lock',
  unlock: 'Unlock',
  search: 'Search',
  break: 'Break',
  use: 'Use',
  disarm: 'Disarm',
  trigger: 'Trigger',
  reset: 'Reset',
}

function records(value: unknown): JsonRecord[] {
  return Array.isArray(value) ? value.filter(isRecord) : []
}

function integer(value: unknown, fallback = 0) {
  const parsed = numberValue(value)
  return parsed === null ? fallback : Math.floor(parsed)
}

function normalized(value: unknown) {
  return stringValue(value).toLowerCase().replace(/[^a-z0-9]+/g, '_').replace(/^_+|_+$/g, '')
}

function health(record: JsonRecord) {
  const hp = isRecord(record.hp)
    ? record.hp
    : isRecord(record.health)
      ? record.health
      : {}
  return {
    current: numberValue(hp.current ?? hp.currentHp ?? hp.current_hp),
    max: numberValue(hp.max ?? hp.maxHp ?? hp.max_hp),
  }
}

function conditions(record: JsonRecord) {
  const hp = isRecord(record.health) ? record.health : {}
  const raw = Array.isArray(record.conditions)
    ? record.conditions
    : Array.isArray(hp.conditions)
      ? hp.conditions
      : []
  return raw.map((value) => normalized(value)).filter(Boolean)
}

function position(record: JsonRecord) {
  return isRecord(record.position) ? record.position : {}
}

function recordTarget(record: JsonRecord, fallbackTeam = 'player'): GameplayTarget | null {
  const id = stringValue(record.id ?? record.actorId ?? record.participantId)
  if (!id) return null
  const hp = health(record)
  return {
    id,
    name: stringValue(record.name ?? record.characterName ?? record.character_name, id),
    team: normalized(record.team) || fallbackTeam,
    rangeBand: normalized(position(record).rangeBand ?? position(record).range_band) || 'near',
    currentHp: hp.current,
    maxHp: hp.max,
    conditions: conditions(record),
    available: true,
    reason: '',
  }
}

function participantPresent(record: JsonRecord) {
  return record.isPresent !== false && record.present !== false
}

function participantAlive(record: JsonRecord) {
  const hp = health(record)
  return record.isAlive !== false && (hp.current === null || hp.current > 0)
}

function playerCharacter(record: JsonRecord) {
  return normalized(record.kind) === 'player_character' || normalized(record.team) === 'player'
}

// Healing and ally spells may reach a player character at 0 HP who is not dead.
function spellRestoresDowned(spell: JsonRecord) {
  const targetSpec = isRecord(spell.target) ? spell.target : {}
  return (
    normalized(targetSpec.relation ?? targetSpec.team) === 'ally' ||
    records(spell.effects).some((effect) => normalized(effect.kind) === 'healing')
  )
}

function friendly(caster: JsonRecord, target: JsonRecord) {
  const casterTeam = normalized(caster.team) || 'player'
  const targetTeam = normalized(target.team) || 'player'
  if (casterTeam === 'player' || casterTeam === 'ally') {
    return targetTeam === 'player' || targetTeam === 'ally'
  }
  return casterTeam === targetTeam
}

function coverTypeForTarget(combat: JsonRecord, target: JsonRecord) {
  const targetPosition = position(target)
  const coverId = stringValue(targetPosition.coverId ?? targetPosition.cover_id)
  const battlefield = isRecord(combat.battlefield) ? combat.battlefield : {}
  const cover = records(battlefield.cover).find((entry) => stringValue(entry.id) === coverId)
  return normalized(cover?.coverType ?? cover?.cover_type)
}

function targetForSpell(
  combat: JsonRecord,
  caster: JsonRecord,
  target: JsonRecord,
  spellTarget: JsonRecord,
  restoresDowned: boolean,
  ignoreBattlefieldRange = false,
): GameplayTarget | null {
  const summary = recordTarget(target, normalized(target.team) || 'player')
  if (!summary) return null
  const dead = summary.conditions.includes('dead')
  const reachesDowned = restoresDowned && playerCharacter(target) && !dead
  const casterId = stringValue(caster.id)
  const relation = normalized(spellTarget.relation ?? spellTarget.team) || 'enemy'
  const allowSelfValue = spellTarget.allowSelf ?? spellTarget.allow_self
  const allowSelf = allowSelfValue === undefined
    ? ['ally', 'self', 'any'].includes(relation)
    : allowSelfValue === true
  const allowDefeated = (spellTarget.allowDefeated ?? spellTarget.allow_defeated) === true
  const requiresPresent = (spellTarget.requiresPresent ?? spellTarget.requires_present) !== false
  const requiresLineOfSight = (spellTarget.requiresLineOfSight ?? spellTarget.requires_line_of_sight) !== false
  const requiresSameZone = (spellTarget.requiresSameZone ?? spellTarget.requires_same_zone) === true
  const ignoreCover = (spellTarget.ignoreCover ?? spellTarget.ignore_cover) === true
  const rawRangeBands = spellTarget.rangeBands ?? spellTarget.range_bands
  const rangeBands = Array.isArray(rawRangeBands)
    ? rawRangeBands.map((value) => normalized(value)).filter(Boolean)
    : ['melee', 'near', 'far', 'distant']
  const casterPosition = position(caster)
  const targetPosition = position(target)
  const casterZone = stringValue(casterPosition.zoneId ?? casterPosition.zone_id)
  const targetZone = stringValue(targetPosition.zoneId ?? targetPosition.zone_id)

  let reason = ''
  if (requiresPresent && !participantPresent(target)) {
    reason = 'Not physically present.'
  } else if (!allowDefeated && !participantAlive(target) && !reachesDowned) {
    reason = restoresDowned && dead ? 'Healing cannot restore the dead.' : 'Already defeated.'
  } else if (relation === 'self' && summary.id !== casterId) {
    reason = 'Requires the caster.'
  } else if (relation === 'ally' && !friendly(caster, target)) {
    reason = 'Requires an ally.'
  } else if (relation === 'enemy' && (friendly(caster, target) || summary.team === 'neutral')) {
    reason = 'Requires a hostile target.'
  } else if (summary.id === casterId && !allowSelf) {
    reason = 'Cannot target the caster.'
  } else if (!ignoreBattlefieldRange && summary.id !== casterId && !rangeBands.includes(summary.rangeBand)) {
    reason = `Out of range (${summary.rangeBand}).`
  } else if (!ignoreBattlefieldRange && summary.id !== casterId && requiresSameZone && casterZone && targetZone !== casterZone) {
    reason = 'In another battlefield zone.'
  } else if (
    !ignoreBattlefieldRange && summary.id !== casterId &&
    requiresLineOfSight &&
    (targetPosition.isHidden ?? targetPosition.is_hidden) === true
  ) {
    reason = 'Hidden from the caster.'
  } else if (
    !ignoreBattlefieldRange && summary.id !== casterId &&
    requiresLineOfSight &&
    !ignoreCover &&
    coverTypeForTarget(combat, target) === 'full'
  ) {
    reason = 'Has full cover.'
  }
  return { ...summary, available: !reason, reason }
}

function spellUsesPreparation(spell: JsonRecord, spellbook: JsonRecord) {
  const policy = isRecord(spellbook.preparationPolicy)
    ? spellbook.preparationPolicy
    : isRecord(spellbook.preparation_policy)
      ? spellbook.preparation_policy
      : {}
  if (policy.requiresPreparation !== true || integer(spell.level) <= 0) return false
  const sourceType = normalized(spell.sourceType ?? spell.source_type)
  if (['race', 'race_catalog', 'ancestry', 'innate'].includes(sourceType)) return false
  return spell.requiresPreparation !== false && spell.requires_preparation !== false
}

function spellPrepared(spell: JsonRecord, spellbook: JsonRecord) {
  if (!spellUsesPreparation(spell, spellbook)) return true
  const rawPrepared = Array.isArray(spellbook.preparedSpells)
    ? spellbook.preparedSpells
    : Array.isArray(spellbook.prepared_spells)
      ? spellbook.prepared_spells
      : []
  const prepared = new Set(rawPrepared.map((value) => normalized(isRecord(value) ? value.name ?? value.id : value)))
  return prepared.has(normalized(spell.name)) || prepared.has(normalized(spell.id))
}

function spellResource(spellLevel: number, rawResources: JsonRecord) {
  if (spellLevel <= 0) return { available: true, label: 'Cantrip' }
  const slots = isRecord(rawResources.slots) ? rawResources.slots : {}
  const standard = Object.entries(slots)
    .map(([level, value]) => ({
      level: integer(level),
      current: isRecord(value) ? integer(value.current ?? value.remaining) : integer(value),
    }))
    .filter((entry) => entry.level >= spellLevel && entry.current > 0)
    .sort((left, right) => left.level - right.level)[0]
  if (standard) {
    return {
      available: true,
      label: `Level ${standard.level} slot · ${standard.current} left`,
    }
  }
  const pact = isRecord(rawResources.pactSlots)
    ? rawResources.pactSlots
    : isRecord(rawResources.pact_slots)
      ? rawResources.pact_slots
      : {}
  const pactLevel = integer(pact.slotLevel ?? pact.slot_level)
  const pactCurrent = integer(pact.current ?? pact.remaining)
  if (pactLevel >= spellLevel && pactCurrent > 0) {
    return {
      available: true,
      label: `Level ${pactLevel} pact slot · ${pactCurrent} left`,
    }
  }
  const arcanum = isRecord(rawResources.mysticArcanum)
    ? rawResources.mysticArcanum
    : isRecord(rawResources.mystic_arcanum)
      ? rawResources.mystic_arcanum
      : {}
  const arcanumEntry = arcanum[String(spellLevel)]
  const arcanumCurrent = isRecord(arcanumEntry)
    ? integer(arcanumEntry.current ?? arcanumEntry.remaining)
    : integer(arcanumEntry)
  if (arcanumCurrent > 0) {
    return {
      available: true,
      label: `Level ${spellLevel} Mystic Arcanum · ${arcanumCurrent} left`,
    }
  }
  return { available: false, label: `No level ${spellLevel}+ resource remains` }
}

function availableCastLevels(spellLevel: number, resources: JsonRecord): number[] {
  if (spellLevel <= 0) return []
  const levels = new Set<number>()
  const slots = isRecord(resources.slots) ? resources.slots : {}
  for (const [level, value] of Object.entries(slots)) {
    const available = isRecord(value) ? integer(value.current ?? value.remaining) : integer(value)
    const parsed = integer(level)
    if (available > 0 && parsed >= spellLevel && parsed <= 9) levels.add(parsed)
  }
  const pact = isRecord(resources.pactSlots) ? resources.pactSlots : isRecord(resources.pact_slots) ? resources.pact_slots : {}
  const pactLevel = integer(pact.slotLevel ?? pact.slot_level)
  if (integer(pact.current ?? pact.remaining) > 0 && pactLevel >= spellLevel && pactLevel <= 9) levels.add(pactLevel)
  return [...levels].sort((a, b) => a - b)
}

function effectLabel(spell: JsonRecord) {
  const labels = records(spell.effects).map((effect) => {
    const kind = normalized(effect.kind).replace(/_/g, ' ')
    const detail = stringValue(effect.damageType ?? effect.condition ?? effect.dice)
    return [kind, detail].filter(Boolean).join(' · ')
  })
  return labels.join(', ') || 'Authoritative spell effect'
}

function spellControls(
  actor: JsonRecord,
  playerCharacters: JsonRecord[],
  activePlayerIds: Set<number>,
  combat: JsonRecord,
  inCombat: boolean,
  isActorTurn: boolean,
  actionRemaining: number | null,
  bonusActionRemaining: number | null,
  leveledSpellCast: boolean,
) {
  const spellbook = isRecord(actor.spellbook) ? actor.spellbook : {}
  const resources = isRecord(actor.spellResources)
    ? actor.spellResources
    : isRecord(actor.spell_resources)
      ? actor.spell_resources
      : {}
  const participants: JsonRecord[] = inCombat ? records(combat.participants) : playerCharacters
    .filter((candidate) => stringValue(candidate.id) === stringValue(actor.id) || activePlayerIds.has(integer(candidate.playerId ?? candidate.player_id, -1)))
    .map((candidate) => ({ ...candidate, team: 'player', kind: 'player_character' }))
  const caster = participants.find((participant) => stringValue(participant.id) === stringValue(actor.id))
  const casterConditions = caster ? conditions(caster) : []
  const casterHp = caster ? health(caster) : health(actor)
  return records(spellbook.knownSpells ?? spellbook.known_spells)
    .filter((spell) => spell.authoritativeEffect === true)
    .map((spell): GameplaySpellControl | null => {
      const id = stringValue(spell.id)
      const name = stringValue(spell.name)
      if (!id || !name) return null
      const targetSpec = isRecord(spell.target) ? spell.target : {}
      const rawRangeBands = targetSpec.rangeBands ?? targetSpec.range_bands
      const rangeBands = Array.isArray(rawRangeBands)
        ? rawRangeBands.map((value) => normalized(value)).filter(Boolean)
        : ['melee', 'near', 'far', 'distant']
      const minTargets = Math.max(1, integer(targetSpec.minTargets ?? targetSpec.min_targets, 1))
      const maxTargets = Math.max(minTargets, integer(targetSpec.maxTargets ?? targetSpec.max_targets, 1))
      const prepared = spellPrepared(spell, spellbook)
      const spellLevel = Math.max(0, integer(spell.level))
      const resource = spellResource(spellLevel, resources)
      const castingTime = normalized(spell.castingTime ?? spell.casting_time) || 'action'
      const economyRemaining = castingTime === 'bonus_action' ? bonusActionRemaining : actionRemaining
      const restoresDowned = spellRestoresDowned(spell)
      const targets = caster
        ? participants
            .map((participant) => targetForSpell(combat, caster, participant, targetSpec, restoresDowned, !inCombat))
            .filter((target): target is GameplayTarget => target !== null)
        : []
      const requiresCombat = spell.outOfCombat !== true && spell.requireActiveCombat !== false && spell.require_active_combat !== false
      const requiresTurn = inCombat && spell.requireActiveTurn !== false && spell.require_active_turn !== false
      let reason = ''
      if (requiresCombat && !inCombat) {
        reason = 'Requires an active encounter.'
      } else if (!caster) {
        reason = 'Caster is not present in the encounter.'
      } else if (
        caster.isAlive === false ||
        caster.isConscious === false ||
        (casterHp.current !== null && casterHp.current <= 0) ||
        casterConditions.some((condition) => BLOCKING_CAST_CONDITIONS.has(condition))
      ) {
        reason = 'Caster cannot act.'
      } else if (requiresTurn && !isActorTurn) {
        reason = 'Only the active combat actor may cast.'
      } else if (castingTime === 'reaction') {
        reason = 'This reaction triggers automatically.'
      } else if (inCombat && economyRemaining === null) {
        reason = 'Turn economy is unavailable; refresh the session.'
      } else if (inCombat && economyRemaining !== null && economyRemaining <= 0) {
        reason = castingTime === 'bonus_action' ? 'The bonus action for this turn is already spent.' : 'The action for this turn is already spent.'
      } else if (inCombat && spellLevel > 0 && leveledSpellCast) {
        reason = 'Only one levelled spell can be cast per turn.'
      } else if (!prepared) {
        reason = 'Known but not prepared.'
      } else if (!resource.available) {
        reason = resource.label
      } else if (targets.filter((target) => target.available).length < minTargets) {
        reason = 'No legal visible target is available.'
      }
      const delivery = isRecord(spell.delivery) ? normalized(spell.delivery.type) : ''
      return {
        id,
        name,
        level: spellLevel,
        description: stringValue(spell.description),
        delivery: delivery.replace(/_/g, ' ') || 'automatic',
        castingTime,
        castLevels: availableCastLevels(spellLevel, resources),
        effectLabel: stringValue(spell.effectSummary) || effectLabel(spell),
        relation: normalized(targetSpec.relation) || 'enemy',
        rangeBands,
        minTargets,
        maxTargets,
        concentration: spell.concentration === true,
        prepared,
        resourceLabel: resource.label,
        available: !reason,
        reason,
        targets,
      }
    })
    .filter((spell): spell is GameplaySpellControl => spell !== null)
    .sort((left, right) => left.level - right.level || left.name.localeCompare(right.name))
}

function capabilityTargets(
  actor: JsonRecord,
  playerCharacters: JsonRecord[],
  combat: JsonRecord,
  inCombat: boolean,
  policy: string,
  effectType: string,
) {
  let rawTargets: JsonRecord[]
  if (policy === 'self') {
    const participant = records(combat.participants)
      .find((entry) => stringValue(entry.id) === stringValue(actor.id))
    rawTargets = [participant ?? actor]
  } else if (inCombat) {
    const participants = records(combat.participants)
    const actorParticipant = participants.find((entry) => stringValue(entry.id) === stringValue(actor.id))
    rawTargets = participants.filter((entry) => !actorParticipant || friendly(actorParticipant, entry))
  } else {
    rawTargets = playerCharacters
  }
  return rawTargets
    .map((record) => recordTarget(record))
    .filter((target): target is GameplayTarget => target !== null)
    .map((target) => {
      let reason = ''
      if (effectType === 'heal' || effectType === 'healing_pool') {
        // Lay on Hands reaches a downed ally; Second Wind cannot be used while down.
        if (effectType === 'healing_pool' && target.conditions.includes('dead')) {
          reason = 'Healing cannot restore the dead.'
        } else if (effectType === 'heal' && target.currentHp !== null && target.currentHp <= 0) {
          reason = 'Cannot restore a defeated target.'
        } else if (
          target.currentHp !== null &&
          target.maxHp !== null &&
          target.currentHp >= target.maxHp
        ) {
          reason = 'Already at full hit points.'
        }
      }
      return { ...target, available: !reason, reason }
    })
}

function capabilityControls(
  actor: JsonRecord,
  playerCharacters: JsonRecord[],
  combat: JsonRecord,
  inCombat: boolean,
  isActorTurn: boolean,
  actionRemaining: number | null,
  bonusActionRemaining: number | null,
) {
  const featureState = isRecord(actor.classFeatureState)
    ? actor.classFeatureState
    : isRecord(actor.class_feature_state)
      ? actor.class_feature_state
      : {}
  return records(actor.classFeatures ?? actor.class_features)
    .map((feature): GameplayCapabilityControl | null => {
      const id = stringValue(feature.id)
      const name = stringValue(feature.name)
      if (!id || !name) return null
      const persisted = isRecord(featureState[id]) ? featureState[id] : {}
      const maximum = Math.max(1, integer(persisted.max ?? feature.maxUses ?? feature.max_uses, 1))
      const current = Math.max(0, Math.min(maximum, integer(persisted.current ?? persisted.remaining, maximum)))
      const effect = isRecord(feature.effect) ? feature.effect : {}
      const effectType = normalized(effect.type)
      const targetPolicy = normalized(feature.targetPolicy ?? feature.target_policy) || 'self'
      const actionEconomy = normalized(feature.actionEconomy ?? feature.action_economy) || 'action'
      const targets = capabilityTargets(
        actor,
        playerCharacters,
        combat,
        inCombat,
        targetPolicy,
        effectType,
      )
      let reason = ''
      if (current <= 0) {
        reason = 'No uses remain.'
      } else if (inCombat && !isActorTurn) {
        reason = 'Only the active combat actor may use this capability.'
      } else if (inCombat && actionEconomy === 'action' && actionRemaining === null) {
        reason = 'Turn economy is unavailable; refresh the session.'
      } else if (inCombat && actionEconomy === 'action' && actionRemaining !== null && actionRemaining <= 0) {
        reason = 'The action for this turn is already spent.'
      } else if (
        inCombat &&
        actionEconomy === 'bonus_action' &&
        bonusActionRemaining === null
      ) {
        reason = 'Turn economy is unavailable; refresh the session.'
      } else if (
        inCombat &&
        actionEconomy === 'bonus_action' &&
        bonusActionRemaining !== null &&
        bonusActionRemaining <= 0
      ) {
        reason = 'The bonus action for this turn is already spent.'
      } else if (effectType === 'restore_action' && !inCombat) {
        reason = 'Available only during active combat.'
      } else if (effectType === 'restore_action' && actionRemaining === null) {
        reason = 'Turn economy is unavailable; refresh the session.'
      } else if (effectType === 'restore_action' && (actionRemaining ?? 1) > 0) {
        reason = 'The action for this turn is still available.'
      } else if (targets.every((target) => !target.available)) {
        reason = 'No legal target is available.'
      }
      return {
        id,
        name,
        description: stringValue(feature.description),
        actionEconomy: actionEconomy.replace(/_/g, ' '),
        targetPolicy,
        effectType,
        current,
        max: maximum,
        refreshesOn: stringValue(persisted.refreshesOn ?? feature.refreshesOn ?? feature.refreshes_on)
          .replace(/_/g, ' '),
        available: !reason,
        reason,
        targets,
      }
    })
    .filter((feature): feature is GameplayCapabilityControl => feature !== null)
}

function objectStates(entry: JsonRecord) {
  const states: string[] = []
  const state = (key: string, whenTrue: string, whenFalse?: string) => {
    if (entry[key] === true) states.push(whenTrue)
    else if (entry[key] === false && whenFalse) states.push(whenFalse)
  }
  state('open', 'Open', 'Closed')
  state('locked', 'Locked', 'Unlocked')
  state('broken', 'Broken')
  state('searched', 'Searched')
  state('inspected', 'Inspected')
  state('used', 'Used')
  state('depleted', 'Depleted')
  state('active', 'Active', 'Inactive')
  state('triggered', 'Triggered')
  state('disarmed', 'Disarmed')
  const remaining = numberValue(entry.usesRemaining)
  if (remaining !== null) states.push(`${Math.max(0, Math.floor(remaining))} uses left`)
  return states
}

function objectActions(entry: JsonRecord, kind: string): GameplayObjectAction[] {
  const candidates = new Set<SceneObjectAction>(['inspect'])
  if (kind === 'door' || kind === 'container' || entry.open !== undefined) {
    candidates.add('open')
    candidates.add('close')
  }
  if (kind === 'lock' || entry.locked !== undefined) {
    candidates.add('lock')
    candidates.add('unlock')
  }
  if (kind === 'container' || entry.searched !== undefined) candidates.add('search')
  if (kind === 'object' || entry.used !== undefined || entry.usesRemaining !== undefined) candidates.add('use')
  if (entry.broken !== undefined) candidates.add('break')
  if (kind === 'hazard') {
    candidates.add('trigger')
    candidates.add('disarm')
    if (entry.triggered === true || entry.disarmed === true) candidates.add('reset')
  }
  return [...candidates].map((action) => {
    let reason = ''
    if (action === 'inspect' && entry.inspected === true) reason = 'Already inspected.'
    if (action === 'open' && entry.open === true) reason = 'Already open.'
    if (action === 'open' && entry.locked === true) reason = 'Locked.'
    if (action === 'open' && entry.broken === true) reason = 'Broken.'
    if (action === 'close' && entry.open !== true) reason = 'Already closed.'
    if (action === 'close' && entry.broken === true) reason = 'Broken.'
    if (action === 'lock' && entry.open === true) reason = 'Must be closed first.'
    if (action === 'lock' && entry.locked === true) reason = 'Already locked.'
    if (action === 'lock' && entry.broken === true) reason = 'Broken.'
    if (action === 'unlock' && entry.locked !== true) reason = 'Already unlocked.'
    if (action === 'unlock' && entry.broken === true) reason = 'Broken.'
    if (action === 'search' && entry.searched === true) reason = 'Already searched.'
    if (action === 'break' && entry.broken === true) reason = 'Already broken.'
    if (action === 'use' && (entry.depleted === true || integer(entry.usesRemaining, 1) <= 0)) {
      reason = 'Depleted.'
    }
    if (action === 'disarm' && entry.disarmed === true) reason = 'Already disarmed.'
    if (action === 'trigger' && entry.triggered === true) reason = 'Already triggered.'
    return {
      id: action,
      label: OBJECT_ACTION_LABELS[action],
      available: !reason,
      reason,
    }
  })
}

function interactableControls(scene: JsonRecord, inCombat: boolean) {
  return [
    ...records(scene.interactables).map((entry) => ({ entry, collection: 'interactable' })),
    ...records(scene.hazards).map((entry) => ({ entry, collection: 'hazard' })),
  ].map(({ entry, collection }): GameplayInteractableControl => {
    const kind = normalized(entry.kind) || collection
    const revision = numberValue(entry.revision)
    const reason = inCombat
      ? 'Scene interactions are unavailable during combat until they consume turn economy.'
      : ''
    return {
      id: stringValue(entry.id),
      name: stringValue(entry.name, 'Scene object'),
      kind: kind.replace(/_/g, ' '),
      description: stringValue(entry.description),
      revision: revision === null ? null : Math.max(0, Math.floor(revision)),
      states: objectStates(entry),
      available: Boolean(stringValue(entry.id)) && !reason,
      reason,
      actions: objectActions(entry, kind),
    }
  }).filter((entry) => Boolean(entry.id))
}

const COST_LABELS: Record<string, string> = { action: 'action', bonus_action: 'bonus action' }

function costLabel(effect: JsonRecord) {
  const self = COST_LABELS[normalized(effect.cost)] ?? 'action'
  const ally = COST_LABELS[normalized(effect.allyCost ?? effect.ally_cost)]
  return ally && ally !== self ? `${self}; ${ally} for an ally` : self
}

function economyCostLabel(economy: JsonRecord) {
  if (integer(economy.bonusAction) > 0) return 'bonus action'
  return integer(economy.action) > 0 ? 'action' : 'no action'
}

function bundleItemControls(combat: JsonRecord, actorId: string): GameplayItemControl[] {
  const bundle = records(combat.legalActions).find((entry) => stringValue(entry.actorId) === actorId)
  return records(bundle?.actions)
    .filter((action) => stringValue(action.id).startsWith('item.use.'))
    .map((action) => {
      const item = isRecord(action.item) ? action.item : {}
      return {
        id: stringValue(item.id),
        name: stringValue(item.name, 'Item'),
        quantity: Math.max(1, integer(item.quantity, 1)),
        effectSummary: stringValue(action.description),
        cost: economyCostLabel(isRecord(action.economy) ? action.economy : {}),
        available: action.available === true,
        reason: stringValue(action.reason),
        targets: records(action.targets).map((target) => ({
          id: stringValue(target.id),
          name: stringValue(target.name, 'Target'),
          team: 'unknown',
          rangeBand: normalized(target.rangeBand) || 'near',
          currentHp: null,
          maxHp: null,
          conditions: [],
          available: target.available === true,
          reason: stringValue(target.reason),
        })),
      }
    })
}

function itemTargets(actor: JsonRecord, playerCharacters: JsonRecord[], rule: string) {
  const actorId = stringValue(actor.id)
  let candidates: JsonRecord[] = []
  if (rule === 'self' || rule === 'spell_self') candidates = [actor]
  else if (rule === 'self_or_ally' || rule === 'spell_ally') candidates = playerCharacters
  else if (rule === 'dying_ally') candidates = playerCharacters.filter((entry) => stringValue(entry.id) !== actorId)
  return candidates
    .map((record) => recordTarget(record))
    .filter((target): target is GameplayTarget => target !== null)
    .map((target) => {
      let reason = ''
      if (rule === 'self_or_ally' && target.conditions.includes('dead')) reason = 'Healing cannot restore the dead.'
      if (rule === 'dying_ally' && !target.conditions.includes('dying')) reason = 'Not dying.'
      return { ...target, available: !reason, reason }
    })
}

function itemControls(
  actor: JsonRecord,
  playerCharacters: JsonRecord[],
  combat: JsonRecord,
  inCombat: boolean,
): GameplayItemControl[] {
  if (inCombat) return bundleItemControls(combat, stringValue(actor.id))
  const inventory = isRecord(actor.inventory) ? actor.inventory : {}
  return records(inventory.items)
    .filter((item) => isRecord(item.itemEffect) && Boolean(stringValue(item.id)))
    .map((item) => {
      const effect = item.itemEffect as JsonRecord
      const rule = normalized(effect.target)
      const targets = itemTargets(actor, playerCharacters, rule)
      let reason = stringValue(effect.unusableReason)
      if (!reason && (rule === 'enemy' || rule === 'spell_enemy')) reason = 'Requires an active encounter.'
      if (!reason && !targets.some((target) => target.available)) reason = 'No legal target is available.'
      return {
        id: stringValue(item.id),
        name: stringValue(item.name, 'Item'),
        quantity: Math.max(1, integer(item.quantity, 1)),
        effectSummary: stringValue(effect.summary),
        cost: costLabel(effect),
        available: !reason,
        reason,
        targets,
      }
    })
}

const MAX_SHORT_RESTS_BETWEEN_LONG = 2

function scenePolicy(scene: JsonRecord) {
  const authored = normalized(scene.restPolicy)
  if (['safe', 'risky', 'none'].includes(authored)) return authored
  const danger = integer(scene.dangerLevel)
  if (danger >= 6) return 'none'
  return danger >= 3 ? 'risky' : 'safe'
}

function restControl(actor: JsonRecord, root: JsonRecord, scene: JsonRecord, inCombat: boolean): GameplayRestControl {
  const clock = isRecord(root.clock) ? root.clock : {}
  const day = Math.max(1, integer(clock.day, 1))
  const lastLongRestDay = numberValue(clock.lastLongRestDay)
  const actorConditions = conditions(actor)
  const hp = health(actor)
  const rawHitDice = isRecord(actor.hitDice) ? actor.hitDice : null
  const policy = scenePolicy(scene)
  let general = ''
  if (inCombat) general = 'Rest is unavailable while combat is active.'
  else if (
    actorConditions.includes('dead') ||
    actorConditions.includes('dying') ||
    (hp.current !== null && hp.current <= 0 && !actorConditions.includes('stable'))
  ) {
    general = 'A dead or dying character cannot complete a rest.'
  } else if (policy === 'none') general = 'It is too dangerous to rest here.'
  const shortReason = general || (
    integer(clock.shortRestsSinceLong) >= MAX_SHORT_RESTS_BETWEEN_LONG
      ? 'You need a long rest before another short rest helps.'
      : ''
  )
  let longReason = general
  if (!longReason && policy === 'risky') longReason = "This place is too dangerous for a full night's rest."
  if (!longReason && lastLongRestDay !== null && day <= lastLongRestDay) longReason = 'You have already rested today.'
  return {
    shortAvailable: !shortReason,
    longAvailable: !longReason,
    reason: longReason || shortReason,
    hitDice: rawHitDice
      ? {
          current: Math.max(0, integer(rawHitDice.current)),
          max: Math.max(1, integer(rawHitDice.max, 1)),
          die: stringValue(rawHitDice.die),
        }
      : null,
  }
}

export function gameplayControlsFromSnapshot(
  snapshot: unknown,
  selectedPlayerId: number | null,
): GameplayControlState {
  const root = isRecord(snapshot) ? snapshot : {}
  const playerCharacters = records(root.playerCharacters)
  const activePlayerIds = new Set(Array.isArray(root.activePlayerIds) ? root.activePlayerIds.map((value) => integer(value, -1)) : [])
  const actor = selectedPlayerId === null
    ? undefined
    : playerCharacters.find((entry) =>
        integer(entry.playerId ?? entry.player_id, -1) === selectedPlayerId,
      )
  const scene = isRecord(root.currentScene) ? root.currentScene : {}
  const combat = isRecord(root.combat) ? root.combat : {}
  const flags = isRecord(combat.flags) ? combat.flags : {}
  const economy = isRecord(flags.turnEconomy)
    ? flags.turnEconomy
    : isRecord(flags.turn_economy)
      ? flags.turn_economy
      : {}
  const inCombat = ['starting', 'active'].includes(normalized(combat.status))
  const activeActorId = stringValue(flags.activeActorId ?? flags.active_actor_id)
  const actorId = stringValue(actor?.id)
  const isActorTurn = !inCombat || Boolean(actorId && activeActorId === actorId)
  const actionRemaining = numberValue(economy.actionRemaining ?? economy.action_remaining)
  const bonusActionRemaining = numberValue(
    economy.bonusActionRemaining ?? economy.bonus_action_remaining,
  )
  const resources = actor && isRecord(actor.spellResources)
    ? actor.spellResources
    : actor && isRecord(actor.spell_resources)
      ? actor.spell_resources
      : {}
  const concentration = isRecord(resources.concentration)
    ? stringValue(resources.concentration.spellName ?? resources.concentration.spell_name)
    : ''
  return {
    actorId,
    actorName: stringValue(actor?.name ?? actor?.characterName ?? actor?.character_name),
    inCombat,
    activeActorId,
    isActorTurn,
    actionRemaining,
    bonusActionRemaining,
    concentration,
    spells: actor
      ? spellControls(actor, playerCharacters, activePlayerIds, combat, inCombat, isActorTurn, actionRemaining, bonusActionRemaining, economy.leveledSpellCast === true)
      : [],
    capabilities: actor
      ? capabilityControls(
          actor,
          playerCharacters,
          combat,
          inCombat,
          isActorTurn,
          actionRemaining,
          bonusActionRemaining,
        )
      : [],
    interactables: interactableControls(scene, inCombat),
    items: actor ? itemControls(actor, playerCharacters, combat, inCombat) : [],
    rest: actor ? restControl(actor, root, scene, inCombat) : undefined,
  }
}
