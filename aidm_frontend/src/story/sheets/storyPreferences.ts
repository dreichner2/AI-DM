import { useSyncExternalStore } from 'react'
import { subscribeToMediaQueryChange } from '../../mediaQuery'

/**
 * The player's own presentation choices, shared by the Settings sheet and any
 * view that honours them. Each lives in the browser key it already used, so
 * the classic console and the Story view read the same values.
 */

export type StoryTextSize = 'default' | 'large' | 'extra'
/** `default` is the Story serif; `mono` is only offered by the classic console. */
export type StoryTypeface = 'default' | 'sans' | 'mono'

export type StoryPreferences = {
  textSize: StoryTextSize
  typeface: StoryTypeface
  reduceMotion: boolean
  musicEnabled: boolean
  /** 0–1. */
  musicVolume: number
  /** End my turn for me: a fight action that leaves only moving ends the turn (sent with each action). */
  autoEndTurn: boolean
}

export const TEXT_SETTINGS_STORAGE_KEY = 'aidm:chatTextSettings'
export const REDUCE_MOTION_STORAGE_KEY = 'aidm:storyReduceMotion'
export const MUSIC_PREFERENCES_STORAGE_KEY = 'aidm:sceneMusicPreferences'
export const MUSIC_ENABLED_STORAGE_KEY = 'aidm:sceneMusicEnabled'
export const AUTO_END_TURN_STORAGE_KEY = 'aidm:autoEndTurn'

const STORAGE_KEYS = [
  TEXT_SETTINGS_STORAGE_KEY,
  REDUCE_MOTION_STORAGE_KEY,
  MUSIC_PREFERENCES_STORAGE_KEY,
  MUSIC_ENABLED_STORAGE_KEY,
  AUTO_END_TURN_STORAGE_KEY,
]
const REDUCED_MOTION_QUERY = '(prefers-reduced-motion: reduce)'
const DEFAULT_MUSIC_VOLUME = 0.7

// Used when the browser refuses storage, so a choice still holds for this page.
const memoryStorage = new Map<string, string>()
const listeners = new Set<() => void>()
let snapshot: StoryPreferences | null = null

function readItem(key: string) {
  try {
    return localStorage.getItem(key)
  } catch {
    return memoryStorage.get(key) ?? null
  }
}

function writeItem(key: string, value: string) {
  try {
    localStorage.setItem(key, value)
  } catch {
    memoryStorage.set(key, value)
  }
}

function readRecord(key: string): Record<string, unknown> {
  try {
    const parsed: unknown = JSON.parse(readItem(key) ?? 'null')
    return parsed && typeof parsed === 'object' && !Array.isArray(parsed) ? (parsed as Record<string, unknown>) : {}
  } catch {
    return {}
  }
}

function isTextSize(value: unknown): value is StoryTextSize {
  return value === 'default' || value === 'large' || value === 'extra'
}

function isTypeface(value: unknown): value is StoryTypeface {
  return value === 'default' || value === 'sans' || value === 'mono'
}

function systemPrefersReducedMotion() {
  return typeof window.matchMedia === 'function' && window.matchMedia(REDUCED_MOTION_QUERY).matches
}

function clampVolume(value: unknown) {
  const volume = typeof value === 'number' ? value : Number.NaN
  return Number.isFinite(volume) ? Math.min(1, Math.max(0, volume)) : DEFAULT_MUSIC_VOLUME
}

function readPreferences(): StoryPreferences {
  const text = readRecord(TEXT_SETTINGS_STORAGE_KEY)
  const storedMotion = readItem(REDUCE_MOTION_STORAGE_KEY)
  return {
    textSize: isTextSize(text.size) ? text.size : 'default',
    typeface: isTypeface(text.font) ? text.font : 'default',
    reduceMotion: storedMotion === null ? systemPrefersReducedMotion() : storedMotion === 'true',
    musicEnabled: readItem(MUSIC_ENABLED_STORAGE_KEY) !== 'false',
    musicVolume: clampVolume(readRecord(MUSIC_PREFERENCES_STORAGE_KEY).volume),
    autoEndTurn: readItem(AUTO_END_TURN_STORAGE_KEY) !== 'false',
  }
}

function samePreferences(left: StoryPreferences, right: StoryPreferences) {
  return (
    left.textSize === right.textSize &&
    left.typeface === right.typeface &&
    left.reduceMotion === right.reduceMotion &&
    left.musicEnabled === right.musicEnabled &&
    left.musicVolume === right.musicVolume &&
    left.autoEndTurn === right.autoEndTurn
  )
}

/** The current choices; the same object until one of them changes. */
export function getStoryPreferences(): StoryPreferences {
  const next = readPreferences()
  if (!snapshot || !samePreferences(snapshot, next)) snapshot = next
  return snapshot
}

function notify() {
  listeners.forEach((listener) => listener())
}

function subscribe(listener: () => void) {
  listeners.add(listener)
  const onStorage = (event: StorageEvent) => {
    if (event.key === null || STORAGE_KEYS.includes(event.key)) listener()
  }
  window.addEventListener('storage', onStorage)
  const unsubscribeMedia =
    typeof window.matchMedia === 'function'
      ? subscribeToMediaQueryChange(window.matchMedia(REDUCED_MOTION_QUERY), listener)
      : () => undefined
  return () => {
    listeners.delete(listener)
    window.removeEventListener('storage', onStorage)
    unsubscribeMedia()
  }
}

/** Live choices: re-renders when this page, another tab or the system changes one. */
export function useStoryPreferences() {
  return useSyncExternalStore(subscribe, getStoryPreferences, getStoryPreferences)
}

function writeTextSettings(patch: { size?: StoryTextSize; font?: StoryTypeface }) {
  const current = readRecord(TEXT_SETTINGS_STORAGE_KEY)
  writeItem(TEXT_SETTINGS_STORAGE_KEY, JSON.stringify({ size: 'default', font: 'default', ...current, ...patch }))
  notify()
}

export function setStoryTextSize(size: StoryTextSize) {
  writeTextSettings({ size })
}

export function setStoryTypeface(font: StoryTypeface) {
  writeTextSettings({ font })
}

export function setStoryReduceMotion(reduceMotion: boolean) {
  writeItem(REDUCE_MOTION_STORAGE_KEY, String(reduceMotion))
  notify()
}

export function setMusicEnabled(enabled: boolean) {
  writeItem(MUSIC_ENABLED_STORAGE_KEY, String(enabled))
  notify()
}

export function setAutoEndTurn(autoEndTurn: boolean) {
  writeItem(AUTO_END_TURN_STORAGE_KEY, String(autoEndTurn))
  notify()
}

export function setMusicVolume(volume: number) {
  const current = readRecord(MUSIC_PREFERENCES_STORAGE_KEY)
  writeItem(MUSIC_PREFERENCES_STORAGE_KEY, JSON.stringify({ ...current, volume: clampVolume(volume) }))
  notify()
}
