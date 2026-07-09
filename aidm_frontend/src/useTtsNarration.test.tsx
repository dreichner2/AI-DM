// @vitest-environment jsdom
import { act, cleanup, renderHook, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi, type Mock } from 'vitest'
import type { StreamingTurn } from './types'
import { useTtsNarration } from './useTtsNarration'

type AudioInstance = {
  onended: (() => void) | null
  onerror: ((event: Event) => void) | null
  onpause: (() => void) | null
  pause: ReturnType<typeof vi.fn>
  play: ReturnType<typeof vi.fn>
  preload: string
  src: string
}

function createStorageMock(): Storage {
  const values = new Map<string, string>()
  return {
    get length() {
      return values.size
    },
    clear: vi.fn(() => values.clear()),
    getItem: vi.fn((key: string) => values.get(key) ?? null),
    key: vi.fn((index: number) => [...values.keys()][index] ?? null),
    removeItem: vi.fn((key: string) => values.delete(key)),
    setItem: vi.fn((key: string, value: string) => values.set(key, value)),
  }
}

function installAudioMock({ rejectPlay = false } = {}) {
  const instances: AudioInstance[] = []
  vi.stubGlobal(
    'Audio',
    vi.fn(function MockAudio(this: AudioInstance, src: string) {
      this.src = src
      this.preload = ''
      this.onended = null
      this.onerror = null
      this.onpause = null
      this.play = vi.fn(() =>
        rejectPlay ? Promise.reject(new Error('Audio error')) : Promise.resolve(),
      )
      this.pause = vi.fn()
      instances.push(this)
    }),
  )
  return instances
}

function renderTtsHarness(pushError = vi.fn()) {
  const rendered = renderHook(
    ({ streamingTurn }: { streamingTurn: StreamingTurn | null }) =>
      useTtsNarration({
        auth: 'token',
        baseUrl: 'https://backend.example.test',
        ttsConfig: {
          provider: 'deepgram',
          configured: true,
          model: 'aura-2-draco-en',
        },
        selectedSessionId: 20,
        sendPending: false,
        streamingTurn,
        speakableDmEntry: null,
        pushError,
      }),
    { initialProps: { streamingTurn: null as StreamingTurn | null } },
  )

  act(() => rendered.result.current.toggleTts())

  const stream = (turnId: number, text: string) => {
    act(() => rendered.result.current.resetTtsFailureForNextResponse())
    rendered.rerender({
      streamingTurn: {
        turnId,
        text,
        requiresRoll: false,
        rulesHint: {},
      },
    })
  }

  return { ...rendered, pushError, stream }
}

describe('useTtsNarration streaming', () => {
  let fetchBodies: unknown[]
  let fetchHandler: Mock<() => Promise<Response>>

  beforeEach(() => {
    fetchBodies = []
    fetchHandler = vi.fn(async () =>
      new Response(new Blob(['audio'], { type: 'audio/mpeg' }), {
        status: 200,
        headers: { 'Content-Type': 'audio/mpeg' },
      }),
    )
    vi.stubGlobal('localStorage', createStorageMock())
    vi.stubGlobal('sessionStorage', createStorageMock())
    let objectUrlIndex = 0
    Object.defineProperty(URL, 'createObjectURL', {
      configurable: true,
      value: vi.fn(() => `blob:tts-${++objectUrlIndex}`),
    })
    Object.defineProperty(URL, 'revokeObjectURL', {
      configurable: true,
      value: vi.fn(),
    })
    vi.stubGlobal(
      'fetch',
      vi.fn(async (_input: RequestInfo | URL, init?: RequestInit) => {
        fetchBodies.push(init?.body ? JSON.parse(String(init.body)) : null)
        return fetchHandler()
      }),
    )
  })

  afterEach(() => cleanup())

  it('requests a complete streamed sentence before the response ends', async () => {
    installAudioMock()
    const { stream } = renderTtsHarness()

    stream(76, 'The first torch gutters out, and a cold draft rolls over the stone.')

    await waitFor(() => expect(fetchHandler).toHaveBeenCalledOnce())
    expect(fetchBodies).toEqual([
      { text: 'The first torch gutters out, and a cold draft rolls over the stone.' },
    ])
  })

  it('prefetches the next queued sentence while current audio is playing', async () => {
    const audioInstances = installAudioMock()
    const { stream } = renderTtsHarness()

    stream(
      82,
      'First sentence carries enough detail to cross the playback threshold. ' +
        'Second sentence follows with another complete narration beat for prefetch.',
    )

    await waitFor(() => expect(fetchHandler).toHaveBeenCalledTimes(2))
    expect(audioInstances).toHaveLength(1)
    expect(fetchBodies).toEqual([
      { text: 'First sentence carries enough detail to cross the playback threshold.' },
      { text: 'Second sentence follows with another complete narration beat for prefetch.' },
    ])

    await act(async () => audioInstances[0].onended?.())
    await waitFor(() => expect(audioInstances).toHaveLength(2))
  })

  it('retries one hard request failure, disables TTS, and avoids later fan-out', async () => {
    installAudioMock()
    fetchHandler.mockImplementation(async () => {
      throw new TypeError('Failed to fetch')
    })
    const pushError = vi.fn()
    const { result, stream } = renderTtsHarness(pushError)

    stream(
      77,
      'The hallway bends sharply left, and the torchlight thins into a wavering copper line. ' +
        'Somewhere below, a chain drags once across stone, but queued narration must stop.',
    )

    await waitFor(
      () => expect(pushError).toHaveBeenCalledWith('tts', 'TTS failed: Failed to fetch'),
      { timeout: 2_000 },
    )
    expect(fetchHandler).toHaveBeenCalledTimes(2)
    expect(fetchBodies).toEqual([
      { text: 'The hallway bends sharply left, and the torchlight thins into a wavering copper line.' },
      { text: 'The hallway bends sharply left, and the torchlight thins into a wavering copper line.' },
    ])
    expect(result.current.ttsEnabled).toBe(false)

    stream(78, 'A second line arrives, but narration stays paused after the hard failure.')
    await new Promise((resolve) => window.setTimeout(resolve, 20))
    expect(fetchHandler).toHaveBeenCalledTimes(2)
    expect(pushError).toHaveBeenCalledTimes(1)
  })

  it('reports one playback failure and suppresses the remaining queued sentence', async () => {
    installAudioMock({ rejectPlay: true })
    const pushError = vi.fn()
    const { result, stream } = renderTtsHarness(pushError)

    stream(
      79,
      'The first torch gutters out, and a cold draft rolls over the stone. ' +
        'The second torch dies, and the chamber answers with a hollow metallic knock.',
    )

    await waitFor(() =>
      expect(pushError).toHaveBeenCalledWith('tts', 'TTS playback failed: Audio error'),
    )
    expect(pushError).toHaveBeenCalledTimes(1)
    expect(fetchHandler).toHaveBeenCalledOnce()
    expect(result.current.ttsEnabled).toBe(false)
  })
})
