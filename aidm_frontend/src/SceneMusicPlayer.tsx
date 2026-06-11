import { Grip, Maximize2, Music, Pause, Play, Rewind, SkipBack, SkipForward, Tags, Volume1 } from 'lucide-react'
import { useCallback, useEffect, useMemo, useRef, useState, type PointerEvent as ReactPointerEvent } from 'react'
import {
  SCENE_MUSIC_TAGS,
  SCENE_MUSIC_TRACKS,
  type SceneMusicTag,
  type SceneMusicTrack,
} from './musicLibrary'

type MusicFilter = SceneMusicTag | 'all'

type StoredMusicPreferences = {
  selectedTag?: MusicFilter
  trackId?: string
  volume?: number
}

type MusicPlayerLayout = {
  left: number
  top: number
  width: number
  height: number
}

type MusicPanelDragState = {
  mode: 'move' | 'resize'
  startX: number
  startY: number
  startLayout: MusicPlayerLayout
}

type MusicPanelMode = 'full' | 'transport' | 'micro'

export type SceneMusicPlaybackStatus = 'playing' | 'paused'

export type SceneMusicSyncState = {
  sessionId: number
  trackId: string
  status: SceneMusicPlaybackStatus
  position: number
  updatedAtMs: number
  receivedAtMs?: number
  updatedByPlayerId?: number | null
}

export type SceneMusicControlPayload = {
  trackId: string
  status: SceneMusicPlaybackStatus
  position: number
}

type SceneMusicPlayerProps = {
  sessionId?: number | null
  playerId?: number | null
  duckForNarration?: boolean
  musicSyncState?: SceneMusicSyncState | null
  onMusicControl?: (payload: SceneMusicControlPayload) => void
}

const MUSIC_PREFS_STORAGE_KEY = 'aidm:sceneMusicPreferences'
const MUSIC_LAYOUT_STORAGE_KEY = 'aidm:sceneMusicLayout'
const REWIND_SECONDS = 15
const MUSIC_SYNC_HEARTBEAT_MS = 10000
const MUSIC_MAX_POSITION_SECONDS = 24 * 60 * 60
const MUSIC_PANEL_MARGIN = 12
const DEFAULT_MUSIC_PANEL_WIDTH = 560
const DEFAULT_MUSIC_PANEL_HEIGHT = 178
const MIN_MUSIC_PANEL_WIDTH = 72
const MIN_MUSIC_PANEL_HEIGHT = 54
const TRANSPORT_MUSIC_PANEL_WIDTH = 430
const TRANSPORT_MUSIC_PANEL_HEIGHT = 122
const MICRO_MUSIC_PANEL_WIDTH = 148
const MICRO_MUSIC_PANEL_HEIGHT = 78
const TTS_MUSIC_DUCK_VOLUME = 0.22

function isMusicFilter(value: unknown): value is MusicFilter {
  return value === 'all' || SCENE_MUSIC_TAGS.some((tag) => tag.id === value)
}

function clampVolume(value: unknown) {
  const numericValue = typeof value === 'number' ? value : Number(value)
  if (!Number.isFinite(numericValue)) return 0.7
  return Math.min(1, Math.max(0, numericValue))
}

function loadMusicPreferences(): StoredMusicPreferences {
  try {
    const rawValue = localStorage.getItem(MUSIC_PREFS_STORAGE_KEY)
    if (!rawValue) return {}
    const parsed = JSON.parse(rawValue) as StoredMusicPreferences
    return {
      selectedTag: isMusicFilter(parsed.selectedTag) ? parsed.selectedTag : 'all',
      trackId: SCENE_MUSIC_TRACKS.some((track) => track.id === parsed.trackId)
        ? parsed.trackId
        : undefined,
      volume: clampVolume(parsed.volume),
    }
  } catch {
    return {}
  }
}

