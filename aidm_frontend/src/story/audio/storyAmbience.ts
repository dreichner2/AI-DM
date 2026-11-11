import type { StoryAmbience, StoryBed } from './storyPlace'
import type { StoryCue } from './storySound'

/**
 * The Story view's generated sound, made entirely with the Web Audio API: no
 * samples, only noise buffers, filters, slow LFOs and short enveloped bursts.
 *
 * A bed is a looping texture for the place; a change of place cross-fades
 * over two seconds, and rain weather plays the rain bed over it at 60 %. Cues
 * are short stingers, one voice each: a cue played again while it still
 * sounds replaces itself. The context is a parameter and nothing is created
 * before `start()`; a context that fails leaves the sound silent, never an error.
 */

export type StoryAudioLevels = {
  /** 0–1; 0 silences the bed. */
  ambience: number
  /** 0–1; 0 drops every cue. */
  effects: number
}

export type StoryAudio = {
  /** Resumes the context and builds the output; false, and silent from then on, when the context cannot start. */
  start(): Promise<boolean>
  /** The bed to play, or null for none. */
  setScene(scene: StoryAmbience | null): void
  /** True while the narrator speaks: the bed drops to 40 % of its level. */
  setDucked(ducked: boolean): void
  setLevels(levels: StoryAudioLevels): void
  playCue(cue: StoryCue): void
  /** A double thump every 1.1 s while on. */
  setHeartbeat(on: boolean): void
  /** Silences and releases everything; the context itself is left to its owner. */
  stop(): void
}

export type StoryAudioOptions = {
  /** 0 ≤ n < 1; Math.random unless a test needs the textures repeatable. */
  random?: () => number
}

const CROSS_FADE_SECONDS = 2
const DUCKED_SHARE = 0.4
const DUCK_SECONDS = 0.3
const UNDUCK_SECONDS = 0.8
const LEVEL_SECONDS = 0.1
const RAIN_OVER_BED = 0.6
const HEARTBEAT_SECONDS = 1.1
/** How far ahead of the clock each event is scheduled, so it is never late. */
const LOOKAHEAD_SECONDS = 0.03
const VOICE_FADE_SECONDS = 0.015
const SILENT = 0.0001

type NoiseColor = 'white' | 'pink' | 'brown'
const NOISE_SECONDS: Record<NoiseColor, number> = { white: 2, pink: 8, brown: 8 }
const NOISE_RMS = 0.2
const LOOP_FADE_SECONDS = 0.25

// Each texture's level before the ambience volume.
const WIND_LEVEL = 0.35
const WIND_CUTOFF_HZ = 440
/** The cutoff's swing either side of WIND_CUTOFF_HZ: 180–700 Hz. */
const WIND_SWEEP_HZ = 260
const GUST_LEVEL = 1.5
const GUST_SECONDS = 3
const RAIN_LEVEL = 0.5
const RAIN_DRIFT = 0.15
const DRIP_LEVEL = 0.05
const CRACKLE_LEVEL = 3
const RUMBLE_LEVEL = 0.35
const CRICKET_HZ = 4400
const CRICKET_LEVEL = 0.06
const CRICKET_PULSE_SECONDS = 0.024
const CRICKET_PULSE_GAP_SECONDS = 0.05
/** Two detuned cricket voices: frequency ratio and stereo position. */
const CRICKET_VOICES: ReadonlyArray<[number, number]> = [
  [1, -0.45],
  [1.013, 0.5],
]
const BUBBLE_LEVEL = 0.25
const WHINE_LEVEL = 0.15
const DRONE_LEVEL = 0.05
/** 55 Hz and a near twin that beats against it, with a quiet octave so small speakers carry it. */
const DRONE_PARTIALS: ReadonlyArray<[number, number]> = [
  [55, 1],
  [55.35, 1],
  [110.4, 0.35],
]
const CAVE_DRIP_LEVEL = 0.2
const CAVE_ECHO_SECONDS = 0.31
const CAVE_FEEDBACK = 0.5
const CAVE_ECHO_LEVEL = 0.7

