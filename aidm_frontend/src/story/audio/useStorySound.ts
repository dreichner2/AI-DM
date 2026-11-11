import { useEffect, useMemo, useRef, useState } from 'react'
import { numberValue } from '../../gameSelectors'
import type { SessionBoardProps } from '../../SessionBoard'
import { entryTurnBeats } from '../storyBeats'
import { useStorySoundPreferences } from './soundPreferences'
import type { StoryAudio } from './storyAmbience'
import { chooseAmbience, storyPlace, type StoryPlace } from './storyPlace'
import { playCue, setStoryAudio, type StoryCue } from './storySound'

type StorySoundBoard = Pick<
  SessionBoardProps,
  'sessionId' | 'sessionState' | 'turnRows' | 'currentResponseEntry' | 'worldState' | 'duckMusicForNarration'
>

type StorySoundState = {
  /** The party is fighting: every place hushes so the cues read. */
  inFight: boolean
  /** The viewer's own hero is dying: a heartbeat plays until they are not. */
  viewerDying: boolean
}

/** The cue for a server story beat, the first that applies when a turn brings several. */
const BEAT_CUES: ReadonlyArray<[string, StoryCue]> = [
  ['defeat', 'defeat'],
  ['combat_end', 'victory'],
  ['level', 'level_up'],
  ['clue', 'clue'],
]

const GESTURES = ['pointerdown', 'keydown', 'touchend'] as const

/**
 * Starts the generated sound on the player's first gesture (browsers allow
 * nothing before one) when ambience or effects are on; the engine's code is
 * fetched only then. Null until it runs, and for good when it cannot.
 */
function useStoryAudio(wanted: boolean) {
  const [audio, setAudio] = useState<{ engine: StoryAudio; context: AudioContext } | null>(null)

  useEffect(() => {
    if (!wanted || audio) return
    let cancelled = false
    const stopListening = () => GESTURES.forEach((type) => window.removeEventListener(type, begin, true))
    function begin() {
      stopListening()
      let context: AudioContext
      try {
        // Created and resumed inside the gesture, which is what lets it play.
        context = new AudioContext()
        context.resume().catch(() => undefined)
      } catch {
        return
      }
      const close = () => context.close().catch(() => undefined)
      import('./storyAmbience')
        .then(async ({ createStoryAudio }) => {
          const engine = createStoryAudio(context)
          if (!cancelled && (await engine.start()) && !cancelled) {
            setAudio({ engine, context })
            return
          }
          engine.stop()
          await close()
        })
        .catch(close)
    }
    GESTURES.forEach((type) => window.addEventListener(type, begin, true))
    return () => {
      cancelled = true
      stopListening()
    }
  }, [wanted, audio])

  useEffect(() => {
    if (!audio) return
    setStoryAudio(audio.engine)
    return () => {
      setStoryAudio(null)
      audio.engine.stop()
      audio.context.close().catch(() => undefined)
    }
  }, [audio])

  return audio?.engine ?? null
}

/** `fight_start` when a fight begins in the session the viewer is watching; never for a fight already on when it loads. */
function useFightStartCue(sessionId: number | null, inFight: boolean) {
  const seen = useRef<{ sessionId: number | null; inFight: boolean } | null>(null)
  useEffect(() => {
    const previous = seen.current
    seen.current = { sessionId, inFight }
    if (sessionId !== null && previous?.sessionId === sessionId && inFight && !previous.inFight) playCue('fight_start')
  }, [sessionId, inFight])
}

type BeatMemory = { sessionId: number | null; historyTurn: number | null; seen: Set<string> }

function turnIdOf(value: unknown) {
  const turnId = numberValue(value)
  return turnId !== null && Number.isInteger(turnId) && turnId > 0 ? turnId : null
}

/**
 * The cue for each turn's new story beats. The turns already there when the
 * session is first seen are its history and never cue, nor do older pages
 * loaded later; each beat cues once.
 */
function useBeatCues({ sessionId, turnRows, currentResponseEntry }: StorySoundBoard) {
  const memory = useRef<BeatMemory>({ sessionId: null, historyTurn: null, seen: new Set() })
  useEffect(() => {
    if (memory.current.sessionId !== sessionId) memory.current = { sessionId, historyTurn: null, seen: new Set() }
    const known = memory.current
    const entries = currentResponseEntry ? [...turnRows, currentResponseEntry] : turnRows
    const turnIds = entries.map((entry) => turnIdOf(entry.metadata.turn_id)).filter((turnId) => turnId !== null)
    const beats = entries.flatMap((entry) => {
      const turnId = turnIdOf(entry.metadata.turn_id)
      if (turnId === null) return []
      return (entryTurnBeats(entry) ?? []).map((beat) => ({ turnId, kind: beat.kind, key: `${turnId}|${beat.kind}|${beat.text}` }))
    })
    const fresh = beats.filter((beat) => !known.seen.has(beat.key))
    fresh.forEach((beat) => known.seen.add(beat.key))
    if (known.historyTurn === null) {
      if (turnIds.length) known.historyTurn = Math.max(...turnIds)
      return
    }
    const historyTurn = known.historyTurn
    const kinds = new Set(fresh.filter((beat) => beat.turnId >= historyTurn).map((beat) => beat.kind))
    const cue = BEAT_CUES.find(([kind]) => kinds.has(kind))?.[1]
    if (cue) playCue(cue)
  }, [sessionId, turnRows, currentResponseEntry])
}

/**
 * The Story view's generated sound: the place's ambience bed (ducked while the
 * narrator speaks), the heartbeat while the viewer's hero is dying, and cues
 * for a fight starting and for each turn's victory, defeat, level or clue.
 * Returns the party's place, whose weather the view draws.
 */
export function useStorySound(board: StorySoundBoard, { inFight, viewerDying }: StorySoundState): StoryPlace {
  const preferences = useStorySoundPreferences()
  const snapshot = board.sessionState?.state_snapshot
  const place = useMemo(() => storyPlace(snapshot), [snapshot])
  const audio = useStoryAudio(preferences.ambienceEnabled || preferences.effectsEnabled)
  const { bed, rain } = chooseAmbience(place, board.worldState.clock?.phase, inFight)
  const { ambienceEnabled, ambienceVolume, effectsEnabled, effectsVolume } = preferences

  useEffect(() => {
    audio?.setLevels({ ambience: ambienceEnabled ? ambienceVolume : 0, effects: effectsEnabled ? effectsVolume : 0 })
  }, [audio, ambienceEnabled, ambienceVolume, effectsEnabled, effectsVolume])

  useEffect(() => {
    audio?.setScene(ambienceEnabled ? { bed, rain } : null)
  }, [audio, ambienceEnabled, bed, rain])

  useEffect(() => {
    audio?.setDucked(board.duckMusicForNarration)
  }, [audio, board.duckMusicForNarration])

  useEffect(() => {
    audio?.setHeartbeat(viewerDying)
  }, [audio, viewerDying])

  useFightStartCue(board.sessionState?.session_id ?? null, inFight)
  useBeatCues(board)
  return place
}
