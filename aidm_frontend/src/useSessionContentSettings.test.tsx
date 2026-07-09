// @vitest-environment jsdom
import { act, cleanup, renderHook } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { ContentSettings } from './contentSettings'
import { useSessionContentSettings } from './useSessionContentSettings'

const apiFetchMock = vi.hoisted(() => vi.fn())

vi.mock('./api', async () => {
  const actual = await vi.importActual<typeof import('./api')>('./api')
  return { ...actual, apiFetch: apiFetchMock }
})

function deferred<T>() {
  let resolve!: (value: T) => void
  const promise = new Promise<T>((resolvePromise) => {
    resolve = resolvePromise
  })
  return { promise, resolve }
}

const defaultContentSettings: ContentSettings = {
  contentRating: 'standard',
  toneTags: ['heroic'],
  updatedAt: null,
}

function createOptions(overrides: Record<string, unknown> = {}) {
  return {
    activeSessionId: 7,
    auth: 'token',
    baseUrl: 'https://backend.example.test',
    canEditContentSettings: true,
    contentSettings: defaultContentSettings,
    clearAuthTokenErrors: vi.fn(),
    pushError: vi.fn(),
    sessionUpserted: vi.fn(),
    setSessionState: vi.fn(),
    ...overrides,
  } as unknown as Parameters<typeof useSessionContentSettings>[0]
}

function updateResponse(sessionId = 7) {
  return {
    session_id: sessionId,
    settings: {
      content_rating: 'mature',
      contentRating: 'mature',
      tone_tags: ['grim'],
      toneTags: ['grim'],
      updated_at: null,
      updatedAt: null,
      ratings: ['standard', 'mature', 'unrestricted'],
      available_tone_tags: ['grim'],
      availableToneTags: ['grim'],
    },
    session: { session_id: sessionId },
    state: { session_id: sessionId },
  }
}

describe('useSessionContentSettings', () => {
  beforeEach(() => apiFetchMock.mockReset())
  afterEach(() => cleanup())

  it('persists a rating through the generated update contract and blocks duplicate writes', async () => {
    const request = deferred<ReturnType<typeof updateResponse>>()
    apiFetchMock.mockReturnValue(request.promise)
    const options = createOptions()
    const { result } = renderHook(() => useSessionContentSettings(options))

    let firstUpdate!: Promise<void>
    act(() => {
      firstUpdate = result.current.updateContentRating('mature')
      void result.current.updateContentRating('unrestricted')
    })

    expect(result.current.contentSettingsPending).toBe(true)
    expect(apiFetchMock).toHaveBeenCalledOnce()
    expect(apiFetchMock).toHaveBeenCalledWith(
      'https://backend.example.test',
      '/api/sessions/7/content-settings',
      'token',
      {
        method: 'PATCH',
        body: JSON.stringify({ content_rating: 'mature' }),
      },
    )

    request.resolve(updateResponse())
    await act(async () => firstUpdate)

    expect(options.sessionUpserted).toHaveBeenCalledWith({ session_id: 7 })
    expect(options.setSessionState).toHaveBeenCalledWith({ session_id: 7 })
    expect(options.clearAuthTokenErrors).toHaveBeenCalledOnce()
    expect(result.current.contentSettingsPending).toBe(false)
  })

  it('normalizes, de-duplicates, and caps tone tags before persisting them', async () => {
    apiFetchMock.mockResolvedValue(updateResponse())
    const options = createOptions()
    const { result } = renderHook(() => useSessionContentSettings(options))

    await act(async () => {
      await result.current.updateContentToneTags([
        ' Grim ',
        'grim',
        'Hopeful',
        '',
        'Noir',
        'Mystery',
        'Tragic',
      ])
    })

    expect(apiFetchMock).toHaveBeenCalledWith(
      'https://backend.example.test',
      '/api/sessions/7/content-settings',
      'token',
      {
        method: 'PATCH',
        body: JSON.stringify({ tone_tags: ['grim', 'hopeful', 'noir', 'mystery'] }),
      },
    )
  })

  it('skips writes when normalized settings are unchanged', async () => {
    const options = createOptions()
    const { result } = renderHook(() => useSessionContentSettings(options))

    await act(async () => {
      await result.current.updateContentRating('standard')
      await result.current.updateContentToneTags([' Heroic ', 'heroic'])
    })

    expect(apiFetchMock).not.toHaveBeenCalled()
  })

  it('validates session and operator access without making a request', async () => {
    const missingSession = createOptions({ activeSessionId: null })
    const { result, rerender } = renderHook(
      ({ options }) => useSessionContentSettings(options),
      { initialProps: { options: missingSession } },
    )

    await act(async () => result.current.updateContentRating('mature'))
    expect(missingSession.pushError).toHaveBeenCalledWith(
      'validation',
      'Choose a session before changing content rating.',
    )

    const playerOptions = createOptions({ canEditContentSettings: false })
    rerender({ options: playerOptions })
    await act(async () => result.current.updateContentToneTags(['grim']))
    expect(playerOptions.pushError).toHaveBeenCalledWith(
      'validation',
      'Only table operators can change tone tags.',
    )
    expect(apiFetchMock).not.toHaveBeenCalled()
  })

  it('does not apply a response after the selected session changes', async () => {
    const request = deferred<ReturnType<typeof updateResponse>>()
    apiFetchMock.mockReturnValue(request.promise)
    const firstOptions = createOptions({ activeSessionId: 7 })
    const secondOptions = createOptions({ activeSessionId: 8 })
    const { result, rerender } = renderHook(
      ({ options }) => useSessionContentSettings(options),
      { initialProps: { options: firstOptions } },
    )

    let update!: Promise<void>
    act(() => {
      update = result.current.updateContentRating('mature')
    })
    rerender({ options: secondOptions })
    expect(result.current.contentSettingsPending).toBe(false)

    request.resolve(updateResponse(7))
    await act(async () => update)

    expect(firstOptions.sessionUpserted).not.toHaveBeenCalled()
    expect(firstOptions.setSessionState).not.toHaveBeenCalled()
    expect(secondOptions.sessionUpserted).not.toHaveBeenCalled()
    expect(secondOptions.setSessionState).not.toHaveBeenCalled()
  })
})
