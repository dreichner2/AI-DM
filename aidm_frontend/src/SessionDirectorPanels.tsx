import { SlidersHorizontal, X } from 'lucide-react'
import {
  CONTENT_RATING_OPTIONS,
  CONTENT_TONE_TAG_OPTIONS,
  type ContentRating,
  type ContentSettings,
} from './contentSettings'
import { truncateText } from './gameSelectors'
import type { SceneDisplayState } from './sceneState'
import type {
  CampaignPackCommentaryCheckpoint,
  CampaignPackCommentaryRecord,
  CampaignPackCommentaryResponse,
  SessionState,
  TimelineEntry,
} from './types'

export type BoardViewMode = 'theater' | 'ops'

export type DmExecutionStats = {
  tokens: number | string
  time: string
  model: string
  temperature: string
}

export type RecentMemoryEntry = [text: string, source: string]

export type OperatorDrawerProps = {
  boardViewMode: BoardViewMode
  canEditContentSettings: boolean
  contentSettings: ContentSettings
  contentSettingsPending: boolean
  dmExecutionStats: DmExecutionStats
  onBoardViewModeChange: (mode: BoardViewMode) => void
  onContentRatingChange: (rating: ContentRating) => void
  onContentToneTagsChange: (toneTags: string[]) => void
}

export type DirectorCommentaryPanelProps = {
  activeSessionTitle: string
  recentMemory: RecentMemoryEntry[]
  commentary: CampaignPackCommentaryResponse | null
  contentSettings: ContentSettings
  currentResponseEntry: TimelineEntry | null
  dmExecutionStats: DmExecutionStats
  latestDmText: string
  onClose: () => void
  questTitle: string
  sceneState: SceneDisplayState | null
  sessionState: SessionState | null
  streamLabel: string
}

function formatDirectorStatus(status: string | undefined) {
  if (!status) return ''
  return status.replace(/[_-]+/g, ' ').replace(/\b\w/g, (match) => match.toUpperCase())
}

function contentRatingLabel(contentSettings: ContentSettings) {
  return CONTENT_RATING_OPTIONS.find((option) => option.value === contentSettings.contentRating)?.label ?? 'Not set'
}

function DirectorCheckpointList({
  emptyText,
  items,
}: {
  emptyText: string
  items: CampaignPackCommentaryCheckpoint[]
}) {
  if (!items.length) return <p>{emptyText}</p>
  return (
    <ol className="director-checkpoint-list">
      {items.slice(0, 5).map((item) => (
        <li key={`${item.fromCheckpointId ?? 'route'}:${item.checkpointId}:${item.edgeType ?? item.status ?? ''}`}>
          <strong>{item.title || item.checkpointId}</strong>
          <span>
            {item.edgeType ? `${formatDirectorStatus(item.edgeType)} from ${item.fromTitle || item.fromCheckpointId}` : formatDirectorStatus(item.status)}
          </span>
        </li>
      ))}
    </ol>
  )
}

function DirectorUndiscoveredList({ records }: { records: Record<string, CampaignPackCommentaryRecord[]> }) {
  const groups = Object.entries(records)
    .map(([collection, items]) => ({ collection, items }))
    .filter((group) => group.items.length)
    .slice(0, 4)
  if (!groups.length) return <p>No hidden campaign-pack records remain.</p>
  return (
    <div className="director-record-groups">
      {groups.map((group) => (
        <div key={group.collection}>
          <strong>{formatDirectorStatus(group.collection)}</strong>
          <span>{group.items.slice(0, 3).map((item) => item.title || item.id).join(', ')}</span>
        </div>
      ))}
    </div>
  )
}

export function OperatorDrawer({
  boardViewMode,
  canEditContentSettings,
  contentSettings,
  contentSettingsPending,
  dmExecutionStats,
  onBoardViewModeChange,
  onContentRatingChange,
  onContentToneTagsChange,
}: OperatorDrawerProps) {
  const toneTagSet = new Set(contentSettings.toneTags)
  const toggleToneTag = (tag: string) => {
    const nextTags = toneTagSet.has(tag)
      ? contentSettings.toneTags.filter((item) => item !== tag)
      : [...contentSettings.toneTags, tag].slice(0, 4)
    onContentToneTagsChange(nextTags)
  }
  return (
    <details className="operator-drawer">
      <summary>
        <SlidersHorizontal size={15} />
        Operator
      </summary>
      <div className="operator-drawer-body">
        <div className="operator-mode-toggle" role="group" aria-label="Board view mode">
          <button
            type="button"
            aria-pressed={boardViewMode === 'theater'}
            onClick={() => onBoardViewModeChange('theater')}
          >
            Theater
          </button>
          <button
            type="button"
            aria-pressed={boardViewMode === 'ops'}
            onClick={() => onBoardViewModeChange('ops')}
          >
            Ops
          </button>
        </div>
        <div className="operator-rating-toggle" role="group" aria-label="Content rating">
          {CONTENT_RATING_OPTIONS.map((option) => (
            <button
              key={option.value}
              type="button"
              aria-pressed={contentSettings.contentRating === option.value}
              disabled={!canEditContentSettings || contentSettingsPending}
              onClick={() => onContentRatingChange(option.value)}
            >
              {option.label}
            </button>
          ))}
        </div>
        <div className="operator-tone-toggle" role="group" aria-label="Tone tags">
          {CONTENT_TONE_TAG_OPTIONS.map((tag) => (
            <button
              key={tag}
              type="button"
              aria-pressed={toneTagSet.has(tag)}
              disabled={!canEditContentSettings || contentSettingsPending}
              onClick={() => toggleToneTag(tag)}
            >
              {formatDirectorStatus(tag)}
            </button>
          ))}
        </div>
        <dl>
          <div>
            <dt>Rating</dt>
            <dd>{CONTENT_RATING_OPTIONS.find((option) => option.value === contentSettings.contentRating)?.label}</dd>
          </div>
          <div>
            <dt>Tokens</dt>
            <dd>{dmExecutionStats.tokens}</dd>
          </div>
          <div>
            <dt>Time</dt>
            <dd>{dmExecutionStats.time}</dd>
          </div>
          <div>
            <dt>Model</dt>
            <dd>{dmExecutionStats.model}</dd>
          </div>
          <div>
            <dt>Temp</dt>
            <dd>{dmExecutionStats.temperature}</dd>
          </div>
          <div>
            <dt>Tone</dt>
            <dd>{contentSettings.toneTags.length ? contentSettings.toneTags.join(', ') : 'Default'}</dd>
          </div>
        </dl>
      </div>
    </details>
  )
}

