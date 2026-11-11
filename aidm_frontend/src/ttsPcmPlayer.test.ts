// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest'
import { createPcmPlayer, PCM_SAMPLE_RATE, playPcmResponse } from './ttsPcmPlayer'

type FakeBuffer = { duration: number; samples: Float32Array; copyToChannel: (source: Float32Array) => void }
type FakeSource = {
  buffer: FakeBuffer | null
  onended: (() => void) | null
  startedAt: number | null
  connect: ReturnType<typeof vi.fn>
  disconnect: ReturnType<typeof vi.fn>
  start: (at: number) => void
  stop: ReturnType<typeof vi.fn>
}

function fakeAudioContext({ state = 'running', resumes = true } = {}) {
  const sources: FakeSource[] = []
  const context = {
    state,
    currentTime: 2,
    destination: {},
    createBuffer: (_channels: number, length: number, sampleRate: number): FakeBuffer => {
      const samples = new Float32Array(length)
      return { duration: length / sampleRate, samples, copyToChannel: (source) => samples.set(source) }
    },
    createBufferSource: () => {
      const source: FakeSource = {
        buffer: null,
        onended: null,
        startedAt: null,
        connect: vi.fn(),
        disconnect: vi.fn(),
        start: (at) => {
          source.startedAt = at
        },
        stop: vi.fn(),
      }
      sources.push(source)
      return source
    },
    resume: vi.fn(() => {
      if (!resumes) return new Promise<void>(() => undefined)
      context.state = 'running'
      return Promise.resolve()
    }),
  }
  const finishPlaying = () => sources.forEach((source) => source.onended?.())
  return { context: context as unknown as AudioContext, sources, finishPlaying }
}

function streamedResponse() {
  let controller!: ReadableStreamDefaultController<Uint8Array>
  const body = new ReadableStream<Uint8Array>({
    start(streamController) {
      controller = streamController
    },
  })
  return {
    response: new Response(body, { headers: { 'Content-Type': 'audio/pcm;rate=24000;channels=1' } }),
    send: (...bytes: number[]) => controller.enqueue(new Uint8Array(bytes)),
    end: () => controller.close(),
  }
}

function sampleCounts(sources: FakeSource[]) {
  return sources.map((source) => source.buffer?.samples.length)
}

afterEach(() => vi.restoreAllMocks())

describe('createPcmPlayer', () => {
  it('converts little-endian samples and carries an odd byte into the next chunk', () => {
    const { context, sources } = fakeAudioContext()
    const player = createPcmPlayer(context)

    player.push(new Uint8Array([0x00, 0x40, 0x01]))
    player.push(new Uint8Array([0x80]))
    player.push(new Uint8Array([0xff]))
    player.push(new Uint8Array([0x7f, 0x00, 0x00]))

    expect(sampleCounts(sources)).toEqual([1, 1, 2])
    expect(sources.flatMap((source) => [...(source.buffer?.samples ?? [])])).toEqual([
      0.5,
      -32767 / 32768,
      32767 / 32768,
      0,
    ])
  })

  it('schedules each chunk to start exactly where the last one ends', () => {
    const { context, sources } = fakeAudioContext()
    const player = createPcmPlayer(context)

    player.push(new Uint8Array(480))
    player.push(new Uint8Array(960))
    player.push(new Uint8Array(2))

    const [first, second, third] = sources.map((source) => source.startedAt ?? Number.NaN)
    expect(first).toBeCloseTo(2 + 0.05, 9)
    expect(second - first).toBeCloseTo(240 / PCM_SAMPLE_RATE, 9)
    expect(third - second).toBeCloseTo(480 / PCM_SAMPLE_RATE, 9)
    expect(sources.every((source) => source.connect.mock.calls[0][0] === context.destination)).toBe(true)
  })
})

