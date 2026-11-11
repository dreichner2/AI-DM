// @vitest-environment jsdom
import '@testing-library/jest-dom/vitest'
import { createRef, type ComponentProps } from 'react'
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { LlmProviderOption } from '../../types'
import { StorySettingsSheet, type NarrationVoiceSetting, type NarratorSetting } from './StorySettingsSheet'

type SettingsProps = ComponentProps<typeof StorySettingsSheet>

function provider(id: string, label: string, configured: boolean, models: Array<[string, string]>): LlmProviderOption {
  return {
    id,
    label,
    configured,
    default_model: models[0][0],
    models: models.map(([model, modelLabel]) => ({ id: model, label: modelLabel })),
  }
}

const PROVIDERS = [
  provider('deepseek', 'DeepSeek', true, [['deepseek-v4-pro', 'DeepSeek V4 Pro'], ['deepseek-v4-flash', 'DeepSeek V4 Flash']]),
  provider('gemini', 'Gemini', true, [['gemini-3-pro', 'Gemini 3 Pro']]),
  provider('nvidia', 'NVIDIA', false, [['kimi-k3', 'Kimi K3']]),
  provider('fallback', 'Fallback', true, [['deterministic-v1', 'Deterministic Local Fallback']]),
]

function narrator(provider: string, model: string, providers: LlmProviderOption[] | null = PROVIDERS): NarratorSetting {
  return { current: { provider, model }, providers, pending: false }
}

