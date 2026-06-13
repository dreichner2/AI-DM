import { useMemo, useState } from 'react'
import { FastForward, RotateCcw, Route, SkipForward, XCircle } from 'lucide-react'
import type { JsonRecord } from './types'

export type CampaignPackControlAction = 'advance' | 'skip' | 'fail' | 'rewind' | 'override'

type CampaignPackPanelProps = {
  snapshot: JsonRecord | null | undefined
  pendingAction: string | null
  onControl: (
    action: CampaignPackControlAction,
    checkpointId?: string | null,
    reason?: string,
  ) => Promise<void>
}

type PackCheckpoint = {
  id: string
  title: string
  summary: string
  nextCheckpointIds: string[]
  optional: boolean
  directorRules: JsonRecord
}

type PackSummary = {
  packId: string
  title: string
  version: string
  schemaVersion: string
  sourceTags: string[]
  policy: JsonRecord
  activePolicy: JsonRecord
  checkpoints: PackCheckpoint[]
  activeCheckpoint: PackCheckpoint | null
  completedIds: string[]
  skippedIds: string[]
  failedIds: string[]
  checkpointStatuses: Record<string, string>
  activeQuestIds: string[]
  currentLocationId: string
}

function isRecord(value: unknown): value is JsonRecord {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function stringValue(value: unknown) {
  return typeof value === 'string' || typeof value === 'number' ? String(value).trim() : ''
}

function stringList(value: unknown) {
  if (typeof value === 'string') {
    return value.split(',').map((item) => item.trim()).filter(Boolean)
  }
  if (!Array.isArray(value)) return []
  return value.map(stringValue).filter(Boolean)
}

function unique(values: string[]) {
  const result: string[] = []
  const seen = new Set<string>()
  values.forEach((value) => {
    const key = value.toLowerCase()
    if (!key || seen.has(key)) return
    seen.add(key)
    result.push(value)
  })
  return result
}

function checkpointId(checkpoint: JsonRecord) {
  return stringValue(checkpoint.id ?? checkpoint.checkpointId ?? checkpoint.checkpoint_id)
}

function checkpointTitle(checkpoint: JsonRecord) {
  return stringValue(checkpoint.title ?? checkpoint.name) || checkpointId(checkpoint)
}

function checkpointNextIds(checkpoint: JsonRecord) {
  return stringList(
    checkpoint.nextCheckpointIds ??
      checkpoint.next_checkpoint_ids ??
      checkpoint.unlocks ??
      checkpoint.downstreamCheckpointIds ??
      checkpoint.downstream_checkpoint_ids,
  )
}

function booleanValue(value: unknown) {
  return value === true || stringValue(value).toLowerCase() === 'true'
}

function packCheckpointFromRecord(record: JsonRecord): PackCheckpoint | null {
  const id = checkpointId(record)
  if (!id) return null
  return {
    id,
    title: checkpointTitle(record),
    summary: stringValue(record.summary ?? record.description),
    nextCheckpointIds: checkpointNextIds(record),
    optional: booleanValue(record.optional ?? record.isOptional ?? record.is_optional),
    directorRules: isRecord(record.directorRules) ? record.directorRules : {},
  }
}

function packSummaryFromSnapshot(snapshot: JsonRecord | null | undefined): PackSummary | null {
  if (!snapshot) return null
  const pack = isRecord(snapshot.campaignPack) ? snapshot.campaignPack : null
  if (!pack) return null
  const packId = stringValue(pack.packId ?? pack.pack_id)
  if (!packId) return null

  const flags = isRecord(snapshot.flags) ? snapshot.flags : {}
  const scene = isRecord(snapshot.currentScene) ? snapshot.currentScene : {}
  const checkpoints = Array.isArray(pack.checkpoints)
    ? pack.checkpoints.filter(isRecord).map(packCheckpointFromRecord).filter((item): item is PackCheckpoint => Boolean(item))
    : []
  const completedIds = unique(
    stringList(pack.completedCheckpointIds ?? pack.completed_checkpoint_ids).length
      ? stringList(pack.completedCheckpointIds ?? pack.completed_checkpoint_ids)
      : stringList(flags.campaignPackCompletedCheckpointIds ?? flags.completedCheckpointIds),
  )
  const skippedIds = unique(
    stringList(pack.skippedCheckpointIds ?? pack.skipped_checkpoint_ids).length
      ? stringList(pack.skippedCheckpointIds ?? pack.skipped_checkpoint_ids)
      : stringList(flags.campaignPackSkippedCheckpointIds ?? flags.skippedCheckpointIds),
  )
  const failedIds = unique(
    stringList(pack.failedCheckpointIds ?? pack.failed_checkpoint_ids).length
      ? stringList(pack.failedCheckpointIds ?? pack.failed_checkpoint_ids)
      : stringList(flags.campaignPackFailedCheckpointIds ?? flags.failedCheckpointIds),
  )
  const checkpointStatuses = isRecord(pack.checkpointStatuses)
    ? Object.fromEntries(
        Object.entries(pack.checkpointStatuses)
          .map(([key, value]) => [key, stringValue(value)])
          .filter((entry): entry is [string, string] => Boolean(entry[0] && entry[1])),
      )
    : {}
  const activeId =
    stringValue(
      pack.activeCheckpointId ??
        pack.active_checkpoint_id ??
        pack.currentCheckpointId ??
        pack.current_checkpoint_id ??
        flags.campaignPackActiveCheckpointId ??
        flags.activeCheckpointId,
    ) || checkpoints.find((checkpoint) => !completedIds.includes(checkpoint.id))?.id || ''
  const activeCheckpoint = checkpoints.find((checkpoint) => checkpoint.id === activeId) ?? null
  const policy = isRecord(pack.directorRules) ? pack.directorRules : {}
  const activePolicy = isRecord(pack.activeDirectorRules)
    ? pack.activeDirectorRules
    : { ...policy, ...(activeCheckpoint?.directorRules ?? {}) }

  return {
    packId,
    title: stringValue(pack.title ?? pack.name) || packId,
    version: stringValue(pack.version) || '1.0.0',
    schemaVersion: stringValue(pack.schemaVersion ?? pack.schema_version) || '1',
    sourceTags: ['campaign_pack', `pack:${packId}`],
    policy,
    activePolicy,
    checkpoints,
    activeCheckpoint,
    completedIds,
    skippedIds,
    failedIds,
    checkpointStatuses,
    activeQuestIds: stringList(scene.activeQuestIds),
    currentLocationId: stringValue(scene.locationId ?? scene.location_id),
  }
}

function policyLabel(policy: JsonRecord, key: string, fallback: string) {
  return stringValue(policy[key]) || fallback
}

export function CampaignPackPanel({ snapshot, pendingAction, onControl }: CampaignPackPanelProps) {
  const summary = useMemo(() => packSummaryFromSnapshot(snapshot), [snapshot])
  const [overrideCheckpointId, setOverrideCheckpointId] = useState('')

  if (!summary) return null

  const pending = pendingAction !== null
  const completedSet = new Set(summary.completedIds)
  const skippedSet = new Set(summary.skippedIds)
  const failedSet = new Set(summary.failedIds)
  const selectedOverrideCheckpointId = summary.checkpoints.some((checkpoint) => checkpoint.id === overrideCheckpointId)
    ? overrideCheckpointId
    : summary.activeCheckpoint?.id || summary.checkpoints[0]?.id || ''

  return (
    <section className="inspector-box campaign-pack-panel">
      <div className="box-title">
        <h3>Campaign Pack</h3>
        <span>{summary.version}</span>
      </div>
      <div className="campaign-pack-header">
        <div>
          <strong>{summary.title}</strong>
          <small>{summary.packId} / schema {summary.schemaVersion}</small>
        </div>
        <div className="campaign-pack-tags">
          {summary.sourceTags.map((tag) => (
            <span key={tag}>{tag}</span>
          ))}
        </div>
      </div>

      <div className="campaign-pack-current">
        <span>Active checkpoint</span>
        <strong>{summary.activeCheckpoint?.title || 'None'}</strong>
        {summary.activeCheckpoint?.summary ? <p>{summary.activeCheckpoint.summary}</p> : null}
      </div>

      <div className="campaign-pack-policy-grid">
        <div>
          <span>Main</span>
          <strong>{policyLabel(summary.activePolicy, 'mainQuestGeneration', 'allowed_tagged')}</strong>
        </div>
        <div>
          <span>Side</span>
          <strong>{policyLabel(summary.activePolicy, 'sideQuestGeneration', 'allowed_tagged')}</strong>
        </div>
        <div>
          <span>Off track</span>
          <strong>{policyLabel(summary.activePolicy, 'offTrackPolicy', 'improvise_and_reconnect')}</strong>
        </div>
      </div>

      <div className="campaign-pack-progress-list" aria-label="Campaign pack checkpoints">
        {summary.checkpoints.slice(0, 8).map((checkpoint) => {
          const status = summary.checkpointStatuses[checkpoint.id] ||
            (checkpoint.id === summary.activeCheckpoint?.id
              ? 'active'
              : failedSet.has(checkpoint.id)
                ? 'failed'
                : skippedSet.has(checkpoint.id)
                  ? 'skipped'
                  : completedSet.has(checkpoint.id)
                    ? 'done'
                    : checkpoint.optional
                      ? 'optional'
                      : 'open')
          return (
            <div key={checkpoint.id} className={status}>
              <span>{status}</span>
              <strong>{checkpoint.title}</strong>
            </div>
          )
        })}
      </div>

      <div className="campaign-pack-context-row">
        <span>Location</span>
        <strong>{summary.currentLocationId || 'Unset'}</strong>
        <span>Active quests</span>
        <strong>{summary.activeQuestIds.length || 0}</strong>
      </div>

      <div className="campaign-pack-actions" aria-label="Campaign pack checkpoint controls">
        <button
          type="button"
          onClick={() => void onControl('advance', null, 'Manual advance')}
          disabled={pending || !summary.activeCheckpoint}
        >
          <FastForward size={13} aria-hidden="true" />
          {pendingAction === 'advance' ? 'Advancing...' : 'Advance'}
        </button>
        <button
          type="button"
          onClick={() => void onControl('skip', null, 'Manual skip')}
          disabled={pending || !summary.activeCheckpoint}
        >
          <SkipForward size={13} aria-hidden="true" />
          {pendingAction === 'skip' ? 'Skipping...' : 'Skip'}
        </button>
        <button
          type="button"
          onClick={() => void onControl('fail', null, 'Manual fail')}
          disabled={pending || !summary.activeCheckpoint}
        >
          <XCircle size={13} aria-hidden="true" />
          {pendingAction === 'fail' ? 'Failing...' : 'Fail'}
        </button>
        <button
          type="button"
          onClick={() => void onControl('rewind', null, 'Manual rewind')}
          disabled={pending}
        >
          <RotateCcw size={13} aria-hidden="true" />
          {pendingAction === 'rewind' ? 'Rewinding...' : 'Rewind'}
        </button>
      </div>

      <div className="campaign-pack-override">
        <select
          value={selectedOverrideCheckpointId}
          onChange={(event) => setOverrideCheckpointId(event.target.value)}
          disabled={pending}
          aria-label="Override active checkpoint"
        >
          {summary.checkpoints.map((checkpoint) => (
            <option key={checkpoint.id} value={checkpoint.id}>
              {checkpoint.title}
            </option>
          ))}
        </select>
        <button
          type="button"
          onClick={() => void onControl('override', selectedOverrideCheckpointId, 'Manual checkpoint override')}
          disabled={pending || !selectedOverrideCheckpointId}
        >
          <Route size={13} aria-hidden="true" />
          {pendingAction === 'override' ? 'Setting...' : 'Override'}
        </button>
      </div>
    </section>
  )
}
