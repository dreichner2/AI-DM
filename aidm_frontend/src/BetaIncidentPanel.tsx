import { useCallback, useEffect, useRef, useState } from 'react'
import { Download, RefreshCcw, X } from 'lucide-react'
import { ApiClientError, apiFetch } from './api'
import { ModalShell } from './ModalShell'
import type { BetaIncidentsResponse, BetaSessionQualityResponse, BetaSupportBundleResponse, JsonRecord } from './types'
import { useModalFocusTrap } from './useModalFocusTrap'

type BetaIncidentPanelProps = {
  baseUrl: string
  auth: string
  selectedSessionId: number | null
}

const INCIDENT_LIMIT = 20

type SupportBundleRequest = {
  sessionId: number | null
  scope: string
}

const INCIDENT_TYPE_LABELS: Record<string, string> = {
  failed_turn: 'Failed Turn',
  failed_canon_job: 'Failed Canon Job',
  bad_turn_report: 'Bad-Turn Report',
  telemetry_event: 'Telemetry Event',
}

function textValue(value: unknown, fallback = '') {
  return typeof value === 'string' && value.trim() ? value.trim() : fallback
}

function numberValue(value: unknown) {
  return typeof value === 'number' && Number.isFinite(value) ? value : null
}

function isRecord(value: unknown): value is JsonRecord {
  return Boolean(value) && typeof value === 'object' && !Array.isArray(value)
}

function incidentTypeLabel(incident: JsonRecord) {
  const type = textValue(incident.type, 'incident')
  return INCIDENT_TYPE_LABELS[type] ?? type.replace(/_/g, ' ')
}

function incidentSeverity(incident: JsonRecord) {
  const severity = textValue(incident.severity, 'medium').toLowerCase()
  return ['low', 'medium', 'high'].includes(severity) ? severity : 'medium'
}

function incidentMessage(incident: JsonRecord) {
  return textValue(incident.message, textValue(incident.event_name, 'Incident recorded.'))
}

function incidentKey(incident: JsonRecord, index: number) {
  return [
    incident.type,
    incident.turn_id,
    incident.job_id,
    incident.feedback_id,
    incident.event_name,
    index,
  ].filter((part) => part !== undefined && part !== null && part !== '').join(':')
}

function formatTime(value: unknown) {
  const text = textValue(value)
  if (!text) return ''
  const date = new Date(text)
  if (Number.isNaN(date.getTime())) return text
  return date.toLocaleString([], {
    month: 'short',
    day: 'numeric',
    hour: 'numeric',
    minute: '2-digit',
  })
}

function filenameTimestamp(date = new Date()) {
  return date.toISOString().replace(/[:.]/g, '-')
}

function downloadJsonFile(payload: BetaSupportBundleResponse, filename: string) {
  const blob = new Blob([JSON.stringify(payload, null, 2)], { type: 'application/json' })
  const objectUrl = URL.createObjectURL(blob)
  const anchor = document.createElement('a')
  anchor.href = objectUrl
  anchor.download = filename
  anchor.rel = 'noopener'
  anchor.style.display = 'none'
  document.body.appendChild(anchor)
  anchor.click()
  anchor.remove()
  URL.revokeObjectURL(objectUrl)
}

function incidentMeta(incident: JsonRecord) {
  const meta = []
  const campaignId = numberValue(incident.campaign_id)
  const sessionId = numberValue(incident.session_id)
  const turnId = numberValue(incident.turn_id)
  const jobId = numberValue(incident.job_id)
  const feedbackId = numberValue(incident.feedback_id)
  const count = numberValue(incident.count)
  const provider = textValue(incident.provider)
  const model = textValue(incident.model)
  const category = textValue(incident.category)
  const latencyMs = numberValue(incident.latency_ms)
  if (campaignId !== null) meta.push(`campaign ${campaignId}`)
  if (sessionId !== null) meta.push(`session ${sessionId}`)
  if (turnId !== null) meta.push(`turn ${turnId}`)
  if (jobId !== null) meta.push(`job ${jobId}`)
  if (feedbackId !== null) meta.push(`feedback ${feedbackId}`)
  if (provider || model) meta.push([provider, model].filter(Boolean).join(' / '))
  if (category) meta.push(category)
  if (latencyMs !== null) meta.push(`${latencyMs} ms`)
  if (count !== null) meta.push(`${count} event${count === 1 ? '' : 's'}`)
  return meta
}

