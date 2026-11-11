import { isRecord, stringValue } from '../gameSelectors'
import type { TimelineEntry } from '../types'

function record(value: unknown) {
  return isRecord(value) ? value : null
}

function turnIdOf(entry: TimelineEntry) {
  const turnId = entry.metadata.turn_id
  return typeof turnId === 'number' ? turnId : null
}

/** The entry whose state changes began a fight (a `combat.start` line). */
function startsFight(entry: TimelineEntry) {
  const lines = record(entry.metadata.state_log)?.lines
  return Array.isArray(lines) && lines.some((line) => stringValue(record(line)?.changeType) === 'combat.start')
}

/**
 * The spells a hero cast in this fight, most recent first: their own typed
 * casts since the turn the latest fight began (every loaded turn when that is
 * not loaded), including casts still on their way to the table.
 */
export function recentSpellNames(entries: TimelineEntry[], heroName: string) {
  const start = [...entries].reverse().find(startsFight)
  const startTurnId = start ? turnIdOf(start) : null
  const names: string[] = []
  for (const entry of [...entries].reverse()) {
    if (entry.role !== 'player' || entry.speaker !== heroName) continue
    const turnId = turnIdOf(entry)
    if (startTurnId !== null && turnId !== null && turnId < startTurnId) break
    const name = stringValue(record(record(entry.metadata.action_intent)?.spell)?.name)
    if (name && !names.includes(name)) names.push(name)
  }
  return names
}
