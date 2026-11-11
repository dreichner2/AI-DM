import { isRecord, numberValue, stringValue } from '../gameSelectors'
import type { JsonRecord, TimelineEntry } from '../types'

/**
 * Story beats: the reading model of a session timeline.
 *
 * The console shows one card per log entry. Story mode reads the same entries
 * as a book would: narration as prose, a player's action as a short indented
 * line, dice as one centred line, mechanical consequences as quiet chips, and
 * engine bookkeeping not at all.
 */

export type StoryGap = 'none' | 'tight' | 'speaker' | 'turn'

export type StoryChangeTone = 'default' | 'fail' | 'success' | 'gold' | 'arcane'
export type StoryChangeGlyph =
  | 'heart'
  | 'coin'
  | 'star'
  | 'pack'
  | 'spark'
  | 'scroll'
  | 'place'
  | 'person'
  | 'swords'
  | 'mark'

export type StoryChange = {
  text: string
  tone: StoryChangeTone
  glyph: StoryChangeGlyph
}

export type StoryRollMode = 'normal' | 'advantage' | 'disadvantage'

/** A Bardic Inspiration die the server added to a roll that would have failed. */
export type StoryInspiration = {
  die: string
  roll: number
}

export type StoryRoll = {
  name: string
  checkLabel: string
  die: string
  faces: number[]
  kept: number | null
  modifier: number | null
  total: number | null
  /** The target number: a DC, or the target's AC for an attack. */
  dc: number | null
  /** An attack or spell attack: the outcome reads hit/miss against AC. */
  attack: boolean
  mode: StoryRollMode
  /** Already included in `total`. */
  inspiration: StoryInspiration | null
  outcome: 'success' | 'failure' | null
  natural: 'twenty' | 'one' | null
}

export type StoryActionDelivery = 'sent' | 'pending' | 'failed'

type BeatBase = { key: string; gap: StoryGap }

export type ChapterBeat = BeatBase & { kind: 'chapter'; entry: TimelineEntry; chapterLabel: string | null }
export type NarrationBeat = BeatBase & {
  kind: 'narration'
  entry: TimelineEntry
  text: string
  streaming: boolean
  /** The first narration of the session; it gets the drop cap. */
  opening: boolean
  notSaved: boolean
  turnId: number | null
}
export type ActionBeat = BeatBase & {
  kind: 'action'
  entry: TimelineEntry
  speaker: string
  text: string
  delivery: StoryActionDelivery
  own: boolean
}
export type RollBeat = BeatBase & { kind: 'roll'; entry: TimelineEntry; roll: StoryRoll }
export type ChangesBeat = BeatBase & { kind: 'changes'; entry: TimelineEntry; changes: StoryChange[] }
export type NoticeBeat = BeatBase & { kind: 'notice'; entry: TimelineEntry; text: string }

export type StoryBeat = ChapterBeat | NarrationBeat | ActionBeat | RollBeat | ChangesBeat | NoticeBeat

export type BuildStoryBeatsOptions = {
  selectedPlayerName?: string | null
  /** False while older log pages are still unloaded: the first visible narration is then not the session's first. */
  historyComplete?: boolean
}

const CHAPTER_TEXT_RE = /^\**\s*(welcome|chapter)\b/i
const CHAPTER_NUMBER_RE = /^\**\s*chapter\s+([0-9]+|[ivxlc]+|[a-z]+)\b/i
const BOOKKEEPING_RE =
  /^\**\s*(?:state (?:updated|applied)|canon (?:applied|pending|updated|saved)|segment triggered|turn (?:saved|persisted)|saved\b|persist|autosave)/i
// "I roll a d20+3 for Stealth: 9 (advantage; rolls 9, 4) 1d4 (3) + Bardic Inspiration d6 (4) = 19":
// kept die, optional mode detail, bonus dice, an optional inspiration die, total.
const CANONICAL_ROLL_RE =
  /\broll(?:s|ed)?\s+(?:a\s+|an\s+)?(d\d+)\s*(?:([+\-−])\s*(\d+))?(?:\s+for\s+([^:\n]+?))?\s*:\s*(\d+)(?:\s*\(([^)]*)\))?(?:\s+[+\-−]?\d*d\d+\s*\(-?\d+\))*(?:\s*\+\s*Bardic Inspiration\s+(d\d+)\s*\((\d+)\))?\s*=\s*(-?\d+)/i