function saveMusicPreferences(preferences: StoredMusicPreferences) {
  try {
    localStorage.setItem(MUSIC_PREFS_STORAGE_KEY, JSON.stringify(preferences))
  } catch {
    // Music controls still work for the current page when storage is unavailable.
  }
}

function viewportSize() {
  if (typeof window === 'undefined') {
    return { width: 1280, height: 800 }
  }
  return {
    width: window.innerWidth || 1280,
    height: window.innerHeight || 800,
  }
}

function clampNumber(value: number, min: number, max: number) {
  if (!Number.isFinite(value)) return min
  return Math.min(max, Math.max(min, value))
}

function defaultMusicLayout(): MusicPlayerLayout {
  const viewport = viewportSize()
  const width = Math.min(DEFAULT_MUSIC_PANEL_WIDTH, Math.max(MIN_MUSIC_PANEL_WIDTH, viewport.width - MUSIC_PANEL_MARGIN * 2))
  const height = Math.min(DEFAULT_MUSIC_PANEL_HEIGHT, Math.max(MIN_MUSIC_PANEL_HEIGHT, viewport.height - MUSIC_PANEL_MARGIN * 2))
  return {
    left: Math.max(MUSIC_PANEL_MARGIN, viewport.width - width - 18),
    top: Math.min(118, Math.max(MUSIC_PANEL_MARGIN, viewport.height - height - MUSIC_PANEL_MARGIN)),
    width,
    height,
  }
}

function clampMusicLayout(layout: MusicPlayerLayout): MusicPlayerLayout {
  const viewport = viewportSize()
  const maxWidth = Math.max(MIN_MUSIC_PANEL_WIDTH, viewport.width - MUSIC_PANEL_MARGIN * 2)
  const maxHeight = Math.max(MIN_MUSIC_PANEL_HEIGHT, viewport.height - MUSIC_PANEL_MARGIN * 2)
  const width = clampNumber(layout.width, MIN_MUSIC_PANEL_WIDTH, maxWidth)
  const height = clampNumber(layout.height, MIN_MUSIC_PANEL_HEIGHT, maxHeight)
  return {
    left: clampNumber(layout.left, MUSIC_PANEL_MARGIN, Math.max(MUSIC_PANEL_MARGIN, viewport.width - width - MUSIC_PANEL_MARGIN)),
    top: clampNumber(layout.top, MUSIC_PANEL_MARGIN, Math.max(MUSIC_PANEL_MARGIN, viewport.height - height - MUSIC_PANEL_MARGIN)),
    width,
    height,
  }
}

function musicPanelMode(layout: MusicPlayerLayout): MusicPanelMode {
  if (layout.width <= MICRO_MUSIC_PANEL_WIDTH || layout.height <= MICRO_MUSIC_PANEL_HEIGHT) {
    return 'micro'
  }
  if (layout.width <= TRANSPORT_MUSIC_PANEL_WIDTH || layout.height <= TRANSPORT_MUSIC_PANEL_HEIGHT) {
    return 'transport'
  }
  return 'full'
}

function loadMusicLayout(): MusicPlayerLayout {
  try {
    const rawValue = localStorage.getItem(MUSIC_LAYOUT_STORAGE_KEY)
    if (!rawValue) return defaultMusicLayout()
    const parsed = JSON.parse(rawValue) as Partial<MusicPlayerLayout>
    return clampMusicLayout({
      left: Number(parsed.left),
      top: Number(parsed.top),
      width: Number(parsed.width),
      height: Number(parsed.height),
    })
  } catch {
    return defaultMusicLayout()
  }
}

function saveMusicLayout(layout: MusicPlayerLayout) {
  try {
    localStorage.setItem(MUSIC_LAYOUT_STORAGE_KEY, JSON.stringify(layout))
  } catch {
    // Layout dragging still works for the current page when storage is unavailable.
  }
}

