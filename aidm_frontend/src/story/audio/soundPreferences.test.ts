// @vitest-environment jsdom
import { act, renderHook } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import {
  getStorySoundPreferences,
  setAmbienceEnabled,
  setAmbienceVolume,
  setEffectsEnabled,
  setEffectsVolume,
  SOUND_PREFERENCES_STORAGE_KEY,
  useStorySoundPreferences,
} from './soundPreferences'

describe('story sound preferences', () => {
  afterEach(() => {
    localStorage.clear()
    vi.restoreAllMocks()
  })

  it('starts with ambience and effects off, at 35 % and 50 % once turned on', () => {
    expect(getStorySoundPreferences()).toEqual({
      ambienceEnabled: false,
      ambienceVolume: 0.35,
      effectsEnabled: false,
      effectsVolume: 0.5,
    })
  })

  it('keeps each choice in this browser and tells the views that read them', () => {
    const { result } = renderHook(() => useStorySoundPreferences())
    act(() => {
      setAmbienceEnabled(true)
      setAmbienceVolume(0.6)
      setEffectsEnabled(true)
      setEffectsVolume(1.4)
    })
    const expected = { ambienceEnabled: true, ambienceVolume: 0.6, effectsEnabled: true, effectsVolume: 1 }
    expect(result.current).toEqual(expected)
    expect(JSON.parse(localStorage.getItem(SOUND_PREFERENCES_STORAGE_KEY) ?? '{}')).toEqual(expected)
  })

  it('follows a change made in another tab and ignores a stored value it cannot read', () => {
    const { result } = renderHook(() => useStorySoundPreferences())
    act(() => {
      localStorage.setItem(SOUND_PREFERENCES_STORAGE_KEY, JSON.stringify({ ambienceVolume: 0.1, effectsEnabled: 'yes' }))
      window.dispatchEvent(new StorageEvent('storage', { key: SOUND_PREFERENCES_STORAGE_KEY }))
    })
    expect(result.current).toEqual({ ambienceEnabled: false, ambienceVolume: 0.1, effectsEnabled: false, effectsVolume: 0.5 })
  })

  it('still holds a choice for this page when the browser refuses storage', () => {
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new Error('denied')
    })
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
      throw new Error('denied')
    })
    setEffectsVolume(0.25)
    expect(getStorySoundPreferences().effectsVolume).toBe(0.25)
  })
})
