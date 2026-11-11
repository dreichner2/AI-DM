import { useEffect, useRef } from 'react'
import type { StoryWeather } from './audio/storyPlace'

type Particle = {
  x: number
  y: number
  /** Rain: streak length. Snow: flake radius. */
  size: number
  speed: number
  sway: number
  phase: number
  alpha: number
}

type FallingWeather = 'rain' | 'snow'

const COMPACT_WIDTH = 700
const PARTICLES: Record<FallingWeather, { desktop: number; compact: number }> = {
  rain: { desktop: 64, compact: 28 },
  snow: { desktop: 46, compact: 20 },
}
/** Rain leans with the wind: sideways pixels per pixel fallen. */
const RAIN_SLANT = 0.26
/** The brightest a particle gets; the CSS mask takes it to a fifth of that (6 %) over the reading column. */
const MAX_ALPHA = 0.3

function random(min: number, max: number) {
  return min + Math.random() * (max - min)
}

function spawn(weather: FallingWeather, width: number, height: number, initial: boolean): Particle {
  const rain = weather === 'rain'
  const size = rain ? random(14, 30) : random(0.8, 2.2)
  return {
    // Rain drifts sideways as it falls, so it starts across a wider band to fill the right edge.
    x: random(rain ? -height * RAIN_SLANT : 0, width),
    y: initial ? random(-size, height) : random(-height * 0.25, -size),
    size,
    speed: rain ? random(420, 640) : random(14, 34),
    sway: rain ? 0 : random(6, 18),
    phase: random(0, Math.PI * 2),
    alpha: random(0.45, 1) * MAX_ALPHA,
  }
}

/** Falling rain streaks or snow flakes on a canvas, drawn each frame until the weather changes. */
function FallingLayer({ weather }: { weather: FallingWeather }) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null)

  useEffect(() => {
    const canvas = canvasRef.current
    const context = canvas?.getContext('2d')
    if (!canvas || !context) return
    let width = 0
    let height = 0
    let particles: Particle[] = []
    let frame = 0
    let lastTime = 0
    let elapsed = 0

    const resize = () => {
      const dpr = Math.min(window.devicePixelRatio || 1, 2)
      width = window.innerWidth
      height = window.innerHeight
      canvas.width = Math.round(width * dpr)
      canvas.height = Math.round(height * dpr)
      canvas.style.width = `${width}px`
      canvas.style.height = `${height}px`
      context.setTransform(dpr, 0, 0, dpr, 0, 0)
      const { desktop, compact } = PARTICLES[weather]
      const count = width < COMPACT_WIDTH ? compact : desktop
      particles = particles.slice(0, count)
      while (particles.length < count) particles.push(spawn(weather, width, height, true))
    }

    const draw = (time: number) => {
      const dt = lastTime ? Math.min((time - lastTime) / 1000, 0.05) : 0
      lastTime = time
      elapsed += dt
      context.clearRect(0, 0, width, height)
      particles.forEach((particle, index) => {
        particle.y += particle.speed * dt
        if (weather === 'rain') particle.x += particle.speed * RAIN_SLANT * dt
        if (particle.y - particle.size > height) {
          particles[index] = spawn(weather, width, height, false)
          return
        }
        if (weather === 'rain') {
          context.strokeStyle = `rgba(200, 214, 226, ${particle.alpha})`
          context.lineWidth = 1
          context.beginPath()
          context.moveTo(particle.x, particle.y)
          context.lineTo(particle.x - particle.size * RAIN_SLANT, particle.y - particle.size)
          context.stroke()
        } else {
          const x = particle.x + Math.sin(particle.phase + elapsed * 0.6) * particle.sway
          context.fillStyle = `rgba(238, 242, 246, ${particle.alpha})`
          context.beginPath()
          context.arc(x, particle.y, particle.size, 0, Math.PI * 2)
          context.fill()
        }
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
  }, [weather])

  return <canvas ref={canvasRef} className="story-weather" data-weather={weather} />
}

/**
 * The place's weather behind the story: thin slanting rain, slow snow, or two
 * wide fog bands drifting sideways. Loaded on demand by StoryAtmosphere, and
 * mounted only when motion is allowed and the weather is not clear.
 */
export function StoryAtmosphereWeather({ weather }: { weather: Exclude<StoryWeather, 'clear'> }) {
  if (weather === 'fog') {
    return (
      <div className="story-weather" data-weather="fog">
        <span className="story-fog-band" />
        <span className="story-fog-band" />
      </div>
    )
  }
  return <FallingLayer key={weather} weather={weather} />
}
