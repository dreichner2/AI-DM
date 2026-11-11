import type {
  StoryRoll as RollCardRecord,
  StoryRollD20,
  StoryRollDamage,
  StoryRollDice,
  StoryRollInitiativeEntry,
  StoryRollKind,
  StoryRollPart,
  StoryRollSide,
} from '../apiContract.generated'
import { isRecord, numberValue, stringValue } from '../gameSelectors'
import type { TimelineEntry } from '../types'

/**
 * The server's `storyRolls`: every roll of a turn, one roll card each, exactly
 * as the engine resolved it. Turns saved before roll cards carry none and keep
 * the roll lines read from their text.
 */

export type { RollCardRecord }

const KINDS = new Set<StoryRollKind>(['attack', 'save', 'check', 'death_save', 'damage', 'healing', 'initiative'])
const SIDES = new Set<StoryRollSide>(['hero', 'enemy', 'ally'])
const MINUS = '−'

function numbers(value: unknown): number[] {
  return Array.isArray(value) ? value.map(numberValue).filter((item): item is number => item !== null) : []
}

function side(value: unknown): StoryRollSide {
  const text = stringValue(value) as StoryRollSide
  return SIDES.has(text) ? text : 'ally'
}

function part(value: unknown): StoryRollPart | null {
  if (!isRecord(value)) return null
  const amount = numberValue(value.value)
  if (amount === null) return null
  const dice = stringValue(value.dice)
  return { label: stringValue(value.label), value: amount, ...(dice ? { dice, faces: numbers(value.faces) } : {}) }
}

function parts(value: unknown): StoryRollPart[] {
  return Array.isArray(value) ? value.map(part).filter((item): item is StoryRollPart => item !== null) : []
}

function d20(value: unknown): StoryRollD20 | null {
  if (!isRecord(value)) return null
  const kept = numberValue(value.kept)
  if (kept === null) return null
  const mode = stringValue(value.mode)
  return {
    faces: numbers(value.faces).length ? numbers(value.faces) : [kept],
    kept,
    mode: mode === 'advantage' || mode === 'disadvantage' ? mode : 'normal',
  }
}

function dice(value: unknown): StoryRollDice | null {
  if (!isRecord(value)) return null
  const total = numberValue(value.total)
  if (total === null) return null
  const extra = parts(value.parts)
  return {
    dice: stringValue(value.dice),
    faces: numbers(value.faces),
    bonus: numberValue(value.bonus) ?? 0,
    total,
    ...(extra.length ? { parts: extra } : {}),
  }
}

function damage(value: unknown): StoryRollDamage | null {
  const rolled = dice(value)
  if (!rolled || !isRecord(value)) return null
  return {
    ...rolled,
    type: stringValue(value.type) || null,
    critical: value.critical === true,
    note: stringValue(value.note) || null,
  }
}

function initiativeEntry(value: unknown): StoryRollInitiativeEntry | null {
  if (!isRecord(value)) return null
  const total = numberValue(value.total)
  if (total === null) return null
  return { name: stringValue(value.name), side: side(value.side), faces: numbers(value.faces), bonus: numberValue(value.bonus) ?? 0, total }
}

/** One server record, or null when it is not one the client can show. */
export function rollCardRecord(value: unknown): RollCardRecord | null {
  if (!isRecord(value)) return null
  const id = stringValue(value.id)
  const kind = stringValue(value.kind) as StoryRollKind
  if (!id || !KINDS.has(kind)) return null
  const actor = isRecord(value.actor)
    ? { id: stringValue(value.actor.id), name: stringValue(value.actor.name), side: side(value.actor.side) }
    : undefined
  const target = isRecord(value.target) ? { id: stringValue(value.target.id), name: stringValue(value.target.name) } : undefined
  const againstKind = isRecord(value.against) ? stringValue(value.against.kind) : ''
  // A contest is against the hero's own Athletics; anything else the client does not know reads as a DC.
  const against: RollCardRecord['against'] = isRecord(value.against) && numberValue(value.against.value) !== null
    ? {
        kind: againstKind === 'AC' || againstKind === 'Athletics' ? againstKind : 'DC',
        value: numberValue(value.against.value) as number,
      }
    : null
  const outcome = stringValue(value.outcome)
  const natural = stringValue(value.natural)
  const rolledDamage = damage(value.damage)
  const healing = dice(value.healing)
  const entries = Array.isArray(value.entries)
    ? value.entries.map(initiativeEntry).filter((entry): entry is StoryRollInitiativeEntry => entry !== null)
    : []
  return {
    id,
    kind,
    ...(actor ? { actor } : {}),
    ...(target ? { target } : {}),
    label: stringValue(value.label),
    ...(stringValue(value.source) ? { source: stringValue(value.source) } : {}),
    d20: d20(value.d20),
    ...(stringValue(value.die) ? { die: stringValue(value.die) } : {}),
    parts: parts(value.parts),
    total: numberValue(value.total),
    against,
    outcome: outcome === 'hit' || outcome === 'miss' || outcome === 'success' || outcome === 'failure' ? outcome : null,
    natural: natural === 'twenty' || natural === 'one' ? natural : null,
    ...(rolledDamage ? { damage: rolledDamage } : {}),
    ...(healing ? { healing } : {}),
    ...(entries.length ? { entries } : {}),
  }
}

/** A list of server records; null when the value is not a list (a turn from before roll cards). */
export function rollCardRecords(value: unknown): RollCardRecord[] | null {
  if (!Array.isArray(value)) return null
  return value.map(rollCardRecord).filter((record): record is RollCardRecord => record !== null)
}

/** The turn's roll cards on an entry; null when it carries none. */
export function entryRollCards(entry: TimelineEntry): RollCardRecord[] | null {
  return rollCardRecords(entry.metadata.storyRolls)
}

export function signedNumber(value: number) {
  return value < 0 ? `${MINUS} ${Math.abs(value)}` : `+ ${value}`
}

/** `1d8` of `1d8+3`: a dice expression without its flat bonus. */
export function diceWithoutBonus(expression: string) {
  return /^\s*[+-]?(\d*d\d+)/i.exec(expression)?.[1] ?? expression
}

/** The flat modifier of a d20 roll: every part that is not a die. */
export function flatModifier(record: RollCardRecord) {
  return (record.parts ?? []).filter((item) => !item.dice).reduce((sum, item) => sum + item.value, 0)
}

export function diceParts(record: RollCardRecord) {
  return (record.parts ?? []).filter((item) => item.dice)
}

/** The sides of a die name (`d20` → 20, `1d8` → 8). */
export function dieSides(die: string) {
  const sides = Number(/d(\d+)/i.exec(die)?.[1])
  return Number.isInteger(sides) && sides > 0 ? sides : 20
}

/** `hit vs AC 17`, `success vs DC 12`, `failure` (no target number). */
export function outcomeText(record: RollCardRecord) {
  if (!record.outcome) return ''
  return record.against ? `${record.outcome} vs ${record.against.kind} ${record.against.value}` : record.outcome
}

/** What the card's details show: each part of the roll, then the extra damage dice. */
export function partsText(record: RollCardRecord) {
  const lines = (record.parts ?? []).map((item) =>
    item.dice ? `${item.label} ${item.dice} ${(item.faces ?? []).join(' ')}` : `${item.label} ${signedNumber(item.value)}`,
  )
  for (const extra of record.damage?.parts ?? record.healing?.parts ?? []) {
    lines.push(`${extra.label} ${extra.dice ?? ''} ${(extra.faces ?? []).join(' ')}`.replace(/\s+/g, ' ').trim())
  }
  return lines
}
