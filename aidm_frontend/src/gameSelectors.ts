import type { AbilityOption, ItemOption } from './gameActions'
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
  item: string
  count: string
  weight: string
  icon: string
  weightValue: number | null
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

export type WorldStatePanel = {
  sceneName: string
  sceneType: string
  mood: string
  dangerLevel: string
  activeQuests: WorldQuestSummary[]
  knownLocations: WorldLocationSummary[]
  knownNpcs: WorldNpcSummary[]
}

export type PendingRollOption = {
  turnId: number
  label: string
  detail: string
}

export function isRecord(value: unknown): value is JsonRecord {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

export function stringValue(value: unknown, fallback = '') {
  if (typeof value === 'string' && value.trim()) return value.trim()
  if (typeof value === 'number' && Number.isFinite(value)) return String(value)
  return fallback
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

export function normalizeInventory(value: unknown): InventoryRow[] {
  const source = Array.isArray(value)
    ? value
    : isRecord(value) && Array.isArray(value.items)
      ? value.items
      : []
  const iconFor = (item: string, index: number) => {
    const normalized = item.toLowerCase()
    if (normalized.includes('sword') || normalized.includes('blade')) return 'sword'
    if (normalized.includes('shield')) return 'shield'
    if (normalized.includes('potion') || normalized.includes('vial')) return 'potion'
    if (normalized.includes('armor') || normalized.includes('mail')) return 'armor'
    if (normalized.includes('ration') || normalized.includes('food')) return 'ration'
    return ['sword', 'shield', 'potion', 'armor', 'ration'][index % 5]
  }
  return source.map((entry, index) => {
    if (typeof entry === 'string') {
      return { item: entry, count: '1', weight: '—', icon: iconFor(entry, index), weightValue: null }
    }
    if (!isRecord(entry)) {
      return { item: `Item ${index + 1}`, count: '1', weight: '—', icon: 'ration', weightValue: null }
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
    return {
      item,
      count: stringValue(entry.quantity ?? entry.count, '1'),
      weight: weightNumber === null ? '—' : `${weightValue} lb`,
      icon: stringValue(entry.icon, iconFor(item, index)),
      weightValue,
    }
  })
}

export function normalizeStats(statsValue: unknown, sheetValue: unknown, level: number | null): StatBlock {
  const records = [...collectRecords(statsValue), ...collectRecords(sheetValue)]
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
    ac: statLabel(['ac', 'armor_class', 'armorClass']),
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
    name: row.item,
    quantity: row.count,
  }))
}

export function normalizeXp(value: unknown, level: number | string): XpProgress {
  const records = collectRecords(value)
  const current = numberValue(findValue(records, ['xp', 'experience', 'current_xp'])) ?? 0
  const max =
    numberValue(findValue(records, ['xp_to_next', 'next_level_xp', 'max_xp'])) ??
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
    .slice(0, 4)
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
  const entries = logEntries.map(timelineFromLog).map(withStatus).concat(optimisticEntries)
  if (streamingTurn) {
    entries.push({
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
    })
  }
  return entries
}

export function pendingRollOptionsFromTimeline(timeline: TimelineEntry[]): PendingRollOption[] {
  const resolvedTurnIds = new Set<number>()
  timeline.forEach((entry) => {
    const resolvedTurnId = numberValue(entry.metadata.resolved_turn_id)
    if (resolvedTurnId !== null && Number.isInteger(resolvedTurnId)) {
      resolvedTurnIds.add(resolvedTurnId)
    }
  })

  return timeline
    .filter((entry) => {
      const turnId = metadataTurnId(entry.metadata)
      return (
        turnId !== null &&
        !resolvedTurnIds.has(turnId) &&
        Boolean(entry.metadata.requires_roll) &&
        stringValue(entry.metadata.outcome_status).toLowerCase() === 'deferred'
      )
    })
    .map((entry, index) => {
      const turnId = metadataTurnId(entry.metadata) as number
      const ruleType = stringValue(entry.metadata.rule_type, 'check').replace(/_/g, ' ')
      const detail = truncateText(entry.text || 'Pending check', 72)
      return {
        turnId,
        label: `Turn ${turnNumber(entry, index)}: ${ruleType}`,
        detail,
      }
    })
    .reverse()
}

export function memorySnippetRecords(value: unknown): JsonRecord[] {
  return Array.isArray(value) ? value.filter(isRecord) : []
}

export function canonFactsFromMemorySnippets(memorySnippets: JsonRecord[], selectedSessionId: number | null) {
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
