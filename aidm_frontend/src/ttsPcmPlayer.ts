/**
 * Plays the narrator's raw PCM (16-bit little-endian, 24 kHz, mono) through Web Audio while it
 * streams in, and keeps the whole clip as a WAV so a replay needs no request. Loaded on demand,
 * only once narration is in use.
 */

export const PCM_SAMPLE_RATE = 24_000
// Start each buffer slightly ahead of the clock so the first one never lands in the past.
const START_LEAD_SECONDS = 0.05
// How long a suspended context gets to resume before playback counts as blocked by the browser.
const RESUME_WAIT_MS = 300

export type PcmPlayback = {
  /** Settles once every sample has played: the clip as a WAV, or null when stopped first. */
  done: Promise<Blob | null>
  stop: () => void
}

/** Converts whole 16-bit little-endian samples to floats in [-1, 1). */
export function pcmToFloat32(bytes: Uint8Array): Float32Array<ArrayBuffer> {
  const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength)
  const samples = new Float32Array(bytes.byteLength >> 1)
  for (let index = 0; index < samples.length; index += 1) {
    samples[index] = view.getInt16(index * 2, true) / 32768
  }
  return samples
}

export function pcmToWav(chunks: Uint8Array<ArrayBuffer>[], sampleRate = PCM_SAMPLE_RATE) {
  const dataBytes = chunks.reduce((total, chunk) => total + chunk.byteLength, 0)
  const header = new DataView(new ArrayBuffer(44))
  const writeText = (offset: number, text: string) => {
    for (let index = 0; index < text.length; index += 1) header.setUint8(offset + index, text.charCodeAt(index))
  }
  writeText(0, 'RIFF')
  header.setUint32(4, 36 + dataBytes, true)
  writeText(8, 'WAVE')
  writeText(12, 'fmt ')
  header.setUint32(16, 16, true)
  header.setUint16(20, 1, true) // PCM
  header.setUint16(22, 1, true) // mono
  header.setUint32(24, sampleRate, true)
  header.setUint32(28, sampleRate * 2, true)
  header.setUint16(32, 2, true)
  header.setUint16(34, 16, true)
  writeText(36, 'data')
  header.setUint32(40, dataBytes, true)
  return new Blob([header.buffer, ...chunks], { type: 'audio/wav' })
}

/** Schedules pushed PCM back to back on `context`; an odd trailing byte waits for the next push. */
export function createPcmPlayer(context: AudioContext) {
  const sources = new Set<AudioBufferSourceNode>()
  const chunks: Uint8Array<ArrayBuffer>[] = []
  let carry: Uint8Array | null = null
  let nextStartAt = 0
  let finished = false
  let stopped = false
  let resolveDrained: () => void = () => undefined
  const drained = new Promise<void>((resolve) => {
    resolveDrained = resolve
  })
  const settle = () => {
    if (finished && sources.size === 0) resolveDrained()
  }

  return {
    drained,
    push(bytes: Uint8Array) {
      if (stopped) return
      let data = bytes
      if (carry) {
        data = new Uint8Array(carry.byteLength + bytes.byteLength)
        data.set(carry)
        data.set(bytes, carry.byteLength)
      }
      const wholeBytes = data.byteLength & ~1
      carry = wholeBytes < data.byteLength ? data.slice(wholeBytes) : null
      if (!wholeBytes) return

      const chunk = data.slice(0, wholeBytes)
      chunks.push(chunk)
      const buffer = context.createBuffer(1, wholeBytes >> 1, PCM_SAMPLE_RATE)
      buffer.copyToChannel(pcmToFloat32(chunk), 0)
      const source = context.createBufferSource()
      source.buffer = buffer
      source.connect(context.destination)
      const startAt = Math.max(nextStartAt, context.currentTime + START_LEAD_SECONDS)
      source.onended = () => {
        sources.delete(source)
        settle()
      }
      sources.add(source)
      source.start(startAt)
      nextStartAt = startAt + buffer.duration
    },
    /** No more bytes will come; `drained` settles once the last buffer has played. */
    finish() {
      finished = true
      settle()
    },
    stop() {
      stopped = true
      finished = true
      for (const source of sources) {
        source.onended = null
        source.stop()
        source.disconnect()
      }
      sources.clear()
      resolveDrained()
    },
    wav: () => pcmToWav(chunks),
  }
}

async function resumeContext(context: AudioContext) {
  const running = () => context.state === 'running'
  if (running()) return
  await Promise.race([
    context.resume(),
    new Promise((resolve) => window.setTimeout(resolve, RESUME_WAIT_MS)),
  ])
  // The browser refuses to start audio until the page has had a user activation.
  if (!running()) throw new Error('Narration audio is waiting for user activation.')
}

/** Plays a streamed PCM response as it arrives; aborting `signal` stops it at once. */
export function playPcmResponse(context: AudioContext, response: Response, signal: AbortSignal): PcmPlayback {
  const player = createPcmPlayer(context)
  const reader = response.body?.getReader() ?? null
  let stopped = false
  const stop = () => {
    if (stopped) return
    stopped = true
    player.stop()
    void reader?.cancel().catch(() => undefined)
  }
  if (signal.aborted) stop()
  signal.addEventListener('abort', stop, { once: true })

  const done = (async () => {
    try {
      await resumeContext(context)
      if (reader) {
        for (let next = await reader.read(); !next.done && !stopped; next = await reader.read()) {
          player.push(next.value)
        }
      } else {
        player.push(new Uint8Array(await response.arrayBuffer()))
      }
      player.finish()
      await player.drained
      return stopped ? null : player.wav()
    } catch (error) {
      if (stopped) return null
      stop()
      throw error
    } finally {
      signal.removeEventListener('abort', stop)
    }
  })()

  return { done, stop }
}