/** Bell partials: frequency ratio, level and share of the decay. */
const BELL_PARTIALS: ReadonlyArray<[number, number, number]> = [
  [1, 1, 1],
  [2.76, 0.4, 0.6],
  [5.4, 0.18, 0.3],
]
const CLUE_HZ = 1320
const VICTORY_ROOT_HZ = 392
/** A fourth, a fifth and an octave above the root. */
const VICTORY_RATIOS = [4 / 3, 3 / 2, 2]
const VICTORY_NOTE_SECONDS = 0.14
/** A major arpeggio rising to the octave. */
const LEVEL_UP_HZ = [880, 1100, 1320, 1760]
const LEVEL_UP_STEP_SECONDS = 0.11
/** D3 falling a whole tone to C3. */
const DEFEAT_HZ: [number, number] = [146.83, 130.81]
const FIGHT_HIT_HZ: [number, number] = [70, 42]
const FIGHT_HIT_GAP_SECONDS = 0.26
/** Each beat's two thumps: offset and level. */
const HEARTBEAT_THUMPS: ReadonlyArray<[number, number]> = [
  [0, 1],
  [0.28, 0.7],
]

type Holder = { add<T extends AudioNode>(node: T): T }

/** Nodes released together: sources stopped, everything disconnected. */
type Nodes = Holder & {
  /** Moves every source's stop to `at`. */
  stopAt(at: number): void
  release(): void
}

type Shot = Holder & {
  /** Starts and stops a source; the shot is released when its last source ends. */
  play<T extends AudioScheduledSourceNode>(source: T, start: number, stop: number): T
  noise(color: NoiseColor, start: number, stop: number): AudioBufferSourceNode
}

/** A bed's lasting nodes, its timers and its short sounds. */
type Part = Holder & {
  /** Calls `fire` with a start time at random gaps of min–max seconds until the part is released. */
  every(range: [number, number], fire: (when: number) => void): void
  shot(build: (sound: Shot) => void): void
  release(): void
}

type Kit = {
  context: AudioContext
  between(min: number, max: number): number
  noise(color: NoiseColor): AudioBuffer
}

type Layer = (kit: Kit, bed: Part, out: AudioNode) => void
type Cue = (kit: Kit, sound: Shot, out: AudioNode, at: number) => void

function isSource(node: AudioNode): node is AudioScheduledSourceNode {
  return typeof (node as Partial<AudioScheduledSourceNode>).stop === 'function'
}

function stopSource(source: AudioScheduledSourceNode, at?: number) {
  try {
    source.stop(at)
  } catch {
    // Older WebKit refuses a second stop; the source is stopping anyway.
  }
}

function nodeSet(): Nodes {
  const nodes = new Set<AudioNode>()
  return {
    add(node) {
      nodes.add(node)
      return node
    },
    stopAt(at) {
      nodes.forEach((node) => {
        if (isSource(node)) stopSource(node, at)
      })
    },
    release() {
      nodes.forEach((node) => {
        if (isSource(node)) {
          node.onended = null
          stopSource(node)
        }
        node.disconnect()
      })
      nodes.clear()
    },
  }
}

/** Builds a short sound into `live`; it leaves `live` and is released when its last source ends. */
function shot(kit: Kit, live: Set<Nodes>, build: (sound: Shot) => void, onEnd?: () => void): Nodes {
  const nodes = nodeSet()
  const last: { source: AudioScheduledSourceNode | null; stop: number } = { source: null, stop: -Infinity }
  const track = <T extends AudioScheduledSourceNode>(source: T, stop: number) => {
    nodes.add(source)
    if (stop > last.stop) Object.assign(last, { source, stop })
    return source
  }
  try {
    build({
      add: nodes.add,
      play(source, start, stop) {
        track(source, stop)
        source.start(start)
        source.stop(stop)
        return source
      },
      noise(color, start, stop) {
        const source = track(kit.context.createBufferSource(), stop)
        source.buffer = kit.noise(color)
        source.start(start, kit.between(0, source.buffer.duration - (stop - start)))
        source.stop(stop)
        return source
      },
    })
  } catch (error) {
    nodes.release()
    throw error
  }
  live.add(nodes)
  if (last.source) {
    last.source.onended = () => {
      live.delete(nodes)
      nodes.release()
      onEnd?.()
    }
  }
  return nodes
}

