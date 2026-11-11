import { isRecord, stringValue } from '../gameSelectors'

/**
 * A hero's own threads (the character sheet's `ties`): what they want, who or
 * what they would not abandon, and what they carry. Every part is optional;
 * the adventure tells them back through its tie lines. A listed desire is sent
 * by its kind alone; the server gives it its words.
 */
export type HeroTies = {
  desire?: { kind: string; words?: string }
  attachment?: { name: string; relation: string }
  burden?: string
}

/** A desire in the player's own words. */
export const OWN_DESIRE = 'own'
export const DEFAULT_RELATION = 'friend'
export const TIE_NAME_LENGTH = { min: 2, max: 40 }
export const OWN_DESIRE_MAX = 80
export const BURDEN_MAX = 120

/** The ties a character sheet holds, as the server stored them. */
export function heroTiesFromSheet(sheet: unknown): HeroTies {
  const ties = isRecord(sheet) && isRecord(sheet.ties) ? sheet.ties : null
  if (!ties) return {}
  const desire = isRecord(ties.desire) ? ties.desire : null
  const attachment = isRecord(ties.attachment) ? ties.attachment : null
  return {
    ...(desire && stringValue(desire.words) ? { desire: { kind: stringValue(desire.kind), words: stringValue(desire.words) } } : {}),
    ...(attachment && stringValue(attachment.name)
      ? { attachment: { name: stringValue(attachment.name), relation: stringValue(attachment.relation, DEFAULT_RELATION) } }
      : {}),
    ...(stringValue(ties.burden) ? { burden: stringValue(ties.burden) } : {}),
  }
}

/** The ties while they are being written: every field as typed. */
export type HeroTiesDraft = {
  desireKind: string
  ownDesire: string
  attachmentName: string
  relation: string
  burden: string
}

export function tiesDraft(ties: HeroTies = {}): HeroTiesDraft {
  return {
    desireKind: ties.desire?.kind ?? '',
    ownDesire: ties.desire?.kind === OWN_DESIRE ? (ties.desire.words ?? '') : '',
    attachmentName: ties.attachment?.name ?? '',
    relation: ties.attachment?.relation ?? DEFAULT_RELATION,
    burden: ties.burden ?? '',
  }
}

/** Why the ties cannot be saved as written; '' when they can (every field may be left empty). */
export function tiesProblem(draft: HeroTiesDraft) {
  const name = draft.attachmentName.trim()
  if (name && (name.length < TIE_NAME_LENGTH.min || name.length > TIE_NAME_LENGTH.max)) {
    return `A name of ${TIE_NAME_LENGTH.min} to ${TIE_NAME_LENGTH.max} letters, or none.`
  }
  if (draft.desireKind === OWN_DESIRE && draft.ownDesire.trim().length > OWN_DESIRE_MAX) {
    return `What you want: at most ${OWN_DESIRE_MAX} characters.`
  }
  if (draft.burden.trim().length > BURDEN_MAX) return `What you carry: at most ${BURDEN_MAX} characters.`
  return ''
}

/** What the server is sent: only the parts the player filled in. */
export function tiesPayload(draft: HeroTiesDraft): HeroTies {
  const ties: HeroTies = {}
  if (draft.desireKind === OWN_DESIRE) {
    if (draft.ownDesire.trim()) ties.desire = { kind: OWN_DESIRE, words: draft.ownDesire.trim() }
  } else if (draft.desireKind) {
    ties.desire = { kind: draft.desireKind }
  }
  if (draft.attachmentName.trim()) ties.attachment = { name: draft.attachmentName.trim(), relation: draft.relation || DEFAULT_RELATION }
  if (draft.burden.trim()) ties.burden = draft.burden.trim()
  return ties
}
