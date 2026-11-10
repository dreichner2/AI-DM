import { useEffect, useRef, useState } from 'react'
import type { StoryMood } from './storyMood'

type StoryAtmosphereProps = {
  mood: StoryMood
  /** The viewer's current HP; a drop flashes the screen edge red. */
  viewerHp: number | null
}

type Mote = {
  x: number
  y: number
  radius: number
  rise: number
  sway: number
  swayRate: number
  phase: number
  life: number
  age: number
  brightness: number
}

const DESKTOP_MOTES = 36
const COMPACT_MOTES = 16
const COMPACT_WIDTH = 700
const FALLBACK_HUE = 22

function random(min: number, max: number) {
  return min + Math.random() * (max - min)
}

function spawnMote(width: number, height: number, initial: boolean): Mote {
  const life = random(6, 14)
  return {
    x: random(0, width),
    // Motes are born low and drift up; on first paint they are already spread out.
    y: initial ? random(height * 0.15, height) : random(height * 0.55, height + 12),
    radius: random(0.6, 1.8),
    rise: random(6, 16),
    sway: random(4, 16),
    swayRate: random(0.15, 0.45),
    phase: random(0, Math.PI * 2),
    life,
    age: initial ? random(0, life) : 0,
    brightness: random(0.35, 0.85),
  }
}

/** Hue of a computed colour such as `rgba(255, 107, 44, 0.16)` or `#ff6b2c`. */
function hueOf(color: string) {
  let channels: number[]
  const hex = /^#([0-9a-f]{6})/i.exec(color.trim())
  if (hex) {
    const value = Number.parseInt(hex[1], 16)
    channels = [(value >> 16) & 255, (value >> 8) & 255, value & 255]
  } else {
    channels = (color.match(/-?\d*\.?\d+/g) ?? []).slice(0, 3).map(Number)
  }
  if (channels.length < 3) return FALLBACK_HUE
  const [r, g, b] = channels.map((channel) => channel / 255)
  const max = Math.max(r, g, b)
  const min = Math.min(r, g, b)
  if (max === min) return FALLBACK_HUE
  const delta = max - min
  const hue = max === r ? ((g - b) / delta) % 6 : max === g ? (b - r) / delta + 2 : (r - g) / delta + 4
  return (hue * 60 + 360) % 360
}

/**
 * The living background: mood light rising from below, a vignette, film grain
 * and a slow drift of embers. Purely decorative.
 */
export function StoryAtmosphere({ mood, viewerHp }: StoryAtmosphereProps) {
  const rootRef = useRef<HTMLDivElement | null>(null)
  const canvasRef = useRef<HTMLCanvasElement | null>(null)
  const moodRef = useRef(mood)
  const [hpSeen, setHpSeen] = useState(viewerHp)
  const [hurtCount, setHurtCount] = useState(0)

  if (hpSeen !== viewerHp) {
    setHpSeen(viewerHp)
    if (hpSeen !== null && viewerHp !== null && viewerHp < hpSeen) setHurtCount((count) => count + 1)
  }

  useEffect(() => {
    moodRef.current = mood
  }, [mood])

  useEffect(() => {
    const canvas = canvasRef.current
    const root = rootRef.current
    if (!canvas || !root || typeof window.matchMedia !== 'function') return
    if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) return
    const context = canvas.getContext('2d')
    if (!context) return

    let width = 0
    let height = 0
    let motes: Mote[] = []
    let frame = 0
    let lastTime = 0
    let hue = FALLBACK_HUE
    let framesSinceColour = Infinity

    const resize = () => {
      const dpr = Math.min(window.devicePixelRatio || 1, 2)
      width = window.innerWidth
      height = window.innerHeight
      canvas.width = Math.round(width * dpr)
      canvas.height = Math.round(height * dpr)
      canvas.style.width = `${width}px`
      canvas.style.height = `${height}px`
      context.setTransform(dpr, 0, 0, dpr, 0, 0)
      const count = width < COMPACT_WIDTH ? COMPACT_MOTES : DESKTOP_MOTES
      motes = motes.slice(0, count)
      while (motes.length < count) motes.push(spawnMote(width, height, true))
    }

    const draw = (time: number) => {
      const dt = lastTime ? Math.min((time - lastTime) / 1000, 0.05) : 0
      lastTime = time
      if (framesSinceColour > 20) {
        // Read the (transitioning) mood colour a few times a second, not every frame.
        hue = hueOf(getComputedStyle(root).getPropertyValue('--mood-ember'))
        framesSinceColour = 0
      }
      framesSinceColour += 1
      const combat = moodRef.current === 'combat'
      const speed = combat ? 2.4 : 1
      const moteHue = combat ? 6 : hue

      context.clearRect(0, 0, width, height)
      context.globalCompositeOperation = 'lighter'
      motes.forEach((mote, index) => {
        mote.age += dt * (combat ? 1.4 : 1)
        if (mote.age >= mote.life || mote.y < -12) {
          motes[index] = spawnMote(width, height, false)
          return
        }
        mote.y -= mote.rise * speed * dt
        const x = mote.x + Math.sin(mote.phase + mote.age * mote.swayRate * Math.PI * 2) * mote.sway
        const alpha = Math.sin((mote.age / mote.life) * Math.PI) * mote.brightness
        const halo = context.createRadialGradient(x, mote.y, 0, x, mote.y, mote.radius * 4)
        halo.addColorStop(0, `hsla(${moteHue}, 95%, 72%, ${alpha})`)
        halo.addColorStop(0.35, `hsla(${moteHue}, 95%, 60%, ${alpha * 0.35})`)
        halo.addColorStop(1, `hsla(${moteHue}, 95%, 55%, 0)`)
        context.fillStyle = halo
        context.beginPath()
        context.arc(x, mote.y, mote.radius * 4, 0, Math.PI * 2)
        context.fill()
      })
      frame = requestAnimationFrame(draw)
    }

    const start = () => {
      if (frame || document.hidden) return
      lastTime = 0
      frame = requestAnimationFrame(draw)
    }
    const stop = () => {
      cancelAnimationFrame(frame)
      frame = 0
    }
    const onVisibility = () => (document.hidden ? stop() : start())

    resize()
    start()
    window.addEventListener('resize', resize)
    document.addEventListener('visibilitychange', onVisibility)
    return () => {
      stop()
      window.removeEventListener('resize', resize)
      document.removeEventListener('visibilitychange', onVisibility)
    }
  }, [])

  return (
    <div ref={rootRef} className="story-atmosphere" aria-hidden="true">
      <div className="story-atmosphere-glow" />
      <div className="story-atmosphere-vignette" />
      <div className="story-atmosphere-grain" />
      <canvas ref={canvasRef} className="story-atmosphere-motes" />
      {hurtCount ? <div key={hurtCount} className="story-hurt-flash" /> : null}
    </div>
  )
}
