// @vitest-environment jsdom
import '@testing-library/jest-dom/vitest'
import { createRef, type ComponentProps } from 'react'
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { StorySettingsSheet } from './StorySettingsSheet'

type SettingsProps = ComponentProps<typeof StorySettingsSheet>

function settingsProps(overrides: Partial<SettingsProps> = {}): SettingsProps {
  return {
    dialogRef: createRef<HTMLElement>(),
    onClose: vi.fn(),
    narration: { enabled: false, problem: '' },
    onNarrationChange: vi.fn(),
    view: 'story',
    onViewChange: vi.fn(),
    fullscreen: false,
    onFullscreenChange: vi.fn(),
    diagnostics: {
      player: 'You are Danny Vale, playing Ember.',
      table: 'At the Ember Table.',
      connection: 'Connected to the game server.',
    },
    onRefresh: vi.fn(),
    onReconnect: vi.fn(),
    onConnectionSettings: vi.fn(),
    ...overrides,
  }
}

function setting(name: string) {
  return screen.getByRole('group', { name })
}

function stored(key: string) {
  return JSON.parse(localStorage.getItem(key) ?? 'null')
}

beforeEach(() => localStorage.clear())
afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

describe('StorySettingsSheet', () => {
  it('offers reading, sound, motion and view settings, with the table plumbing tucked away', () => {
    render(<StorySettingsSheet {...settingsProps()} />)

    const dialog = screen.getByRole('dialog', { name: 'Settings' })
    for (const heading of ['Reading', 'Sound', 'Motion', 'View']) {
      expect(within(dialog).getByRole('heading', { name: heading })).toBeInTheDocument()
    }
    const disclosure = within(dialog).getByText('Table and connection').closest('details') as HTMLElement
    expect(disclosure).not.toHaveAttribute('open')
    expect(within(disclosure).getByText('You are Danny Vale, playing Ember.')).toBeInTheDocument()
    expect(within(disclosure).getByText('At the Ember Table.')).toBeInTheDocument()
    expect(within(disclosure).getByText('Connected to the game server.')).toBeInTheDocument()
    expect(dialog).not.toHaveTextContent(/backend|runtime|workspace|session/i)
  })

  it('stores text size and typeface in the shared reading settings', () => {
    localStorage.setItem('aidm:chatTextSettings', JSON.stringify({ size: 'default', font: 'mono' }))
    render(<StorySettingsSheet {...settingsProps()} />)

    expect(within(setting('Typeface')).getByRole('button', { name: 'Story serif' })).toHaveAttribute('aria-pressed', 'false')
    fireEvent.click(within(setting('Text size')).getByRole('button', { name: 'Large' }))
    expect(stored('aidm:chatTextSettings')).toEqual({ size: 'large', font: 'mono' })
    expect(within(setting('Text size')).getByRole('button', { name: 'Large' })).toHaveAttribute('aria-pressed', 'true')

    fireEvent.click(within(setting('Typeface')).getByRole('button', { name: 'Sans' }))
    expect(stored('aidm:chatTextSettings')).toEqual({ size: 'large', font: 'sans' })
    fireEvent.click(within(setting('Typeface')).getByRole('button', { name: 'Story serif' }))
    expect(stored('aidm:chatTextSettings')).toEqual({ size: 'large', font: 'default' })
  })

  it('hides the narration voice when narration is not configured and explains when it cannot play', () => {
    const { rerender } = render(<StorySettingsSheet {...settingsProps({ narration: null })} />)
    expect(screen.queryByRole('group', { name: 'Narration voice' })).not.toBeInTheDocument()

    const onNarrationChange = vi.fn()
    rerender(<StorySettingsSheet {...settingsProps({ onNarrationChange })} />)
    fireEvent.click(within(setting('Narration voice')).getByRole('button', { name: 'On' }))
    expect(onNarrationChange).toHaveBeenCalledWith(true)

    rerender(
      <StorySettingsSheet
        {...settingsProps({ narration: { enabled: false, problem: 'The narration voice could not be played just now.' } })}
      />,
    )
    expect(screen.getByText('The narration voice could not be played just now.')).toBeInTheDocument()
    expect(within(setting('Narration voice')).getByRole('button', { name: 'On' })).toBeDisabled()
  })

  it('turns music off and sets its volume in the music preferences', () => {
    localStorage.setItem('aidm:sceneMusicPreferences', JSON.stringify({ selectedTag: 'tavern', trackId: 'inn', volume: 0.7 }))
    render(<StorySettingsSheet {...settingsProps()} />)

    const volume = screen.getByRole('slider', { name: 'Music volume' })
    expect(volume).toHaveValue('70')
    fireEvent.change(volume, { target: { value: '35' } })
    expect(stored('aidm:sceneMusicPreferences')).toEqual({ selectedTag: 'tavern', trackId: 'inn', volume: 0.35 })

    fireEvent.click(within(setting('Music')).getByRole('button', { name: 'Off' }))
    expect(localStorage.getItem('aidm:sceneMusicEnabled')).toBe('false')
    expect(within(setting('Music')).getByRole('button', { name: 'Off' })).toHaveAttribute('aria-pressed', 'true')
    expect(screen.getByRole('slider', { name: 'Music volume' })).toBeDisabled()
  })

  it('follows the system for reduced motion until the player chooses', () => {
    vi.stubGlobal('matchMedia', (query: string) => ({
      matches: query === '(prefers-reduced-motion: reduce)',
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
    }))
    render(<StorySettingsSheet {...settingsProps()} />)

    expect(within(setting('Reduce motion')).getByRole('button', { name: 'On' })).toHaveAttribute('aria-pressed', 'true')
    fireEvent.click(within(setting('Reduce motion')).getByRole('button', { name: 'Off' }))
    expect(localStorage.getItem('aidm:storyReduceMotion')).toBe('false')
    expect(within(setting('Reduce motion')).getByRole('button', { name: 'Off' })).toHaveAttribute('aria-pressed', 'true')
  })

  it('switches the view, fullscreen and the table maintenance actions through their handlers', () => {
    const props = settingsProps()
    render(<StorySettingsSheet {...props} />)

    fireEvent.click(within(setting('Play in')).getByRole('button', { name: 'Table tools' }))
    expect(props.onViewChange).toHaveBeenCalledWith('console')
    fireEvent.click(within(setting('Fullscreen')).getByRole('button', { name: 'On' }))
    expect(props.onFullscreenChange).toHaveBeenCalledWith(true)

    fireEvent.click(screen.getByText('Table and connection'))
    fireEvent.click(screen.getByRole('button', { name: 'Refresh' }))
    fireEvent.click(screen.getByRole('button', { name: 'Reconnect' }))
    fireEvent.click(screen.getByRole('button', { name: 'Connection settings' }))
    expect(props.onRefresh).toHaveBeenCalledOnce()
    expect(props.onReconnect).toHaveBeenCalledOnce()
    expect(props.onConnectionSettings).toHaveBeenCalledOnce()
  })
})