function numberLabel(value: unknown, fallback = '0') {
  const numeric = numberValue(value)
  if (numeric === null) return fallback
  return Number.isInteger(numeric) ? String(numeric) : numeric.toFixed(1)
}

function percentLabel(value: unknown) {
  const numeric = numberValue(value)
  return numeric === null ? 'n/a' : `${Math.round(numeric * 100)}%`
}

function latencyLabel(value: unknown) {
  const numeric = numberValue(value)
  return numeric === null ? 'n/a' : `${Math.round(numeric)} ms`
}

function providerModelSummary(rows: JsonRecord[]) {
  const first = rows.find(isRecord)
  if (!first) return 'Provider/model: none'
  const provider = textValue(first.provider, 'unknown')
  const model = textValue(first.model, 'unknown')
  const turnCount = numberValue(first.turn_count)
  const countSuffix = turnCount === null ? '' : ` (${turnCount} turn${turnCount === 1 ? '' : 's'})`
  return `Provider/model: ${provider} / ${model}${countSuffix}`
}

function operatorSummaryDetails(value: unknown) {
  if (!isRecord(value)) return { headline: '', details: [] as string[] }
  const headline = textValue(value.headline)
  const details = Array.isArray(value.details)
    ? value.details.map((item) => textValue(item)).filter(Boolean)
    : []
  return { headline, details }
}

function qualityStatusLabel(status: unknown) {
  return textValue(status, 'clean').toLowerCase() === 'review' ? 'Review' : 'Clean'
}