function part(kit: Kit): Part {
  const nodes = nodeSet()
  const shots = new Set<Nodes>()
  const timers = new Set<ReturnType<typeof setTimeout>>()
  return {
    add: nodes.add,
    every([min, max], fire) {
      const schedule = () => {
        const timer = setTimeout(() => {
          timers.delete(timer)
          fire(kit.context.currentTime + LOOKAHEAD_SECONDS)
          schedule()
        }, kit.between(min, max) * 1000)
        timers.add(timer)
      }
      schedule()
    },
    shot(build) {
      shot(kit, shots, build)
    },
    release() {
      timers.forEach((timer) => clearTimeout(timer))
      timers.clear()
      shots.forEach((sound) => sound.release())
      shots.clear()
      nodes.release()
    },
  }
}

/** Seconds of seamlessly looping noise at NOISE_RMS: white, pink (Kellet's filter) or brown (leaky integration). */
function noiseBuffer(context: BaseAudioContext, color: NoiseColor, random: () => number) {
  const rate = context.sampleRate
  const length = Math.round(NOISE_SECONDS[color] * rate)
  const fade = Math.round(LOOP_FADE_SECONDS * rate)
  const samples = new Float32Array(length + fade)
  let b0 = 0
  let b1 = 0
  let b2 = 0
  let brown = 0
  for (let index = 0; index < samples.length; index += 1) {
    const white = random() * 2 - 1
    if (color === 'white') {
      samples[index] = white
    } else if (color === 'pink') {
      b0 = 0.99765 * b0 + white * 0.099046
      b1 = 0.963 * b1 + white * 0.2965164
      b2 = 0.57 * b2 + white * 1.0526913
      samples[index] = b0 + b1 + b2 + white * 0.1848
    } else {
      brown = (brown + 0.02 * white) / 1.02
      samples[index] = brown
    }
  }
  // The overhang fades into the start (equal power), so the loop has no seam.
  for (let index = 0; index < fade; index += 1) {
    const share = index / fade
    samples[index] = samples[index] * Math.sqrt(share) + samples[length + index] * Math.sqrt(1 - share)
  }
  let mean = 0
  for (let index = 0; index < length; index += 1) mean += samples[index]
  mean /= length
  let power = 0
  for (let index = 0; index < length; index += 1) power += (samples[index] - mean) ** 2
  const scale = NOISE_RMS / Math.sqrt(power / length || 1)
  const buffer = context.createBuffer(1, length, rate)
  const channel = buffer.getChannelData(0)
  for (let index = 0; index < length; index += 1) channel[index] = (samples[index] - mean) * scale
  return buffer
}

function gain(kit: Kit, holder: Holder, value: number) {
  const node = holder.add(kit.context.createGain())
  node.gain.value = value
  return node
}

function filter(kit: Kit, holder: Holder, type: BiquadFilterType, frequency: number, q: number) {
  const node = holder.add(kit.context.createBiquadFilter())
  node.type = type
  node.frequency.value = frequency
  node.Q.value = q
  return node
}

function pan(kit: Kit, holder: Holder, position: number) {
  const node = holder.add(kit.context.createStereoPanner())
  node.pan.value = position
  return node
}

function loop(kit: Kit, bed: Part, color: NoiseColor) {
  const source = bed.add(kit.context.createBufferSource())
  source.buffer = kit.noise(color)
  source.loop = true
  source.start(kit.context.currentTime, kit.between(0, source.buffer.duration))
  return source
}

/** A sine LFO moving `target` by ±depth. */
function lfo(kit: Kit, bed: Part, frequency: number, depth: number, target: AudioParam) {
  const oscillator = bed.add(kit.context.createOscillator())
  oscillator.frequency.value = frequency
  oscillator.connect(gain(kit, bed, depth)).connect(target)
  oscillator.start(kit.context.currentTime)
}

/** Up to `peak` in `attack` seconds, then an exponential fall to silence at `end`. */
function envelope(param: AudioParam, start: number, peak: number, attack: number, end: number) {
  param.setValueAtTime(0, start)
  param.linearRampToValueAtTime(peak, start + attack)
  param.exponentialRampToValueAtTime(SILENT, end)
}

type ToneOptions = {
  type?: OscillatorType
  frequency: number
  /** Swept to by `end`. */
  to?: number
  at: number
  peak: number
  attack?: number
  end: number
}

