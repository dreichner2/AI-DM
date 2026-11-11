// @vitest-environment jsdom
import { act, renderHook } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import {
  getStoryPreferences,
  setAutoEndTurn,
  setMusicEnabled,
  setMusicVolume,
  setStoryReduceMotion,
  setStoryTextSize,
  useStoryPreferences,
} from './storyPreferences'

beforeEach(() => localStorage.clear())
afterEach(() => vi.unstubAllGlobals())

describe('storyPreferences', () => {
  it('reads the defaults, and ignores values it does not know', () => {
    localStorage.setItem('aidm:chatTextSettings', JSON.stringify({ size: 'huge', font: 'comic' }))
    localStorage.setItem('aidm:sceneMusicPreferences', '{not json')

    expect(getStoryPreferences()).toEqual({
      textSize: 'default',
      typeface: 'default',
      reduceMotion: false,
      musicEnabled: true,
      musicVolume: 0.7,
      autoEndTurn: true,
    })
  })

  it('ends the turn for the player by default, until they turn it off', () => {
    expect(getStoryPreferences().autoEndTurn).toBe(true)
    setAutoEndTurn(false)
    expect(localStorage.getItem('aidm:autoEndTurn')).toBe('false')
    expect(getStoryPreferences().autoEndTurn).toBe(false)
    setAutoEndTurn(true)
    expect(getStoryPreferences().autoEndTurn).toBe(true)
  })

  it('keeps the same snapshot until a value changes', () => {
    const first = getStoryPreferences()
    expect(getStoryPreferences()).toBe(first)
    setStoryTextSize('extra')
    expect(getStoryPreferences()).not.toBe(first)
    expect(getStoryPreferences().textSize).toBe('extra')
  })

  it('clamps the music volume and keeps the player’s other music choices', () => {
    localStorage.setItem('aidm:sceneMusicPreferences', JSON.stringify({ trackId: 'inn', volume: 0.4 }))
    setMusicVolume(1.6)
    expect(JSON.parse(localStorage.getItem('aidm:sceneMusicPreferences') ?? '{}')).toEqual({ trackId: 'inn', volume: 1 })
  })

  it('re-renders subscribers when a choice changes here or in another tab', () => {
    const { result } = renderHook(() => useStoryPreferences())
    expect(result.current.musicEnabled).toBe(true)

    act(() => setMusicEnabled(false))
    expect(result.current.musicEnabled).toBe(false)

    act(() => {
      localStorage.setItem('aidm:storyReduceMotion', 'true')
      window.dispatchEvent(new StorageEvent('storage', { key: 'aidm:storyReduceMotion' }))
    })
    expect(result.current.reduceMotion).toBe(true)

    act(() => setStoryReduceMotion(false))
    expect(result.current.reduceMotion).toBe(false)
  })
})
