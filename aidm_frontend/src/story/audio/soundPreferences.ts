import { useSyncExternalStore } from 'react'

/**
 * The Story view's generated sound: ambience beds and effect cues, each with
 * its own switch and volume, kept in this browser like the music choices.
 */

export type StorySoundPreferences = {
  ambienceEnabled: boolean
  /** 0–1. */
  ambienceVolume: number
  effectsEnabled: boolean
  /** 0–1. */
  effectsVolume: number
}

export const SOUND_PREFERENCES_STORAGE_KEY = 'aidm:storySoundPreferences'

// Off until the player turns them on in Settings → Sound.
const DEFAULTS: StorySoundPreferences = {
  ambienceEnabled: false,
  ambienceVolume: 0.35,
  effectsEnabled: false,
  effectsVolume: 0.5,
}

// Used when the browser refuses storage, so a choice still holds for this page.
let memoryValue: string | null = null
const listeners = new Set<() => void>()
let snapshot: StorySoundPreferences | null = null

function readStored(): string | null {
  try {
    return localStorage.getItem(SOUND_PREFERENCES_STORAGE_KEY)
  } catch {
    return memoryValue
  }
}

function readRecord(): Record<string, unknown> {
  try {
    const parsed: unknown = JSON.parse(readStored() ?? 'null')
    return parsed && typeof parsed === 'object' && !Array.isArray(parsed) ? (parsed as Record<string, unknown>) : {}
  } catch {
    return {}
  }
}

function volume(value: unknown, fallback: number) {
  return typeof value === 'number' && Number.isFinite(value) ? Math.min(1, Math.max(0, value)) : fallback
}

function readPreferences(): StorySoundPreferences {
  const stored = readRecord()
  return {
    ambienceEnabled: typeof stored.ambienceEnabled === 'boolean' ? stored.ambienceEnabled : DEFAULTS.ambienceEnabled,
    ambienceVolume: volume(stored.ambienceVolume, DEFAULTS.ambienceVolume),
    effectsEnabled: typeof stored.effectsEnabled === 'boolean' ? stored.effectsEnabled : DEFAULTS.effectsEnabled,
    effectsVolume: volume(stored.effectsVolume, DEFAULTS.effectsVolume),
  }
}

/** The current choices; the same object until one of them changes. */
export function getStorySoundPreferences(): StorySoundPreferences {
  const next = readPreferences()
  const same =
    snapshot &&
    (Object.keys(next) as Array<keyof StorySoundPreferences>).every((key) => snapshot?.[key] === next[key])
  if (!same) snapshot = next
  return snapshot as StorySoundPreferences
}

function subscribe(listener: () => void) {
  listeners.add(listener)
  const onStorage = (event: StorageEvent) => {
    if (event.key === null || event.key === SOUND_PREFERENCES_STORAGE_KEY) listener()
  }
  window.addEventListener('storage', onStorage)
  return () => {
    listeners.delete(listener)
    window.removeEventListener('storage', onStorage)
  }
}

/** Live choices: re-renders when this page or another tab changes one. */
export function useStorySoundPreferences() {
  return useSyncExternalStore(subscribe, getStorySoundPreferences, getStorySoundPreferences)
}

function write(patch: Partial<StorySoundPreferences>) {
  const value = JSON.stringify({ ...readPreferences(), ...patch })
  try {
    localStorage.setItem(SOUND_PREFERENCES_STORAGE_KEY, value)
  } catch {
    memoryValue = value
  }
  listeners.forEach((listener) => listener())
}

export function setAmbienceEnabled(ambienceEnabled: boolean) {
  write({ ambienceEnabled })
}

export function setAmbienceVolume(ambienceVolume: number) {
  write({ ambienceVolume: volume(ambienceVolume, DEFAULTS.ambienceVolume) })
}

export function setEffectsEnabled(effectsEnabled: boolean) {
  write({ effectsEnabled })
}

export function setEffectsVolume(effectsVolume: number) {
  write({ effectsVolume: volume(effectsVolume, DEFAULTS.effectsVolume) })
}
