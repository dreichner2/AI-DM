import { isRecord, stringValue, worldMapFromSnapshot } from '../../gameSelectors'

/** The generated sound beds (see storyAmbience). */
export const STORY_BEDS = ['rain', 'wind', 'hearth', 'night', 'marsh', 'cave', 'hush'] as const
export type StoryBed = (typeof STORY_BEDS)[number]

export const STORY_WEATHERS = ['rain', 'fog', 'snow', 'clear'] as const
export type StoryWeather = (typeof STORY_WEATHERS)[number]

/** What the party's place sounds and looks like: its map icon and its pack's `ambience` and `weather`. */
export type StoryPlace = {
  icon: string
  ambience: StoryBed | null
  weather: StoryWeather | null
}

/** The bed to play and whether rain plays over it. */
export type StoryAmbience = {
  bed: StoryBed
  rain: boolean
}

const DARK_PHASES = new Set(['evening', 'night'])

function oneOf<T extends string>(values: readonly T[], value: unknown): T | null {
  return values.find((candidate) => candidate === stringValue(value)) ?? null
}

/** The party's place in a player snapshot: the current map node's icon and the current location's fields. */
export function storyPlace(snapshot: unknown): StoryPlace {
  const root = isRecord(snapshot) ? snapshot : {}
  const scene = isRecord(root.currentScene) ? root.currentScene : {}
  const locationId = stringValue(scene.locationId)
  const locations = Array.isArray(root.locations) ? root.locations.filter(isRecord) : []
  const location = (locationId && locations.find((record) => stringValue(record.id) === locationId)) || {}
  const node = worldMapFromSnapshot(snapshot)?.nodes.find((candidate) => candidate.state === 'current')
  return {
    icon: node?.icon ?? 'unknown',
    ambience: oneOf(STORY_BEDS, location.ambience),
    weather: oneOf(STORY_WEATHERS, location.weather),
  }
}

function bedForIcon(icon: string, phase: string | undefined): StoryBed {
  switch (icon) {
    case 'inn':
      return 'hearth'
    case 'cave':
      return 'cave'
    case 'marsh':
      return 'marsh'
    case 'forest':
    case 'camp':
    case 'valley':
      return phase && DARK_PHASES.has(phase) ? 'night' : 'wind'
    default:
      return 'wind'
  }
}

/**
 * The bed for a place: its pack `ambience`, else one from its map icon and the
 * clock. A fight hushes every place so the cues read; rain weather plays the
 * rain bed over whatever bed is chosen.
 */
export function chooseAmbience(place: StoryPlace, phase: string | undefined, inFight: boolean): StoryAmbience {
  const bed = inFight ? 'hush' : (place.ambience ?? bedForIcon(place.icon, phase))
  return { bed, rain: place.weather === 'rain' && bed !== 'rain' }
}
