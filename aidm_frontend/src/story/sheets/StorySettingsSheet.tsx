import { type CSSProperties, type ReactNode, type RefObject } from 'react'
import { ChevronRight } from 'lucide-react'
import { ModalShell } from '../../ModalShell'
import type { LlmProviderOption, TtsChoice } from '../../types'
import {
  setMusicEnabled,
  setMusicVolume,
  setStoryReduceMotion,
  setStoryTextSize,
  setStoryTypeface,
  useStoryPreferences,
  type StoryTextSize,
  type StoryTypeface,
} from './storyPreferences'

/** Narration voice: `'none'` when the server has no voice set up, `null` until it has said. */
export type NarrationSetting = {
  enabled: boolean
  /** Why the voice cannot be used right now; empty when it can. */
  problem: string
  /** Present when the server's voice offers a choice of voice, accent and quality. */
  voice?: NarrationVoiceSetting
} | 'none' | null

/** The player's voice choices, kept in this browser and sent with every narration request. */
export type NarrationVoiceSetting = {
  /** Voice ids, the best first. */
  voices: string[]
  voice: string
  accents: TtsChoice[]
  accent: string
  qualities: TtsChoice[]
  quality: string
  /** Applies from the next narration; a playing sample stops. */
  onChange: (choice: { voice?: string; accent?: string; quality?: string }) => void
  /** Speaks a short line in the chosen voice. */
  onPlaySample: () => void
}

/** The table's narrator: who narrates now and which providers the server offers. */
export type NarratorSetting = {
  /** `fallback` is the scripted narrator. */
  current: { provider: string; model: string }
  /** Every provider the server lists; null until the list has loaded. */
  providers: LlmProviderOption[] | null
  /** A change is on its way to the server. */
  pending: boolean
} | null

export type TableDiagnostics = {
  /** Who you are, which table, and the connection, each as one plain line. */
  player: string
  table: string
  connection: string
}

type StorySettingsSheetProps = {
  dialogRef: RefObject<HTMLElement | null>
  onClose: () => void
  narrator: NarratorSetting
  /** Asks the server to narrate with this provider and model. */
  onNarratorChange: (provider: string, model: string) => void
  narration: NarrationSetting
  onNarrationChange: (enabled: boolean) => void
  view: 'story' | 'console'
  onViewChange: (view: 'story' | 'console') => void
  fullscreen: boolean
  onFullscreenChange: (fullscreen: boolean) => void
  diagnostics: TableDiagnostics
  onRefresh: () => void
  onReconnect: () => void
  onConnectionSettings: () => void
}

const TEXT_SIZES: ReadonlyArray<[StoryTextSize, string]> = [
  ['default', 'Default'],
  ['large', 'Large'],
  ['extra', 'Extra'],
]
const TYPEFACES: ReadonlyArray<[StoryTypeface, string]> = [
  ['default', 'Story serif'],
  ['sans', 'Sans'],
]
const ON_OFF: ReadonlyArray<[boolean, string]> = [
  [true, 'On'],
  [false, 'Off'],
]
const NO_NARRATOR_VOICE = 'No narrator voice is set up on this server.'
const SCRIPTED_PROVIDER = 'fallback'
const SCRIPTED_NARRATOR = 'Scripted narrator'
const NO_AI_NARRATOR =
  "No AI narrator is set up on this server. Add a provider key to the server's environment (see the README), then choose it here."

/** One setting: its name, then its choices as text, the chosen one in ember. */
function SettingRow<T extends string | boolean>({
  name,
  nameHidden = false,
  choices,
  value,
  onChange,
  disabled = false,
  note,
}: {
  name: string
  /** The row continues the line above it, which already says what it sets. */
  nameHidden?: boolean
  choices: ReadonlyArray<[T, string]>
  value: T
  onChange: (value: T) => void
  disabled?: boolean
  note?: ReactNode
}) {
  return (
    <div className="story-dialog-setting">
      {nameHidden ? null : <span className="story-dialog-setting-name">{name}</span>}
      <span className="story-dialog-choices" role="group" aria-label={name}>
        {choices.map(([choice, label]) => (
          <button
            key={label}
            type="button"
            className="story-link story-dialog-choice"
            aria-pressed={choice === value}
            disabled={disabled}
            onClick={() => onChange(choice)}
          >
            {label}
          </button>
        ))}
      </span>
      {note ? <p className="story-dialog-setting-note">{note}</p> : null}
    </div>
  )
}

