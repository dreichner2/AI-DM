import { useCallback, useMemo, useState, type ChangeEvent, type FormEvent } from 'react'
import { AlertTriangle, CheckCircle2, FileJson, GitBranch, Sparkles, Upload } from 'lucide-react'
import { ApiClientError, apiFetch } from './api'

type CampaignPackCounts = {
  locations?: number
  npcs?: number
  quests?: number
  segments?: number
  checkpoints?: number
  encounters?: number
  enemies?: number
  bestiary_entries?: number
}

type CampaignPackPreview = {
  title?: string
  description?: string
  world?: {
    mode?: string
    world_id?: number | null
    name?: string | null
    description?: string | null
  }
  starting_location?: string | null
  starting_location_id?: string | null
  starting_quest?: string | null
  starting_quest_id?: string | null
  visible_at_start?: {
    locations?: string[]
    npcs?: string[]
    quests?: string[]
  }
}

type CampaignPackImportResponse = {
  dry_run?: boolean
  imported: boolean
  pack_id: string
  schema_version?: string
  pack_version?: string
  campaign_id?: number
  session_id?: number
  counts: CampaignPackCounts
  preview?: CampaignPackPreview
}

type CampaignPackLintIssue = {
  severity: 'error' | 'warning' | string
  code: string
  path: string
  message: string
}

type CampaignPackAuthoringCollection = {
  collection?: string
  count?: number
  visibleAtStartCount?: number
  hiddenToPlayersCount?: number
  visibleAtStartIds?: string[]
  hiddenToPlayersIds?: string[]
}

type CampaignPackLintResponse = {
  ok: boolean
  issues: CampaignPackLintIssue[]
  preview?: CampaignPackImportResponse | null
  authoring_report?: {
    starting?: {
      locationId?: string
      questId?: string
      checkpointId?: string
    }
    collections?: CampaignPackAuthoringCollection[]
    checkpoints?: {
      total?: number
      reachable?: number
      unreachableIds?: string[]
      optionalIds?: string[]
      terminalIds?: string[]
      items?: Array<{
        id?: string
        title?: string
        reachable?: boolean
        optional?: boolean
        terminal?: boolean
        branchCount?: number
        encounterIds?: string[]
        completionCues?: string[]
      }>
    }
    encounters?: {
      total?: number
      linkedToCheckpoint?: number
      unlinkedIds?: string[]
      items?: Array<{
        id?: string
        title?: string
        checkpointIds?: string[]
        enemyCount?: number
        completionOutcomes?: string[]
      }>
    }
  }
  graph?: {
    nodes?: string[]
    edges?: Array<{ from?: string; to?: string; type?: string }>
    reachable?: string[]
  }
  summary?: {
    packId?: string
    title?: string
    version?: string
    counts?: Record<string, number>
  }
}

type CampaignPackForgeResponse = {
  ok: boolean
  sourceFilename: string
  pack: Record<string, unknown>
  payload?: unknown
  manifestText?: string
  lint?: CampaignPackLintResponse
}

type CampaignPackImportDialogProps = {
  auth: string
  baseUrl: string
  onClose: () => void
  onImported: (campaignId: number, sessionId: number) => Promise<void>
  pushError: (category: 'persistence' | 'validation', message: string) => void
}

const countKeys: Array<[keyof CampaignPackCounts, string]> = [
  ['locations', 'Locations'],
  ['npcs', 'NPCs'],
  ['quests', 'Quests'],
  ['segments', 'Segments'],
  ['checkpoints', 'Checkpoints'],
  ['encounters', 'Encounters'],
  ['enemies', 'Enemies'],
  ['bestiary_entries', 'Bestiary'],
]

function errorMessage(error: unknown) {
  if (error instanceof ApiClientError && typeof error.payload === 'object' && error.payload) {
    const payload = error.payload as { error?: unknown; error_code?: unknown }
    const message = typeof payload.error === 'string' ? payload.error : error.message
    return typeof payload.error_code === 'string' ? `${payload.error_code}: ${message}` : message
  }
  return error instanceof Error ? error.message : String(error)
}

function formatVisibleIds(values: string[] | undefined) {
  if (!values?.length) return 'None'
  return values.slice(0, 4).join(', ') + (values.length > 4 ? ` +${values.length - 4}` : '')
}

function reportCollections(collections: CampaignPackAuthoringCollection[] | undefined) {
  return Array.isArray(collections) ? collections.filter((item) => item.count) : []
}