describe('playPcmResponse', () => {
  it('plays a streamed clip to its end and keeps it as a WAV for replay', async () => {
    const { context, sources, finishPlaying } = fakeAudioContext()
    const stream = streamedResponse()
    const playback = playPcmResponse(context, stream.response, new AbortController().signal)

    stream.send(0x01, 0x02, 0x03)
    stream.send(0x04, 0x05, 0x06, 0x07, 0x08)
    stream.end()
    await vi.waitFor(() => expect(sampleCounts(sources)).toEqual([1, 3]))
    finishPlaying()
    const wav = await playback.done

    expect(wav?.type).toBe('audio/wav')
    const bytes = new Uint8Array(await (wav as Blob).arrayBuffer())
    const header = new DataView(bytes.buffer)
    const text = (offset: number) => String.fromCharCode(...bytes.slice(offset, offset + 4))
    expect([text(0), text(8), text(12), text(36)]).toEqual(['RIFF', 'WAVE', 'fmt ', 'data'])
    expect(header.getUint32(4, true)).toBe(36 + 8)
    expect(header.getUint16(22, true)).toBe(1)
    expect(header.getUint32(24, true)).toBe(PCM_SAMPLE_RATE)
    expect(header.getUint16(34, true)).toBe(16)
    expect(header.getUint32(40, true)).toBe(8)
    expect([...bytes.slice(44)]).toEqual([1, 2, 3, 4, 5, 6, 7, 8])
  })

  it('waits for the last buffer to finish playing before it settles', async () => {
    const { context, sources, finishPlaying } = fakeAudioContext()
    const stream = streamedResponse()
    const settled = vi.fn()
    void playPcmResponse(context, stream.response, new AbortController().signal).done.then(settled)

    stream.send(0x01, 0x02)
    stream.end()
    await vi.waitFor(() => expect(sources).toHaveLength(1))
    await new Promise((resolve) => window.setTimeout(resolve, 10))
    expect(settled).not.toHaveBeenCalled()

    finishPlaying()
    await vi.waitFor(() => expect(settled).toHaveBeenCalledOnce())
  })

  it('stops every scheduled buffer at once when aborted mid-stream, and keeps nothing', async () => {
    const { context, sources } = fakeAudioContext()
    const stream = streamedResponse()
    const controller = new AbortController()
    const playback = playPcmResponse(context, stream.response, controller.signal)

    stream.send(0x01, 0x02, 0x03, 0x04)
    await vi.waitFor(() => expect(sources).toHaveLength(1))
    controller.abort()

    expect(sources[0].stop).toHaveBeenCalledOnce()
    expect(sources[0].disconnect).toHaveBeenCalledOnce()
    await expect(playback.done).resolves.toBeNull()
  })

  it('stop() ends playback the same way', async () => {
    const { context, sources } = fakeAudioContext()
    const stream = streamedResponse()
    const playback = playPcmResponse(context, stream.response, new AbortController().signal)

    stream.send(0x01, 0x02)
    await vi.waitFor(() => expect(sources).toHaveLength(1))
    playback.stop()

    expect(sources[0].stop).toHaveBeenCalledOnce()
    await expect(playback.done).resolves.toBeNull()
  })

  it('resumes a suspended context and says so when the browser keeps audio blocked', async () => {
    const resumed = fakeAudioContext({ state: 'suspended' })
    const okStream = streamedResponse()
    const playback = playPcmResponse(resumed.context, okStream.response, new AbortController().signal)
    okStream.send(0x01, 0x02)
    okStream.end()
    await vi.waitFor(() => expect(resumed.sources).toHaveLength(1))
    resumed.finishPlaying()
    await expect(playback.done).resolves.toBeInstanceOf(Blob)
    expect(resumed.context.resume).toHaveBeenCalled()

    const blocked = fakeAudioContext({ state: 'suspended', resumes: false })
    const blockedStream = streamedResponse()
    await expect(
      playPcmResponse(blocked.context, blockedStream.response, new AbortController().signal).done,
    ).rejects.toThrow('Narration audio is waiting for user activation.')
    expect(blocked.sources).toHaveLength(0)
  })
})
