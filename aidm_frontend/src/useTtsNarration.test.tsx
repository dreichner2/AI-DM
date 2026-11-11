// @vitest-environment jsdom
import { act, cleanup, renderHook, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi, type Mock } from 'vitest'
import type { NarrationVoiceSetting } from './story/sheets/StorySettingsSheet'
import type { StreamingTurn, TtsRuntimeConfig } from './types'
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

const DEEPGRAM_CONFIG: TtsRuntimeConfig = {
  provider: 'deepgram',
  configured: true,
  model: 'aura-2-draco-en',
}

const OPENAI_CONFIG: TtsRuntimeConfig = {
  provider: 'openai',
  configured: true,
  model: 'gpt-realtime-2.1',
  voice: 'cedar',
  accent: 'default',
  audio_format: 'audio/pcm;rate=24000;channels=1',
  voices: ['cedar', 'marin', 'ash'],
  accents: [
    { id: 'default', label: 'Default' },
    { id: 'british', label: 'British' },
  ],
  qualities: [
    { id: 'best', label: 'Best' },
    { id: 'faster', label: 'Faster' },
  ],
}

type FakeSource = { onended: (() => void) | null; stop: Mock<() => void> }

function installAudioContextMock() {
  const sources: FakeSource[] = []
  const contexts: unknown[] = []
  vi.stubGlobal(
    'AudioContext',
    vi.fn(function MockAudioContext(this: Record<string, unknown>) {
      Object.assign(this, {
        state: 'running',
        currentTime: 0,
        destination: {},
        resume: vi.fn(() => Promise.resolve()),
        createBuffer: (_channels: number, length: number, rate: number) => ({
          duration: length / rate,
          copyToChannel: vi.fn(),
        }),
        createBufferSource: () => {
          const source = { onended: null, connect: vi.fn(), disconnect: vi.fn(), start: vi.fn(), stop: vi.fn() }
          sources.push(source)
          return source
        },
      })
      contexts.push(this)
    }),
  )
  return { sources, contexts, finishPlaying: () => sources.forEach((source) => source.onended?.()) }
}

function pcmResponse() {
  return new Response(new Uint8Array([1, 2, 3, 4]), {
    status: 200,
    headers: { 'Content-Type': 'audio/pcm;rate=24000;channels=1' },
  })
}