/**
 * Who narrates: the current narrator, then the scripted narrator and every AI
 * provider the server has a key for. Keys are never typed in here.
 */
function NarratorSection({
  narrator,
  onChange,
}: {
  narrator: NonNullable<NarratorSetting>
  onChange: (provider: string, model: string) => void
}) {
  const { current, providers, pending } = narrator
  const scripted = current.provider === SCRIPTED_PROVIDER
  const currentOption = providers?.find((provider) => provider.id === current.provider) ?? null
  const currentModelLabel =
    currentOption?.models.find((model) => model.id === current.model)?.label ?? current.model
  const currentLabel = scripted
    ? SCRIPTED_NARRATOR
    : `${currentOption?.label ?? current.provider} · ${currentModelLabel}`
  const aiProviders = (providers ?? []).filter(
    (provider) => provider.configured && provider.id !== SCRIPTED_PROVIDER,
  )
  const choices: Array<[string, string]> = [
    [SCRIPTED_PROVIDER, SCRIPTED_NARRATOR],
    ...aiProviders.map((provider): [string, string] => [provider.id, provider.label]),
  ]
  const choose = (providerId: string) => {
    if (providerId === current.provider) return
    const option = providers?.find((provider) => provider.id === providerId)
    if (option) onChange(option.id, option.default_model)
  }
  const models = currentOption?.models ?? []

  return (
    <section className="story-dialog-section" aria-labelledby="settings-narrator">
      <div className="story-dialog-setting">
        <span id="settings-narrator" className="story-dialog-setting-name">Narrator</span>
        <span className="story-dialog-setting-value">{currentLabel}</span>
      </div>
      {providers && !aiProviders.length ? (
        <p className="story-dialog-help">{NO_AI_NARRATOR}</p>
      ) : null}
      {aiProviders.length ? (
        <SettingRow
          name="Narrator"
          nameHidden
          choices={choices}
          value={current.provider}
          onChange={choose}
          disabled={pending}
        />
      ) : null}
      {aiProviders.length && models.length > 1 ? (
        <SettingRow
          name="Model"
          choices={models.map((model): [string, string] => [model.id, model.label])}
          value={current.model}
          onChange={(model) => {
            if (model !== current.model) onChange(current.provider, model)
          }}
          disabled={pending}
        />
      ) : null}
    </section>
  )
}

const choiceLabels = (choices: TtsChoice[]) => choices.map(({ id, label }): [string, string] => [id, label])

/** Voice, a sample of it, accent and quality: the narration voice's own choices. */
function NarrationVoiceRows({ voice }: { voice: NarrationVoiceSetting }) {
  return (
    <>
      <SettingRow
        name="Voice"
        choices={voice.voices.map((id): [string, string] => [id, id.charAt(0).toUpperCase() + id.slice(1)])}
        value={voice.voice}
        onChange={(id) => voice.onChange({ voice: id })}
      />
      <div className="story-dialog-links story-dialog-links-quiet">
        <button type="button" className="story-link" onClick={voice.onPlaySample}>
          Play sample
        </button>
      </div>
      <SettingRow
        name="Accent"
        choices={choiceLabels(voice.accents)}
        value={voice.accent}
        onChange={(accent) => voice.onChange({ accent })}
      />
      <SettingRow
        name="Voice quality"
        choices={choiceLabels(voice.qualities)}
        value={voice.quality}
        onChange={(quality) => voice.onChange({ quality })}
      />
    </>
  )
}

