import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createStoryAudio, type StoryAudio } from './storyAmbience'
import { STORY_BEDS } from './storyPlace'
import { STORY_CUES, type StoryCue } from './storySound'

type ParamEvent = { kind: 'set' | 'linear' | 'exp'; value: number; time: number }

/** An AudioParam that keeps its automation and answers its value at the context's clock. */
class FakeParam {
  events: ParamEvent[] = []
  cancels: number[] = []
  private base: number
  private readonly context: FakeAudioContext

  constructor(context: FakeAudioContext, value: number) {
    this.context = context
    this.base = value
  }

  get value() {
    return this.valueAt(this.context.currentTime)
  }

  set value(value: number) {
    this.base = value
  }

  valueAt(time: number) {
    let previous = { time: 0, value: this.base }
    for (const event of this.events) {
      if (event.time <= time) {
        previous = event
        continue
      }
      if (event.kind === 'linear') {
        return previous.value + ((event.value - previous.value) * (time - previous.time)) / (event.time - previous.time)
      }
      break
    }
    return previous.value
  }

  setValueAtTime(value: number, time: number) {
    this.events.push({ kind: 'set', value, time })
  }

  linearRampToValueAtTime(value: number, time: number) {
    this.events.push({ kind: 'linear', value, time })
  }

  exponentialRampToValueAtTime(value: number, time: number) {
    this.events.push({ kind: 'exp', value, time })
  }

  cancelScheduledValues(time: number) {
    this.cancels.push(time)
    this.events = this.events.filter((event) => event.time < time)
  }
}

class FakeNode {
  connections: unknown[] = []
  readonly context: FakeAudioContext

  constructor(context: FakeAudioContext) {
    this.context = context
    context.created.push(this)
    context.live.add(this)
  }

  connect<T>(target: T): T {
    this.connections.push(target)
    return target
  }

  disconnect() {
    this.connections = []
    this.context.live.delete(this)
  }
}

class FakeGain extends FakeNode {
  gain = new FakeParam(this.context, 1)
}

class FakeFilter extends FakeNode {
  type = 'lowpass'
  frequency = new FakeParam(this.context, 350)
  Q = new FakeParam(this.context, 1)
}

class FakePanner extends FakeNode {
  pan = new FakeParam(this.context, 0)
}

class FakeDelay extends FakeNode {
  delayTime = new FakeParam(this.context, 0)
}

class FakeSource extends FakeNode {
  startedAt: number | null = null
  stoppedAt: number | null = null
  ended = false
  onended: (() => void) | null = null

  start(when = 0) {
    this.startedAt = when
  }

  stop(when = 0) {
    this.stoppedAt = when
  }
}

class FakeOscillator extends FakeSource {
  type = 'sine'
  frequency = new FakeParam(this.context, 440)
}

class FakeBuffer {
  readonly duration: number
  private readonly data: Float32Array

  constructor(length: number, rate: number) {
    this.duration = length / rate
    this.data = new Float32Array(length)
  }

  getChannelData() {
    return this.data
  }
}

class FakeBufferSource extends FakeSource {
  buffer: FakeBuffer | null = null
  loop = false
}

class FakeAudioContext {
  currentTime = 0
  readonly sampleRate = 3000
  readonly destination = { name: 'destination' }
  readonly created: FakeNode[] = []
  readonly live = new Set<FakeNode>()
  resume = vi.fn(async () => undefined)

  createGain() {
    return new FakeGain(this)
  }

  createBiquadFilter() {
    return new FakeFilter(this)
  }

  createStereoPanner() {
    return new FakePanner(this)
  }

  createDelay() {
    return new FakeDelay(this)
  }

  createOscillator() {
    return new FakeOscillator(this)
  }

  createBufferSource() {
    return new FakeBufferSource(this)
  }

  createBuffer(_channels: number, length: number, rate: number) {
    return new FakeBuffer(length, rate)
  }

  /** Ends every source whose stop time has passed, as the audio thread would. */
  endSources() {
    this.created.forEach((node) => {
      if (node instanceof FakeSource && !node.ended && node.stoppedAt !== null && node.stoppedAt <= this.currentTime) {
        node.ended = true
        node.onended?.()
      }
    })
  }

  /** Moves the clock and the timers on together, in 50 ms steps. */
  advance(seconds: number) {
    for (let step = 0; step < Math.round(seconds / 0.05); step += 1) {
      this.currentTime = Math.round((this.currentTime + 0.05) * 1000) / 1000
      vi.advanceTimersByTime(50)
      this.endSources()
    }
  }

