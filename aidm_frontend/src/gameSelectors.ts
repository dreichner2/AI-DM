import {
  normalizeDie,
  type AbilityOption,
  type ItemOption,
  type PendingRollGuidance,
  type ResultVisibility,
  type RollMode,
} from './gameActions'
import type {
  CampaignSegment,
  JsonRecord,
  MapItem,
  Player,
  SessionLogEntry,
  StreamingTurn,
  TimelineEntry,
  TimelineRole,
} from './types'

export type InventoryRow = {
  id: string
  item: string
  count: string
  weight: string
  icon: string
  weightValue: number | null
  type: string
  subtype: string
  equipped: boolean
  slot: string
  equippable: boolean
}

export type StatBlock = {
  hp: string
  ac: string
  init: string
  speed: string
  abilities: Array<[string, string, string]>
  proficiency: string
  inspiration: boolean
}

export type XpProgress = {
  current: number
  max: number
  percent: number
  label: string
}

export type SpellSummary = {
  id: string
  name: string
  level: number
  levelLabel: string
  source: string
  sourceType: string
  sourceDetail: string
  description: string
  tags: string[]
  catalog: string
  prepared: boolean
}

export type SpellbookSummary = {
  knownSpells: SpellSummary[]
  preparedSpellNames: string[]
  sources: string[]
}

export type CharacterTraitSummary = {
  id: string
  name: string
  category: string
  typeLabel: string
  source: string
  description: string
  actionType: string
  cooldown: string
  active: boolean
}

export type MapPanelMeta = {
  explored: string
  threat: string
  threatTone: 'low' | 'medium' | 'high'
  weather: string
}

export type WorldQuestSummary = {
  id: string
  title: string
  status: string
  stage: string
}

export type WorldLocationSummary = {
  id: string
  name: string
  status: string
  type: string
}

export type WorldNpcSummary = {
  id: string
  name: string
  race: string
  role: string
  disposition: string
  status: string
}

export type CombatParticipantSummary = {
  id: string
  name: string
  team: string
  kind: string
  source: string
  health: string
  healthTone: 'healthy' | 'hurt' | 'critical' | 'down'
  conditions: string[]
  morale: string
  moraleEvents: string[]
  intent: string
  telegraph: string
  tacticSource: string
  brainSource: string
  position: string
  selectionScore: string
  selectionMethod: string
}

export type CombatLegalTarget = {
  id: string
  name: string
  rangeBand: string
  available: boolean
  reason: string
}

export type CombatActionEconomy = {
  action: number
  movement: string
  endsTurn: boolean
  tracking: string
  reactionTracked: boolean
  subTurnCountersTracked: boolean
}

export type CombatLegalAction = {
  id: string
  type: string
  label: string
  description: string
  message: string
  available: boolean
  reason: string
  requiresTarget: boolean
  authoritative: boolean
  economy: CombatActionEconomy
  targets: CombatLegalTarget[]
  rangeClassification: string
  allowedRangeBands: string[]
  weaponName: string
}

export type CombatLegalActionBundle = {
  schemaVersion: number
  playerId: number
  actorId: string
  actorName: string
  round: number
  currentActorId: string
  currentActorName: string
  isCurrentActor: boolean
  economyTracking: string
  subTurnCountersTracked: boolean
  actions: CombatLegalAction[]
}

export type CombatStatePanel = {
  active: boolean
  status: string
  round: string
  battlefield: string
  goal: string
  creatureSource: string
  resolverMethod: string
  tacticalLevel: string
  endReason: string
  combatStartedBy: string
  enemyGroupSummary: string
  initiativeRequired: boolean
  debugEnabled: boolean
  enemies: CombatParticipantSummary[]
  allies: CombatParticipantSummary[]
  telegraphs: string[]
  legalActionBundles: CombatLegalActionBundle[]
}

export type WorldStatePanel = {
  sceneName: string
  sceneType: string
  mood: string
  dangerLevel: string
  activeQuests: WorldQuestSummary[]
  knownLocations: WorldLocationSummary[]
  knownNpcs: WorldNpcSummary[]
  combat: CombatStatePanel
}

export type PendingRollOption = {
  turnId: number
  label: string
  detail: string
  guidance: PendingRollGuidance
}

export type PendingRollNotice = {
  turnId: number
  waitingOnLabel: string
  waitingPlayerIds: number[]
  waitingPlayerNames: string[]
  turnLabel: string
  ruleLabel: string
  detail: string
  pendingCount: number
  isWaitingOnSelectedPlayer: boolean
  guidance: PendingRollGuidance
}

