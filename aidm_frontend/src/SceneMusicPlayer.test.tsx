// @vitest-environment jsdom
import '@testing-library/jest-dom/vitest'
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { SceneMusicPlayer } from './SceneMusicPlayer'

function createStorageMock(): Storage {
  const store = new Map<string, string>()
  return {
    get length() {
      return store.size
    },
    clear: vi.fn(() => store.clear()),
    getItem: vi.fn((key: string) => store.get(key) ?? null),
    key: vi.fn((index: number) => [...store.keys()][index] ?? null),
    removeItem: vi.fn((key: string) => {
      store.delete(key)
    }),
    setItem: vi.fn((key: string, value: string) => {
      store.set(key, value)
    }),
  }
}

function storeMusicLayout(width: number, height: number) {
  localStorage.setItem(
    'aidm:sceneMusicLayout',
    JSON.stringify({
      left: 24,
      top: 24,
      width,
      height,
    }),
  )
}

function installMatchMediaMock(matches: boolean) {
  vi.stubGlobal(
    'matchMedia',
    vi.fn((query: string) => ({
      matches,
      media: query,
      onchange: null,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      addListener: vi.fn(),
      removeListener: vi.fn(),
      dispatchEvent: vi.fn(),
    })),
  )
}

function installLegacyMatchMediaMock(matches: boolean) {
  const addListener = vi.fn()
  const removeListener = vi.fn()
  vi.stubGlobal(
    'matchMedia',
    vi.fn((query: string) => ({
      matches,
      media: query,
      onchange: null,
      addListener,
      removeListener,
      dispatchEvent: vi.fn(),
    })),
  )
  return { addListener, removeListener }
}

