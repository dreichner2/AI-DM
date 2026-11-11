// @vitest-environment jsdom
import '@testing-library/jest-dom/vitest'
import { act, cleanup, render, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { setStoryReduceMotion } from './sheets/storyPreferences'
import { StoryAtmosphere } from './StoryAtmosphere'

describe('StoryAtmosphere', () => {
  let requestFrame: ReturnType<typeof vi.fn>
  let cancelFrame: ReturnType<typeof vi.fn>
  let clearRect: ReturnType<typeof vi.fn>

  beforeEach(() => {
    vi.stubGlobal('matchMedia', (query: string) => ({
      matches: false,
      media: query,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
    }))
    requestFrame = vi.fn(() => 7)
    cancelFrame = vi.fn()
    vi.stubGlobal('requestAnimationFrame', requestFrame)
    vi.stubGlobal('cancelAnimationFrame', cancelFrame)
    clearRect = vi.fn()
    vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue({
      setTransform: vi.fn(),
      clearRect,
    } as unknown as CanvasRenderingContext2D)
  })

  afterEach(() => {
    cleanup()
    localStorage.clear()
    vi.restoreAllMocks()
    vi.unstubAllGlobals()
  })

  it('runs the ember loop until reduce motion is turned on, then holds still until it is off again', () => {
    const { container } = render(<StoryAtmosphere mood="hearth" viewerHp={null} />)
    expect(requestFrame).toHaveBeenCalledOnce()

    act(() => setStoryReduceMotion(true))
    expect(cancelFrame).toHaveBeenCalledWith(7)
    expect(clearRect).toHaveBeenCalled()
    expect(container.querySelector('.story-atmosphere')).toHaveAttribute('data-still')
    expect(requestFrame).toHaveBeenCalledOnce()

    act(() => setStoryReduceMotion(false))
    expect(requestFrame).toHaveBeenCalledTimes(2)
    expect(container.querySelector('.story-atmosphere')).not.toHaveAttribute('data-still')
  })

  it('never starts the loop when reduce motion is already on', () => {
    setStoryReduceMotion(true)
    render(<StoryAtmosphere mood="hearth" viewerHp={null} />)
    expect(requestFrame).not.toHaveBeenCalled()
  })

  it('tints the light through one variable for each phase of the day, and not at all without a clock', () => {
    const { container, rerender } = render(<StoryAtmosphere mood="calm" viewerHp={null} />)
    const atmosphere = () => container.querySelector<HTMLElement>('.story-atmosphere')
    expect(atmosphere()?.style.getPropertyValue('--story-daylight')).toBe('')
    for (const phase of ['morning', 'afternoon', 'evening', 'night']) {
      rerender(<StoryAtmosphere mood="calm" viewerHp={null} phase={phase} />)
      expect(atmosphere()?.style.getPropertyValue('--story-daylight')).toBe(`var(--story-daylight-${phase})`)
    }
    rerender(<StoryAtmosphere mood="calm" viewerHp={null} phase="dusk" />)
    expect(atmosphere()?.style.getPropertyValue('--story-daylight')).toBe('')
  })

  it('draws rain and snow on their own canvas, fog as two bands, and nothing for clear weather', async () => {
    const { container, rerender } = render(<StoryAtmosphere mood="wild" viewerHp={null} weather="rain" />)
    const weather = () => container.querySelectorAll('.story-weather')
    await waitFor(() => expect(weather()).toHaveLength(1))
    expect(weather()[0].tagName).toBe('CANVAS')
    expect(weather()[0]).toHaveAttribute('data-weather', 'rain')

    rerender(<StoryAtmosphere mood="wild" viewerHp={null} weather="snow" />)
    expect(weather()[0].tagName).toBe('CANVAS')
    expect(weather()[0]).toHaveAttribute('data-weather', 'snow')

    rerender(<StoryAtmosphere mood="wild" viewerHp={null} weather="fog" />)
    expect(weather()[0]).toHaveAttribute('data-weather', 'fog')
    expect(container.querySelectorAll('.story-fog-band')).toHaveLength(2)

    for (const clear of ['clear', null] as const) {
      rerender(<StoryAtmosphere mood="wild" viewerHp={null} weather={clear} />)
      expect(weather()).toHaveLength(0)
    }
  })

  it('stops drawing falling weather when it clears', async () => {
    const { rerender } = render(<StoryAtmosphere mood="wild" viewerHp={null} weather="rain" />)
    // The embers' loop, then the rain's.
    await waitFor(() => expect(requestFrame).toHaveBeenCalledTimes(2))
    rerender(<StoryAtmosphere mood="wild" viewerHp={null} weather="clear" />)
    expect(cancelFrame).toHaveBeenCalledOnce()
  })

  it('keeps the tint but draws no weather while reduce motion is on', async () => {
    setStoryReduceMotion(true)
    const { container } = render(<StoryAtmosphere mood="wild" viewerHp={null} weather="fog" phase="night" />)
    expect(container.querySelector('.story-weather')).toBeNull()
    expect(container.querySelector<HTMLElement>('.story-atmosphere')?.style.getPropertyValue('--story-daylight')).toBe(
      'var(--story-daylight-night)',
    )
    act(() => setStoryReduceMotion(false))
    await waitFor(() => expect(container.querySelectorAll('.story-fog-band')).toHaveLength(2))
  })
})
