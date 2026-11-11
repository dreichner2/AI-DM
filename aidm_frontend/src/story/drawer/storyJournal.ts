import { isRecord, stringValue } from '../../gameSelectors'
import type { JsonRecord } from '../../types'

/**
 * The server's player-safe journal (`state_snapshot.journal`): the quest and
 * its current stage, the objectives the party has completed or is working on
 * (never later ones), only the clues, people and places the party knows (each
 * person with their disposition word and what they remember of the viewer's
 * heroes), the threads of the viewer's own hero the adventure has told, and
 * their word given (`yourWord`).
 */
export type StoryJournal = {
  quest: {
    id: string
    title: string
    summary: string
    status: string
    stageTitle: string
    stageSummary: string
  } | null
  objectives: Array<{ id: string; title: string; optional: boolean; status: 'completed' | 'active' }>
  clues: Array<{ id: string; title: string; text: string }>
  people: Array<{ id: string; name: string; role: string; summary: string; disposition: string; remembers: string[] }>
  places: Array<{ id: string; name: string; state: string }>
  threads: Array<{ id: string; title: string; text: string }>
  yourWord: Array<{ id: string; text: string; to: string; status: string }>
}

function records(value: unknown): JsonRecord[] {
  return Array.isArray(value) ? value.filter(isRecord) : []
}

/** "innkeeper_and_local_witness" → "innkeeper and local witness". */
export function humanizedRole(role: string) {
  return role.replace(/[_-]+/g, ' ').replace(/\s+/g, ' ').trim()
}

/** The journal block from a state snapshot; null when the server sends none (an older server). */
export function storyJournalFromSnapshot(snapshot: unknown): StoryJournal | null {
  const journal = isRecord(snapshot) && isRecord(snapshot.journal) ? snapshot.journal : null
  if (!journal) return null
  const quest = isRecord(journal.quest) ? journal.quest : null
  return {
    quest: quest && stringValue(quest.title)
      ? {
          id: stringValue(quest.id),
          title: stringValue(quest.title),
          summary: stringValue(quest.summary),
          status: stringValue(quest.status),
          stageTitle: stringValue(quest.stageTitle),
          stageSummary: stringValue(quest.stageSummary),
        }
      : null,
    objectives: records(journal.objectives)
      .map((objective, index) => ({
        id: stringValue(objective.id, `objective-${index + 1}`),
        title: stringValue(objective.title),
        optional: objective.optional === true,
        status: stringValue(objective.status) === 'completed' ? 'completed' as const : 'active' as const,
      }))
      .filter((objective) => objective.title),
    clues: records(journal.clues)
      .map((clue) => ({ id: stringValue(clue.id), title: stringValue(clue.title), text: stringValue(clue.text) }))
      .filter((clue) => clue.title),
    people: records(journal.people)
      .filter((person) => person.met !== false)
      .map((person) => ({
        id: stringValue(person.id),
        name: stringValue(person.name),
        role: humanizedRole(stringValue(person.role)),
        summary: stringValue(person.summary),
        disposition: stringValue(person.disposition),
        remembers: Array.isArray(person.remembers) ? person.remembers.map((line) => stringValue(line)).filter(Boolean) : [],
      }))
      .filter((person) => person.name),
    places: records(journal.places)
      .map((place) => ({ id: stringValue(place.id), name: stringValue(place.name), state: stringValue(place.state) }))
      .filter((place) => place.name),
    threads: records(journal.threads)
      .map((thread) => ({ id: stringValue(thread.id), title: stringValue(thread.title), text: stringValue(thread.text) }))
      .filter((thread) => thread.text),
    yourWord: records(journal.yourWord)
      .map((promise) => ({
        id: stringValue(promise.id),
        text: stringValue(promise.text),
        to: stringValue(promise.to),
        status: stringValue(promise.status),
      }))
      .filter((promise) => promise.text),
  }
}