function tone(kit: Kit, sound: Shot, out: AudioNode, { type = 'sine', frequency, to, at, peak, attack = 0.004, end }: ToneOptions) {
  const oscillator = sound.play(kit.context.createOscillator(), at, end + 0.02)
  oscillator.type = type
  oscillator.frequency.setValueAtTime(frequency, at)
  if (to) oscillator.frequency.exponentialRampToValueAtTime(to, end)
  const level = gain(kit, sound, 0)
  oscillator.connect(level).connect(out)
  envelope(level.gain, at, peak, attack, end)
}

/** A short burst of low-passed noise; it fades out in a straight line, so it has body rather than a click. */
function thump(kit: Kit, sound: Shot, out: AudioNode, at: number, cutoff: number, peak: number, length: number) {
  const level = gain(kit, sound, 0)
  sound
    .noise('white', at, at + length + 0.01)
    .connect(filter(kit, sound, 'lowpass', cutoff, 0.7))
    .connect(level)
    .connect(out)
  level.gain.setValueAtTime(0, at)
  level.gain.linearRampToValueAtTime(peak, at + 0.002)
  level.gain.linearRampToValueAtTime(0, at + length)
}

function bell(kit: Kit, sound: Shot, out: AudioNode, frequency: number, at: number, peak: number, decay: number) {
  for (const [ratio, level, share] of BELL_PARTIALS) {
    tone(kit, sound, out, { frequency: frequency * ratio, at, peak: peak * level, attack: 0.003, end: at + decay * share })
  }
}

/** Brown noise through a low-pass swept 180–700 Hz by a 9–14 s LFO, with a 3 s level swell now and then. */
function wind(share: number): Layer {
  return (kit, bed, out) => {
    const lowpass = filter(kit, bed, 'lowpass', WIND_CUTOFF_HZ, 0.9)
    const gust = gain(kit, bed, 1)
    loop(kit, bed, 'brown').connect(lowpass).connect(gust).connect(gain(kit, bed, WIND_LEVEL * share)).connect(out)
    lfo(kit, bed, 1 / kit.between(9, 14), WIND_SWEEP_HZ, lowpass.frequency)
    bed.every([8, 20], (when) => {
      gust.gain.setValueAtTime(1, when)
      gust.gain.linearRampToValueAtTime(GUST_LEVEL, when + GUST_SECONDS)
      gust.gain.linearRampToValueAtTime(1, when + 2 * GUST_SECONDS)
    })
  }
}

/** Pink noise through a band-pass at 2.5 kHz drifting ±15 %, and 2–5 soft drip clicks a second. */
const rain: Layer = (kit, bed, out) => {
  const level = gain(kit, bed, RAIN_LEVEL)
  loop(kit, bed, 'pink').connect(filter(kit, bed, 'bandpass', 2500, 0.6)).connect(level).connect(out)
  lfo(kit, bed, kit.between(0.05, 0.09), RAIN_LEVEL * RAIN_DRIFT, level.gain)
  bed.every([0.2, 0.5], (when) =>
    bed.shot((sound) => {
      const end = when + kit.between(0.012, 0.03)
      const drip = sound.play(kit.context.createOscillator(), when, end + 0.01)
      drip.frequency.value = kit.between(2200, 4800)
      const dripLevel = gain(kit, sound, 0)
      drip.connect(dripLevel).connect(pan(kit, sound, kit.between(-0.8, 0.8))).connect(out)
      envelope(dripLevel.gain, when, DRIP_LEVEL * kit.between(0.4, 1), 0.002, end)
    }),
  )
}

/** Wind at 20 %, an 80 Hz rumble and fire crackle: 8–40 ms noise bursts at 1–4 kHz, 6–14 a second. */
const hearth: Layer = (kit, bed, out) => {
  wind(0.2)(kit, bed, out)
  loop(kit, bed, 'brown').connect(filter(kit, bed, 'lowpass', 80, 0.8)).connect(gain(kit, bed, RUMBLE_LEVEL)).connect(out)
  bed.every([1 / 14, 1 / 6], (when) =>
    bed.shot((sound) => {
      const end = when + kit.between(0.008, 0.04)
      const level = gain(kit, sound, 0)
      sound
        .noise('white', when, end + 0.01)
        .connect(filter(kit, sound, 'bandpass', kit.between(1000, 4000), 1.2))
        .connect(level)
        .connect(pan(kit, sound, kit.between(-0.35, 0.35)))
        .connect(out)
      envelope(level.gain, when, CRACKLE_LEVEL * kit.between(0.25, 1), 0.001, end)
    }),
  )
}