export function BetaIncidentPanel({ baseUrl, auth, selectedSessionId }: BetaIncidentPanelProps) {
  const [incidents, setIncidents] = useState<BetaIncidentsResponse | null>(null)
  const [sessionQuality, setSessionQuality] = useState<BetaSessionQualityResponse | null>(null)
  const [loading, setLoading] = useState(false)
  const [qualityLoading, setQualityLoading] = useState(false)
  const [bundleLoadingKey, setBundleLoadingKey] = useState('')
  const [supportBundleRequest, setSupportBundleRequest] = useState<SupportBundleRequest | null>(null)
  const [error, setError] = useState('')
  const [qualityError, setQualityError] = useState('')
  const [bundleError, setBundleError] = useState('')
  const supportBundleDialogRef = useRef<HTMLElement | null>(null)
  const supportBundleReturnFocusRef = useRef<HTMLElement | null>(null)

  const loadIncidents = useCallback(async (cancelled?: () => boolean) => {
    if (cancelled?.()) return
    setLoading(true)
    setError('')
    try {
      const payload = await apiFetch<BetaIncidentsResponse>(
        baseUrl,
        `/api/beta/incidents?limit=${INCIDENT_LIMIT}`,
        auth,
      )
      if (!cancelled?.()) setIncidents(payload)
    } catch (loadError) {
      const message =
        loadError instanceof ApiClientError && loadError.status === 403
          ? 'Workspace admin access is required.'
          : loadError instanceof Error
            ? loadError.message
            : String(loadError)
      if (!cancelled?.()) setError(message)
    } finally {
      if (!cancelled?.()) setLoading(false)
    }
  }, [auth, baseUrl])

  const loadSessionQuality = useCallback(async (cancelled?: () => boolean) => {
    if (cancelled?.()) return
    if (selectedSessionId === null) {
      setSessionQuality(null)
      setQualityError('')
      setQualityLoading(false)
      return
    }
    setQualityLoading(true)
    setQualityError('')
    try {
      const payload = await apiFetch<BetaSessionQualityResponse>(
        baseUrl,
        `/api/beta/session-quality?session_id=${selectedSessionId}&limit=5`,
        auth,
      )
      if (!cancelled?.()) setSessionQuality(payload)
    } catch (loadError) {
      const message =
        loadError instanceof ApiClientError && loadError.status === 403
          ? 'Workspace admin access is required to inspect session quality.'
          : loadError instanceof Error
            ? loadError.message
            : String(loadError)
      if (!cancelled?.()) {
        setSessionQuality(null)
        setQualityError(message)
      }
    } finally {
      if (!cancelled?.()) setQualityLoading(false)
    }
  }, [auth, baseUrl, selectedSessionId])

  const downloadSupportBundle = useCallback(async (sessionId?: number | null) => {
    const scope = sessionId !== undefined && sessionId !== null ? `session-${sessionId}` : 'workspace'
    const params = new URLSearchParams({ limit: String(INCIDENT_LIMIT) })
    if (sessionId !== undefined && sessionId !== null) {
      params.set('session_id', String(sessionId))
    }
    setBundleLoadingKey(scope)
    setBundleError('')
    try {
      const payload = await apiFetch<BetaSupportBundleResponse>(
        baseUrl,
        `/api/beta/support-bundle?${params.toString()}`,
        auth,
      )
      downloadJsonFile(payload, `aidm-support-bundle-${scope}-${filenameTimestamp()}.json`)
    } catch (bundleLoadError) {
      const message =
        bundleLoadError instanceof ApiClientError && bundleLoadError.status === 403
          ? 'Workspace admin access is required to export support bundles.'
          : bundleLoadError instanceof Error
            ? bundleLoadError.message
            : String(bundleLoadError)
      setBundleError(message)
    } finally {
      setBundleLoadingKey('')
    }
  }, [auth, baseUrl])

  const closeSupportBundleConfirmation = useCallback(() => {
    setSupportBundleRequest(null)
  }, [])

  const requestSupportBundle = useCallback((sessionId: number | null, opener: HTMLElement) => {
    supportBundleReturnFocusRef.current = opener
    setSupportBundleRequest({
      sessionId,
      scope: sessionId === null ? 'workspace' : `session ${sessionId}`,
    })
  }, [])

  const confirmSupportBundleDownload = useCallback(() => {
    if (!supportBundleRequest) return
    const { sessionId } = supportBundleRequest
    closeSupportBundleConfirmation()
    void downloadSupportBundle(sessionId)
  }, [closeSupportBundleConfirmation, downloadSupportBundle, supportBundleRequest])

  useModalFocusTrap({
    activeKey: supportBundleRequest ? `support-bundle-${supportBundleRequest.scope}` : null,
    dialogRef: supportBundleDialogRef,
    onClose: closeSupportBundleConfirmation,
    returnFocusRef: supportBundleReturnFocusRef,
  })

  useEffect(() => {
    let cancelled = false
    void Promise.resolve().then(() => loadIncidents(() => cancelled))
    return () => {
      cancelled = true
    }
  }, [loadIncidents])

  useEffect(() => {
    let cancelled = false
    void Promise.resolve().then(() => loadSessionQuality(() => cancelled))
    return () => {
      cancelled = true
    }
  }, [loadSessionQuality])

  const incidentSummary = incidents?.summary
  const incidentRows = incidents?.incidents ?? []
  const displayError = error || bundleError
  const qualitySummary = sessionQuality?.summary ?? null
  const qualityStatus = qualityStatusLabel(qualitySummary?.quality_status)
  const qualityStatusClass = qualityStatus.toLowerCase()
  const operatorSummary = operatorSummaryDetails(sessionQuality?.operator_summary)
  const qualitySessionName = sessionQuality
    ? textValue(sessionQuality.session.name, `session ${sessionQuality.session.session_id ?? selectedSessionId ?? ''}`)
    : ''

  return (
    <section className="inspector-box beta-incident-panel" aria-label="Beta incidents">
      <div className="box-title">
        <h3>Beta Incidents</h3>
        <div className="incident-panel-actions">
          <button
            type="button"
            onClick={(event) => requestSupportBundle(null, event.currentTarget)}
            disabled={bundleLoadingKey === 'workspace'}
            aria-label="Export workspace support bundle"
          >
            <Download aria-hidden="true" size={14} />
            <span>{bundleLoadingKey === 'workspace' ? 'Exporting...' : 'Bundle'}</span>
          </button>
          <button
            type="button"
            onClick={() => void loadIncidents()}
            disabled={loading}
            aria-label="Refresh beta incidents"
          >
            <RefreshCcw aria-hidden="true" size={14} />
            <span>{loading ? 'Refreshing...' : 'Refresh'}</span>
          </button>
        </div>
      </div>

      <div className="incident-summary-grid" aria-label="Incident summary">
        <div>
          <span>Turns</span>
          <strong>{incidentSummary?.failed_turn_count ?? 0}</strong>
        </div>
        <div>
          <span>Canon</span>
          <strong>{incidentSummary?.failed_canon_job_count ?? 0}</strong>
        </div>
        <div>
          <span>Reports</span>
          <strong>{incidentSummary?.bad_turn_report_count ?? 0}</strong>
        </div>
        <div>
          <span>Events</span>
          <strong>{incidentSummary?.telemetry_incident_count ?? 0}</strong>
        </div>
      </div>

      <section className="session-quality-card" aria-label="Selected session quality">
        <div className="session-quality-header">
          <div>
            <h3>Session Quality</h3>
            <span>{selectedSessionId === null ? 'No session selected' : qualitySessionName || `session ${selectedSessionId}`}</span>
          </div>
          {sessionQuality ? (
            <strong className={`session-quality-status ${qualityStatusClass}`}>{qualityStatus}</strong>
          ) : null}
        </div>
        {selectedSessionId === null ? <div className="empty-row">Select a session to view quality.</div> : null}
        {qualityError ? <div className="bestiary-message error">{qualityError}</div> : null}
        {!qualityError && qualityLoading && !sessionQuality ? <div className="empty-row">Loading session quality...</div> : null}
        {!qualityError && sessionQuality && qualitySummary ? (
          <>
            <div className="session-quality-grid" aria-label="Session quality metrics">
              <div>
                <span>Turns</span>
                <strong>{numberLabel(qualitySummary.total_turn_count)}</strong>
              </div>
              <div>
                <span>Failed</span>
                <strong>
                  {numberLabel(qualitySummary.failed_turn_count)}
                  <small>{percentLabel(qualitySummary.turn_failure_rate)}</small>
                </strong>
              </div>
              <div>
                <span>P95</span>
                <strong>{latencyLabel(qualitySummary.dm_response_latency_ms_p95)}</strong>
              </div>
              <div>
                <span>Canon</span>
                <strong>
                  {numberLabel(qualitySummary.canon_job_failed_count)} / {numberLabel(qualitySummary.canon_job_count)}
                </strong>
              </div>
              <div>
                <span>Reports</span>
                <strong>{numberLabel(qualitySummary.bad_turn_report_count)}</strong>
              </div>
              <div>
                <span>Clarify</span>
                <strong>{numberLabel(qualitySummary.awaiting_clarification_turn_count)}</strong>
              </div>
            </div>
            <div className="session-quality-meta">
              <span>{providerModelSummary(sessionQuality.provider_model_turn_counts)}</span>
              <span>
                State/audit: {numberLabel(qualitySummary.state_mutation_count)} state,{' '}
                {numberLabel(qualitySummary.operator_action_count)} operator
              </span>
              <span>
                Coherence: {numberLabel(qualitySummary.coherence_feedback_avg, 'n/a')} from{' '}
                {numberLabel(qualitySummary.coherence_feedback_count)} report
                {numberValue(qualitySummary.coherence_feedback_count) === 1 ? '' : 's'}
              </span>
            </div>
            {operatorSummary.headline || operatorSummary.details.length ? (
              <div className="session-quality-operator-summary" aria-label="Operator session summary">
                {operatorSummary.headline ? <strong>{operatorSummary.headline}</strong> : null}
                {operatorSummary.details.length ? (
                  <ul>
                    {operatorSummary.details.map((detail) => <li key={detail}>{detail}</li>)}
                  </ul>
                ) : null}
              </div>
            ) : null}
          </>
        ) : null}
      </section>

      {displayError ? <div className="bestiary-message error">{displayError}</div> : null}
      {!displayError && loading && !incidents ? <div className="empty-row">Loading incidents...</div> : null}
      {!displayError && !loading && incidents && !incidentRows.length ? (
        <div className="empty-row">No beta incidents recorded.</div>
      ) : null}

      <div className="incident-list" aria-label="Beta incident list">
        {incidentRows.map((incident, index) => {
          const severity = incidentSeverity(incident)
          const meta = incidentMeta(incident)
          const createdAt = formatTime(incident.created_at)
          const sessionId = numberValue(incident.session_id)
          const sessionBundleKey = sessionId !== null ? `session-${sessionId}` : ''
          return (
            <article key={incidentKey(incident, index)} className={`incident-card severity-${severity}`}>
              <div className="incident-card-title">
                <strong>{incidentTypeLabel(incident)}</strong>
                <div className="incident-card-title-actions">
                  <span>{severity}</span>
                  {sessionId !== null ? (
                    <button
                      type="button"
                      className="incident-bundle-button"
                      onClick={(event) => requestSupportBundle(sessionId, event.currentTarget)}
                      disabled={bundleLoadingKey === sessionBundleKey}
                      aria-label={`Export support bundle for session ${sessionId}`}
                      title={`Export support bundle for session ${sessionId}`}
                    >
                      <Download aria-hidden="true" size={13} />
                    </button>
                  ) : null}
                </div>
              </div>
              <p>{incidentMessage(incident)}</p>
              {meta.length ? (
                <div className="incident-meta">
                  {meta.map((item) => <span key={item}>{item}</span>)}
                </div>
              ) : null}
              {createdAt ? <time>{createdAt}</time> : null}
            </article>
          )
        })}
      </div>
      {supportBundleRequest ? (
        <ModalShell
          className="campaign-dialog support-bundle-dialog"
          dialogRef={supportBundleDialogRef}
          labelledBy="support-bundle-confirmation-title"
          describedBy="support-bundle-confirmation-description"
          onClose={closeSupportBundleConfirmation}
        >
          <header>
            <div>
              <span>Operator export</span>
              <h2 id="support-bundle-confirmation-title">Confirm support bundle export</h2>
            </div>
            <button
              type="button"
              aria-label="Close support bundle confirmation"
              onClick={closeSupportBundleConfirmation}
            >
              <X aria-hidden="true" size={18} />
            </button>
          </header>
          <div className="dialog-body">
            <div id="support-bundle-confirmation-description" className="dialog-warning">
              <strong>{supportBundleRequest.scope} support bundle</strong>
              <span>
                This bundle can contain session IDs, provider/model metadata, and audit references
                and should be handled as operator data. Review where you store or share it.
              </span>
            </div>
            <footer>
              <button
                type="button"
                className="secondary"
                data-autofocus
                onClick={closeSupportBundleConfirmation}
              >
                Cancel
              </button>
              <button type="button" onClick={confirmSupportBundleDownload}>
                <Download aria-hidden="true" size={14} />
                Download bundle
              </button>
            </footer>
          </div>
        </ModalShell>
      ) : null}
    </section>
  )
}
