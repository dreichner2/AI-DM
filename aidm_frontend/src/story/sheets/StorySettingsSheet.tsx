import { type CSSProperties, type ReactNode, type RefObject } from 'react'
import { ChevronRight } from 'lucide-react'
import { ModalShell } from '../../ModalShell'
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

/** Narration voice: `null` when narration is not configured at this table. */
export type NarrationSetting = {
  enabled: boolean
  /** Why the voice cannot be used right now; empty when it can. */
  problem: string
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

/** One setting: its name, then its choices as text, the chosen one in ember. */
function SettingRow<T extends string | boolean>({
  name,
  choices,
  value,
  onChange,
  disabled = false,
  note,
}: {
  name: string
  choices: ReadonlyArray<[T, string]>
  value: T
  onChange: (value: T) => void
  disabled?: boolean
  note?: ReactNode
}) {
  return (
    <div className="story-dialog-setting">
      <span className="story-dialog-setting-name">{name}</span>
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

/** Settings a player recognises: reading, sound, motion and view, with the table's plumbing tucked away. */
export function StorySettingsSheet({
  diagnostics,
  dialogRef,
  fullscreen,
  narration,
  onClose,
  onConnectionSettings,
  onFullscreenChange,
  onNarrationChange,
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
        <section className="story-dialog-section" aria-labelledby="settings-reading">
          <h3 id="settings-reading" className="story-label">Reading</h3>
          <SettingRow name="Text size" choices={TEXT_SIZES} value={preferences.textSize} onChange={setStoryTextSize} />
          <SettingRow name="Typeface" choices={TYPEFACES} value={preferences.typeface} onChange={setStoryTypeface} />
        </section>

        <hr className="story-rule" />
        <section className="story-dialog-section" aria-labelledby="settings-sound">
          <h3 id="settings-sound" className="story-label">Sound</h3>
          {narration ? (
            <SettingRow
              name="Narration voice"
              choices={ON_OFF}
              value={narration.enabled}
              onChange={onNarrationChange}
              disabled={Boolean(narration.problem) && !narration.enabled}
              note={narration.problem || undefined}
            />
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