function formatSeconds(value: number) {
  if (!Number.isFinite(value) || value <= 0) return '0:00'
  const totalSeconds = Math.floor(value)
  const hours = Math.floor(totalSeconds / 3600)
  const minutes = Math.floor((totalSeconds % 3600) / 60)
  const seconds = totalSeconds % 60
  if (hours > 0) {
    return `${hours}:${String(minutes).padStart(2, '0')}:${String(seconds).padStart(2, '0')}`
  }
  return `${minutes}:${String(seconds).padStart(2, '0')}`
}

function tagsLabel(track: SceneMusicTrack) {
  return track.tags
    .map((tagId) => SCENE_MUSIC_TAGS.find((tag) => tag.id === tagId)?.label ?? tagId)
    .join(' / ')
}

function tracksForFilter(filter: MusicFilter) {
  return filter === 'all'
    ? SCENE_MUSIC_TRACKS
    : SCENE_MUSIC_TRACKS.filter((track) => track.tags.includes(filter))
}

function initialMusicState() {
  const storedPreferences = loadMusicPreferences()
  const selectedTag = storedPreferences.selectedTag ?? 'all'
  const filteredTracks = tracksForFilter(selectedTag)
  const trackId =
    storedPreferences.trackId && filteredTracks.some((track) => track.id === storedPreferences.trackId)
      ? storedPreferences.trackId
      : filteredTracks[0]?.id ?? SCENE_MUSIC_TRACKS[0].id
  return {
    selectedTag,
    trackId,
    volume: storedPreferences.volume ?? 0.7,
  }
}

