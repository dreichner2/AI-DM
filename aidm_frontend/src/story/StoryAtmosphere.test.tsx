// @vitest-environment jsdom
import '@testing-library/jest-dom/vitest'
import { act, cleanup, render } from '@testing-library/react'
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
})