function renderTtsHarness(pushError = vi.fn(), ttsConfig: TtsRuntimeConfig = DEEPGRAM_CONFIG) {
  const rendered = renderHook(
    ({ streamingTurn }: { streamingTurn: StreamingTurn | null }) =>
      useTtsNarration({
        auth: 'token',
        baseUrl: 'https://backend.example.test',
        ttsConfig,
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

  it('reports configured narration as ready after the user enables it', () => {
    installAudioMock()
    const { result } = renderTtsHarness()

    expect(result.current.ttsEnabled).toBe(true)
    expect(result.current.effectiveTtsStatus).toBe('ready')
    expect(result.current.ttsStatusLabel).toBe('Ready')
  })

  it('turns off persisted narration when configuration is unavailable', async () => {
    localStorage.setItem('aidm:ttsEnabled', 'true')
    installAudioMock()
    const pushError = vi.fn()
    const { result } = renderHook(() =>
      useTtsNarration({
        auth: 'token',
        baseUrl: 'https://backend.example.test',
        ttsConfig: {
          provider: 'deepgram',
          configured: false,
          model: 'aura-2-draco-en',
        },
        selectedSessionId: 20,
        sendPending: false,
        streamingTurn: null,
        speakableDmEntry: null,
        pushError,
      }),
    )

    expect(result.current.ttsEnabled).toBe(false)
    expect(result.current.effectiveTtsStatus).toBe('unavailable')
    expect(result.current.ttsStatusLabel).toBe('Unavailable')
    await waitFor(() => expect(localStorage.getItem('aidm:ttsEnabled')).toBe('false'))
    expect(pushError).not.toHaveBeenCalled()
  })

  it('keeps narration off while configuration is unknown and explains a user request', () => {
    localStorage.setItem('aidm:ttsEnabled', 'true')
    installAudioMock()
    const pushError = vi.fn()
    const { result } = renderHook(() =>
      useTtsNarration({
        auth: 'token',
        baseUrl: 'https://backend.example.test',
        ttsConfig: null,
        selectedSessionId: 20,
        sendPending: false,
        streamingTurn: null,
        speakableDmEntry: null,
        pushError,
      }),
    )

    expect(result.current.effectiveTtsStatus).toBe('checking')
    expect(result.current.ttsStatusLabel).toBe('Checking')
    expect(result.current.ttsEnabled).toBe(false)
    act(() => result.current.toggleTts())
    expect(result.current.ttsEnabled).toBe(false)
    expect(localStorage.getItem('aidm:ttsEnabled')).toBe('true')
    expect(pushError).toHaveBeenCalledWith('tts', 'Narration availability is still being checked.')
  })

  it('restores a persisted narration preference when configuration finishes loading', async () => {
    localStorage.setItem('aidm:ttsEnabled', 'true')
    installAudioMock()
    const pushError = vi.fn()
    const { result, rerender } = renderHook(
      ({ configured }: { configured: boolean | null }) =>
        useTtsNarration({
          auth: 'token',
          baseUrl: 'https://backend.example.test',
          ttsConfig: configured === null
            ? null
            : {
                provider: 'deepgram',
                configured,
                model: 'aura-2-draco-en',
              },
          selectedSessionId: 20,
          sendPending: false,
          streamingTurn: null,
          speakableDmEntry: null,
          pushError,
        }),
      { initialProps: { configured: null as boolean | null } },
    )

    expect(result.current.effectiveTtsStatus).toBe('checking')
    rerender({ configured: true })

    await waitFor(() => expect(result.current.ttsEnabled).toBe(true))
    await waitFor(() => expect(result.current.effectiveTtsStatus).toBe('ready'))
    expect(localStorage.getItem('aidm:ttsEnabled')).toBe('true')
    expect(pushError).not.toHaveBeenCalled()
  })

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
    await waitFor(() => expect(result.current.ttsEnabled).toBe(false))

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
    await waitFor(() => expect(result.current.ttsEnabled).toBe(false))
  })

  describe('with a voice that streams PCM', () => {
    const line = 'The first torch gutters out, and a cold draft rolls over the stone.'

    function voiceSetting(setting: unknown) {
      return (setting as { voice: NarrationVoiceSetting }).voice
    }

    it('sends the stored voice, accent and quality with every narration request', async () => {
      localStorage.setItem('aidm:ttsVoice', JSON.stringify({ voice: 'marin', accent: 'british', quality: 'faster' }))
      installAudioMock()
      installAudioContextMock()
      fetchHandler.mockImplementation(async () => pcmResponse())
      const { stream } = renderTtsHarness(vi.fn(), OPENAI_CONFIG)

      stream(90, line)

      await waitFor(() => expect(fetchHandler).toHaveBeenCalledOnce())
      expect(fetchBodies).toEqual([{ text: line, voice: 'marin', accent: 'british', quality: 'faster' }])
    })

    it('uses the server defaults for a choice the server does not offer', async () => {
      localStorage.setItem('aidm:ttsVoice', JSON.stringify({ voice: 'darth', accent: 'texan' }))
      installAudioMock()
      installAudioContextMock()
      fetchHandler.mockImplementation(async () => pcmResponse())
      const { result, stream } = renderTtsHarness(vi.fn(), OPENAI_CONFIG)

      stream(91, line)

      await waitFor(() => expect(fetchHandler).toHaveBeenCalledOnce())
      expect(fetchBodies).toEqual([{ text: line, voice: 'cedar', accent: 'default', quality: 'best' }])
      expect(voiceSetting(result.current.narrationSetting)).toMatchObject({
        voices: ['cedar', 'marin', 'ash'],
        voice: 'cedar',
        accent: 'default',
        quality: 'best',
      })
    })

    it('plays the stream through one AudioContext made when narration is turned on, then replays it from the cache', async () => {
      const audioInstances = installAudioMock()
      const webAudio = installAudioContextMock()
      fetchHandler.mockImplementation(async () => pcmResponse())
      const { result, stream } = renderTtsHarness(vi.fn(), OPENAI_CONFIG)
      expect(webAudio.contexts).toHaveLength(1)

      stream(92, line)
      await waitFor(() => expect(webAudio.sources).toHaveLength(1))
      expect(result.current.ttsSpeaking).toBe(true)
      expect(audioInstances).toHaveLength(0)
      act(() => webAudio.finishPlaying())
      await waitFor(() => expect(result.current.ttsSpeaking).toBe(false))

      act(() => result.current.queueTtsNarrationRef.current?.(line))

      await waitFor(() => expect(audioInstances).toHaveLength(1))
      expect(audioInstances[0].src).toMatch(/^blob:tts-/)
      expect(fetchHandler).toHaveBeenCalledOnce()
      expect(webAudio.contexts).toHaveLength(1)
    })

    it('stops streamed narration at once', async () => {
      installAudioMock()
      const webAudio = installAudioContextMock()
      fetchHandler.mockImplementation(async () => pcmResponse())
      const { result, stream } = renderTtsHarness(vi.fn(), OPENAI_CONFIG)

      stream(93, line)
      await waitFor(() => expect(webAudio.sources).toHaveLength(1))
      act(() => result.current.stopTtsAudio())

      expect(webAudio.sources[0].stop).toHaveBeenCalledOnce()
      await waitFor(() => expect(result.current.ttsSpeaking).toBe(false))
    })

    it('plays a sample line in the chosen voice, and a new choice stops it and is kept', async () => {
      const webAudio = installAudioContextMock()
      fetchHandler.mockImplementation(async () => pcmResponse())
      const pushError = vi.fn()
      const { result } = renderHook(() =>
        useTtsNarration({
          auth: 'token',
          baseUrl: 'https://backend.example.test',
          ttsConfig: OPENAI_CONFIG,
          selectedSessionId: 20,
          sendPending: false,
          streamingTurn: null,
          speakableDmEntry: null,
          pushError,
        }),
      )
      expect(result.current.ttsEnabled).toBe(false)

      act(() => voiceSetting(result.current.narrationSetting).onPlaySample())

      await waitFor(() => expect(webAudio.sources).toHaveLength(1))
      expect(fetchBodies).toEqual([
        { text: 'The road remembers every name it was given.', voice: 'cedar', accent: 'default', quality: 'best' },
      ])
      act(() => voiceSetting(result.current.narrationSetting).onChange({ voice: 'marin', accent: 'british' }))

      expect(webAudio.sources[0].stop).toHaveBeenCalledOnce()
      expect(voiceSetting(result.current.narrationSetting)).toMatchObject({ voice: 'marin', accent: 'british' })
      expect(JSON.parse(localStorage.getItem('aidm:ttsVoice') ?? 'null')).toEqual({ voice: 'marin', accent: 'british' })
      expect(pushError).not.toHaveBeenCalled()
    })
  })

  it('reads the narration setting as none when the server has no voice, and says so on a request', () => {
    const pushError = vi.fn()
    const { result, rerender } = renderHook(
      ({ ttsConfig }: { ttsConfig: TtsRuntimeConfig | null }) =>
        useTtsNarration({
          auth: 'token',
          baseUrl: 'https://backend.example.test',
          ttsConfig,
          selectedSessionId: 20,
          sendPending: false,
          streamingTurn: null,
          speakableDmEntry: null,
          pushError,
        }),
      { initialProps: { ttsConfig: null as TtsRuntimeConfig | null } },
    )
    expect(result.current.narrationSetting).toBeNull()

    rerender({ ttsConfig: { provider: null, configured: false, model: null } })
    expect(result.current.narrationSetting).toBe('none')
    act(() => result.current.toggleTts())
    expect(pushError).toHaveBeenCalledWith('tts', 'No narrator voice is set up on this server.')

    rerender({ ttsConfig: DEEPGRAM_CONFIG })
    expect(result.current.narrationSetting).toEqual({ enabled: false, problem: '', voice: undefined })
  })
})
