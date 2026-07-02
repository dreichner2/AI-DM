export type SceneMusicTag =
  | 'calm'
  | 'combat'
  | 'discovery'
  | 'dungeon'
  | 'forest'
  | 'mystery'
  | 'tension'
  | 'town'
  | 'travel'

export type SceneMusicTrack = {
  id: string
  title: string
  artist: string
  src: string
  sourceUrl: string
  license: 'CC0-1.0' | 'user-provided'
  attribution: string
  tags: SceneMusicTag[]
  durationLabel: string
  loop: boolean
}

export const SCENE_MUSIC_TAGS: Array<{ id: SceneMusicTag; label: string }> = [
  { id: 'calm', label: 'Calm' },
  { id: 'combat', label: 'Combat' },
  { id: 'tension', label: 'Tension' },
  { id: 'travel', label: 'Travel' },
  { id: 'discovery', label: 'Discovery' },
  { id: 'dungeon', label: 'Dungeon' },
  { id: 'forest', label: 'Forest' },
  { id: 'town', label: 'Town' },
  { id: 'mystery', label: 'Mystery' },
]

export function isSceneMusicTag(value: unknown): value is SceneMusicTag {
  return typeof value === 'string' && SCENE_MUSIC_TAGS.some((tag) => tag.id === value)
}

export const SCENE_MUSIC_TRACKS: SceneMusicTrack[] = [
  {
    id: 'dnd-calm-fantasy-adventure-exploration',
    title: 'DnD Calm Fantasy Music for Adventure and Exploration',
    artist: 'Everrune',
    src: '/music/dnd-calm-fantasy-adventure-exploration.mp3',
    sourceUrl: 'https://youtu.be/sHA_4wfQhE8',
    license: 'user-provided',
    attribution: 'User-provided MP3 from Everrune.',
    tags: ['calm', 'combat', 'discovery', 'dungeon', 'forest', 'mystery', 'tension', 'town', 'travel'],
    durationLabel: '3:04:55',
    loop: true,
  },
]