export function DirectorCommentaryPanel({
  activeSessionTitle,
  recentMemory,
  commentary,
  contentSettings,
  currentResponseEntry,
  dmExecutionStats,
  latestDmText,
  onClose,
  questTitle,
  sceneState,
  sessionState,
  streamLabel,
}: DirectorCommentaryPanelProps) {
  const sceneName = sceneState?.locationName || sessionState?.current_location || 'Scene unset'
  const sceneDetail = sceneState
    ? `${sceneState.sceneType}${sceneState.mood ? ` / ${sceneState.mood}` : ''}${
        sceneState.inCombat ? ' / combat' : ` / danger ${sceneState.dangerLevel}`
      }`
    : questTitle
  const latestResponseLabel = currentResponseEntry?.streaming
    ? 'Streaming'
    : currentResponseEntry
      ? 'Latest response ready'
      : 'Awaiting response'
  const responseSummary = latestDmText.trim()
    ? truncateText(latestDmText, 156)
    : 'No DM prose recorded yet.'
  const memoryHighlights = recentMemory.slice(0, 3)
  const packTitle = commentary?.pack?.title || commentary?.pack?.packId || ''
  const commentaryNotes = commentary?.commentary.slice(0, 3) ?? []

  return (
    <section
      id="director-commentary-panel"
      className="director-commentary-panel"
      aria-labelledby="director-commentary-title"
    >
      <div className="director-commentary-heading">
        <div>
          <span>{activeSessionTitle}</span>
          <h2 id="director-commentary-title">Director Commentary</h2>
        </div>
        <button
          type="button"
          aria-label="Close Director Commentary"
          title="Close Director Commentary"
          onClick={onClose}
        >
          <X size={17} />
        </button>
      </div>
      <dl className="director-commentary-list">
        {commentary?.enabled ? (
          <div>
            <dt>Pack</dt>
            <dd>
              <strong>{packTitle}</strong>
              <span>
                {commentary.summary.routeTakenCount} reached / {commentary.summary.roadsNotTakenCount} branch
                {commentary.summary.roadsNotTakenCount === 1 ? '' : 'es'} missed /{' '}
                {commentary.summary.undiscoveredRecordsCount} hidden
              </span>
            </dd>
          </div>
        ) : null}
        <div>
          <dt>Scene</dt>
          <dd>
            <strong>{sceneName}</strong>
            <span>{sceneDetail}</span>
          </dd>
        </div>
        <div>
          <dt>Pacing</dt>
          <dd>
            <strong>{latestResponseLabel}</strong>
            <span>
              {streamLabel} / {dmExecutionStats.time} / {dmExecutionStats.tokens} tokens
            </span>
          </dd>
        </div>
        <div>
          <dt>Tone</dt>
          <dd>
            <strong>{contentRatingLabel(contentSettings)}</strong>
            <span>{dmExecutionStats.model} at temp {dmExecutionStats.temperature}</span>
          </dd>
        </div>
        <div>
          <dt>Latest Beat</dt>
          <dd>
            <span>{responseSummary}</span>
          </dd>
        </div>
      </dl>
      {commentary?.enabled ? (
        <div className="director-pack-grid" aria-label="Campaign pack director notes">
          <section>
            <span>Route Taken</span>
            <DirectorCheckpointList emptyText="No checkpoints reached yet." items={commentary.routeTaken} />
          </section>
          <section>
            <span>Roads Not Taken</span>
            <DirectorCheckpointList emptyText="No alternate branches recorded yet." items={commentary.roadsNotTaken} />
          </section>
          <section>
            <span>Undiscovered</span>
            <DirectorUndiscoveredList records={commentary.undiscoveredRecords} />
          </section>
          {commentaryNotes.length ? (
            <section>
              <span>Notes</span>
              {commentaryNotes.map((note) => (
                <p key={note}>{note}</p>
              ))}
            </section>
          ) : null}
        </div>
      ) : null}
      <div className="director-memory-strip" aria-label="Director memory">
        <span>Memory</span>
        {memoryHighlights.length ? (
          memoryHighlights.map(([fact, source]) => (
            <p key={`${fact}-${source}`}>
              {fact}
              <small>{source}</small>
            </p>
          ))
        ) : (
          <p>No memory snippets recorded yet.</p>
        )}
      </div>
    </section>
  )
}
