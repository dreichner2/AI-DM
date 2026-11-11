import type { RollCardRecord } from './storyRolls'

/**
 * When each roll card that arrived live plays. A card read from history is
 * never here and shows landed. The cards of one arrival play one after
 * another, 200 ms apart; a card that would start 2.5 s or more after they
 * arrived appears landed at that moment instead.
 */

/** The d20 number cycles before it lands. */
export const D20_CYCLE_MS = 700
/** Then the modifier and total settle. */
export const SETTLE_MS = 150
/** Then each damage or healing die cycles before it lands. */
export const DICE_CYCLE_MS = 450
export const CARD_GAP_MS = 200
export const PLAY_LIMIT_MS = 2500
/** A roll the player pressed lands no sooner than this after the press. */
export const PRESS_MIN_MS = 500
/** Landed, the pressed roll's card stays where it was pressed this long before the story shows it. */
export const PRESSED_HOLD_MS = 600

export type RollPlayback = {
  /** `Date.now()` time the card starts; before it the card is held back. */
  startAt: number
  /** Started this late, the card appears landed without playing. */
  instant: boolean
}

const playback = new Map<string, RollPlayback>()
/** Turn id → when the card of the roll the viewer pressed has landed in the roll prompt. */
const pressed = new Map<number, number>()
const PRESSED_KINDS = new Set(['check', 'save', 'attack'])

/** How long a card plays, from the first flicker to its last die landing. */
export function cardDuration(record: RollCardRecord) {
  if (record.kind === 'damage' || record.kind === 'healing') return DICE_CYCLE_MS
  const dice = record.damage || record.healing ? DICE_CYCLE_MS : 0
  return D20_CYCLE_MS + SETTLE_MS + dice
}

/** The roll the viewer pressed in turn `turnId` plays in the prompt and hands over to the story at `doneAt`. */
export function notePressedRoll(turnId: number, doneAt: number) {
  pressed.set(turnId, doneAt)
}

/**
 * Schedule the cards of turn `turnId` that arrived live now, in order; cards
 * already scheduled keep their time. The card of a roll the viewer pressed has
 * played in the prompt: it appears landed when that has, and the turn's other
 * cards play after it.
 */
export function playRolls(turnId: number, records: RollCardRecord[], at = Date.now()) {
  const pressedDoneAt = pressed.get(turnId)
  const pressedRecord = pressedDoneAt === undefined
    ? undefined
    : records.find((record) => record.actor?.side === 'hero' && PRESSED_KINDS.has(record.kind))
  let offset = 0
  for (const record of records) {
    if (playback.has(record.id)) continue
    if (record === pressedRecord && pressedDoneAt !== undefined) {
      playback.set(record.id, { startAt: pressedDoneAt, instant: true })
      offset = Math.max(offset, pressedDoneAt - at + CARD_GAP_MS)
      continue
    }
    playback.set(record.id, offset >= PLAY_LIMIT_MS ? { startAt: at + PLAY_LIMIT_MS, instant: true } : { startAt: at + offset, instant: false })
    offset += cardDuration(record) + CARD_GAP_MS
  }
}

export function rollPlayback(id: string): RollPlayback | null {
  return playback.get(id) ?? null
}

/** Forget every scheduled card; tests start from a clean page. */
export function resetRollPlayback() {
  playback.clear()
  pressed.clear()
}