/** Wind at 15 % and two detuned 4.4 kHz cricket voices chirping three pulses at a time, 0.6–2.4 s apart. */
const night: Layer = (kit, bed, out) => {
  wind(0.15)(kit, bed, out)
  for (const [ratio, position] of CRICKET_VOICES) {
    bed.every([0.6, 2.4], (when) =>
      bed.shot((sound) => {
        const chirp = sound.play(kit.context.createOscillator(), when, when + 3 * CRICKET_PULSE_GAP_SECONDS + 0.02)
        chirp.frequency.value = CRICKET_HZ * ratio
        const level = gain(kit, sound, 0)
        chirp.connect(level).connect(pan(kit, sound, position)).connect(out)
        for (let pulse = 0; pulse < 3; pulse += 1) {
          const start = when + pulse * CRICKET_PULSE_GAP_SECONDS
          level.gain.setValueAtTime(0, start)
          level.gain.linearRampToValueAtTime(CRICKET_LEVEL, start + 0.004)
          level.gain.linearRampToValueAtTime(0, start + CRICKET_PULSE_SECONDS)
        }
      }),
    )
  }
}

/** Night, low bubbling blips (140–260 Hz, 60 ms, every 2–6 s) and a faint 6 kHz insect whine. */
const marsh: Layer = (kit, bed, out) => {
  night(kit, bed, out)
  bed.every([2, 6], (when) => {
    let start = when
    for (let blip = Math.floor(kit.between(1, 4)); blip > 0; blip -= 1) {
      const at = start
      bed.shot((sound) => {
        const frequency = kit.between(140, 260)
        tone(kit, sound, out, {
          frequency,
          to: frequency * 1.5,
          at,
          peak: BUBBLE_LEVEL * kit.between(0.5, 1),
          attack: 0.008,
          end: at + 0.06,
        })
      })
      start += kit.between(0.08, 0.14)
    }
  })
  const band = filter(kit, bed, 'bandpass', 6000, 14)
  loop(kit, bed, 'white').connect(band).connect(gain(kit, bed, WHINE_LEVEL)).connect(out)
  lfo(kit, bed, 0.21, 160, band.frequency)
}

/** A 55 Hz drone beating slowly, and drips (900–1600 Hz plucks, every 2–7 s) with a long feedback-delay tail. */
const cave: Layer = (kit, bed, out) => {
  const now = kit.context.currentTime
  const drone = gain(kit, bed, DRONE_LEVEL)
  drone.connect(out)
  for (const [frequency, share] of DRONE_PARTIALS) {
    const oscillator = bed.add(kit.context.createOscillator())
    oscillator.frequency.value = frequency
    oscillator.connect(gain(kit, bed, share)).connect(drone)
    oscillator.start(now)
  }
  const echo = bed.add(kit.context.createDelay(1))
  echo.delayTime.value = CAVE_ECHO_SECONDS
  const damp = filter(kit, bed, 'lowpass', 1800, 0.7)
  echo.connect(damp).connect(gain(kit, bed, CAVE_FEEDBACK)).connect(echo)
  damp.connect(gain(kit, bed, CAVE_ECHO_LEVEL)).connect(out)
  bed.every([2, 7], (when) =>
    bed.shot((sound) => {
      const frequency = kit.between(900, 1600)
      const drip = sound.play(kit.context.createOscillator(), when, when + 0.2)
      drip.frequency.setValueAtTime(frequency, when)
      drip.frequency.exponentialRampToValueAtTime(frequency * 1.25, when + 0.03)
      const level = gain(kit, sound, 0)
      drip.connect(level).connect(out)
      level.connect(echo)
      envelope(level.gain, when, CAVE_DRIP_LEVEL, 0.002, when + 0.18)
    }),
  )
}

const BEDS: Record<StoryBed, Layer> = {
  rain,
  wind: wind(1),
  hearth,
  night,
  marsh,
  cave,
  hush: wind(0.08),
}