describe('SceneMusicPlayer', () => {
  let playMock: ReturnType<typeof vi.spyOn>
  let pauseMock: ReturnType<typeof vi.spyOn>

  beforeEach(() => {
    const storage = createStorageMock()
    Object.defineProperty(globalThis, 'localStorage', {
      configurable: true,
      value: storage,
    })
    Object.defineProperty(window, 'localStorage', {
      configurable: true,
      value: storage,
    })
    localStorage.clear()
    playMock = vi
      .spyOn(window.HTMLMediaElement.prototype, 'play')
      .mockImplementation(() => Promise.resolve())
    pauseMock = vi
      .spyOn(window.HTMLMediaElement.prototype, 'pause')
      .mockImplementation(() => undefined)
  })

  afterEach(() => {
    cleanup()
    vi.restoreAllMocks()
    vi.unstubAllGlobals()
    localStorage.clear()
  })

  it('filters tracks by scene tag and shows the active now-playing metadata', async () => {
    render(<SceneMusicPlayer />)

    const nowPlaying = screen.getByLabelText('Scene music player').querySelector('.scene-music-now')
    expect(nowPlaying).not.toBeNull()
    expect(
      within(nowPlaying as HTMLElement).getByText('DnD Calm Fantasy Music for Adventure and Exploration'),
    ).toBeInTheDocument()
    expect(screen.getByText(/Everrune \/ Calm \/ Travel \/ Discovery/i)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Battle' })).not.toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: 'Travel' }))

    await waitFor(() =>
      expect(
        within(nowPlaying as HTMLElement).getByText('DnD Calm Fantasy Music for Adventure and Exploration'),
      ).toBeInTheDocument(),
    )
    expect(screen.getByRole('combobox', { name: 'Music track' })).toHaveValue(
      'dnd-calm-fantasy-adventure-exploration',
    )
    expect(screen.getByRole('button', { name: 'Move music player' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Resize music player' })).toBeInTheDocument()
  })

  it('plays, pauses, rewinds, and skips tracks', async () => {
    const { container } = render(<SceneMusicPlayer />)
    const audio = container.querySelector('audio')
    expect(audio).not.toBeNull()
    if (!audio) return

    Object.defineProperty(audio, 'duration', { configurable: true, value: 11095.235 })
    audio.currentTime = 42
    fireEvent.loadedMetadata(audio)
    const progress = container.querySelector('.scene-music-progress')
    expect(progress).not.toBeNull()
    expect(within(progress as HTMLElement).getByText('3:04:55')).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: 'Play music' }))

    await waitFor(() => expect(playMock).toHaveBeenCalled())
    expect(screen.getByRole('button', { name: 'Pause music' })).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: 'Rewind music 15 seconds' }))
    expect(audio.currentTime).toBe(27)

    fireEvent.click(screen.getByRole('button', { name: 'Next music track' }))
    expect(audio.currentTime).toBe(0)
    expect(screen.getAllByText('DnD Calm Fantasy Music for Adventure and Exploration').length).toBeGreaterThan(0)

    fireEvent.click(screen.getByRole('button', { name: 'Pause music' }))
    expect(pauseMock).toHaveBeenCalled()
  })

  it('emits synced transport controls without sharing volume', async () => {
    const onMusicControl = vi.fn()
    const { container } = render(
      <SceneMusicPlayer sessionId={4} playerId={8} onMusicControl={onMusicControl} />,
    )
    const audio = container.querySelector('audio')
    expect(audio).not.toBeNull()
    if (!audio) return

    Object.defineProperty(audio, 'duration', { configurable: true, value: 11095.235 })
    audio.currentTime = 42
    fireEvent.loadedMetadata(audio)

    fireEvent.click(screen.getByRole('button', { name: 'Play music' }))
    await waitFor(() => expect(playMock).toHaveBeenCalled())

    expect(onMusicControl).toHaveBeenCalledWith({
      trackId: 'dnd-calm-fantasy-adventure-exploration',
      status: 'playing',
      position: 42,
    })
    expect(onMusicControl.mock.calls[0][0]).not.toHaveProperty('volume')

    const callCount = onMusicControl.mock.calls.length
    fireEvent.change(screen.getByLabelText('Music volume'), { target: { value: '0' } })
    expect(onMusicControl).toHaveBeenCalledTimes(callCount)
  })

  it('ducks actual music volume during TTS without changing the saved slider volume', async () => {
    const { container, rerender } = render(<SceneMusicPlayer />)
    const audio = container.querySelector('audio')
    expect(audio).not.toBeNull()
    if (!audio) return

    fireEvent.change(screen.getByLabelText('Music volume'), { target: { value: '0.8' } })
    await waitFor(() => expect(audio.volume).toBeCloseTo(0.8))

    rerender(<SceneMusicPlayer duckForNarration />)

    await waitFor(() => expect(audio.volume).toBeCloseTo(0.22))
    expect(screen.getByLabelText('Music volume')).toHaveValue('0.8')

    rerender(<SceneMusicPlayer duckForNarration={false} />)

    await waitFor(() => expect(audio.volume).toBeCloseTo(0.8))
  })

  it('collapses controls based on the resized player size', () => {
    storeMusicLayout(240, 96)
    const transportRender = render(<SceneMusicPlayer />)

    expect(screen.getByLabelText('Scene music player')).toHaveClass('is-transport')
    expect(screen.queryByText('Scene music')).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Previous music track' })).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Rewind music 15 seconds' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Play music' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Next music track' })).toBeInTheDocument()
    expect(screen.queryByRole('combobox', { name: 'Music track' })).not.toBeInTheDocument()
    expect(screen.queryByLabelText(/Scene music filters/i)).not.toBeInTheDocument()

    transportRender.unmount()
    localStorage.clear()
    storeMusicLayout(96, 60)
    render(<SceneMusicPlayer />)

    expect(screen.getByLabelText('Scene music player')).toHaveClass('is-micro')
    expect(screen.queryByRole('button', { name: 'Move music player' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Rewind music 15 seconds' })).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Play music' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Next music track' })).not.toBeInTheDocument()
    expect(screen.queryByRole('combobox', { name: 'Music track' })).not.toBeInTheDocument()
  })

  it('uses a static full transport on mobile without floating handles', () => {
    installMatchMediaMock(true)
    storeMusicLayout(96, 60)
    render(<SceneMusicPlayer />)

    const player = screen.getByLabelText('Scene music player')
    expect(player).toHaveClass('is-mobile-static')
    expect(player).toHaveClass('is-full')
    expect(player).not.toHaveClass('is-micro')
    expect(player.style.left).toBe('')
    expect(player.style.top).toBe('')
    expect(player.style.width).toBe('')
    expect(player.style.height).toBe('')
    expect(screen.getByText('Scene music')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Previous music track' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Move music player' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Resize music player' })).not.toBeInTheDocument()
  })

  it('mounts mobile layout with legacy MediaQueryList listeners', () => {
    const legacyListeners = installLegacyMatchMediaMock(true)
    storeMusicLayout(96, 60)
    const rendered = render(<SceneMusicPlayer />)

    const player = screen.getByLabelText('Scene music player')
    expect(player).toHaveClass('is-mobile-static')
    expect(legacyListeners.addListener).toHaveBeenCalledWith(expect.any(Function))

    rendered.unmount()

    expect(legacyListeners.removeListener).toHaveBeenCalledWith(legacyListeners.addListener.mock.calls[0][0])
  })

  it('applies remote session music state while keeping local volume independent', async () => {
    const syncState = {
      sessionId: 4,
      trackId: 'dnd-calm-fantasy-adventure-exploration',
      status: 'playing' as const,
      position: 60,
      updatedAtMs: Date.now(),
      receivedAtMs: Date.now(),
      updatedByPlayerId: 9,
    }
    const { container, rerender } = render(<SceneMusicPlayer sessionId={4} playerId={8} />)
    const audio = container.querySelector('audio')
    expect(audio).not.toBeNull()
    if (!audio) return

    Object.defineProperty(audio, 'duration', { configurable: true, value: 11095.235 })
    fireEvent.loadedMetadata(audio)
    fireEvent.change(screen.getByLabelText('Music volume'), { target: { value: '0.25' } })

    rerender(<SceneMusicPlayer sessionId={4} playerId={8} musicSyncState={syncState} />)

    await waitFor(() => expect(playMock).toHaveBeenCalled())
    expect(audio.currentTime).toBeGreaterThanOrEqual(60)
    expect((screen.getByLabelText('Music volume') as HTMLInputElement).value).toBe('0.25')
  })

  it('keeps play and pause controlled by the session when remote playback is blocked', async () => {
    playMock.mockRejectedValue(new Error('blocked'))
    const playingState = {
      sessionId: 4,
      trackId: 'dnd-calm-fantasy-adventure-exploration',
      status: 'playing' as const,
      position: 90,
      updatedAtMs: Date.now(),
      receivedAtMs: Date.now(),
      updatedByPlayerId: 9,
    }
    const pausedState = {
      ...playingState,
      status: 'paused' as const,
      position: 91,
      updatedAtMs: playingState.updatedAtMs + 1,
      receivedAtMs: playingState.receivedAtMs + 1,
    }
    const { container, rerender } = render(<SceneMusicPlayer sessionId={4} playerId={8} />)
    const audio = container.querySelector('audio')
    expect(audio).not.toBeNull()
    if (!audio) return

    Object.defineProperty(audio, 'duration', { configurable: true, value: 11095.235 })
    fireEvent.loadedMetadata(audio)

    rerender(<SceneMusicPlayer sessionId={4} playerId={8} musicSyncState={playingState} />)

    await waitFor(() => expect(playMock).toHaveBeenCalled())
    expect(screen.getByRole('button', { name: 'Pause music' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Join audio' })).toBeInTheDocument()

    rerender(<SceneMusicPlayer sessionId={4} playerId={8} musicSyncState={pausedState} />)

    await waitFor(() => expect(pauseMock).toHaveBeenCalled())
    expect(screen.getByRole('button', { name: 'Play music' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Join audio' })).not.toBeInTheDocument()
  })

  it('moves and resizes the floating player with pointer controls', async () => {
    render(<SceneMusicPlayer />)
    const player = screen.getByLabelText('Scene music player')
    const initialLeft = Number.parseFloat(player.style.left)
    const initialTop = Number.parseFloat(player.style.top)
    const initialWidth = Number.parseFloat(player.style.width)
    const initialHeight = Number.parseFloat(player.style.height)

    fireEvent.pointerDown(screen.getByRole('button', { name: 'Move music player' }), {
      button: 0,
      clientX: 120,
      clientY: 120,
    })
    fireEvent.pointerMove(window, { clientX: 90, clientY: 165 })
    fireEvent.pointerUp(window)

    await waitFor(() => {
      expect(Number.parseFloat(player.style.left)).toBe(initialLeft - 30)
      expect(Number.parseFloat(player.style.top)).toBe(initialTop + 45)
    })

    fireEvent.pointerDown(screen.getByRole('button', { name: 'Resize music player' }), {
      button: 0,
      clientX: 200,
      clientY: 200,
    })
    fireEvent.pointerMove(window, { clientX: 260, clientY: 245 })
    fireEvent.pointerUp(window)

    await waitFor(() => {
      expect(Number.parseFloat(player.style.width)).toBe(initialWidth + 60)
      expect(Number.parseFloat(player.style.height)).toBe(initialHeight + 45)
    })
  })
})