/** Settings a player recognises: narrator, reading, sound, motion and view, with the table's plumbing tucked away. */
export function StorySettingsSheet({
  diagnostics,
  dialogRef,
  fullscreen,
  narration,
  narrator,
  onClose,
  onConnectionSettings,
  onFullscreenChange,
  onNarrationChange,
  onNarratorChange,
  onReconnect,
  onRefresh,
  onViewChange,
  view,
}: StorySettingsSheetProps) {
  const preferences = useStoryPreferences()
  const volumePercent = Math.round(preferences.musicVolume * 100)

  return (
    <ModalShell
      closeLabel="Close settings"
      dialogRef={dialogRef}
      labelledBy="story-settings-title"
      onClose={onClose}
      title="Settings"
    >
      <div className="story-dialog-body story-settings">
        {narrator ? (
          <>
            <NarratorSection narrator={narrator} onChange={onNarratorChange} />
            <hr className="story-rule" />
          </>
        ) : null}
        <section className="story-dialog-section" aria-labelledby="settings-reading">
          <h3 id="settings-reading" className="story-label">Reading</h3>
          <SettingRow name="Text size" choices={TEXT_SIZES} value={preferences.textSize} onChange={setStoryTextSize} />
          <SettingRow name="Typeface" choices={TYPEFACES} value={preferences.typeface} onChange={setStoryTypeface} />
        </section>

        <hr className="story-rule" />
        <section className="story-dialog-section" aria-labelledby="settings-sound">
          <h3 id="settings-sound" className="story-label">Sound</h3>
          {narration === 'none' ? (
            <div className="story-dialog-setting">
              <span className="story-dialog-setting-name">Narration voice</span>
              <span className="story-dialog-setting-value">{NO_NARRATOR_VOICE}</span>
            </div>
          ) : narration ? (
            <>
              <SettingRow
                name="Narration voice"
                choices={ON_OFF}
                value={narration.enabled}
                onChange={onNarrationChange}
                disabled={Boolean(narration.problem) && !narration.enabled}
                note={narration.problem || undefined}
              />
              {narration.voice ? <NarrationVoiceRows voice={narration.voice} /> : null}
            </>
          ) : null}
          <SettingRow name="Music" choices={ON_OFF} value={preferences.musicEnabled} onChange={setMusicEnabled} />
          <label className="story-dialog-setting">
            <span className="story-dialog-setting-name">Music volume</span>
            <span className="story-dialog-range">
              <input
                type="range"
                min={0}
                max={100}
                step={5}
                value={volumePercent}
                disabled={!preferences.musicEnabled}
                aria-valuetext={`${volumePercent}%`}
                style={{ '--range-fill': `${volumePercent}%` } as CSSProperties}
                onChange={(event) => setMusicVolume(Number(event.target.value) / 100)}
              />
              <output aria-hidden="true">{volumePercent}%</output>
            </span>
          </label>
        </section>

        <hr className="story-rule" />
        <section className="story-dialog-section" aria-labelledby="settings-motion">
          <h3 id="settings-motion" className="story-label">Motion</h3>
          <SettingRow
            name="Reduce motion"
            choices={ON_OFF}
            value={preferences.reduceMotion}
            onChange={setStoryReduceMotion}
          />
        </section>

        <hr className="story-rule" />
        <section className="story-dialog-section" aria-labelledby="settings-view">
          <h3 id="settings-view" className="story-label">View</h3>
          <SettingRow
            name="Play in"
            choices={[
              ['story', 'Story'],
              ['console', 'Table tools'],
            ]}
            value={view}
            onChange={onViewChange}
          />
          <SettingRow name="Fullscreen" choices={ON_OFF} value={fullscreen} onChange={onFullscreenChange} />
        </section>

        <hr className="story-rule" />
        <details className="story-dialog-disclosure">
          <summary>
            <ChevronRight size={14} strokeWidth={1.8} aria-hidden="true" />
            <span className="story-label">Table and connection</span>
          </summary>
          <div className="story-dialog-disclosure-body">
            <p className="story-dialog-line">{diagnostics.player}</p>
            <p className="story-dialog-line">{diagnostics.table}</p>
            <p className="story-dialog-line">{diagnostics.connection}</p>
            <div className="story-dialog-links">
              <button type="button" className="story-link" onClick={onRefresh}>
                Refresh
              </button>
              <button type="button" className="story-link" onClick={onReconnect}>
                Reconnect
              </button>
              <button type="button" className="story-link" onClick={onConnectionSettings}>
                Connection settings
              </button>
            </div>
          </div>
        </details>
      </div>
    </ModalShell>
  )
}