const CUES: Record<StoryCue, Cue> = {
  fight_start: (kit, sound, out, at) => {
    for (const hit of [at, at + FIGHT_HIT_GAP_SECONDS]) {
      tone(kit, sound, out, { frequency: FIGHT_HIT_HZ[0], to: FIGHT_HIT_HZ[1], at: hit, peak: 0.45, end: hit + 0.18 })
      thump(kit, sound, out, hit, 1200, 1.8, 0.12)
    }
  },
  victory: (kit, sound, out, at) => {
    VICTORY_RATIOS.forEach((ratio, index) => {
      const start = at + index * VICTORY_NOTE_SECONDS
      tone(kit, sound, out, {
        type: 'triangle',
        frequency: VICTORY_ROOT_HZ * ratio,
        at: start,
        peak: 0.32,
        end: start + VICTORY_NOTE_SECONDS,
      })
    })
  },
  defeat: (kit, sound, out, at) => {
    const lowpass = filter(kit, sound, 'lowpass', 1500, 0.7)
    lowpass.connect(out)
    tone(kit, sound, lowpass, {
      type: 'triangle',
      frequency: DEFEAT_HZ[0],
      to: DEFEAT_HZ[1],
      at,
      peak: 0.4,
      attack: 0.04,
      end: at + 0.9,
    })
  },
  clue: (kit, sound, out, at) => bell(kit, sound, out, CLUE_HZ, at, 0.22, 1.2),
  level_up: (kit, sound, out, at) => {
    LEVEL_UP_HZ.forEach((frequency, index) => bell(kit, sound, out, frequency, at + index * LEVEL_UP_STEP_SECONDS, 0.15, 0.9))
  },
  nat20: (kit, sound, out, at) => {
    bell(kit, sound, out, CLUE_HZ * 1.5, at, 0.22, 1.4)
    tone(kit, sound, out, { frequency: CLUE_HZ * 3, at, peak: 0.05, attack: 0.003, end: at + 0.9 })
  },
  nat1: (kit, sound, out, at) => {
    tone(kit, sound, out, { frequency: 90, at, peak: 0.5, attack: 0.003, end: at + 0.12 })
    thump(kit, sound, out, at, 900, 1, 0.08)
  },
  dice_tick: (kit, sound, out, at) => {
    sound
      .noise('white', at, at + 0.003)
      .connect(filter(kit, sound, 'highpass', 1800, 0.7))
      .connect(gain(kit, sound, 0.1))
      .connect(out)
  },
  heartbeat: (kit, sound, out, at) => {
    for (const [offset, share] of HEARTBEAT_THUMPS) {
      const beat = at + offset
      tone(kit, sound, out, { frequency: 64, to: 44, at: beat, peak: 0.4 * share, end: beat + 0.14 })
      thump(kit, sound, out, beat, 700, share, 0.08)
    }
  },
}

type Slot = { bed: StoryBed; fader: GainNode; part: Part }
type Voice = { nodes: Nodes; out: GainNode }