export function CampaignPackImportDialog({
  auth,
  baseUrl,
  onClose,
  onImported,
  pushError,
}: CampaignPackImportDialogProps) {
  const [fileName, setFileName] = useState('')
  const [packText, setPackText] = useState('')
  const [packPayload, setPackPayload] = useState<unknown>(null)
  const [preview, setPreview] = useState<CampaignPackImportResponse | null>(null)
  const [lintResult, setLintResult] = useState<CampaignPackLintResponse | null>(null)
  const [error, setError] = useState('')
  const [forgeTitle, setForgeTitle] = useState('')
  const [forgePrompt, setForgePrompt] = useState('')
  const [forgePending, setForgePending] = useState(false)
  const [previewPending, setPreviewPending] = useState(false)
  const [importPending, setImportPending] = useState(false)

  const hasLintErrors = Boolean(lintResult?.issues.some((issue) => issue.severity === 'error'))
  const canImport = Boolean(preview && packPayload && !previewPending && !importPending && !forgePending && !hasLintErrors)
  const pending = previewPending || importPending || forgePending
  const authoringReport = lintResult?.authoring_report
  const reportCollectionRows = reportCollections(authoringReport?.collections)
  const checkpointReport = authoringReport?.checkpoints
  const encounterReport = authoringReport?.encounters
  const worldLabel = useMemo(() => {
    const world = preview?.preview?.world
    if (!world) return 'Not resolved'
    return world.mode === 'existing'
      ? `${world.name || `World ${world.world_id}`} / existing`
      : `${world.name || 'New world'} / new`
  }, [preview])

  const previewPack = useCallback(
    async (text: string, nextFileName = fileName) => {
      const trimmed = text.trim()
      if (!trimmed) {
        setError('Choose a campaign pack JSON file.')
        setPreview(null)
        setLintResult(null)
        setPackPayload(null)
        return
      }

      let parsed: unknown
      try {
        parsed = JSON.parse(trimmed)
      } catch {
        setError('Campaign pack JSON could not be parsed.')
        setPreview(null)
        setLintResult(null)
        setPackPayload(null)
        return
      }

      setPreviewPending(true)
      setError('')
      setPreview(null)
      setPackPayload(parsed)
      setFileName(nextFileName)
      try {
        const lintResponse = await apiFetch<CampaignPackLintResponse>(
          baseUrl,
          '/api/campaigns/pack-tools/lint',
          auth,
          {
            method: 'POST',
            body: JSON.stringify(parsed),
          },
        )
        setLintResult(lintResponse)
        setPreview(lintResponse.preview ?? null)
      } catch (requestError) {
        const message = errorMessage(requestError)
        setError(message)
        setPreview(null)
        setLintResult(null)
        pushError('validation', `Campaign pack preview failed: ${message}`)
      } finally {
        setPreviewPending(false)
      }
    },
    [auth, baseUrl, fileName, pushError],
  )

  const loadFile = useCallback(
    async (event: ChangeEvent<HTMLInputElement>) => {
      const file = event.target.files?.[0]
      if (!file) return
      const text = await file.text()
      setPackText(text)
      await previewPack(text, file.name)
    },
    [previewPack],
  )

  const forgePack = useCallback(async () => {
    const title = forgeTitle.trim()
    if (!title) {
      setError('Pack title is required.')
      return
    }
    setForgePending(true)
    setError('')
    try {
      const response = await apiFetch<CampaignPackForgeResponse>(
        baseUrl,
        '/api/campaigns/pack-tools/forge',
        auth,
        {
          method: 'POST',
          body: JSON.stringify({
            title,
            prompt: forgePrompt,
          }),
        },
      )
      const nextPayload = response.payload ?? {
        sourceFilename: response.sourceFilename,
        pack: response.pack,
      }
      const nextText = response.manifestText || JSON.stringify(nextPayload, null, 2)
      setFileName(response.sourceFilename)
      setPackText(nextText)
      setPackPayload(nextPayload)
      setLintResult(response.lint ?? null)
      setPreview(response.lint?.preview ?? null)
      if (response.lint && !response.lint.ok) {
        setError('Generated pack needs authoring attention before import.')
      }
    } catch (requestError) {
      const message = errorMessage(requestError)
      setError(message)
      setPreview(null)
      setLintResult(null)
      setPackPayload(null)
      pushError('validation', `Campaign pack forge failed: ${message}`)
    } finally {
      setForgePending(false)
    }
  }, [auth, baseUrl, forgePrompt, forgeTitle, pushError])

  const submitPreview = useCallback(
    async (event: FormEvent<HTMLFormElement>) => {
      event.preventDefault()
      await previewPack(packText)
    },
    [packText, previewPack],
  )

  const importPack = useCallback(async () => {
    if (!packPayload) {
      setError('Preview a valid campaign pack before importing.')
      return
    }
    setImportPending(true)
    setError('')
    try {
      const response = await apiFetch<CampaignPackImportResponse>(
        baseUrl,
        '/api/campaigns/import-pack',
        auth,
        {
          method: 'POST',
          body: JSON.stringify(packPayload),
        },
      )
      if (!response.campaign_id || !response.session_id) {
        throw new Error('Campaign pack import did not return a campaign and session.')
      }
      await onImported(response.campaign_id, response.session_id)
    } catch (requestError) {
      const message = errorMessage(requestError)
      setError(message)
      pushError('persistence', `Campaign pack import failed: ${message}`)
    } finally {
      setImportPending(false)
    }
  }, [auth, baseUrl, onImported, packPayload, pushError])

  return (
    <form className="story-dialog-body" onSubmit={(event) => void submitPreview(event)}>
      <section className="story-dialog-section" aria-labelledby="campaign-pack-forge-title">
        <h3 id="campaign-pack-forge-title" className="story-label">
          <Sparkles size={13} aria-hidden="true" />
          Pack Forge
        </h3>
        <div className="story-dialog-field-pair">
          <label className="story-dialog-field">
            <span className="story-label">Pack Title</span>
            <input
              type="text"
              value={forgeTitle}
              onChange={(event) => setForgeTitle(event.target.value)}
              disabled={pending}
              maxLength={120}
              placeholder="Lanterns under Blackwater"
            />
          </label>
          <label className="story-dialog-field">
            <span className="story-label">Premise</span>
            <textarea
              value={forgePrompt}
              onChange={(event) => setForgePrompt(event.target.value)}
              rows={2}
              disabled={pending}
              maxLength={1200}
              placeholder="Harbor intrigue, a drowned archive, and a guide with divided loyalties."
            />
          </label>
        </div>
        <div className="story-dialog-links">
          <button
            type="button"
            className="story-link"
            onClick={() => void forgePack()}
            disabled={pending || !forgeTitle.trim()}
          >
            {forgePending ? 'Forging...' : 'Forge Pack'}
          </button>
        </div>
      </section>

      <hr className="story-rule" />
      <label className="story-dialog-field story-dialog-file">
        <span className="story-label">Campaign Pack JSON</span>
        <input
          data-autofocus
          type="file"
          accept="application/json,.json"
          onChange={(event) => void loadFile(event)}
          disabled={pending}
        />
      </label>
      <label className="story-dialog-field">
        <span className="story-label">JSON Preview</span>
        <textarea
          className="story-dialog-code"
          value={packText}
          onChange={(event) => {
            setPackText(event.target.value)
            setPreview(null)
            setLintResult(null)
            setPackPayload(null)
            setError('')
          }}
          rows={7}
          spellCheck={false}
          disabled={pending}
          placeholder="{"
        />
      </label>

      {preview ? (
        <section className="story-dialog-section" aria-live="polite">
          <p className="story-dialog-subject">
            <CheckCircle2 size={16} aria-hidden="true" />
            {preview.preview?.title || preview.pack_id}
          </p>
          <p className="story-dialog-row-meta">
            {preview.pack_id} · schema {preview.schema_version || '1'} · version {preview.pack_version || '1.0.0'}
          </p>
          <dl className="story-dialog-facts">
            <div>
              <dt className="story-label">World</dt>
              <dd>{worldLabel}</dd>
            </div>
            <div>
              <dt className="story-label">Start</dt>
              <dd>{preview.preview?.starting_location || preview.preview?.starting_location_id || 'Unset'}</dd>
            </div>
            <div>
              <dt className="story-label">Quest</dt>
              <dd>{preview.preview?.starting_quest || preview.preview?.starting_quest_id || 'Unset'}</dd>
            </div>
          </dl>
          <p className="story-dialog-line">
            {countKeys.map(([key, label]) => `${preview.counts[key] ?? 0} ${label}`).join(' · ')}
          </p>
          <p className="story-dialog-line">
            Visible at start: {formatVisibleIds(preview.preview?.visible_at_start?.locations)} ·{' '}
            {formatVisibleIds(preview.preview?.visible_at_start?.npcs)} ·{' '}
            {formatVisibleIds(preview.preview?.visible_at_start?.quests)}
          </p>
        </section>
      ) : null}

      {lintResult ? (
        <section
          className="story-dialog-section pack-lint"
          data-state={lintResult.ok ? 'ok' : 'has-errors'}
          aria-live="polite"
        >
          <h3 className="story-label pack-lint-title">
            {lintResult.ok ? <CheckCircle2 size={13} aria-hidden="true" /> : <AlertTriangle size={13} aria-hidden="true" />}
            {lintResult.ok ? 'Authoring checks passed' : 'Authoring checks need attention'}
            <span>{lintResult.issues.length} issues</span>
          </h3>
          {lintResult.issues.length ? (
            <ul className="story-dialog-list pack-lint-list">
              {lintResult.issues.slice(0, 5).map((issue, index) => (
                <li
                  key={`${issue.code}-${issue.path}-${index}`}
                  className="story-dialog-row"
                  data-severity={issue.severity === 'error' ? 'error' : 'warning'}
                >
                  <span className="story-dialog-row-copy">
                    <span className="story-dialog-row-name">{issue.code}</span>
                    <span className="story-dialog-row-meta">
                      {issue.severity} · {issue.path}
                    </span>
                    <span className="story-dialog-line">{issue.message}</span>
                  </span>
                </li>
              ))}
            </ul>
          ) : null}
          {lintResult.graph ? (
            <p className="story-dialog-line">
              <GitBranch size={14} aria-hidden="true" /> {lintResult.graph.nodes?.length ?? 0} nodes ·{' '}
              {lintResult.graph.edges?.length ?? 0} edges · {lintResult.graph.reachable?.length ?? 0} reachable
            </p>
          ) : null}
          {authoringReport ? (
            <div aria-label="Campaign pack authoring report">
              <dl className="story-dialog-facts">
                <div>
                  <dt className="story-label">Checkpoint Spine</dt>
                  <dd>
                    {checkpointReport?.reachable ?? 0} / {checkpointReport?.total ?? 0} reachable
                  </dd>
                  <dd className="story-dialog-row-meta">
                    {(checkpointReport?.optionalIds?.length ?? 0)} optional ·{' '}
                    {(checkpointReport?.terminalIds?.length ?? 0)} terminal
                  </dd>
                </div>
                <div>
                  <dt className="story-label">Encounters</dt>
                  <dd>
                    {encounterReport?.linkedToCheckpoint ?? 0} / {encounterReport?.total ?? 0} linked
                  </dd>
                  <dd className="story-dialog-row-meta">{formatVisibleIds(encounterReport?.unlinkedIds)}</dd>
                </div>
                <div>
                  <dt className="story-label">Authored Records</dt>
                  <dd>{reportCollectionRows.reduce((total, row) => total + (row.count ?? 0), 0)}</dd>
                  <dd className="story-dialog-row-meta">{reportCollectionRows.length} populated groups</dd>
                </div>
              </dl>
              {reportCollectionRows.length ? (
                <ul className="story-dialog-inline-list">
                  {reportCollectionRows.slice(0, 8).map((row) => (
                    <li key={row.collection}>
                      {row.collection}: {row.count}
                      {row.visibleAtStartCount ? ` / ${row.visibleAtStartCount} visible` : ''}
                      {row.hiddenToPlayersCount ? ` / ${row.hiddenToPlayersCount} hidden` : ''}
                    </li>
                  ))}
                </ul>
              ) : null}
              {checkpointReport?.unreachableIds?.length ? (
                <p className="story-dialog-note story-dialog-warning">
                  Unreachable checkpoints: {formatVisibleIds(checkpointReport.unreachableIds)}
                </p>
              ) : null}
            </div>
          ) : null}
        </section>
      ) : null}

      {error ? (
        <p className="story-dialog-error" role="alert">
          {error}
        </p>
      ) : null}

      <footer className="story-dialog-footer">
        <button type="button" className="story-link" onClick={onClose} disabled={pending}>
          Cancel
        </button>
        <button type="submit" className="story-link" disabled={previewPending || importPending}>
          <FileJson size={15} aria-hidden="true" />
          {previewPending ? 'Checking...' : fileName ? 'Check Again' : 'Check Pack'}
        </button>
        <button type="button" className="story-primary" onClick={() => void importPack()} disabled={!canImport}>
          <Upload size={15} aria-hidden="true" />
          {importPending ? 'Importing...' : 'Create Campaign from Pack'}
        </button>
      </footer>
    </form>
  )
}
