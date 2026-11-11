import type { StoryAudio } from './storyAmbience'

/** The short generated cues (see storyAmbience for how each one sounds). */
export const STORY_CUES = [
  'fight_start',
  'victory',
  'defeat',
  'clue',
  'level_up',
  'nat20',
  'nat1',
  'dice_tick',
  'heartbeat',
] as const
export type StoryCue = (typeof STORY_CUES)[number]

let current: StoryAudio | null = null

/** The Story view's running sound, or null; set by useStorySound once the player's first gesture has started it. */
export function setStoryAudio(audio: StoryAudio | null) {
  current = audio
}

/**
 * Plays a cue when the Story view's sound is running and effects are on;
 * otherwise does nothing. Safe to call from anywhere, any number of times.
 */
export function playCue(cue: StoryCue) {
  current?.playCue(cue)
}
