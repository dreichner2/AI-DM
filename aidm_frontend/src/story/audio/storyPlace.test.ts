import { describe, expect, it } from 'vitest'
import { chooseAmbience, storyPlace, STORY_BEDS, type StoryBed, type StoryWeather } from './storyPlace'

const ICONS = [
  'inn',
  'town',
  'road',
  'bridge',
  'ruin',
  'tower',
  'forest',
  'cave',
  'camp',
  'shrine',
  'cairn',
  'valley',
  'gate',
  'marsh',
  'peak',
  'unknown',
]
const PHASES = ['morning', 'afternoon', 'evening', 'night', undefined]
const WEATHERS: Array<StoryWeather | null> = ['rain', 'fog', 'snow', 'clear', null]

/** The spec's table, written out independently of the code. */
function expectedBed(icon: string, phase: string | undefined): StoryBed {
  const dark = phase === 'evening' || phase === 'night'
  if (icon === 'inn') return 'hearth'
  if (icon === 'cave') return 'cave'
  if (icon === 'marsh') return 'marsh'
  if (['forest', 'camp', 'valley'].includes(icon)) return dark ? 'night' : 'wind'
  return 'wind'
}

function snapshot(location: Record<string, unknown>, icon = 'road') {
  return {
    currentScene: { locationId: 'here' },
    locations: [{ id: 'elsewhere', ambience: 'cave', weather: 'snow' }, { id: 'here', ...location }],
    worldMap: {
      nodes: [
        { id: 'elsewhere', name: 'Elsewhere', icon: 'cave', x: 10, y: 10, state: 'visited' },
        { id: 'here', name: 'Here', icon, x: 50, y: 50, state: 'current' },
      ],
      routes: [],
    },
  }
}

describe('chooseAmbience', () => {
  it('follows the map icon and the clock for every place, hushes every fight and adds rain for rain weather', () => {
    for (const icon of ICONS) {
      for (const phase of PHASES) {
        for (const weather of WEATHERS) {
          for (const inFight of [false, true]) {
            const bed = inFight ? 'hush' : expectedBed(icon, phase)
            expect(chooseAmbience({ icon, ambience: null, weather }, phase, inFight), `${icon} ${phase} ${weather} ${inFight}`).toEqual({
              bed,
              rain: weather === 'rain',
            })
          }
        }
      }
    }
  })

  it("plays the pack's ambience wherever it is set, except in a fight, and never rains over the rain bed", () => {
    for (const ambience of STORY_BEDS) {
      for (const weather of WEATHERS) {
        expect(chooseAmbience({ icon: 'inn', ambience, weather }, 'night', false)).toEqual({
          bed: ambience,
          rain: weather === 'rain' && ambience !== 'rain',
        })
        expect(chooseAmbience({ icon: 'inn', ambience, weather }, 'night', true)).toEqual({ bed: 'hush', rain: weather === 'rain' })
      }
    }
  })
})

describe('storyPlace', () => {
  it("reads the current map node's icon and the current location's ambience and weather", () => {
    expect(storyPlace(snapshot({ ambience: 'marsh', weather: 'fog' }, 'bridge'))).toEqual({
      icon: 'bridge',
      ambience: 'marsh',
      weather: 'fog',
    })
  })

  it('ignores values it does not know and reads a place without a map as unknown', () => {
    expect(storyPlace(snapshot({ ambience: 'thunder', weather: 'Rain' }))).toEqual({ icon: 'road', ambience: null, weather: null })
    expect(storyPlace({ currentScene: { locationId: 'here' }, locations: [{ id: 'here', weather: 'snow' }] })).toEqual({
      icon: 'unknown',
      ambience: null,
      weather: 'snow',
    })
    expect(storyPlace(null)).toEqual({ icon: 'unknown', ambience: null, weather: null })
  })
})
