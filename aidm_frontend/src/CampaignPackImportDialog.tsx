import { useCallback, useMemo, useState, type ChangeEvent, type FormEvent } from 'react'
import { AlertTriangle, CheckCircle2, FileJson, Upload } from 'lucide-react'
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
  const [error, setError] = useState('')
  const [previewPending, setPreviewPending] = useState(false)
  const [importPending, setImportPending] = useState(false)

  const canImport = Boolean(preview && packPayload && !previewPending && !importPending)
  const pending = previewPending || importPending
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
        setPackPayload(null)
        return
      }

      let parsed: unknown
      try {
        parsed = JSON.parse(trimmed)
      } catch {
        setError('Campaign pack JSON could not be parsed.')
        setPreview(null)
        setPackPayload(null)
        return
      }

      setPreviewPending(true)
      setError('')
      setPreview(null)
      setPackPayload(parsed)
      setFileName(nextFileName)
      try {
        const response = await apiFetch<CampaignPackImportResponse>(
          baseUrl,
          '/api/campaigns/import-pack?dry_run=true',
          auth,
          {
            method: 'POST',
            body: JSON.stringify(parsed),
          },
        )
        setPreview(response)
      } catch (requestError) {
        const message = errorMessage(requestError)
        setError(message)
        setPreview(null)
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
    <form className="campaign-pack-import-form" onSubmit={(event) => void submitPreview(event)}>
      <label className="file-picker-field">
        Campaign Pack JSON
        <input
          data-autofocus
          type="file"
          accept="application/json,.json"
          onChange={(event) => void loadFile(event)}
          disabled={pending}
        />
      </label>
      <label>
        JSON Preview
        <textarea
          value={packText}
          onChange={(event) => {
            setPackText(event.target.value)
            setPreview(null)
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
        <div className="campaign-pack-preview" aria-live="polite">
          <div className="campaign-pack-preview-title">
            <CheckCircle2 size={16} aria-hidden="true" />
            <span>
              <strong>{preview.preview?.title || preview.pack_id}</strong>
              <small>{preview.pack_id} / schema {preview.schema_version || '1'} / version {preview.pack_version || '1.0.0'}</small>
            </span>
          </div>
          <div className="campaign-pack-preview-grid">
            <div>
              <span>World</span>
              <strong>{worldLabel}</strong>
            </div>
            <div>
              <span>Start</span>
              <strong>{preview.preview?.starting_location || preview.preview?.starting_location_id || 'Unset'}</strong>
            </div>
            <div>
              <span>Quest</span>
              <strong>{preview.preview?.starting_quest || preview.preview?.starting_quest_id || 'Unset'}</strong>
            </div>
          </div>
          <div className="campaign-pack-counts">
            {countKeys.map(([key, label]) => (
              <div key={key}>
                <span>{label}</span>
                <strong>{preview.counts[key] ?? 0}</strong>
              </div>
            ))}
          </div>
          <div className="campaign-pack-visible">
            <span>Visible at start</span>
            <strong>{formatVisibleIds(preview.preview?.visible_at_start?.locations)}</strong>
            <strong>{formatVisibleIds(preview.preview?.visible_at_start?.npcs)}</strong>
            <strong>{formatVisibleIds(preview.preview?.visible_at_start?.quests)}</strong>
          </div>
        </div>
      ) : null}

      {error ? (
        <div className="dialog-error">
          <AlertTriangle size={14} aria-hidden="true" />
          {error}
        </div>
      ) : null}

      <footer>
        <button type="button" className="secondary" onClick={onClose} disabled={pending}>
          Cancel
        </button>
        <button type="submit" className="secondary" disabled={previewPending || importPending}>
          <FileJson size={15} aria-hidden="true" />
          {previewPending ? 'Previewing...' : fileName ? 'Preview Again' : 'Preview Pack'}
        </button>
        <button type="button" onClick={() => void importPack()} disabled={!canImport}>
          <Upload size={15} aria-hidden="true" />
          {importPending ? 'Importing...' : 'Create Campaign from Pack'}
        </button>
      </footer>
    </form>
  )
}
