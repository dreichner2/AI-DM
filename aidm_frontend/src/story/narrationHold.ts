import { useEffect, useMemo, useState } from 'react'
import { numberValue } from '../gameSelectors'
import { cardDuration, PLAY_LIMIT_MS, rollPlayback } from './rollPlayback'
import type { StoryBeat } from './storyBeats'

/**
 * When each turn whose roll cards arrived live may tell its story: once its last
 * card has landed, or 2.5 s after its first card appeared, whichever is sooner.
 * Cards read from history have no playback and hold nothing.
 */
function narrationHolds(beats: StoryBeat[]) {
  const spans = new Map<number, { first: number; last: number }>()
  for (const beat of beats) {
    if (beat.kind !== 'rollCard') continue
    const playback = rollPlayback(beat.card.id)
    if (!playback) continue
    const landed = playback.instant ? playback.startAt : playback.startAt + cardDuration(beat.card)
    const span = spans.get(beat.turnId)
    spans.set(beat.turnId, {
      first: Math.min(span?.first ?? playback.startAt, playback.startAt),
      last: Math.max(span?.last ?? landed, landed),
    })
  }
  return new Map([...spans].map(([turnId, span]) => [turnId, Math.min(span.last, span.first + PLAY_LIMIT_MS)]))
}

function beatTurnId(beat: StoryBeat) {
  if (beat.kind === 'narration') return beat.turnId
  if (beat.kind === 'turnBeats') return numberValue(beat.entry.metadata.turn_id)
  return null
}

/**
 * The beats as they may be read now: a turn's narration and its beats wait while
 * its roll cards play, so the dice land before the story tells the outcome. A
 * narration still streaming shows from the moment its hold ends. With reduced
 * motion nothing waits.
 */
export function useNarrationHold(beats: StoryBeat[], reduceMotion: boolean): StoryBeat[] {
  const [now, setNow] = useState(() => Date.now())
  const holds = useMemo(() => (reduceMotion ? new Map<number, number>() : narrationHolds(beats)), [beats, reduceMotion])
  const held = useMemo(
    () => new Set([...holds].filter(([, until]) => until > now).map(([turnId]) => turnId)),
    [holds, now],
  )
  const nextRelease = Math.min(...[...holds.values()].filter((until) => until > now))

  useEffect(() => {
    if (!Number.isFinite(nextRelease)) return undefined
    const timer = window.setTimeout(() => setNow(Date.now()), Math.max(0, nextRelease - Date.now()))
    return () => window.clearTimeout(timer)
  }, [nextRelease])

  return useMemo(() => {
    if (!held.size) return beats
    return beats.filter((beat) => {
      const turnId = beatTurnId(beat)
      return turnId === null || !held.has(turnId)
    })
  }, [beats, held])
}