export function createStoryAudio(context: AudioContext, { random = Math.random }: StoryAudioOptions = {}): StoryAudio {
  const buffers = new Map<NoiseColor, AudioBuffer>()
  const kit: Kit = {
    context,
    between: (min, max) => min + random() * (max - min),
    noise(color) {
      const buffer = buffers.get(color) ?? noiseBuffer(context, color, random)
      buffers.set(color, buffer)
      return buffer
    },
  }
  let status: 'idle' | 'starting' | 'running' | 'stopped' = 'idle'
  let levels: StoryAudioLevels = { ambience: 0, effects: 0 }
  let ducked = false
  let scene: StoryAmbience | null = null
  let heartbeat = false
  let heartbeatTimer: ReturnType<typeof setInterval> | null = null
  let buses: { ambience: GainNode; effects: GainNode } | null = null
  let bedSlot: Slot | null = null
  let rainSlot: Slot | null = null
  const fading = new Map<Slot, ReturnType<typeof setTimeout>>()
  const cueSounds = new Set<Nodes>()
  const voices = new Map<StoryCue, Voice>()

  const ramp = (param: AudioParam, value: number, seconds: number) => {
    const now = context.currentTime
    param.cancelScheduledValues(now)
    param.setValueAtTime(param.value, now)
    param.linearRampToValueAtTime(value, now + seconds)
  }

  const applyAmbienceLevel = (seconds: number) => {
    if (buses) ramp(buses.ambience.gain, levels.ambience * (ducked ? DUCKED_SHARE : 1), seconds)
  }

  const openSlot = (ambience: GainNode, bed: StoryBed, level: number): Slot => {
    const slot = { bed, part: part(kit) }
    const fader = gain(kit, slot.part, 0)
    fader.connect(ambience)
    try {
      BEDS[bed](kit, slot.part, fader)
    } catch (error) {
      slot.part.release()
      throw error
    }
    ramp(fader.gain, level, CROSS_FADE_SECONDS)
    return { ...slot, fader }
  }

  const disposeSlot = (slot: Slot) => {
    clearTimeout(fading.get(slot))
    fading.delete(slot)
    slot.part.release()
  }

  const closeSlot = (slot: Slot) => {
    ramp(slot.fader.gain, 0, CROSS_FADE_SECONDS)
    fading.set(slot, setTimeout(() => disposeSlot(slot), CROSS_FADE_SECONDS * 1000 + 100))
  }

  const applyScene = () => {
    if (!buses) return
    if (scene?.bed !== bedSlot?.bed) {
      if (bedSlot) closeSlot(bedSlot)
      bedSlot = scene ? openSlot(buses.ambience, scene.bed, 1) : null
    }
    if (Boolean(scene?.rain) !== Boolean(rainSlot)) {
      if (rainSlot) closeSlot(rainSlot)
      rainSlot = scene?.rain ? openSlot(buses.ambience, 'rain', RAIN_OVER_BED) : null
    }
  }

  const play = (cue: StoryCue) => {
    if (!buses || levels.effects <= 0) return
    const previous = voices.get(cue)
    if (previous) {
      ramp(previous.out.gain, 0, VOICE_FADE_SECONDS)
      previous.nodes.stopAt(context.currentTime + VOICE_FADE_SECONDS + 0.005)
    }
    const out = context.createGain()
    out.connect(buses.effects)
    const nodes = shot(
      kit,
      cueSounds,
      (sound) => {
        sound.add(out)
        CUES[cue](kit, sound, out, context.currentTime + LOOKAHEAD_SECONDS)
      },
      () => {
        if (voices.get(cue)?.nodes === nodes) voices.delete(cue)
      },
    )
    voices.set(cue, { nodes, out })
  }

  const applyHeartbeat = () => {
    if (buses && heartbeat) {
      if (heartbeatTimer !== null) return
      play('heartbeat')
      heartbeatTimer = setInterval(() => play('heartbeat'), HEARTBEAT_SECONDS * 1000)
    } else if (heartbeatTimer !== null) {
      clearInterval(heartbeatTimer)
      heartbeatTimer = null
    }
  }

  const stop = () => {
    status = 'stopped'
    heartbeat = false
    applyHeartbeat()
    fading.forEach((_, slot) => disposeSlot(slot))
    for (const slot of [bedSlot, rainSlot]) if (slot) disposeSlot(slot)
    bedSlot = null
    rainSlot = null
    cueSounds.forEach((sound) => sound.release())
    cueSounds.clear()
    voices.clear()
    buses?.ambience.disconnect()
    buses?.effects.disconnect()
    buses = null
  }

  /** Changes the running graph; any failure there leaves the sound silent instead of throwing. */
  const safely = (change: () => void) => {
    if (status !== 'running') return
    try {
      change()
    } catch {
      stop()
    }
  }

  return {
    async start() {
      if (status !== 'idle') return status === 'running'
      status = 'starting'
      try {
        await context.resume()
        if (status !== 'starting') return false
        const ambience = context.createGain()
        const effects = context.createGain()
        ambience.gain.value = 0
        effects.gain.value = levels.effects
        ambience.connect(context.destination)
        effects.connect(context.destination)
        buses = { ambience, effects }
        status = 'running'
        applyAmbienceLevel(CROSS_FADE_SECONDS)
        applyScene()
        applyHeartbeat()
        return true
      } catch {
        stop()
        return false
      }
    },
    setScene(next) {
      scene = next
      safely(applyScene)
    },
    setDucked(next) {
      if (next === ducked) return
      ducked = next
      safely(() => applyAmbienceLevel(ducked ? DUCK_SECONDS : UNDUCK_SECONDS))
    },
    setLevels(next) {
      levels = next
      safely(() => {
        applyAmbienceLevel(LEVEL_SECONDS)
        if (buses) ramp(buses.effects.gain, levels.effects, LEVEL_SECONDS)
      })
    },
    playCue(cue) {
      safely(() => play(cue))
    },
    setHeartbeat(on) {
      heartbeat = on
      safely(applyHeartbeat)
    },
    stop,
  }
}