  buses() {
    const [ambience, effects] = this.created.filter(
      (node): node is FakeGain => node instanceof FakeGain && node.connections.includes(this.destination),
    )
    return { ambience, effects }
  }

  /** The gains feeding the ambience bus: one fader per bed. */
  faders() {
    const { ambience } = this.buses()
    return this.created.filter((node): node is FakeGain => node instanceof FakeGain && node.connections.includes(ambience))
  }
}

function seeded(seed = 7) {
  let state = seed
  return () => {
    state = (state * 1664525 + 1013904223) % 4294967296
    return state / 4294967296
  }
}

async function started(context = new FakeAudioContext(), levels = { ambience: 0.5, effects: 0.5 }) {
  const audio = createStoryAudio(context as unknown as AudioContext, { random: seeded() })
  audio.setLevels(levels)
  await audio.start()
  return audio
}

function oscillatorsAfter(context: FakeAudioContext, from: number) {
  return context.created.slice(from).filter((node): node is FakeOscillator => node instanceof FakeOscillator)
}

function firstFrequency(oscillator: FakeOscillator) {
  return oscillator.frequency.events[0]?.value ?? oscillator.frequency.value
}

describe('createStoryAudio', () => {
  let audio: StoryAudio | null = null

  beforeEach(() => {
    vi.useFakeTimers()
  })

  afterEach(() => {
    audio?.stop()
    audio = null
    vi.useRealTimers()
  })

  it('creates nothing and resumes nothing before start(), then plays the scene it was given', async () => {
    const context = new FakeAudioContext()
    audio = createStoryAudio(context as unknown as AudioContext, { random: seeded() })
    audio.setLevels({ ambience: 0.35, effects: 0.5 })
    audio.setScene({ bed: 'hearth', rain: true })
    audio.setDucked(true)
    audio.setHeartbeat(true)
    audio.playCue('clue')
    expect(context.created).toHaveLength(0)
    expect(context.resume).not.toHaveBeenCalled()

    await expect(audio.start()).resolves.toBe(true)
    expect(context.resume).toHaveBeenCalledOnce()
    const faders = context.faders()
    expect(faders.map((fader) => fader.gain.valueAt(2))).toEqual([1, 0.6])
    expect(context.buses().ambience.gain.valueAt(2)).toBeCloseTo(0.35 * 0.4)
  })

  it.each(STORY_BEDS)('builds the %s bed and releases every node it made once stopped', async (bed) => {
    const context = new FakeAudioContext()
    audio = await started(context)
    audio.setScene({ bed, rain: false })
    context.advance(12)
    const running = context.live.size
    context.advance(12)
    // Short sounds are released as they end, so a bed does not grow while it plays.
    expect(context.live.size).toBeLessThan(running + 12)

    audio.stop()
    expect(context.live.size).toBe(0)
  })

  it('releases a bed two seconds after the place goes quiet', async () => {
    const context = new FakeAudioContext()
    audio = await started(context)
    audio.setScene({ bed: 'marsh', rain: true })
    context.advance(5)
    audio.setScene(null)
    context.advance(2.2)
    expect([...context.live]).toEqual(Object.values(context.buses()))
  })

  it('cross-fades a change of place over two seconds and plays rain over the bed at 60 %', async () => {
    const context = new FakeAudioContext()
    audio = await started(context)
    context.currentTime = 10
    audio.setScene({ bed: 'wind', rain: false })
    const [wind] = context.faders()
    expect(wind.gain.events).toEqual([
      { kind: 'set', value: 0, time: 10 },
      { kind: 'linear', value: 1, time: 12 },
    ])

    context.currentTime = 20
    audio.setScene({ bed: 'cave', rain: true })
    const [, cave, rain] = context.faders()
    expect(wind.gain.cancels).toEqual([10, 20])
    expect(wind.gain.events.slice(-2)).toEqual([
      { kind: 'set', value: 1, time: 20 },
      { kind: 'linear', value: 0, time: 22 },
    ])
    expect(cave.gain.events).toEqual([
      { kind: 'set', value: 0, time: 20 },
      { kind: 'linear', value: 1, time: 22 },
    ])
    expect(rain.gain.events).toEqual([
      { kind: 'set', value: 0, time: 20 },
      { kind: 'linear', value: 0.6, time: 22 },
    ])

    context.currentTime = 30
    audio.setScene({ bed: 'cave', rain: false })
    expect(cave.gain.events).toHaveLength(2)
    expect(rain.gain.events.slice(-1)).toEqual([{ kind: 'linear', value: 0, time: 32 }])
  })

  it('ducks the bed to 40 % while the narrator speaks and brings it back after', async () => {
    const context = new FakeAudioContext()
    audio = await started(context)
    const { ambience, effects } = context.buses()
    context.currentTime = 5
    audio.setDucked(true)
    expect(ambience.gain.events.slice(-2)).toEqual([
      { kind: 'set', value: 0.5, time: 5 },
      { kind: 'linear', value: 0.2, time: 5.3 },
    ])
    context.currentTime = 8
    audio.setDucked(false)
    expect(ambience.gain.events.slice(-2)).toEqual([
      { kind: 'set', value: 0.2, time: 8 },
      { kind: 'linear', value: 0.5, time: 8.8 },
    ])
    context.currentTime = 9
    audio.setLevels({ ambience: 0.3, effects: 0.8 })
    expect(ambience.gain.valueAt(9.1)).toBeCloseTo(0.3)
    expect(effects.gain.valueAt(9.1)).toBeCloseTo(0.8)
  })

  describe('cues', () => {
    async function cue(name: StoryCue) {
      const context = new FakeAudioContext()
      audio = await started(context)
      context.currentTime = 1
      const from = context.created.length
      audio.playCue(name)
      return { context, oscillators: oscillatorsAfter(context, from), from }
    }

    const at = 1.03

    it('fight_start: two low hits 260 ms apart, each a 70→42 Hz sine over 180 ms with a noise thump', async () => {
      const { context, oscillators, from } = await cue('fight_start')
      expect(oscillators.map((oscillator) => [oscillator.type, firstFrequency(oscillator), oscillator.startedAt])).toEqual([
        ['sine', 70, expect.closeTo(at)],
        ['sine', 70, expect.closeTo(at + 0.26)],
      ])
      expect(oscillators.map((oscillator) => oscillator.frequency.events[1])).toEqual([
        { kind: 'exp', value: 42, time: expect.closeTo(at + 0.18) },
        { kind: 'exp', value: 42, time: expect.closeTo(at + 0.26 + 0.18) },
      ])
      const thumps = context.created.slice(from).filter((node): node is FakeBufferSource => node instanceof FakeBufferSource)
      expect(thumps.map((thump) => thump.startedAt)).toEqual([expect.closeTo(at), expect.closeTo(at + 0.26)])
    })

    it('victory: three triangle plucks rising a fourth, a fifth and an octave, 140 ms each', async () => {
      const { oscillators } = await cue('victory')
      expect(oscillators.map((oscillator) => oscillator.type)).toEqual(['triangle', 'triangle', 'triangle'])
      expect(oscillators.map(firstFrequency)).toEqual([392 * (4 / 3), 392 * 1.5, 784].map((value) => expect.closeTo(value)))
      expect(oscillators.map((oscillator) => oscillator.startedAt)).toEqual([at, at + 0.14, at + 0.28].map((value) => expect.closeTo(value)))
    })

    it('defeat: one low note falling a whole tone over 900 ms', async () => {
      const { oscillators } = await cue('defeat')
      expect(oscillators).toHaveLength(1)
      expect(oscillators[0].frequency.events).toEqual([
        { kind: 'set', value: 146.83, time: at },
        { kind: 'exp', value: 130.81, time: at + 0.9 },
      ])
      expect(146.83 / 130.81).toBeCloseTo(2 ** (2 / 12), 3)
    })

    it('clue: one soft bell at 1320 Hz with partials at ×2.76 and ×5.4, decaying over 1.2 s', async () => {
      const { context, oscillators } = await cue('clue')
      expect(oscillators.map(firstFrequency)).toEqual([1320, 1320 * 2.76, 1320 * 5.4].map((value) => expect.closeTo(value)))
      const fundamental = context.created.find(
        (node): node is FakeGain => node instanceof FakeGain && oscillators[0].connections.includes(node),
      )
      expect(fundamental?.gain.events.at(-1)).toEqual({ kind: 'exp', value: 0.0001, time: at + 1.2 })
    })

    it('level_up: a four-note rising bell arpeggio', async () => {
      const { oscillators } = await cue('level_up')
      const fundamentals = oscillators.filter((_, index) => index % 3 === 0)
      expect(fundamentals.map(firstFrequency)).toEqual([880, 1100, 1320, 1760])
      expect(fundamentals.map((oscillator) => oscillator.startedAt)).toEqual(
        [at, at + 0.11, at + 0.22, at + 0.33].map((value) => expect.closeTo(value)),
      )
    })

    it('nat20: a bright bell a fifth above the clue with a quiet octave', async () => {
      const { oscillators } = await cue('nat20')
      expect(oscillators.map(firstFrequency)).toEqual([1980, 1980 * 2.76, 1980 * 5.4, 3960].map((value) => expect.closeTo(value)))
    })

    it('nat1: a dull 90 Hz thud of 120 ms', async () => {
      const { context, oscillators } = await cue('nat1')
      expect(oscillators.map(firstFrequency)).toEqual([90])
      const level = context.created.find((node): node is FakeGain => node instanceof FakeGain && oscillators[0].connections.includes(node))
      expect(level?.gain.events.at(-1)).toEqual({ kind: 'exp', value: 0.0001, time: expect.closeTo(at + 0.12) })
    })

    it('dice_tick: a quiet 3 ms click', async () => {
      const { context, oscillators, from } = await cue('dice_tick')
      expect(oscillators).toEqual([])
      const [click] = context.created.slice(from).filter((node): node is FakeBufferSource => node instanceof FakeBufferSource)
      expect([click.startedAt, click.stoppedAt]).toEqual([at, expect.closeTo(at + 0.003)])
    })

    it('heartbeat: a double thump while on, every 1.1 s, and nothing once off', async () => {
      const context = new FakeAudioContext()
      audio = await started(context)
      const beats = () => oscillatorsAfter(context, 0).filter((oscillator) => firstFrequency(oscillator) === 64)
      audio.setHeartbeat(true)
      expect(beats().map((oscillator) => oscillator.startedAt)).toEqual([expect.closeTo(0.03), expect.closeTo(0.31)])
      context.advance(2.2)
      expect(beats()).toHaveLength(6)
      audio.setHeartbeat(false)
      context.advance(3.3)
      expect(beats()).toHaveLength(6)
    })

    it('never overlaps itself: a cue played again fades the one still sounding', async () => {
      const { context, oscillators } = await cue('clue')
      context.currentTime = 1.2
      audio?.playCue('clue')
      expect(oscillators.map((oscillator) => oscillator.stoppedAt)).toEqual([1.22, 1.22, 1.22].map((value) => expect.closeTo(value)))
      context.advance(0.1)
      expect(oscillators.some((oscillator) => context.live.has(oscillator))).toBe(false)
      context.advance(2)
      expect(context.live.size).toBe(2)
    })

    it('plays nothing while effects are off', async () => {
      const context = new FakeAudioContext()
      audio = await started(context, { ambience: 0.5, effects: 0 })
      const before = context.created.length
      STORY_CUES.forEach((name) => audio?.playCue(name))
      expect(context.created).toHaveLength(before)
    })
  })

  describe('a context that fails', () => {
    it('stays silent when it cannot resume', async () => {
      const context = new FakeAudioContext()
      context.resume.mockRejectedValueOnce(new Error('not allowed'))
      audio = createStoryAudio(context as unknown as AudioContext)
      await expect(audio.start()).resolves.toBe(false)
      audio.setLevels({ ambience: 1, effects: 1 })
      audio.setScene({ bed: 'rain', rain: false })
      audio.playCue('victory')
      expect(context.created).toHaveLength(0)
    })

    it('stays silent when it cannot build the output', async () => {
      const context = new FakeAudioContext()
      vi.spyOn(context, 'createGain').mockImplementation(() => {
        throw new Error('closed')
      })
      audio = createStoryAudio(context as unknown as AudioContext)
      await expect(audio.start()).resolves.toBe(false)
      expect(context.live.size).toBe(0)
    })

    it('goes silent instead of throwing when a bed cannot be built', async () => {
      const context = new FakeAudioContext()
      audio = await started(context)
      vi.spyOn(context, 'createBufferSource').mockImplementation(() => {
        throw new Error('closed')
      })
      expect(() => audio?.setScene({ bed: 'wind', rain: false })).not.toThrow()
      expect(context.live.size).toBe(0)
      expect(() => audio?.playCue('clue')).not.toThrow()
      expect(context.live.size).toBe(0)
    })
  })
})