function settingsProps(overrides: Partial<SettingsProps> = {}): SettingsProps {
  return {
    dialogRef: createRef<HTMLElement>(),
    onClose: vi.fn(),
    narrator: narrator('deepseek', 'deepseek-v4-pro'),
    onNarratorChange: vi.fn(),
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
    expect(within(dialog).getAllByRole('region').map((section) => section.getAttribute('aria-labelledby'))[0]).toBe(
      'settings-narrator',
    )
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

  it('opens on the narrator: the current one, then the scripted narrator and every provider with a key', () => {
    const props = settingsProps()
    render(<StorySettingsSheet {...props} />)

    const narratorSection = screen.getByRole('region', { name: 'Narrator' })
    expect(narratorSection).toHaveTextContent('NarratorDeepSeek · DeepSeek V4 Pro')
    const choices = within(narratorSection).getByRole('group', { name: 'Narrator' })
    expect(within(choices).getAllByRole('button').map((choice) => choice.textContent)).toEqual([
      'Scripted narrator',
      'DeepSeek',
      'Gemini',
    ])
    expect(within(choices).getByRole('button', { name: 'DeepSeek' })).toHaveAttribute('aria-pressed', 'true')

    fireEvent.click(within(choices).getByRole('button', { name: 'Gemini' }))
    expect(props.onNarratorChange).toHaveBeenLastCalledWith('gemini', 'gemini-3-pro')
    fireEvent.click(within(choices).getByRole('button', { name: 'Scripted narrator' }))
    expect(props.onNarratorChange).toHaveBeenLastCalledWith('fallback', 'deterministic-v1')
    fireEvent.click(within(choices).getByRole('button', { name: 'DeepSeek' }))
    expect(props.onNarratorChange).toHaveBeenCalledTimes(2)

    // DeepSeek has two models, so a Model row follows.
    const model = within(narratorSection).getByRole('group', { name: 'Model' })
    expect(within(model).getByRole('button', { name: 'DeepSeek V4 Pro' })).toHaveAttribute('aria-pressed', 'true')
    fireEvent.click(within(model).getByRole('button', { name: 'DeepSeek V4 Flash' }))
    expect(props.onNarratorChange).toHaveBeenLastCalledWith('deepseek', 'deepseek-v4-flash')
  })

  it('names the scripted narrator, offers no Model row for a single model, and holds the choices while one is applied', () => {
    const { rerender } = render(<StorySettingsSheet {...settingsProps({ narrator: narrator('fallback', 'deterministic-v1') })} />)
    const narratorSection = screen.getByRole('region', { name: 'Narrator' })
    expect(narratorSection).toHaveTextContent('NarratorScripted narrator')
    expect(within(narratorSection).getByRole('button', { name: 'Scripted narrator' })).toHaveAttribute('aria-pressed', 'true')
    expect(within(narratorSection).queryByRole('group', { name: 'Model' })).not.toBeInTheDocument()

    rerender(<StorySettingsSheet {...settingsProps({ narrator: { current: { provider: 'gemini', model: 'gemini-3-pro' }, providers: PROVIDERS, pending: true } })} />)
    expect(within(narratorSection).queryByRole('group', { name: 'Model' })).not.toBeInTheDocument()
    for (const choice of within(narratorSection).getAllByRole('button')) expect(choice).toBeDisabled()
  })

  it('says how to set up an AI narrator when the server has no provider key, and never asks for one', () => {
    const unconfigured = PROVIDERS.map((option) => (option.id === 'fallback' ? option : { ...option, configured: false }))
    const { rerender } = render(
      <StorySettingsSheet {...settingsProps({ narrator: narrator('fallback', 'deterministic-v1', unconfigured) })} />,
    )
    const narratorSection = screen.getByRole('region', { name: 'Narrator' })
    expect(narratorSection).toHaveTextContent(
      "No AI narrator is set up on this server. Add a provider key to the server's environment (see the README), then choose it here.",
    )
    expect(within(narratorSection).queryByRole('button')).not.toBeInTheDocument()
    expect(within(narratorSection).queryByRole('textbox')).not.toBeInTheDocument()

    // Before the provider list loads, only the current narrator shows.
    rerender(<StorySettingsSheet {...settingsProps({ narrator: narrator('deepseek', 'deepseek-v4-pro', null) })} />)
    expect(narratorSection).toHaveTextContent('Narratordeepseek · deepseek-v4-pro')
    expect(narratorSection).not.toHaveTextContent('No AI narrator')
    expect(within(narratorSection).queryByRole('button')).not.toBeInTheDocument()

    rerender(<StorySettingsSheet {...settingsProps({ narrator: null })} />)
    expect(screen.queryByRole('region', { name: 'Narrator' })).not.toBeInTheDocument()
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

  it('hides the narration voice until the server has answered and explains when it cannot play', () => {
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

  it('says so when the server has no narrator voice, with nothing to switch on', () => {
    render(<StorySettingsSheet {...settingsProps({ narration: 'none' })} />)

    const sound = screen.getByRole('region', { name: 'Sound' })
    expect(sound).toHaveTextContent('Narration voiceNo narrator voice is set up on this server.')
    expect(screen.queryByRole('group', { name: 'Narration voice' })).not.toBeInTheDocument()
    expect(screen.queryByRole('group', { name: 'Voice' })).not.toBeInTheDocument()
  })

  it('offers the voice, a sample of it, the accent and the quality when the server has them', () => {
    const voice: NarrationVoiceSetting = {
      voices: ['cedar', 'marin', 'ash', 'ballad', 'coral', 'echo', 'sage', 'shimmer', 'verse', 'alloy'],
      voice: 'cedar',
      accents: [
        { id: 'default', label: 'Default' },
        { id: 'british', label: 'British' },
        { id: 'northern', label: 'Northern English' },
        { id: 'scottish', label: 'Scottish' },
        { id: 'welsh', label: 'Welsh' },
        { id: 'west_country', label: 'West Country' },
      ],
      accent: 'british',
      qualities: [
        { id: 'best', label: 'Best' },
        { id: 'faster', label: 'Faster' },
      ],
      quality: 'best',
      onChange: vi.fn(),
      onPlaySample: vi.fn(),
    }
    render(<StorySettingsSheet {...settingsProps({ narration: { enabled: true, problem: '', voice } })} />)

    const sound = screen.getByRole('region', { name: 'Sound' })
    const rows = within(sound).getAllByRole('group').map((group) => group.getAttribute('aria-label'))
    expect(rows.slice(0, 4)).toEqual(['Narration voice', 'Voice', 'Accent', 'Voice quality'])
    const voices = setting('Voice')
    expect(within(voices).getAllByRole('button').map((choice) => choice.textContent)).toEqual([
      'Cedar', 'Marin', 'Ash', 'Ballad', 'Coral', 'Echo', 'Sage', 'Shimmer', 'Verse', 'Alloy',
    ])
    expect(within(voices).getByRole('button', { name: 'Cedar' })).toHaveAttribute('aria-pressed', 'true')
    expect(within(setting('Accent')).getAllByRole('button').map((choice) => choice.textContent)).toEqual([
      'Default', 'British', 'Northern English', 'Scottish', 'Welsh', 'West Country',
    ])
    expect(within(setting('Accent')).getByRole('button', { name: 'British' })).toHaveAttribute('aria-pressed', 'true')
    expect(within(setting('Voice quality')).getByRole('button', { name: 'Best' })).toHaveAttribute('aria-pressed', 'true')

    fireEvent.click(within(voices).getByRole('button', { name: 'Marin' }))
    expect(voice.onChange).toHaveBeenLastCalledWith({ voice: 'marin' })
    fireEvent.click(within(setting('Accent')).getByRole('button', { name: 'West Country' }))
    expect(voice.onChange).toHaveBeenLastCalledWith({ accent: 'west_country' })
    fireEvent.click(within(setting('Voice quality')).getByRole('button', { name: 'Faster' }))
    expect(voice.onChange).toHaveBeenLastCalledWith({ quality: 'faster' })
    fireEvent.click(within(sound).getByRole('button', { name: 'Play sample' }))
    expect(voice.onPlaySample).toHaveBeenCalledOnce()
  })

  it('offers no voice rows when the server voice has no choices', () => {
    render(<StorySettingsSheet {...settingsProps()} />)

    expect(setting('Narration voice')).toBeInTheDocument()
    expect(screen.queryByRole('group', { name: 'Voice' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Play sample' })).not.toBeInTheDocument()
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
