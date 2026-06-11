export type SceneMusicTag =
  | 'calm'
  | 'discovery'
  | 'forest'
  | 'mystery'
  | 'town'
  | 'travel'

export type SceneMusicTrack = {
  id: string
  title: string
  artist: string
  src: string
  sourceUrl: string
  license: 'CC0-1.0' | 'user-provided'
  tags: SceneMusicTag[]
  durationLabel: string
}

export const SCENE_MUSIC_TAGS: Array<{ id: SceneMusicTag; label: string }> = [
  { id: 'calm', label: 'Calm' },
  { id: 'travel', label: 'Travel' },
  { id: 'discovery', label: 'Discovery' },
  { id: 'forest', label: 'Forest' },
  { id: 'town', label: 'Town' },
  { id: 'mystery', label: 'Mystery' },
]

export const SCENE_MUSIC_TRACKS: SceneMusicTrack[] = [
  {
    id: 'dnd-calm-fantasy-adventure-exploration',
    title: 'DnD Calm Fantasy Music for Adventure and Exploration',
    artist: 'Everrune',
    src: '/music/dnd-calm-fantasy-adventure-exploration.mp3',
    sourceUrl: 'https://youtu.be/sHA_4wfQhE8',
    license: 'user-provided',
    tags: ['calm', 'travel', 'discovery', 'forest', 'town', 'mystery'],
    durationLabel: '3:04:55',
  },
]