export function isRecord(value: unknown): value is JsonRecord {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

export function stringValue(value: unknown, fallback = '') {
  if (typeof value === 'string' && value.trim()) return value.trim()
  if (typeof value === 'number' && Number.isFinite(value)) return String(value)
  return fallback
}

export function turnStatusAllowsNextSend(status: unknown, details?: JsonRecord | null) {
  const normalizedStatus = stringValue(status)
  if (normalizedStatus === 'failed' || normalizedStatus === 'canon_pending') return true
  if (normalizedStatus !== 'saved') return false

  const stage = stringValue(details?.stage)
  return stage !== 'dm_response'
}

export function numberValue(value: unknown) {
  if (typeof value === 'number' && Number.isFinite(value)) return value
  if (typeof value === 'string') {
    const parsed = Number(value.replace(/[^0-9.-]/g, ''))
    return Number.isFinite(parsed) ? parsed : null
  }
  return null
}

export function formatCompactNumber(value: number) {
  return new Intl.NumberFormat(undefined, {
    notation: value >= 1000 ? 'compact' : 'standard',
    maximumFractionDigits: value >= 1000 ? 1 : 0,
  }).format(value)
}

export function truncateText(value: string, maxLength: number) {
  const compact = value.replace(/\s+/g, ' ').trim()
  if (compact.length <= maxLength) return compact
  return `${compact.slice(0, maxLength - 1).trim()}…`
}

export function stripMarkdown(value: string) {
  return value
    .replace(/\*\*/g, '')
    .replace(/\*/g, '')
    .replace(/---+/g, '')
    .replace(/#+\s*/g, '')
}

export function metadataTurnId(metadata: JsonRecord) {
  const parsed = numberValue(metadata.turn_id)
  return parsed !== null && Number.isInteger(parsed) ? parsed : null
}

export function turnPersistenceLabel(entry: TimelineEntry) {
  const status = stringValue(entry.metadata.persistence_status)
  if (status) return status.replace(/_/g, ' ')
  if (entry.streaming) return 'streaming'
  return ''
}

function collectRecords(value: unknown): JsonRecord[] {
  if (!isRecord(value)) return []
  const records: JsonRecord[] = [value]
  ;[
    'stats',
    'ability_scores',
    'abilities',
    'attributes',
    'combat',
    'derived',
    'health',
    'character',
    'currency',
  ].forEach((key) => {
    if (isRecord(value[key])) records.push(value[key] as JsonRecord)
  })
  return records
}

function healthTone(current: number | null, max: number | null): CombatParticipantSummary['healthTone'] {
  if (current !== null && current <= 0) return 'down'
  if (!current || !max) return 'healthy'
  const pct = current / Math.max(1, max)
  if (pct <= 0.25) return 'critical'
  if (pct <= 0.6) return 'hurt'
  return 'healthy'
}

function combatParticipantSummary(participant: JsonRecord): CombatParticipantSummary {
  const hp = isRecord(participant.hp) ? participant.hp : {}
  const current = numberValue(hp.current ?? hp.currentHp)
  const max = numberValue(hp.max ?? hp.maxHp)
  const intent = isRecord(participant.currentIntent)
    ? participant.currentIntent
    : isRecord(participant.intent)
      ? participant.intent
      : {}
  const conditions = Array.isArray(participant.conditions)
    ? participant.conditions.map((value) => stringValue(value)).filter(Boolean)
    : []
  const position = isRecord(participant.position) ? participant.position : {}
  const rangeBand = stringValue(position.rangeBand).replace(/_/g, ' ')
  const zoneId = stringValue(position.zoneId || position.zone_id).replace(/_/g, ' ')
  const health =
    current !== null && max !== null
      ? current <= 0
        ? 'Down'
        : current >= max
          ? 'Unhurt'
          : current / Math.max(1, max) <= 0.25
            ? 'Critical'
            : current / Math.max(1, max) <= 0.6
              ? 'Wounded'
              : 'Hurt'
      : 'Unknown'
  return {
    id: stringValue(participant.id),
    name: stringValue(participant.name, 'Unknown combatant'),
    team: stringValue(participant.team, 'enemy'),
    kind: stringValue(participant.kind, 'creature'),
    source: stringValue(participant.source),
    health,
    healthTone: healthTone(current, max),
    conditions,
    morale: participant.morale === undefined || participant.morale === null ? '—' : stringValue(participant.morale),
    moraleEvents: Array.isArray(participant.moraleEvents)
      ? participant.moraleEvents.map((value) => stringValue(value).replace(/_/g, ' ')).filter(Boolean)
      : [],
    intent: stringValue(intent.intentType).replace(/_/g, ' '),
    telegraph: stringValue(intent.visibleTelegraph),
    tacticSource: stringValue(intent.tacticSource),
    brainSource: stringValue(intent.brainSource || intent.tacticSource),
    position: [rangeBand, zoneId].filter(Boolean).join(' / '),
    selectionScore: intent.selectionScore === undefined || intent.selectionScore === null ? '' : stringValue(intent.selectionScore),
    selectionMethod: stringValue(intent.selectionMethod).replace(/_/g, ' '),
  }
}

function combatLegalAction(action: JsonRecord): CombatLegalAction | null {
  const id = stringValue(action.id)
  if (!id) return null
  const economy = isRecord(action.economy) ? action.economy : {}
  const range = isRecord(action.range) ? action.range : {}
  const weapon = isRecord(action.weapon) ? action.weapon : {}
  return {
    id,
    type: stringValue(action.type, 'turn_action'),
    label: stringValue(action.label, 'Combat action'),
    description: stringValue(action.description),
    message: stringValue(action.message),
    available: Boolean(action.available),
    reason: stringValue(action.reason),
    requiresTarget: Boolean(action.requiresTarget),
    authoritative: action.authoritative === true,
    economy: {
      action: numberValue(economy.action) ?? 0,
      movement: stringValue(economy.movement, 'unused'),
      endsTurn: economy.endsTurn !== false,
      tracking: stringValue(economy.tracking, 'turn_order_derived'),
      reactionTracked: economy.reactionTracked === true,
      subTurnCountersTracked: economy.subTurnCountersTracked === true,
    },
    targets: recordArray(action.targets).map((target) => ({
      id: stringValue(target.id),
      name: stringValue(target.name, 'Unknown target'),
      rangeBand: stringValue(target.rangeBand, 'unknown'),
      available: Boolean(target.available),
      reason: stringValue(target.reason),
    })).filter((target) => Boolean(target.id)),
    rangeClassification: stringValue(range.classification),
    allowedRangeBands: Array.isArray(range.allowedBands)
      ? range.allowedBands.map((value) => stringValue(value)).filter(Boolean)
      : [],
    weaponName: stringValue(weapon.name),
  }
}

function combatLegalActionBundle(bundle: JsonRecord): CombatLegalActionBundle | null {
  const playerId = numberValue(bundle.playerId)
  if (playerId === null || playerId < 1) return null
  const economy = isRecord(bundle.economy) ? bundle.economy : {}
  return {
    schemaVersion: numberValue(bundle.schemaVersion) ?? 1,
    playerId,
    actorId: stringValue(bundle.actorId),
    actorName: stringValue(bundle.actorName, 'Player'),
    round: numberValue(bundle.round) ?? 1,
    currentActorId: stringValue(bundle.currentActorId),
    currentActorName: stringValue(bundle.currentActorName),
    isCurrentActor: Boolean(bundle.isCurrentActor),
    economyTracking: stringValue(economy.tracking, 'turn_order_derived'),
    subTurnCountersTracked: economy.subTurnCountersTracked === true,
    actions: recordArray(bundle.actions)
      .map(combatLegalAction)
      .filter((action): action is CombatLegalAction => action !== null),
  }
}

function combatStateFromSnapshot(snapshot: JsonRecord): CombatStatePanel {
  const combat = isRecord(snapshot.combat) ? snapshot.combat : {}
  const status = stringValue(combat.status, 'none')
  const battlefield = isRecord(combat.battlefield) ? combat.battlefield : {}
  const encounterGoal = isRecord(combat.encounterGoal) ? combat.encounterGoal : {}
  const flags = isRecord(combat.flags) ? combat.flags : {}
  const difficultyAI = isRecord(flags.combatDifficultyAI) ? flags.combatDifficultyAI : {}
  const enemyGroups = recordArray(flags.enemyGroups)
  const participants = recordArray(combat.participants).map(combatParticipantSummary)
  const legalActionBundles = recordArray(combat.legalActions)
    .map(combatLegalActionBundle)
    .filter((bundle): bundle is CombatLegalActionBundle => bundle !== null)
  const enemies = participants.filter((participant) => participant.team === 'enemy')
  const allies = participants.filter((participant) => participant.team !== 'enemy')
  const telegraphs = enemies.map((enemy) => enemy.telegraph).filter(Boolean).slice(0, 4)
  return {
    active: ['starting', 'active'].includes(status) && enemies.length > 0,
    status,
    round: stringValue(combat.round, '1'),
    battlefield: [
      stringValue(battlefield.lighting),
      stringValue(battlefield.environmentType).replace(/_/g, ' '),
      stringValue(battlefield.visibility),
    ]
      .filter(Boolean)
      .join(' / ') || 'No battlefield recorded',
    goal: stringValue(encounterGoal.description || encounterGoal.playerObjective || combat.lastRoundSummary, 'Resolve the threat'),
    creatureSource: stringValue(flags.creatureSource).replace(/_/g, ' '),
    resolverMethod: stringValue(flags.resolverMethod).replace(/_/g, ' '),
    tacticalLevel: stringValue(difficultyAI.tacticalLevel, 'normal'),
    endReason: stringValue(flags.endReason).replace(/_/g, ' '),
    combatStartedBy: stringValue(flags.combatStartedBy).replace(/_/g, ' '),
    enemyGroupSummary: enemyGroups
      .map((group) => {
        const count = stringValue(group.count)
        const name = stringValue(group.name || group.label, 'enemy')
        return [count, name].filter(Boolean).join(' x ')
      })
      .filter(Boolean)
      .slice(0, 3)
      .join(', '),
    initiativeRequired: Boolean(flags.initiativeRequired),
    debugEnabled: Boolean(flags.debugCombat || flags.showDebug || flags.debug),
    enemies,
    allies,
    telegraphs,
    legalActionBundles,
  }
}

function findValue(records: JsonRecord[], keys: string[]) {
  for (const record of records) {
    for (const key of keys) {
      if (record[key] !== undefined && record[key] !== null && record[key] !== '') {
        return record[key]
      }
    }
  }
  return null
}

function parsedJsonValue(value: unknown): unknown {
  if (typeof value !== 'string') return value
  const trimmed = value.trim()
  if (!trimmed || (!trimmed.startsWith('{') && !trimmed.startsWith('['))) return value
  try {
    return JSON.parse(trimmed) as unknown
  } catch {
    return value
  }
}

function collectSpellbookCandidates(value: unknown): unknown[] {
  const parsed = parsedJsonValue(value)
  if (Array.isArray(parsed)) return [parsed]
  if (!isRecord(parsed)) return []

  const nestedCandidates: unknown[] = []
  ;['spellbook', 'magic', 'casting', 'character', 'stats'].forEach((key) => {
    const nested = parsedJsonValue(parsed[key])
    if (isRecord(nested) || Array.isArray(nested)) nestedCandidates.push(nested)
  })
  return parsed.spellbook !== undefined ? [...nestedCandidates, parsed] : [parsed, ...nestedCandidates]
}

function sourceLabel(sourceType: string, sourceDetail: string, source: string) {
  const cleanedSource = source.replace(/_/g, ' ').replace(/:/g, ' / ').trim()
  const cleanedType = sourceType.replace(/_/g, ' ').replace(/\bcatalog\b/g, '').trim()
  const cleanedDetail = sourceDetail.replace(/_/g, ' ').trim()
  const typeLabel = cleanedType ? cleanedType.replace(/\b\w/g, (letter) => letter.toUpperCase()) : ''
  if (typeLabel && cleanedDetail) return `${typeLabel} / ${cleanedDetail}`
  if (typeLabel) return typeLabel
  return cleanedSource
}

function spellLevelLabel(level: number) {
  return level <= 0 ? 'Cantrip' : `Lv ${level}`
}

function spellNameKey(value: string) {
  return value.toLowerCase().replace(/[^a-z0-9]+/g, ' ').trim()
}

function titleCaseWords(value: string) {
  return value
    .replace(/([a-z])([A-Z])/g, '$1 $2')
    .replace(/[_-]+/g, ' ')
    .replace(/\s+/g, ' ')
    .trim()
    .replace(/\b\w/g, (letter) => letter.toUpperCase())
}

function characterTraitSourceLabel(source: string, detail: string) {
  const sourceLabel = titleCaseWords(source || 'Trait')
  const detailLabel = titleCaseWords(detail)
  if (sourceLabel && detailLabel) return `${sourceLabel} / ${detailLabel}`
  return sourceLabel || detailLabel || 'Character Trait'
}

function characterTraitTypeLabel(category: string, mechanics: JsonRecord) {
  if (category === 'active_ability' || isRecord(mechanics.activeAbility) || isRecord(mechanics.active_ability)) {
    return 'Active'
  }
  if (category === 'passive_ability' || isRecord(mechanics.passiveAbility) || isRecord(mechanics.passive_ability)) {
    return 'Passive'
  }
  if (category) return titleCaseWords(category)
  return 'Trait'
}

function characterTraitActiveMechanic(mechanics: JsonRecord) {
  if (isRecord(mechanics.activeAbility)) return mechanics.activeAbility
  if (isRecord(mechanics.active_ability)) return mechanics.active_ability
  return null
}

function collectCharacterTraitCandidates(value: unknown): Array<{ source: string; detail: string; entries: unknown[] }> {
  const parsed = parsedJsonValue(value)
  if (Array.isArray(parsed)) return [{ source: 'trait', detail: '', entries: parsed }]
  if (!isRecord(parsed)) return []

  const candidates: Array<{ source: string; detail: string; entries: unknown[] }> = []
  const customRace =
    isRecord(parsed.customRaceDefinition)
      ? parsed.customRaceDefinition
      : isRecord(parsed.custom_race_definition)
        ? parsed.custom_race_definition
        : null
  const raceName = stringValue(parsed.raceName ?? parsed.race_name ?? customRace?.name)
  if (customRace && Array.isArray(customRace.traits)) {
    candidates.push({ source: 'race', detail: raceName, entries: customRace.traits })
  }
  if (Array.isArray(parsed.traits)) {
    candidates.push({ source: raceName ? 'race' : 'trait', detail: raceName, entries: parsed.traits })
  }

  ;[
    ['class', 'classFeatures'],
    ['class', 'class_features'],
    ['class', 'features'],
    ['race', 'racialTraits'],
    ['race', 'racial_traits'],
    ['trait', 'specialAbilities'],
    ['trait', 'special_abilities'],
  ].forEach(([source, key]) => {
    const entries = parsed[key]
    if (Array.isArray(entries)) candidates.push({ source, detail: '', entries })
  })

  return candidates
}

function normalizeCharacterTraitEntry(entry: unknown, source: string, detail: string): CharacterTraitSummary | null {
  if (typeof entry === 'string') {
    const name = stringValue(entry)
    if (!name) return null
    return {
      id: `trait-${spellNameKey(name).replace(/\s+/g, '-')}`,
      name,
      category: '',
      typeLabel: 'Trait',
      source: characterTraitSourceLabel(source, detail),
      description: '',
      actionType: '',
      cooldown: '',
      active: false,
    }
  }
  if (!isRecord(entry)) return null

  const name =
    stringValue(entry.name) ||
    stringValue(entry.traitName) ||
    stringValue(entry.trait_name) ||
    stringValue(entry.label) ||
    stringValue(entry.title)
  if (!name) return null

  const mechanics = isRecord(entry.mechanics) ? entry.mechanics : {}
  const activeMechanic = characterTraitActiveMechanic(mechanics)
  const category = stringValue(entry.category ?? entry.type).toLowerCase()
  const actionType = activeMechanic
    ? titleCaseWords(stringValue(activeMechanic.actionType ?? activeMechanic.action_type))
    : titleCaseWords(stringValue(entry.actionType ?? entry.action_type))
  const cooldown = activeMechanic
    ? titleCaseWords(stringValue(activeMechanic.cooldown))
    : titleCaseWords(stringValue(entry.cooldown ?? entry.recharge))
  const typeLabel = characterTraitTypeLabel(category, mechanics)

  return {
    id: stringValue(entry.id ?? entry.traitId ?? entry.trait_id, `trait-${spellNameKey(name).replace(/\s+/g, '-')}`),
    name,
    category,
    typeLabel,
    source: characterTraitSourceLabel(source, detail),
    description: stringValue(entry.description ?? entry.summary ?? entry.effect),
    actionType,
    cooldown,
    active: typeLabel === 'Active',
  }
}

export function normalizeCharacterTraits(...values: unknown[]): CharacterTraitSummary[] {
  const traits: CharacterTraitSummary[] = []
  const seen = new Set<string>()

  values.flatMap(collectCharacterTraitCandidates).forEach((candidate) => {
    candidate.entries.forEach((entry) => {
      const trait = normalizeCharacterTraitEntry(entry, candidate.source, candidate.detail)
      if (!trait) return
      const key = `${spellNameKey(trait.name)}:${trait.source.toLowerCase()}`
      if (seen.has(key)) return
      seen.add(key)
      traits.push(trait)
    })
  })

  return traits.sort((left, right) => {
    if (left.active !== right.active) return left.active ? -1 : 1
    return left.name.localeCompare(right.name)
  })
}

function normalizeSpellEntry(entry: unknown, preparedNames: Set<string>): SpellSummary | null {
  if (typeof entry === 'string') {
    const name = stringValue(entry)
    if (!name) return null
    const id = `spell-${spellNameKey(name).replace(/\s+/g, '-')}`
    return {
      id,
      name,
      level: 0,
      levelLabel: 'Cantrip',
      source: '',
      sourceType: '',
      sourceDetail: '',
      description: '',
      tags: [],
      catalog: '',
      prepared: preparedNames.has(spellNameKey(name)),
    }
  }
  if (!isRecord(entry)) return null

  const name =
    stringValue(entry.name) ||
    stringValue(entry.spellName) ||
    stringValue(entry.spell_name) ||
    stringValue(entry.label) ||
    stringValue(entry.title)
  if (!name) return null

  const level = Math.max(
    0,
    Math.min(
      9,
      Math.floor(numberValue(entry.level ?? entry.spellLevel ?? entry.spell_level) ?? 0),
    ),
  )
  const sourceType = stringValue(entry.sourceType ?? entry.source_type).toLowerCase()
  const sourceDetail = stringValue(entry.sourceDetail ?? entry.source_detail)
  const rawSource =
    stringValue(entry.source) ||
    (Array.isArray(entry.sources) ? stringValue(entry.sources[0]) : '') ||
    [sourceType, sourceDetail].filter(Boolean).join(':')
  const source = sourceLabel(sourceType, sourceDetail, rawSource)
  const tags = Array.isArray(entry.tags)
    ? entry.tags.map((tag) => stringValue(tag).toLowerCase()).filter(Boolean)
    : []

  return {
    id: stringValue(entry.id ?? entry.spellId ?? entry.spell_id, `spell-${spellNameKey(name).replace(/\s+/g, '-')}`),
    name,
    level,
    levelLabel: spellLevelLabel(level),
    source,
    sourceType,
    sourceDetail,
    description: stringValue(entry.description ?? entry.summary ?? entry.effect),
    tags,
    catalog: stringValue(entry.catalog).replace(/_/g, ' '),
    prepared: preparedNames.has(spellNameKey(name)),
  }
}

function mergeSpellSummary(existing: SpellSummary, incoming: SpellSummary) {
  const existingHasDetails = Boolean(
    existing.description ||
    existing.source ||
    existing.sourceType ||
    existing.sourceDetail ||
    existing.catalog ||
    existing.tags.length,
  )
  const incomingHasDetails = Boolean(
    incoming.description ||
    incoming.source ||
    incoming.sourceType ||
    incoming.sourceDetail ||
    incoming.catalog ||
    incoming.tags.length,
  )

  if (incomingHasDetails && !existingHasDetails) {
    Object.assign(existing, incoming)
    return
  }

  if (!existing.id && incoming.id) existing.id = incoming.id
  if (!existing.description && incoming.description) existing.description = incoming.description
  if (!existing.source && incoming.source) existing.source = incoming.source
  if (!existing.sourceType && incoming.sourceType) existing.sourceType = incoming.sourceType
  if (!existing.sourceDetail && incoming.sourceDetail) existing.sourceDetail = incoming.sourceDetail
  if (!existing.catalog && incoming.catalog) existing.catalog = incoming.catalog
  if (!existing.source && incoming.source && existing.level === 0 && incoming.level > 0) {
    existing.level = incoming.level
    existing.levelLabel = incoming.levelLabel
  }
  incoming.tags.forEach((tag) => {
    if (!existing.tags.includes(tag)) existing.tags.push(tag)
  })
  existing.prepared = existing.prepared || incoming.prepared
}

export function normalizeSpellbook(...values: unknown[]): SpellbookSummary {
  const knownSpells: SpellSummary[] = []
  const preparedSpellNames: string[] = []
  const sources: string[] = []
  const seen = new Set<string>()
  const spellsByName = new Map<string, SpellSummary>()

  values.flatMap(collectSpellbookCandidates).forEach((candidate) => {
    const parsed = parsedJsonValue(candidate)
    const book = isRecord(parsed) ? parsed : {}
    const rawKnown = Array.isArray(parsed)
      ? parsed
      : Array.isArray(book.knownSpells)
        ? book.knownSpells
        : Array.isArray(book.known_spells)
          ? book.known_spells
          : Array.isArray(book.spells)
            ? book.spells
            : []
    const rawPrepared = Array.isArray(book.preparedSpells)
      ? book.preparedSpells
      : Array.isArray(book.prepared_spells)
        ? book.prepared_spells
        : []
    const preparedNames = new Set(rawPrepared.map((item) => spellNameKey(stringValue(item))).filter(Boolean))
    preparedNames.forEach((name) => {
      if (!preparedSpellNames.some((existing) => spellNameKey(existing) === name)) {
        const rawName = rawPrepared.find((item) => spellNameKey(stringValue(item)) === name)
        preparedSpellNames.push(stringValue(rawName))
      }
    })

    rawKnown.forEach((entry) => {
      const spell = normalizeSpellEntry(entry, preparedNames)
      if (!spell) return
      const key = spellNameKey(spell.name)
      if (seen.has(key)) {
        const existing = spellsByName.get(key)
        if (existing) mergeSpellSummary(existing, spell)
        ;[spell.source, spell.catalog, ...spell.tags].forEach((source) => {
          if (source && !sources.includes(source)) sources.push(source)
        })
        return
      }
      seen.add(key)
      knownSpells.push(spell)
      spellsByName.set(key, spell)
      ;[spell.source, spell.catalog, ...spell.tags].forEach((source) => {
        if (source && !sources.includes(source)) sources.push(source)
      })
    })

    if (Array.isArray(book.sources)) {
      book.sources.map((item) => stringValue(item)).filter(Boolean).forEach((source) => {
        if (!sources.includes(source)) sources.push(source)
      })
    }
  })

  knownSpells.sort((left, right) => left.level - right.level || left.name.localeCompare(right.name))

  return { knownSpells, preparedSpellNames, sources }
}

export function normalizeInventory(value: unknown): InventoryRow[] {
  const source = Array.isArray(value)
    ? value
    : isRecord(value) && Array.isArray(value.items)
      ? value.items
      : []
  const iconFor = (item: string, index: number) => {
    const normalized = item.toLowerCase()
    if (normalized.includes('sword') || normalized.includes('blade') || normalized.includes('axe') || normalized.includes('hammer') || normalized.includes('maul')) return 'sword'
    if (normalized.includes('shield')) return 'shield'
    if (normalized.includes('potion') || normalized.includes('vial')) return 'potion'
    if (normalized.includes('armor') || normalized.includes('mail')) return 'armor'
    if (normalized.includes('ration') || normalized.includes('food')) return 'ration'
    return ['sword', 'shield', 'potion', 'armor', 'ration'][index % 5]
  }
  const inferredSlotFor = (entry: JsonRecord, item: string, itemType: string, subtype: string) => {
    const rawSlot = stringValue(entry.slot ?? entry.equipmentSlot ?? entry.equipment_slot)
    if (rawSlot && rawSlot !== 'none') return rawSlot
    const normalized = `${item} ${itemType} ${subtype} ${(Array.isArray(entry.tags) ? entry.tags.join(' ') : '')}`.toLowerCase()
    const looksLikeWeapon = itemType === 'weapon' || /\b(?:axe|battleaxe|battle axe|blade|bow|club|crossbow|dagger|flail|greataxe|great axe|greatsword|great sword|handaxe|hand axe|javelin|knife|lance|longsword|mace|maul|morningstar|pike|quarterstaff|rapier|scimitar|shortsword|sickle|sling|spear|staff|sword|trident|war pick|warhammer|whip)\b/.test(normalized)
    if (looksLikeWeapon) return /\b(?:greatsword|great sword|greataxe|great axe|greatclub|great club|maul|longbow|shortbow|heavy crossbow|halberd|glaive|pike|two.?hand)\b/.test(normalized) ? 'two_hands' : 'main_hand'
    if (normalized.includes('shield')) return 'off_hand'
    if (normalized.includes('helmet') || normalized.includes('helm')) return 'helmet'
    if (normalized.includes('hood') || normalized.includes('cowl')) return 'hood'
    if (normalized.includes('underwear') || normalized.includes('underclothes')) return 'underwear'
    if (normalized.includes('cloak') || normalized.includes('cape')) return 'cloak'
    if (normalized.includes('glove') || normalized.includes('gauntlet')) return 'hands'
    if (normalized.includes('boot') || normalized.includes('shoe')) return 'feet'
    if (normalized.includes('armor') || normalized.includes('armour') || normalized.includes('mail') || normalized.includes('breastplate') || normalized.includes('vest')) return 'body_armor'
    if (normalized.includes('clothing') || normalized.includes('clothes') || normalized.includes('shirt') || normalized.includes('tunic') || normalized.includes('robe')) return 'clothing'
    return ''
  }
  return source.map((entry, index) => {
    if (typeof entry === 'string') {
      return { id: '', item: entry, count: '1', weight: '—', icon: iconFor(entry, index), weightValue: null, type: '', subtype: '', equipped: false, slot: '', equippable: false }
    }
    if (!isRecord(entry)) {
      return { id: '', item: `Item ${index + 1}`, count: '1', weight: '—', icon: 'ration', weightValue: null, type: '', subtype: '', equipped: false, slot: '', equippable: false }
    }
    const item =
      stringValue(entry.name) ||
      stringValue(entry.item) ||
      stringValue(entry.label) ||
      `Item ${index + 1}`
    const countNumber = numberValue(entry.quantity ?? entry.count) ?? 1
    const weightNumber = numberValue(entry.weight)
    const weightValue =
      weightNumber === null ? null : Math.round(weightNumber * countNumber * 10) / 10
    const type = stringValue(entry.type)
    const subtype = stringValue(entry.subtype)
    const slot = inferredSlotFor(entry, item, type.toLowerCase(), subtype.toLowerCase())
    return {
      id: stringValue(entry.id ?? entry.itemId ?? entry.item_id),
      item,
      count: stringValue(entry.quantity ?? entry.count, '1'),
      weight: weightNumber === null ? '—' : `${weightValue} lb`,
      icon: stringValue(entry.icon, iconFor(item, index)),
      weightValue,
      type,
      subtype,
      equipped: entry.equipped === true,
      slot,
      equippable: Boolean(slot),
    }
  })
}

export function normalizeStats(
  statsValue: unknown,
  sheetValue: unknown,
  level: number | null,
  derivedValue?: unknown,
): StatBlock {
  const records = [
    ...collectRecords(derivedValue),
    ...collectRecords(statsValue),
    ...collectRecords(sheetValue),
  ]
  const scoreFor = (longKey: string, shortKey: string) =>
    numberValue(findValue(records, [longKey, shortKey, `${longKey}_score`, `${shortKey}_score`]))
  const statLabel = (keys: string[], fallback = '—') =>
    stringValue(findValue(records, keys), fallback)
  const hpCurrent = statLabel(['current_hp', 'hp_current', 'hp', 'hit_points', 'currentHitPoints'])
  const hpMax = statLabel(['max_hp', 'hp_max', 'max_hit_points', 'maxHitPoints'])
  const hp = hpMax !== '—' && hpMax !== hpCurrent ? `${hpCurrent} / ${hpMax}` : hpCurrent
  const abilityEntries: Array<[string, string, string]> = [
    ['STR', 'strength', 'str'],
    ['DEX', 'dexterity', 'dex'],
    ['CON', 'constitution', 'con'],
    ['INT', 'intelligence', 'int'],
    ['WIS', 'wisdom', 'wis'],
    ['CHA', 'charisma', 'cha'],
  ].map(([label, longKey, shortKey]) => {
    const score = scoreFor(longKey, shortKey)
    if (score === null) return [label, '—', '—']
    const modifier = Math.floor((score - 10) / 2)
    return [label, String(score), modifier >= 0 ? `+${modifier}` : String(modifier)]
  })
  const proficiencyValue = statLabel(['proficiency_bonus', 'proficiency', 'prof_bonus'])

  return {
    hp,
    ac: statLabel(['armorClass', 'armor_class', 'ac']),
    init: statLabel(['initiative', 'init']),
    speed: statLabel(['speed', 'movement', 'walk_speed']),
    abilities: abilityEntries,
    proficiency: proficiencyValue !== '—' ? proficiencyValue : level ? `+${2 + Math.floor((level - 1) / 4)}` : '—',
    inspiration: Boolean(findValue(records, ['inspiration', 'inspired'])),
  }
}

export function abilityOptionsFromStatBlock(statBlock: StatBlock): AbilityOption[] {
  return statBlock.abilities.map(([label, score, modifier]) => ({
    key:
      label === 'STR'
        ? 'strength'
        : label === 'DEX'
          ? 'dexterity'
          : label === 'CON'
            ? 'constitution'
            : label === 'INT'
              ? 'intelligence'
              : label === 'WIS'
                ? 'wisdom'
                : 'charisma',
    label,
    score,
    modifier,
  }))
}

export function itemOptionsFromInventory(inventoryRows: InventoryRow[]): ItemOption[] {
  return inventoryRows.map((row) => ({
    id: row.id,
    name: row.item,
    quantity: row.count,
    equipped: row.equipped,
    slot: row.slot,
  }))
}

export function normalizeXp(value: unknown, level: number | string): XpProgress {
  const records = collectRecords(value)
  const current = numberValue(findValue(records, ['xp', 'experience', 'current_xp'])) ?? 0
  const max =
    numberValue(findValue(records, ['xp_to_next', 'next_level_at', 'nextLevelAt', 'next_level_xp', 'max_xp'])) ??
    Math.max(300, Number(level) * 300)
  const percent = max > 0 ? Math.min(100, Math.round((current / max) * 100)) : 0
  return {
    current,
    max,
    percent,
    label: `${formatCompactNumber(current)} / ${formatCompactNumber(max)} XP`,
  }
}

export function inventoryCapacity(value: unknown) {
  const records = collectRecords(value)
  return numberValue(findValue(records, ['carrying_capacity', 'capacity', 'max_weight', 'maxWeight']))
}

export function inventoryWeightLabel(inventoryRows: InventoryRow[], capacity: number | null) {
  const carriedWeight = inventoryRows.reduce(
    (total, row) => total + (row.weightValue ?? 0),
    0,
  )
  if (capacity === null) {
    return `Weight ${carriedWeight ? carriedWeight.toFixed(carriedWeight % 1 ? 1 : 0) : '—'} / — lb`
  }
  return `Weight ${carriedWeight.toFixed(carriedWeight % 1 ? 1 : 0)} / ${capacity} lb`
}

export function inventoryGoldLabel(...values: unknown[]) {
  const records = values.flatMap((value) => collectRecords(value))
  const gold = Math.max(
    0,
    Math.floor(numberValue(findValue(records, ['gold', 'gp', 'gold_pieces', 'goldPieces'])) ?? 0),
  )
  const extraCoins = [
    ['platinum', 'pp'],
    ['electrum', 'ep'],
    ['silver', 'sp'],
    ['copper', 'cp'],
  ]
    .map(([key, label]) => {
      const amount = Math.max(0, Math.floor(numberValue(findValue(records, [key])) ?? 0))
      return amount ? `${formatCompactNumber(amount)} ${label}` : ''
    })
    .filter(Boolean)
  return [`${formatCompactNumber(gold)} gp`, ...extraCoins].join(' · ')
}

export function buildMapMeta(map: MapItem | undefined, segment: CampaignSegment | null): MapPanelMeta {
  const data = map?.map_data ?? {}
  const exploredNumber =
    numberValue(data.explored_percent ?? data.exploredPercent ?? data.explored ?? data.progress) ??
    (map ? 0 : null)
  const rawThreat =
    stringValue(data.threat_level) ||
    stringValue(data.threat) ||
    (segment?.tags?.toLowerCase().includes('high') ? 'High' : '') ||
    (segment?.is_triggered ? 'Elevated' : 'Unknown')
  const normalizedThreat = rawThreat.toLowerCase()
  const threatTone =
    normalizedThreat.includes('high') || normalizedThreat.includes('danger')
      ? 'high'
      : normalizedThreat.includes('medium') || normalizedThreat.includes('elevated')
        ? 'medium'
        : 'low'
  return {
    explored: exploredNumber === null ? '—' : `${Math.round(exploredNumber)}%`,
    threat: rawThreat,
    threatTone,
    weather: stringValue(data.weather) || stringValue(data.climate) || 'Not recorded',
  }
}

function recordArray(value: unknown): JsonRecord[] {
  return Array.isArray(value) ? value.filter(isRecord) : []
}

function normalizedLookup(value: unknown) {
  return stringValue(value).toLowerCase().replace(/[^a-z0-9]+/g, ' ').trim()
}

function recordTurnValue(record: JsonRecord, keys: string[]) {
  for (const key of keys) {
    const value = numberValue(record[key])
    if (value !== null) return value
  }
  return 0
}

export function worldStateFromSnapshot(snapshot: unknown): WorldStatePanel {
  const root = isRecord(snapshot) ? snapshot : {}
  const scene = isRecord(root.currentScene) ? root.currentScene : {}
  const quests = recordArray(root.quests)
  const locations = recordArray(root.locations)
  const npcs = [...recordArray(root.knownNpcs), ...recordArray(root.partyNpcs)]
  const playerRecords = recordArray(root.playerCharacters)
  const playerNames = new Set(playerRecords.map((player) => normalizedLookup(player.name)).filter(Boolean))
  const playerIds = new Set(
    playerRecords
      .flatMap((player) => [stringValue(player.id), stringValue(player.playerId), stringValue(player.player_id)])
      .filter(Boolean),
  )
  const activeNpcIds = Array.isArray(scene.activeNpcIds)
    ? scene.activeNpcIds.map((value) => stringValue(value)).filter(Boolean)
    : []
  const activeNpcIdSet = new Set(activeNpcIds)
  const activeQuestIds = new Set(
    Array.isArray(scene.activeQuestIds)
      ? scene.activeQuestIds.map((value) => stringValue(value)).filter(Boolean)
      : [],
  )
  const currentLocationId = stringValue(scene.locationId)
  const currentLocationName = normalizedLookup(scene.name)
  const activeQuests = quests
    .filter((quest) => {
      const id = stringValue(quest.id)
      const status = stringValue(quest.status).toLowerCase()
      return activeQuestIds.has(id) || status === 'active' || status === 'available'
    })
    .map((quest) => ({
      id: stringValue(quest.id),
      title: stringValue(quest.title, 'Untitled quest'),
      status: stringValue(quest.status, 'unknown'),
      stage: stringValue(quest.stage, 'No stage recorded'),
    }))

  return {
    sceneName: stringValue(scene.name, 'No scene recorded'),
    sceneType: stringValue(scene.sceneType, 'unknown'),
    mood: stringValue(scene.mood, 'unknown'),
    dangerLevel:
      scene.dangerLevel === undefined || scene.dangerLevel === null
        ? '0'
        : stringValue(scene.dangerLevel, '0'),
    activeQuests,
    knownLocations: locations
      .map((location, index) => {
        const id = stringValue(location.id)
        const rawName = stringValue(location.name)
        const name = rawName || 'Unknown location'
        const status = stringValue(location.status).toLowerCase()
        const current =
          Boolean(currentLocationId && id === currentLocationId) ||
          Boolean(currentLocationName && normalizedLookup(name) === currentLocationName)
        const visited = status === 'visited' || current
        return {
          location,
          index,
          id,
          rawName,
          name,
          status,
          current,
          visited,
          lastVisitedTurn: recordTurnValue(location, ['lastVisitedTurn']),
          recencyTurn: recordTurnValue(location, [
            'lastVisitedTurn',
            'updatedAtTurn',
            'firstDiscoveredTurn',
            'createdAtTurn',
          ]),
        }
      })
      .filter(({ id, rawName, status, current }) => {
        if (!id && !rawName) return false
        return current || !['hidden', 'inaccessible'].includes(status)
      })
      .sort((left, right) => {
        if (left.current !== right.current) return left.current ? -1 : 1
        if (left.visited !== right.visited) return left.visited ? -1 : 1
        if (left.visited && right.visited) {
          return right.lastVisitedTurn - left.lastVisitedTurn || left.index - right.index
        }
        return right.recencyTurn - left.recencyTurn || left.index - right.index
      })
      .map(({ location }) => ({
        id: stringValue(location.id),
        name: stringValue(location.name, 'Unknown location'),
        status: stringValue(location.status, 'unknown'),
        type: stringValue(location.type, 'other'),
      })),
    knownNpcs: npcs
      .map((npc, index) => ({
        npc,
        index,
        activeIndex: activeNpcIds.indexOf(stringValue(npc.id)),
        lastSeenTurn: numberValue(npc.lastSeenTurn) ?? numberValue(npc.updatedAtTurn) ?? 0,
      }))
      .filter(({ npc }) => {
        const id = stringValue(npc.id)
        const name = normalizedLookup(npc.name)
        return Boolean(id || name) && !playerIds.has(id) && !playerNames.has(name)
      })
      .sort((left, right) => {
        const leftActive = activeNpcIdSet.has(stringValue(left.npc.id))
        const rightActive = activeNpcIdSet.has(stringValue(right.npc.id))
        if (leftActive !== rightActive) return leftActive ? -1 : 1
        if (leftActive && rightActive) return left.activeIndex - right.activeIndex
        return right.lastSeenTurn - left.lastSeenTurn || left.index - right.index
      })
      .map(({ npc }) => ({
        id: stringValue(npc.id),
        name: stringValue(npc.name, 'Unknown NPC'),
        race: stringValue(npc.race) || stringValue(npc.species) || stringValue(npc.ancestry),
        role: stringValue(npc.role, 'Role unknown'),
        disposition: stringValue(npc.disposition, 'unknown'),
        status: stringValue(npc.status, 'unknown'),
      })),
    combat: combatStateFromSnapshot(root),
  }
}

export function turnNumber(entry: TimelineEntry, fallbackIndex: number) {
  const localTurn = entry.metadata.turn_number
  if (typeof localTurn === 'number') return localTurn
  const metadataTurn = entry.metadata.turn_id
  return typeof metadataTurn === 'number' && metadataTurn <= fallbackIndex + 1
    ? metadataTurn
    : fallbackIndex + 1
}

export function speakerDetail(entry: TimelineEntry, selectedPlayer: Player | null) {
  if (entry.role === 'dm') return 'Narration'
  if (entry.role === 'system') return 'System'
  if (selectedPlayer && entry.speaker === selectedPlayer.character_name) {
    return `${selectedPlayer.race || 'Adventurer'} ${selectedPlayer.char_class || selectedPlayer.class_ || ''}`.trim()
  }
  return 'Player'
}

function stripSpeakerPrefix(message: string, speaker: string) {
  const prefix = `${speaker}:`
  return message.startsWith(prefix) ? message.slice(prefix.length).trim() : message
}

export function timelineFromLog(entry: SessionLogEntry): TimelineEntry {
  let role: TimelineRole =
    entry.entry_type === 'player' ? 'player' : entry.entry_type === 'system' ? 'system' : 'dm'
  let speaker = role === 'player' ? 'Player' : 'DM'
  let text = entry.message

  if (text.startsWith('**')) {
    role = 'system'
    speaker = 'System'
    text = text.replaceAll('**', '')
  } else if (text.startsWith('DM:')) {
    speaker = 'DM'
    text = stripSpeakerPrefix(text, 'DM')
  } else if (role === 'player' && text.includes(':')) {
    const splitIndex = text.indexOf(':')
    speaker = text.slice(0, splitIndex)
    text = text.slice(splitIndex + 1).trim()
  }

  return {
    id: `log-${entry.id}`,
    role,
    speaker,
    text,
    timestamp: entry.timestamp,
    metadata: entry.metadata ?? {},
  }
}

export function buildTimeline({
  logEntries,
  optimisticEntries,
  streamingTurn,
  turnStatuses,
}: {
  logEntries: SessionLogEntry[]
  optimisticEntries: TimelineEntry[]
  streamingTurn: StreamingTurn | null
  turnStatuses: Record<number, string>
}): TimelineEntry[] {
  const withStatus = (entry: TimelineEntry): TimelineEntry => {
    const turnId = metadataTurnId(entry.metadata)
    const persistenceStatus = turnId !== null ? turnStatuses[turnId] : undefined
    return persistenceStatus
      ? {
          ...entry,
          metadata: {
            ...entry.metadata,
            persistence_status: persistenceStatus,
          },
        }
      : entry
  }
  const logTimeline = logEntries.map(timelineFromLog).map(withStatus)
  const loggedClientMessageIds = new Set(
    logTimeline.map((entry) => stringValue(entry.metadata.client_message_id)).filter(Boolean),
  )
  const loggedTurnRoleKeys = new Set(
    logTimeline
      .map((entry) => {
        const turnId = metadataTurnId(entry.metadata)
        return turnId !== null ? `${entry.role}:${turnId}` : ''
      })
      .filter(Boolean),
  )
  const unresolvedOptimisticEntries = optimisticEntries
    .filter((entry) => {
      const clientMessageId = stringValue(entry.metadata.client_message_id)
      if (clientMessageId && loggedClientMessageIds.has(clientMessageId)) return false
      const turnId = metadataTurnId(entry.metadata)
      if (turnId !== null && loggedTurnRoleKeys.has(`${entry.role}:${turnId}`)) return false
      return true
    })
    .map(withStatus)
  const entries = logTimeline.concat(unresolvedOptimisticEntries)
  if (streamingTurn) {
    const streamingEntry: TimelineEntry = {
      id: `stream-${streamingTurn.turnId}`,
      role: 'dm',
      speaker: 'DM',
      text: streamingTurn.text || '...',
      timestamp: null,
      metadata: {
        turn_id: streamingTurn.turnId,
        turn_number: streamingTurn.turnNumber ?? null,
        requires_roll: streamingTurn.requiresRoll,
        persistence_status: turnStatuses[streamingTurn.turnId] ?? 'streaming',
        ...streamingTurn.rulesHint,
      },
      streaming: true,
    }
    if (turnStatusAllowsNextSend(turnStatuses[streamingTurn.turnId])) {
      return logTimeline.concat([streamingEntry], unresolvedOptimisticEntries)
    }
    entries.push(streamingEntry)
  }
  return entries
}

type RollResolutionState = {
  resolved: boolean
  remainingPlayerIds: number[] | null
}

type PendingRollTimelineEntry = {
  turnId: number
  label: string
  detail: string
  ruleLabel: string
  waitingPlayerIds: number[]
  guidance: PendingRollGuidance
}

function uniquePositiveIntegers(values: number[]) {
  return [...new Set(values.filter((value) => Number.isInteger(value) && value > 0))]
}

function numberArrayValue(value: unknown): number[] | null {
  if (!Array.isArray(value)) return null
  return uniquePositiveIntegers(
    value
      .map((item) => numberValue(item))
      .filter((item): item is number => item !== null),
  )
}

function metadataRollGate(metadata: JsonRecord) {
  return isRecord(metadata.roll_gate) ? metadata.roll_gate : {}
}

function pendingRollSpecFromMetadata(metadata: JsonRecord, ruleType: string) {
  const rollGate = metadataRollGate(metadata)
  const nestedRulesHint = isRecord(metadata.rules_hint) ? metadata.rules_hint : {}
  const rawSpec = isRecord(rollGate.roll_spec)
    ? rollGate.roll_spec
    : isRecord(metadata.roll_spec)
      ? metadata.roll_spec
      : isRecord(nestedRulesHint.roll_spec)
        ? nestedRulesHint.roll_spec
        : {}
  const modeValue = stringValue(rawSpec.mode).toLowerCase()
  const mode: RollMode = ['advantage', 'disadvantage'].includes(modeValue)
    ? modeValue as RollMode
    : 'normal'
  const visibilityValue = stringValue(rawSpec.result_visibility).toLowerCase()
  const resultVisibility: ResultVisibility = visibilityValue === 'visible' ? 'visible' : 'hidden_until_landed'
  const rawAbility = isRecord(rawSpec.ability) ? rawSpec.ability : null
  const abilityKey = stringValue(rawAbility?.key).toLowerCase()
  const abilityLabel = stringValue(rawAbility?.label)
  return {
    die: normalizeDie(stringValue(rawSpec.die, 'd20')),
    mode,
    ruleType: stringValue(rawSpec.rule_type, ruleType),
    reason: stringValue(rawSpec.reason, ruleType.replace(/_/g, ' ')),
    resultVisibility,
    ability: abilityKey
      ? { key: abilityKey, label: abilityLabel || abilityKey.toUpperCase() }
      : null,
  }
}

function metadataNumberArray(metadata: JsonRecord, key: string): number[] | null {
  const direct = numberArrayValue(metadata[key])
  if (direct !== null) return direct
  const rollGate = metadataRollGate(metadata)
  return numberArrayValue(rollGate[key])
}

function pendingRollResolutionState(timeline: TimelineEntry[]) {
  const state = new Map<number, RollResolutionState>()
  timeline.forEach((entry) => {
    const resolvedTurnId = numberValue(entry.metadata.resolved_turn_id)
    if (resolvedTurnId !== null && Number.isInteger(resolvedTurnId)) {
      const remainingPlayerIds = metadataNumberArray(entry.metadata, 'remaining_player_ids')
      state.set(resolvedTurnId, {
        resolved: remainingPlayerIds !== null ? remainingPlayerIds.length === 0 : true,
        remainingPlayerIds,
      })
    }
  })
  return state
}

function metadataPendingRoll(entry: TimelineEntry) {
  if (!entry.metadata.requires_roll) return false
  const outcomeStatus = stringValue(entry.metadata.outcome_status).toLowerCase()
  const outcomeDeferred = entry.metadata.outcome_deferred
  return outcomeStatus === 'deferred' || outcomeDeferred === true || stringValue(outcomeDeferred).toLowerCase() === 'true'
}

function pendingPlayerIds(entry: TimelineEntry, resolutionState?: RollResolutionState) {
  if (resolutionState?.remainingPlayerIds !== null && resolutionState?.remainingPlayerIds !== undefined) {
    return resolutionState.remainingPlayerIds
  }
  const remainingPlayerIds = metadataNumberArray(entry.metadata, 'remaining_player_ids')
  if (remainingPlayerIds !== null) return remainingPlayerIds
  const requiredPlayerIds = metadataNumberArray(entry.metadata, 'required_player_ids')
  if (requiredPlayerIds !== null) return requiredPlayerIds
  const fallbackPlayerId = numberValue(entry.metadata.pending_player_id ?? entry.metadata.player_id)
  return fallbackPlayerId !== null ? [fallbackPlayerId] : []
}

function pendingRollTimelineEntries(timeline: TimelineEntry[]): PendingRollTimelineEntry[] {
  const resolutionStates = pendingRollResolutionState(timeline)
  return timeline
    .filter((entry) => {
      const turnId = metadataTurnId(entry.metadata)
      const resolutionState = turnId !== null ? resolutionStates.get(turnId) : undefined
      return (
        turnId !== null &&
        resolutionState?.resolved !== true &&
        metadataPendingRoll(entry)
      )
    })
    .map((entry, index) => {
      const turnId = metadataTurnId(entry.metadata) as number
      const resolutionState = resolutionStates.get(turnId)
      const ruleType = stringValue(entry.metadata.rule_type ?? entry.metadata.roll_type, 'check').replace(/_/g, ' ')
      const detail = truncateText(entry.text || 'Pending check', 72)
      const waitingPlayerIds = pendingPlayerIds(entry, resolutionState)
      return {
        turnId,
        label: `Turn ${turnNumber(entry, index)}: ${ruleType}`,
        detail,
        ruleLabel: ruleType,
        waitingPlayerIds,
        guidance: {
          pendingTurnId: turnId,
          ruleType,
          dcHint: stringValue(entry.metadata.dc_hint) || null,
          prompt: detail,
          remainingPlayerIds: waitingPlayerIds,
          rollSpec: pendingRollSpecFromMetadata(entry.metadata, ruleType),
        },
      }
    })
    .reverse()
}

export function pendingRollOptionsFromTimeline(timeline: TimelineEntry[]): PendingRollOption[] {
  return pendingRollTimelineEntries(timeline).map(({ turnId, label, detail, guidance }) => ({
    turnId,
    label,
    detail,
    guidance,
  }))
}

function playerDisplayName(player: Player) {
  return player.character_name || player.name || `Player ${player.player_id}`
}

function formatWaitingNames(names: string[]) {
  if (!names.length) return 'the acting character'
  if (names.length === 1) return names[0]
  if (names.length === 2) return `${names[0]} and ${names[1]}`
  return `${names.slice(0, -1).join(', ')}, and ${names[names.length - 1]}`
}

export function pendingRollNoticeFromTimeline(
  timeline: TimelineEntry[],
  players: Player[],
  selectedPlayerId: number | null,
): PendingRollNotice | null {
  const pendingRolls = pendingRollTimelineEntries(timeline)
  if (!pendingRolls.length) return null

  const current = pendingRolls[0]
  const playerNamesById = new Map(players.map((player) => [player.player_id, playerDisplayName(player)]))
  const waitingPlayerIds = uniquePositiveIntegers(current.waitingPlayerIds)
  const waitingPlayerNames = waitingPlayerIds.map((playerId) => playerNamesById.get(playerId) ?? `Player ${playerId}`)
  return {
    turnId: current.turnId,
    waitingOnLabel: formatWaitingNames(waitingPlayerNames),
    waitingPlayerIds,
    waitingPlayerNames,
    turnLabel: current.label.split(':')[0] || `Turn ${current.turnId}`,
    ruleLabel: current.ruleLabel,
    detail: current.detail,
    pendingCount: pendingRolls.length,
    isWaitingOnSelectedPlayer: selectedPlayerId !== null && waitingPlayerIds.includes(selectedPlayerId),
    guidance: current.guidance,
  }
}

export function memorySnippetRecords(value: unknown): JsonRecord[] {
  return Array.isArray(value) ? value.filter(isRecord) : []
}

export function recentMemoryFromSnippets(memorySnippets: JsonRecord[], selectedSessionId: number | null) {
  return [...memorySnippets]
    .reverse()
    .map((snippet) => {
      const source = stringValue(snippet.turn_id, '—')
      const text = stripMarkdown(
        stringValue(snippet.dm_output) || stringValue(snippet.player_input),
      )
      return [
        truncateText(text || 'Memory snippet has no text.', 86),
        `S${selectedSessionId ?? '—'}E${source}`,
      ] as [string, string]
    })
}