export function SceneMusicPlayer({
  sessionId = null,
  playerId = null,
  duckForNarration = false,
  musicSyncState = null,
  onMusicControl,
}: SceneMusicPlayerProps) {
  const audioRef = useRef<HTMLAudioElement | null>(null)
  const dragStateRef = useRef<MusicPanelDragState | null>(null)
  const pendingRemoteSyncRef = useRef<SceneMusicSyncState | null>(null)
  const lastAppliedSyncRef = useRef<number | null>(null)
  const [storedPreferences] = useState(() => initialMusicState())
  const [panelLayout, setPanelLayout] = useState(() => loadMusicLayout())
  const [isMovingPanel, setIsMovingPanel] = useState(false)
  const [selectedTag, setSelectedTag] = useState<MusicFilter>(storedPreferences.selectedTag)
  const [currentTrackId, setCurrentTrackId] = useState(storedPreferences.trackId)
  const [isPlaying, setIsPlaying] = useState(false)
  const [playbackError, setPlaybackError] = useState('')
  const [currentTime, setCurrentTime] = useState(0)
  const [duration, setDuration] = useState(0)
  const [volume, setVolume] = useState(storedPreferences.volume)
  const effectiveVolume = duckForNarration ? Math.min(volume, TTS_MUSIC_DUCK_VOLUME) : volume

  const filteredTracks = useMemo(() => tracksForFilter(selectedTag), [selectedTag])
  const currentTrack =
    SCENE_MUSIC_TRACKS.find((track) => track.id === currentTrackId) ?? SCENE_MUSIC_TRACKS[0]
  const syncEnabled = Boolean(sessionId && playerId && onMusicControl)

  useEffect(() => {
    saveMusicPreferences({ selectedTag, trackId: currentTrackId, volume })
  }, [currentTrackId, selectedTag, volume])

  useEffect(() => {
    saveMusicLayout(panelLayout)
  }, [panelLayout])

  useEffect(() => {
    const handleWindowResize = () => {
      setPanelLayout((current) => clampMusicLayout(current))
    }
    window.addEventListener('resize', handleWindowResize)
    return () => window.removeEventListener('resize', handleWindowResize)
  }, [])

  useEffect(() => {
    const audio = audioRef.current
    if (!audio) return
    audio.volume = effectiveVolume
  }, [effectiveVolume])

  useEffect(() => {
    if (!isMovingPanel) return

    const handlePointerMove = (event: PointerEvent) => {
      const dragState = dragStateRef.current
      if (!dragState) return
      const deltaX = event.clientX - dragState.startX
      const deltaY = event.clientY - dragState.startY
      setPanelLayout(
        clampMusicLayout(
          dragState.mode === 'move'
            ? {
                ...dragState.startLayout,
                left: dragState.startLayout.left + deltaX,
                top: dragState.startLayout.top + deltaY,
              }
            : {
                ...dragState.startLayout,
                width: dragState.startLayout.width + deltaX,
                height: dragState.startLayout.height + deltaY,
              },
        ),
      )
    }

    const handlePointerUp = () => {
      dragStateRef.current = null
      setIsMovingPanel(false)
    }

    window.addEventListener('pointermove', handlePointerMove)
    window.addEventListener('pointerup', handlePointerUp, { once: true })
    return () => {
      window.removeEventListener('pointermove', handlePointerMove)
      window.removeEventListener('pointerup', handlePointerUp)
    }
  }, [isMovingPanel])

  const startPanelDrag = useCallback(
    (mode: MusicPanelDragState['mode'], event: ReactPointerEvent<HTMLElement>) => {
      if (event.button !== 0) return
      event.preventDefault()
      dragStateRef.current = {
        mode,
        startX: event.clientX,
        startY: event.clientY,
        startLayout: panelLayout,
      }
      setIsMovingPanel(true)
    },
    [panelLayout],
  )

  const resetTrackProgress = useCallback(() => {
    setCurrentTime(0)
    setDuration(0)
    setPlaybackError('')
  }, [])

  const broadcastMusicControl = useCallback(
    (trackId: string, status: SceneMusicPlaybackStatus, position: number) => {
      if (!syncEnabled || !onMusicControl) return
      const safePosition = Math.min(MUSIC_MAX_POSITION_SECONDS, Math.max(0, Number.isFinite(position) ? position : 0))
      onMusicControl({
        trackId,
        status,
        position: safePosition,
      })
    },
    [onMusicControl, syncEnabled],
  )

  const syncedTrackPosition = useCallback((syncState: SceneMusicSyncState, maxPosition: number) => {
    const elapsedSeconds =
      syncState.status === 'playing'
        ? Math.max(0, (Date.now() - (syncState.receivedAtMs ?? syncState.updatedAtMs)) / 1000)
        : 0
    const upperBound = Number.isFinite(maxPosition) && maxPosition > 0 ? maxPosition : MUSIC_MAX_POSITION_SECONDS
    return Math.min(upperBound, Math.max(0, syncState.position + elapsedSeconds))
  }, [])

  const applySyncToCurrentTrack = useCallback(
    (syncState: SceneMusicSyncState) => {
      const audio = audioRef.current
      const nextTime = syncedTrackPosition(syncState, audio?.duration ?? duration)
      if (audio) {
        try {
          audio.currentTime = nextTime
        } catch {
          // Some browsers reject currentTime updates before metadata is ready; loadedmetadata applies pending sync.
        }
      }
      setCurrentTime(nextTime)
      if (syncState.status === 'playing') {
        setIsPlaying(true)
        if (audio) {
          void audio.play().then(
            () => setPlaybackError(''),
            () => {
              setPlaybackError('Group music is playing. Join audio to hear it here.')
            },
          )
        }
        return
      }
      audio?.pause()
      setIsPlaying(false)
    },
    [duration, syncedTrackPosition],
  )

  useEffect(() => {
    const audio = audioRef.current
    if (!audio) return
    if (!isPlaying) return
    void audio.play().then(
      () => setPlaybackError(''),
      () => {
        setPlaybackError('Group music is playing. Join audio to hear it here.')
      },
    )
  }, [currentTrack.src, isPlaying])

  useEffect(() => {
    if (!musicSyncState || musicSyncState.sessionId !== sessionId) return
    if (!SCENE_MUSIC_TRACKS.some((track) => track.id === musicSyncState.trackId)) return
    if (lastAppliedSyncRef.current !== null && musicSyncState.updatedAtMs < lastAppliedSyncRef.current) return

    lastAppliedSyncRef.current = musicSyncState.updatedAtMs
    const syncTimer = window.setTimeout(() => {
      setSelectedTag((currentFilter) =>
        tracksForFilter(currentFilter).some((track) => track.id === musicSyncState.trackId)
          ? currentFilter
          : 'all',
      )
      setPlaybackError('')

      if (musicSyncState.trackId !== currentTrackId) {
        pendingRemoteSyncRef.current = musicSyncState
        setCurrentTrackId(musicSyncState.trackId)
        setIsPlaying(musicSyncState.status === 'playing')
        setCurrentTime(syncedTrackPosition(musicSyncState, duration))
        return
      }

      pendingRemoteSyncRef.current = null
      applySyncToCurrentTrack(musicSyncState)
    }, 0)
    return () => window.clearTimeout(syncTimer)
  }, [
    applySyncToCurrentTrack,
    currentTrackId,
    duration,
    musicSyncState,
    sessionId,
    syncedTrackPosition,
  ])

  useEffect(() => {
    const pendingSync = pendingRemoteSyncRef.current
    const audio = audioRef.current
    if (!pendingSync || pendingSync.trackId !== currentTrack.id || !audio || audio.readyState < 1) return
    pendingRemoteSyncRef.current = null
    applySyncToCurrentTrack(pendingSync)
  }, [applySyncToCurrentTrack, currentTrack.id])

  const activeTracks = filteredTracks.length ? filteredTracks : SCENE_MUSIC_TRACKS

  useEffect(() => {
    if (!isPlaying || !syncEnabled || musicSyncState?.updatedByPlayerId !== playerId) return
    const timer = window.setInterval(() => {
      const audio = audioRef.current
      broadcastMusicControl(currentTrack.id, 'playing', audio?.currentTime ?? currentTime)
    }, MUSIC_SYNC_HEARTBEAT_MS)
    return () => window.clearInterval(timer)
  }, [
    broadcastMusicControl,
    currentTime,
    currentTrack.id,
    isPlaying,
    musicSyncState?.updatedByPlayerId,
    playerId,
    syncEnabled,
  ])

  const updateMusicFilter = (nextFilter: MusicFilter) => {
    const nextTracks = tracksForFilter(nextFilter)
    setSelectedTag(nextFilter)
    if (nextTracks.some((track) => track.id === currentTrack.id)) return
    resetTrackProgress()
    const nextTrackId = nextTracks[0]?.id ?? SCENE_MUSIC_TRACKS[0].id
    setCurrentTrackId(nextTrackId)
    broadcastMusicControl(nextTrackId, isPlaying ? 'playing' : 'paused', 0)
  }

  const selectTrack = (trackId: string) => {
    resetTrackProgress()
    setCurrentTrackId(trackId)
    broadcastMusicControl(trackId, isPlaying ? 'playing' : 'paused', 0)
  }

  const skipBy = useCallback(
    (offset: number) => {
      const currentIndex = activeTracks.findIndex((track) => track.id === currentTrack.id)
      const safeIndex = currentIndex >= 0 ? currentIndex : 0
      const nextTrack = activeTracks[(safeIndex + offset + activeTracks.length) % activeTracks.length]
      if (nextTrack.id === currentTrack.id) {
        const audio = audioRef.current
        if (audio) {
          audio.currentTime = 0
          setCurrentTime(0)
          broadcastMusicControl(currentTrack.id, isPlaying ? 'playing' : 'paused', 0)
          if (isPlaying) {
            void audio.play().catch(() => {
              setPlaybackError('Group music is playing. Join audio to hear it here.')
            })
          }
        }
        return
      }
      resetTrackProgress()
      setCurrentTrackId(nextTrack.id)
      broadcastMusicControl(nextTrack.id, isPlaying ? 'playing' : 'paused', 0)
    },
    [activeTracks, broadcastMusicControl, currentTrack.id, isPlaying, resetTrackProgress],
  )

  const rewindCurrentTrack = () => {
    const audio = audioRef.current
    if (!audio) return
    const nextTime = Math.max(0, audio.currentTime - REWIND_SECONDS)
    audio.currentTime = nextTime
    setCurrentTime(nextTime)
    broadcastMusicControl(currentTrack.id, isPlaying ? 'playing' : 'paused', nextTime)
  }

  const togglePlayback = () => {
    if (isPlaying) {
      audioRef.current?.pause()
      setIsPlaying(false)
      broadcastMusicControl(currentTrack.id, 'paused', audioRef.current?.currentTime ?? currentTime)
      return
    }
    setPlaybackError('')
    setIsPlaying(true)
    broadcastMusicControl(currentTrack.id, 'playing', audioRef.current?.currentTime ?? currentTime)
  }

  const joinSessionAudio = () => {
    const audio = audioRef.current
    if (!audio) return
    void audio.play().then(
      () => {
        setPlaybackError('')
        setIsPlaying(true)
      },
      () => setPlaybackError('Audio is still blocked in this tab. Click the player, then try again.'),
    )
  }

  const updateTrackTime = () => {
    const audio = audioRef.current
    if (!audio) return
    setCurrentTime(audio.currentTime)
  }

  const updateTrackDuration = () => {
    const audio = audioRef.current
    if (!audio) return
    setDuration(Number.isFinite(audio.duration) ? audio.duration : 0)
    setCurrentTime(audio.currentTime)
    const pendingSync = pendingRemoteSyncRef.current
    if (pendingSync && pendingSync.trackId === currentTrack.id) {
      pendingRemoteSyncRef.current = null
      applySyncToCurrentTrack(pendingSync)
    }
  }

  const seekTrack = (value: string) => {
    const audio = audioRef.current
    if (!audio) return
    const nextTime = Math.min(duration || 0, Math.max(0, Number(value)))
    audio.currentTime = nextTime
    setCurrentTime(nextTime)
    broadcastMusicControl(currentTrack.id, isPlaying ? 'playing' : 'paused', nextTime)
  }

  const updateVolume = (value: string) => {
    setVolume(clampVolume(Number(value)))
  }

  const currentTagLabel = selectedTag === 'all'
    ? 'All scenes'
    : SCENE_MUSIC_TAGS.find((tag) => tag.id === selectedTag)?.label ?? selectedTag
  const progressMax = Math.max(0, duration)
  const trackProgress = Math.min(currentTime, progressMax)
  const syncStatusLabel = syncEnabled ? 'Session synced' : 'Local player'
  const panelMode = musicPanelMode(panelLayout)
  const playerClassName = `scene-music-player is-${panelMode}${isMovingPanel ? ' is-moving' : ''}`
  const panelStyle = {
    left: `${panelLayout.left}px`,
    top: `${panelLayout.top}px`,
    width: `${panelLayout.width}px`,
    height: `${panelLayout.height}px`,
  }
  const moveMicroPanel = (event: ReactPointerEvent<HTMLElement>) => {
    if (panelMode !== 'micro' || event.target !== event.currentTarget) return
    startPanelDrag('move', event)
  }

  return (
    <section
      className={playerClassName}
      aria-label="Scene music player"
      style={panelStyle}
      onPointerDown={moveMicroPanel}
    >
      <audio
        ref={audioRef}
        preload="metadata"
        src={currentTrack.src}
        onEnded={() => skipBy(1)}
        onLoadedMetadata={updateTrackDuration}
        onTimeUpdate={updateTrackTime}
      />
      {panelMode !== 'micro' ? (
        <button
          type="button"
          className="scene-music-drag-handle"
          aria-label="Move music player"
          title="Move music player"
          onPointerDown={(event) => startPanelDrag('move', event)}
        >
          <Grip size={15} />
        </button>
      ) : null}
      {panelMode === 'full' ? (
        <div className="scene-music-now" aria-live="polite">
          <Music size={17} />
          <div>
            <span>{isPlaying ? 'Now playing' : 'Scene music'}</span>
            <strong>{currentTrack.title}</strong>
            <small>
              {currentTrack.artist} / {tagsLabel(currentTrack)} / {currentTrack.durationLabel} / {syncStatusLabel}
            </small>
          </div>
        </div>
      ) : null}
      <div className="scene-music-controls" aria-label="Music transport controls">
        {panelMode === 'full' ? (
          <button type="button" aria-label="Previous music track" title="Previous track" onClick={() => skipBy(-1)}>
            <SkipBack size={16} />
          </button>
        ) : null}
        {panelMode !== 'micro' ? (
          <button type="button" aria-label="Rewind music 15 seconds" title="Rewind 15 seconds" onClick={rewindCurrentTrack}>
            <Rewind size={16} />
          </button>
        ) : null}
        <button
          type="button"
          className="scene-music-play"
          aria-label={isPlaying ? 'Pause music' : 'Play music'}
          aria-pressed={isPlaying}
          title={isPlaying ? 'Pause music' : 'Play music'}
          onClick={togglePlayback}
        >
          {isPlaying ? <Pause size={17} /> : <Play size={17} />}
        </button>
        {panelMode !== 'micro' ? (
          <button type="button" aria-label="Next music track" title="Next track" onClick={() => skipBy(1)}>
            <SkipForward size={16} />
          </button>
        ) : null}
      </div>
      {panelMode === 'full' ? (
        <>
          <div className="scene-music-progress">
            <span>{formatSeconds(trackProgress)}</span>
            <input
              type="range"
              aria-label="Music position"
              min="0"
              max={String(progressMax)}
              step="1"
              value={String(trackProgress)}
              disabled={!progressMax}
              onChange={(event) => seekTrack(event.target.value)}
            />
            <span>{formatSeconds(progressMax)}</span>
          </div>
          <label className="scene-music-select">
            <span>Track</span>
            <select
              aria-label="Music track"
              value={currentTrack.id}
              onChange={(event) => selectTrack(event.target.value)}
            >
              {filteredTracks.map((track) => (
                <option key={track.id} value={track.id}>
                  {track.title}
                </option>
              ))}
            </select>
          </label>
          <label className="scene-music-volume">
            <Volume1 size={15} />
            <input
              type="range"
              aria-label="Music volume"
              min="0"
              max="1"
              step="0.05"
              value={String(volume)}
              onChange={(event) => updateVolume(event.target.value)}
            />
          </label>
          <div className="scene-music-tags" aria-label={`Scene music filters: ${currentTagLabel}`}>
            <span>
              <Tags size={14} />
              Tags
            </span>
            <button
              type="button"
              aria-pressed={selectedTag === 'all'}
              className={selectedTag === 'all' ? 'selected' : ''}
              onClick={() => updateMusicFilter('all')}
            >
              All
            </button>
            {SCENE_MUSIC_TAGS.map((tag) => (
              <button
                type="button"
                key={tag.id}
                aria-pressed={selectedTag === tag.id}
                className={selectedTag === tag.id ? 'selected' : ''}
                onClick={() => updateMusicFilter(tag.id)}
              >
                {tag.label}
              </button>
            ))}
          </div>
        </>
      ) : null}
      {panelMode === 'full' && playbackError ? (
        <div className="scene-music-error" role="alert">
          <span>{playbackError}</span>
          {isPlaying ? (
            <button type="button" onClick={joinSessionAudio}>
              Join audio
            </button>
          ) : null}
        </div>
      ) : null}
      <button
        type="button"
        className="scene-music-resize-handle"
        aria-label="Resize music player"
        title="Resize music player"
        onPointerDown={(event) => startPanelDrag('resize', event)}
      >
        <Maximize2 size={13} />
      </button>
    </section>
  )
}