const RESOLVED_ROLL_RE = /^\**\s*(?:check|roll) resolved\**\s*:.*?\b(?:with roll|result)\s+(-?\d+)/i
const DC_RE = /(?:dc\s*)?(\d{1,2})/i
const CONDITION_RE =
  /\b(?:spell|slot|condition|concentrat|cantrip|blinded|charmed|deafened|exhaust|frightened|grappled|incapacitated|invisible|paralyzed|petrified|poisoned|prone|restrained|stunned|unconscious)/
const LEADING_LABEL_RE =/^([A-Za-z][\w .'-]{0,31}?):\s+(?=\S)/
/** A bare word such as "calm": a mood-only scene update logged by older servers, not a change worth a chip. */
const BARE_WORD_RE = /^[a-z]+$/
const ATTACK_RULE_TYPES = new Set(['attack', 'spell_attack'])
const DEATH_SAVE_CHANGE = 'combat.death_save'
const DEATH_SAVE_RE = /^death saving throw:\s*(.+?)\s+rolled\s+(\d{1,2})\s*\(([^)]*)\)/i

function metadataRecord(metadata: JsonRecord, key: string): JsonRecord | null {
  const value = metadata[key]
  return isRecord(value) ? value : null
}

function persistenceStatus(entry: TimelineEntry) {
  return stringValue(entry.metadata.persistence_status).trim().toLowerCase()
}

function turnIdOf(entry: TimelineEntry) {
  const parsed = numberValue(entry.metadata.turn_id)
  return parsed !== null && Number.isInteger(parsed) && parsed > 0 ? parsed : null
}

function capitalize(value: string) {
  const trimmed = value.trim()
  return trimmed ? trimmed.charAt(0).toUpperCase() + trimmed.slice(1) : trimmed
}

function numberList(value: unknown): number[] {
  if (!Array.isArray(value)) return []
  return value.map((item) => numberValue(item)).filter((item): item is number => item !== null)
}

function dcValue(...values: unknown[]): number | null {
  for (const value of values) {
    const direct = numberValue(value)
    if (direct !== null && direct > 0) return direct
    const text = stringValue(value)
    const match = text ? DC_RE.exec(text) : null
    if (match) return Number(match[1])
  }
  return null
}

function inspirationValue(value: unknown): StoryInspiration | null {
  if (!isRecord(value)) return null
  const die = stringValue(value.die).toLowerCase()
  const roll = numberValue(value.roll)
  return /^d\d+$/.test(die) && roll !== null && roll > 0 ? { die, roll } : null
}

function rollMode(value: unknown): StoryRollMode {
  const mode = stringValue(value).toLowerCase()
  return mode === 'advantage' || mode === 'disadvantage' ? mode : 'normal'
}

function explicitOutcome(...records: Array<JsonRecord | null>): 'success' | 'failure' | null {
  for (const record of records) {
    if (!record) continue
    if (typeof record.success === 'boolean') return record.success ? 'success' : 'failure'
    const outcome = stringValue(record.outcome ?? record.result).toLowerCase()
    if (/^(success|succeeded|hit|pass(ed)?)$/.test(outcome)) return 'success'
    if (/^(fail(ure|ed)?|miss(ed)?)$/.test(outcome)) return 'failure'
  }
  return null
}

function checkLabelFrom(reason: string, ruleType: string, abilityLabel: string) {
  if (reason) return capitalize(reason)
  const rule = ruleType.replace(/_/g, ' ').trim()
  if (abilityLabel && rule) {
    return capitalize(`${abilityLabel} ${rule.replace(/^ability\s+/, '')}`)
  }
  return capitalize(rule || abilityLabel || 'roll')
}

function finishRoll(roll: Omit<StoryRoll, 'outcome' | 'natural'>, outcome: 'success' | 'failure' | null): StoryRoll {
  const derivedOutcome =
    outcome ?? (roll.total !== null && roll.dc !== null ? (roll.total >= roll.dc ? 'success' : 'failure') : null)
  const natural =
    roll.die === 'd20' && roll.kept === 20 ? 'twenty' : roll.die === 'd20' && roll.kept === 1 ? 'one' : null
  return { ...roll, outcome: derivedOutcome, natural }
}

function isAttackRule(ruleType: string) {
  return ATTACK_RULE_TYPES.has(ruleType.trim().toLowerCase())
}

function rollFromRecord(record: JsonRecord, entry: TimelineEntry, name: string): StoryRoll {
  const metadata = entry.metadata
  const ruleType = stringValue(record.rule_type ?? metadata.rule_type ?? metadata.roll_type)
  const faces = numberList(record.rolls)
  const kept = numberValue(record.kept) ?? (faces.length === 1 ? faces[0] : null)
  const modifier = numberValue(record.modifier)
  const total =
    numberValue(record.total) ??
    numberValue(metadata.roll_value) ??
    (kept !== null ? kept + (modifier ?? 0) : null)
  const ability = isRecord(record.ability) ? record.ability : null
  return finishRoll(
    {
      name,
      checkLabel: checkLabelFrom(stringValue(record.reason), ruleType, stringValue(ability?.label)),
      die: stringValue(record.die, 'd20').toLowerCase(),
      faces,
      kept,
      modifier,
      total,
      dc: dcValue(record.task_dc, record.dc, metadata.task_dc, metadata.dc, metadata.dc_hint),
      attack: isAttackRule(ruleType),
      mode: rollMode(record.mode),
      inspiration: inspirationValue(record.inspiration),
    },
    explicitOutcome(record, metadata),
  )
}

function rollFromText(text: string, entry: TimelineEntry, name: string): StoryRoll | null {
  const metadata = entry.metadata
  const attack = isAttackRule(stringValue(metadata.rule_type ?? metadata.roll_type))
  const canonical = CANONICAL_ROLL_RE.exec(text)
  if (canonical) {
    const [, die, sign, modifierText, reason, keptText, detail, inspirationDie, inspirationRoll, totalText] = canonical
    const kept = Number(keptText)
    const modifier = modifierText ? (sign === '+' ? 1 : -1) * Number(modifierText) : 0
    const detailFaces = detail ? numberList(detail.match(/\d+/g)?.map(Number) ?? []) : []
    return finishRoll(
      {
        name,
        checkLabel: checkLabelFrom(reason ?? '', stringValue(metadata.rule_type), ''),
        die: die.toLowerCase(),
        faces: detailFaces.length ? detailFaces : [kept],
        kept,
        modifier,
        total: Number(totalText),
        dc: dcValue(metadata.task_dc, metadata.dc, metadata.dc_hint),
        attack,
        mode: rollMode(detail?.split(';')[0]),
        inspiration: inspirationDie ? { die: inspirationDie.toLowerCase(), roll: Number(inspirationRoll) } : null,
      },
      explicitOutcome(metadata),
    )
  }
  const resolved = RESOLVED_ROLL_RE.exec(text)
  if (resolved) {
    return finishRoll(
      {
        name,
        checkLabel: checkLabelFrom('', stringValue(metadata.rule_type), ''),
        die: 'd20',
        faces: [],
        kept: null,
        modifier: null,
        total: Number(resolved[1]),
        dc: dcValue(metadata.task_dc, metadata.dc, metadata.dc_hint),
        attack,
        mode: 'normal',
        inspiration: null,
      },
      explicitOutcome(metadata),
    )
  }
  return null
}

function rollRecord(entry: TimelineEntry): JsonRecord | null {
  const metadata = entry.metadata
  const authoritative = metadataRecord(metadata, 'authoritative_roll')
  if (authoritative) return authoritative
  const intent = metadataRecord(metadata, 'action_intent')
  if (intent && stringValue(intent.kind) === 'roll' && isRecord(intent.roll)) return intent.roll
  return metadataRecord(metadata, 'roll')
}

/** The roll an entry records, from metadata when present, otherwise from its text. */
export function rollFromEntry(entry: TimelineEntry): StoryRoll | null {
  if (entry.role === 'dm') return null
  const name = entry.role === 'player' ? entry.speaker : ''
  const record = rollRecord(entry)
  if (record) return rollFromRecord(record, entry, name)
  return rollFromText(entry.text, entry, name)
}

function actionTextBesideRoll(text: string) {
  return text
    .split('\n')
    .filter((line) => !CANONICAL_ROLL_RE.test(line))
    .join('\n')
    .trim()
}

function changeStyle(changeType: string, text: string): Pick<StoryChange, 'tone' | 'glyph'> {
  const type = changeType.toLowerCase()
  const lower = text.toLowerCase()
  if (type === 'health.damage' || /\b(took|takes|lost|loses)\s+\d+\s+(damage|hp)\b/.test(lower)) {
    return { tone: 'fail', glyph: 'heart' }
  }
  if (type === 'health.heal' || /\b(restored|regained|healed|heals?)\b/.test(lower)) {
    return { tone: 'success', glyph: 'heart' }
  }
  if (type.startsWith('combat.') || /\bdefeated\b/.test(lower)) return { tone: 'default', glyph: 'swords' }
  if (type.startsWith('currency.') || /\b\d+\s*(gp|sp|cp|pp|gold|silver|copper)\b/.test(lower)) {
    return { tone: 'gold', glyph: 'coin' }
  }
  if (type.startsWith('xp.') || /\bxp\b/.test(lower)) return { tone: 'gold', glyph: 'star' }
  if (type.startsWith('inventory.') || type.startsWith('scene.item')) return { tone: 'gold', glyph: 'pack' }
  if (type.startsWith('spell') || type.startsWith('condition') || CONDITION_RE.test(lower)) {
    return { tone: 'arcane', glyph: 'spark' }
  }
  if (type.startsWith('quest') || /\bquest\b/.test(lower)) {
    if (type === 'quest.complete') return { tone: 'success', glyph: 'scroll' }
    if (type === 'quest.fail') return { tone: 'fail', glyph: 'scroll' }
    return { tone: 'default', glyph: 'scroll' }
  }
  if (type.startsWith('location') || type.startsWith('scene.move')) return { tone: 'default', glyph: 'place' }
  if (type.startsWith('npc')) return { tone: 'default', glyph: 'person' }
  return { tone: 'default', glyph: 'mark' }
}

function chipText(message: string) {
  return message.replace(LEADING_LABEL_RE, '').replace(/\.\s*$/, '').trim()
}

function visibleStateLines(entry: TimelineEntry): JsonRecord[] {
  const stateLog = metadataRecord(entry.metadata, 'state_log')
  const lines = stateLog && Array.isArray(stateLog.lines) ? stateLog.lines : []
  return lines.filter((line): line is JsonRecord => isRecord(line) && line.visibleToPlayer !== false)
}

function isDeathSaveLine(line: JsonRecord) {
  return stringValue(line.changeType) === DEATH_SAVE_CHANGE && stringValue(line.status) !== 'rejected'
}

/**
 * The engine's death saving throws in a state update, as roll lines. The
 * server logs each as "Death saving throw: <name> rolled <d20> (<result>); …".
 */
export function deathSaveRolls(entry: TimelineEntry): StoryRoll[] {
  return visibleStateLines(entry).filter(isDeathSaveLine).flatMap((line): StoryRoll[] => {
    const match = DEATH_SAVE_RE.exec(stringValue(line.message))
    if (!match) return []
    const [, name, rollText, result] = match
    const roll = Number(rollText)
    return [{
      name: name.trim(),
      checkLabel: 'Death save',
      die: 'd20',
      faces: [roll],
      kept: null,
      modifier: null,
      total: roll,
      dc: null,
      attack: false,
      mode: 'normal',
      inspiration: null,
      outcome: /success/i.test(result) ? 'success' : 'failure',
      natural: roll === 20 ? 'twenty' : roll === 1 ? 'one' : null,
    }]
  })
}

/** "What changed" lines of a state-update entry, as chips. Hidden lines and death saves (roll lines) are skipped. */
export function storyChanges(entry: TimelineEntry): StoryChange[] {
  return visibleStateLines(entry).flatMap((line): StoryChange[] => {
    if (isDeathSaveLine(line) && DEATH_SAVE_RE.test(stringValue(line.message))) return []
    const message = stringValue(line.message).trim()
    const text = chipText(message)
    if (!text || BARE_WORD_RE.test(text)) return []
    const style =
      stringValue(line.status).toLowerCase() === 'rejected'
        ? { tone: 'fail' as const, glyph: 'mark' as const }
        : changeStyle(stringValue(line.changeType), message)
    return [{ text, ...style }]
  })
}

function isChapterEntry(entry: TimelineEntry) {
  if (entry.role !== 'system') return false
  return stringValue(entry.metadata.kind) === 'session_welcome' || CHAPTER_TEXT_RE.test(entry.text.trim())
}

function chapterLabel(entry: TimelineEntry) {
  const match = CHAPTER_NUMBER_RE.exec(entry.text.trim())
  return match ? `Chapter ${match[1]}` : null
}

function isBookkeeping(entry: TimelineEntry) {
  return BOOKKEEPING_RE.test(entry.text.trim())
}

function actionDelivery(entry: TimelineEntry): StoryActionDelivery {
  const status = persistenceStatus(entry)
  if (status === 'failed') return 'failed'
  if (status === 'pending' || status === 'sending' || status === 'queued') return 'pending'
  return 'sent'
}

function narrationText(entry: TimelineEntry) {
  return entry.streaming && entry.text.trim() === '...' ? '' : entry.text
}

type PendingBeat = StoryBeat extends infer Beat ? (Beat extends StoryBeat ? Omit<Beat, 'gap'> : never) : never

function gapBefore(beat: PendingBeat, previous: PendingBeat | undefined): StoryGap {
  if (!previous) return 'none'
  switch (beat.kind) {
    case 'changes':
      return 'tight'
    case 'action':
      if (previous.kind === 'action' || previous.kind === 'roll') {
        const previousName = previous.kind === 'action' ? previous.speaker : previous.roll.name
        return previousName === beat.speaker ? 'tight' : 'speaker'
      }
      return previous.kind === 'chapter' ? 'speaker' : 'turn'
    case 'roll':
      if (previous.kind === 'action' && previous.speaker === beat.roll.name) return 'tight'
      if (previous.kind === 'roll' && previous.roll.name === beat.roll.name) return 'tight'
      return 'speaker'
    default:
      return 'speaker'
  }
}

/**
 * Build the beats of a timeline (`turnRows` followed by the current response
 * entry). Pure: the same entries always produce the same beats and keys.
 */
export function buildStoryBeats(
  entries: TimelineEntry[],
  { selectedPlayerName = null, historyComplete = true }: BuildStoryBeatsOptions = {},
): StoryBeat[] {
  const loggedDmTurnIds = new Set(
    entries
      .filter((entry) => entry.role === 'dm' && !entry.streaming)
      .map(turnIdOf)
      .filter((turnId): turnId is number => turnId !== null),
  )
  const rolls = new Map<TimelineEntry, StoryRoll>()
  const playerRollTurnIds = new Set<number>()
  entries.forEach((entry) => {
    const roll = rollFromEntry(entry)
    if (!roll) return
    rolls.set(entry, roll)
    const turnId = turnIdOf(entry)
    if (entry.role === 'player' && turnId !== null) playerRollTurnIds.add(turnId)
  })
  // A server-resolved roll is logged twice for one turn: once as the
  // authoritative system result and once as the player's canonical sentence.
  // Keep the player's line (it has the name) and fill gaps from the system one.
  const systemRollsByTurn = new Map<number, StoryRoll>()
  rolls.forEach((roll, entry) => {
    const turnId = turnIdOf(entry)
    if (entry.role === 'system' && turnId !== null && playerRollTurnIds.has(turnId)) {
      systemRollsByTurn.set(turnId, roll)
    }
  })

  const beats: PendingBeat[] = []
  const usedKeys = new Map<string, number>()
  const uniqueKey = (base: string) => {
    const count = usedKeys.get(base) ?? 0
    usedKeys.set(base, count + 1)
    return count ? `${base}~${count}` : base
  }
  let sawNarration = false
  let sawStory = false

  entries.forEach((entry) => {
    const turnId = turnIdOf(entry)

    if (entry.role === 'dm') {
      if (entry.streaming && turnId !== null && loggedDmTurnIds.has(turnId)) return
      const text = narrationText(entry)
      if (!text.trim() && !entry.streaming) return
      beats.push({
        kind: 'narration',
        key: uniqueKey(turnId !== null ? `dm-${turnId}` : `dm-${entry.id}`),
        entry,
        text,
        streaming: Boolean(entry.streaming),
        opening: historyComplete && !sawNarration,
        notSaved: persistenceStatus(entry) === 'failed',
        turnId,
      })
      sawNarration = true
      sawStory = true
      return
    }

    const roll = rolls.get(entry)
    if (roll) {
      if (entry.role === 'system' && turnId !== null && playerRollTurnIds.has(turnId)) return
      const merged = turnId !== null ? systemRollsByTurn.get(turnId) : undefined
      const finalRoll = merged
        ? finishRoll(
            {
              ...roll,
              dc: roll.dc ?? merged.dc,
              attack: roll.attack || merged.attack,
              faces: roll.faces.length ? roll.faces : merged.faces,
              kept: roll.kept ?? merged.kept,
              modifier: roll.modifier ?? merged.modifier,
              total: roll.total ?? merged.total,
              inspiration: roll.inspiration ?? merged.inspiration,
            },
            roll.outcome ?? merged.outcome,
          )
        : roll
      if (entry.role === 'player') {
        const actionText = actionTextBesideRoll(entry.text)
        if (actionText) {
          beats.push({
            kind: 'action',
            key: uniqueKey(`act-${stringValue(entry.metadata.client_message_id) || entry.id}`),
            entry,
            speaker: entry.speaker,
            text: actionText,
            delivery: actionDelivery(entry),
            own: Boolean(selectedPlayerName) && entry.speaker === selectedPlayerName,
          })
        }
      }
      beats.push({ kind: 'roll', key: uniqueKey(`roll-${turnId ?? entry.id}`), entry, roll: finalRoll })
      sawStory = true
      return
    }

    if (entry.role === 'player') {
      const text = entry.text.trim()
      if (!text) return
      beats.push({
        kind: 'action',
        key: uniqueKey(`act-${stringValue(entry.metadata.client_message_id) || entry.id}`),
        entry,
        speaker: entry.speaker,
        text,
        delivery: actionDelivery(entry),
        own: Boolean(selectedPlayerName) && entry.speaker === selectedPlayerName,
      })
      sawStory = true
      return
    }

    const deathSaves = deathSaveRolls(entry)
    deathSaves.forEach((deathSave, index) => {
      beats.push({ kind: 'roll', key: uniqueKey(`death-${entry.id}-${index}`), entry, roll: deathSave })
    })
    const changes = storyChanges(entry)
    if (changes.length) {
      beats.push({ kind: 'changes', key: uniqueKey(`chg-${entry.id}`), entry, changes })
      return
    }
    if (deathSaves.length) {
      sawStory = true
      return
    }
    if (!sawStory && isChapterEntry(entry) && !beats.some((beat) => beat.kind === 'chapter')) {
      beats.push({ kind: 'chapter', key: uniqueKey(`chapter-${entry.id}`), entry, chapterLabel: chapterLabel(entry) })
      return
    }
    if (isBookkeeping(entry)) return
    const text = entry.text.replace(/\*\*/g, '').trim()
    if (!text) return
    beats.push({ kind: 'notice', key: uniqueKey(`note-${entry.id}`), entry, text })
  })

  return beats.map((beat, index) => ({ ...beat, gap: gapBefore(beat, beats[index - 1]) }) as StoryBeat)
}

/** True while the session has no story yet: nothing but (at most) its chapter heading. */
export function isEmptyStory(beats: StoryBeat[]) {
  return beats.every((beat) => beat.kind === 'chapter')
}
