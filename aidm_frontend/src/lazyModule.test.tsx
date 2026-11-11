// @vitest-environment jsdom
import '@testing-library/jest-dom/vitest'
import { Suspense, type ComponentType } from 'react'
import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { lazyModule, whenIdle } from './lazyModule'

function Panel({ label }: { label: string }) {
  return <p>{label}</p>
}

function renderLazy(Component: ComponentType<{ label: string }>) {
  return render(
    <Suspense fallback={<p>Waiting</p>}>
      <Component label="Panel ready" />
    </Suspense>,
  )
}

afterEach(() => {
  cleanup()
  vi.useRealTimers()
  vi.unstubAllGlobals()
})

describe('lazyModule', () => {
  it('fetches the code once, however often it is asked for ahead, and then renders at once', async () => {
    const load = vi.fn(async () => ({ Panel }))
    const chunk = lazyModule(load)
    const LazyPanel = chunk.component((module) => module.Panel)

    chunk.preload()
    chunk.preload()
    expect(load).toHaveBeenCalledOnce()
    await vi.waitFor(() => expect(chunk.isLoaded()).toBe(true))

    renderLazy(LazyPanel)
    expect(screen.getByText('Panel ready')).toBeInTheDocument()
    expect(screen.queryByText('Waiting')).not.toBeInTheDocument()
    expect(load).toHaveBeenCalledOnce()
  })

  it('fetches the code on the first render when nobody reached for it first', async () => {
    const load = vi.fn(async () => ({ Panel }))
    const chunk = lazyModule(load)

    renderLazy(chunk.component((module) => module.Panel))
    expect(screen.getByText('Waiting')).toBeInTheDocument()
    expect(await screen.findByText('Panel ready')).toBeInTheDocument()
    expect(load).toHaveBeenCalledOnce()
  })

  it('tries again after a fetch that failed', async () => {
    const load = vi.fn<() => Promise<{ Panel: typeof Panel }>>()
      .mockRejectedValueOnce(new TypeError('Failed to fetch'))
      .mockResolvedValue({ Panel })
    const chunk = lazyModule(load)

    chunk.preload()
    await vi.waitFor(() => expect(load).toHaveBeenCalledOnce())
    await Promise.resolve()
    expect(chunk.isLoaded()).toBe(false)

    chunk.preload()
    await vi.waitFor(() => expect(chunk.isLoaded()).toBe(true))
    expect(load).toHaveBeenCalledTimes(2)
  })
})

describe('whenIdle', () => {
  it('waits for the browser to be idle, and can be called off', () => {
    const callbacks: Array<() => void> = []
    const cancelIdleCallback = vi.fn()
    vi.stubGlobal('requestIdleCallback', vi.fn((callback: () => void) => callbacks.push(callback)))
    vi.stubGlobal('cancelIdleCallback', cancelIdleCallback)
    const task = vi.fn()

    const cancel = whenIdle(task)
    expect(task).not.toHaveBeenCalled()
    callbacks[0]()
    expect(task).toHaveBeenCalledOnce()
    cancel()
    expect(cancelIdleCallback).toHaveBeenCalledWith(1)
  })

  it('waits a second where the browser cannot say when it is idle', () => {
    vi.useFakeTimers()
    vi.stubGlobal('requestIdleCallback', undefined)
    const task = vi.fn()

    whenIdle(task)
    vi.advanceTimersByTime(999)
    expect(task).not.toHaveBeenCalled()
    vi.advanceTimersByTime(1)
    expect(task).toHaveBeenCalledOnce()

    const called = vi.fn()
    whenIdle(called)()
    vi.advanceTimersByTime(1_000)
    expect(called).not.toHaveBeenCalled()
  })
})
