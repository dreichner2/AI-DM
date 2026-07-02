import { useState, type ChangeEvent, type Dispatch, type FormEvent, type SetStateAction } from 'react'
import {
  addCookieCsrfHeader,
  addNgrokBrowserWarningBypassHeader,
  addWorkspaceTokenHeader,
  apiFetch,
  normalizeBaseUrl,
} from './api'
import { createClientMessageId } from './gameActions'
import type { MainTab } from './SessionBoard'
import type {
  BetaSummary,
  Campaign,
  CampaignCanon,
  CampaignSegment,
  JsonRecord,
  MapItem,
  Player,
  PlayerDetail,
  SessionEventsResponse,
  SessionImportResponse,
  SessionLogEntry,
  SessionState,
  SessionSummary,
  StreamingTurn,
  TimelineEntry,
} from './types'

type ValueUpdater<T> = T | ((current: T) => T)

export type SessionActionDialogState = {
  mode: 'rename' | 'delete'
  session: SessionSummary
  name: string
  error: string
  pending: boolean
} | null

type UseSessionActionsOptions = {
  auth: string
  baseUrl: string
  campaign: Campaign | null
  activeSession: SessionSummary | null
  sessionDisplayFallback: string | number | null
  selectedCampaignId: number | null
  selectedSessionId: number | null
  selectedPlayerId: number | null
  players: Player[]
  selectedPlayer: Player | null
  playerDetail: PlayerDetail | null
  sessionState: SessionState | null
  logEntries: SessionLogEntry[]
  maps: MapItem[]
  segments: CampaignSegment[]
  metrics: BetaSummary | null
  rememberDialogTrigger: (fallback?: HTMLElement | null) => void
  sessionMenuButton: () => HTMLElement | null
  sessionDisplayName: (session: SessionSummary, fallbackPrefix: string | number | null) => string
  loadSessionData: (sessionId: number) => Promise<void>
  refreshRoot: () => Promise<void>
  refreshCampaignWorkspace: (campaignId: number) => Promise<void>
  sessionUpserted: (session: SessionSummary) => void
  setSelectedCampaignId: (value: ValueUpdater<number | null>) => void
  setSelectedSessionId: (value: ValueUpdater<number | null>) => void
  setLogEntries: (value: ValueUpdater<SessionLogEntry[]>) => void
  setSessionState: (value: ValueUpdater<SessionState | null>) => void
  setOptimisticEntries: Dispatch<SetStateAction<TimelineEntry[]>>
  setStreamingTurn: Dispatch<SetStateAction<StreamingTurn | null>>
  setMainTab: Dispatch<SetStateAction<MainTab>>
  setSessionMenuOpen: Dispatch<SetStateAction<boolean>>
  pushError: (
    category: 'persistence' | 'workspace' | 'system',
    message: string,
  ) => void
}

function readFileText(file: File): Promise<string> {
  if (typeof file.text === 'function') return file.text()
  return new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = () => resolve(String(reader.result ?? ''))
    reader.onerror = () => reject(reader.error ?? new Error('Could not read import file.'))
    reader.readAsText(file)
  })
}

function filenameFromDisposition(disposition: string | null, fallback: string) {
  const match = disposition?.match(/filename\*=UTF-8''([^;]+)|filename="?([^"]+)"?/i)
  const encodedName = match?.[1] || match?.[2]
  if (!encodedName) return fallback
  try {
    return decodeURIComponent(encodedName)
  } catch {
    return encodedName
  }
}

async function downloadHtmlResource(
  baseUrl: string,
  path: string,
  token: string,
  fallbackFilename: string,
) {
  const headers = new Headers()
  if (token.trim()) {
    headers.set('Authorization', `Bearer ${token.trim()}`)
  }
  addWorkspaceTokenHeader(headers)
  addCookieCsrfHeader(headers)
  addNgrokBrowserWarningBypassHeader(headers, baseUrl)

  const response = await fetch(`${normalizeBaseUrl(baseUrl)}${path}`, { headers })
  if (!response.ok) {
    const text = await response.text().catch(() => '')
    throw new Error(text || `Download failed with status ${response.status}`)
  }

  const blob = await response.blob()
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = filenameFromDisposition(response.headers.get('Content-Disposition'), fallbackFilename)
  link.click()
  URL.revokeObjectURL(url)
}

export function useSessionActions({
  auth,
  baseUrl,
  campaign,
  activeSession,
  sessionDisplayFallback,
  selectedCampaignId,
  selectedSessionId,
  selectedPlayerId,
  players,
  selectedPlayer,
  playerDetail,
  sessionState,
  logEntries,
  maps,
  segments,
  metrics,
  rememberDialogTrigger,
  sessionMenuButton,
  sessionDisplayName,
  loadSessionData,
  refreshRoot,
  refreshCampaignWorkspace,
  sessionUpserted,
  setSelectedCampaignId,
  setSelectedSessionId,
  setLogEntries,
  setSessionState,
  setOptimisticEntries,
  setStreamingTurn,
  setMainTab,
  setSessionMenuOpen,
  pushError,
}: UseSessionActionsOptions) {
  const [sessionImportPending, setSessionImportPending] = useState(false)
  const [shareSessionUrl, setShareSessionUrl] = useState('')
  const [sessionActionDialog, setSessionActionDialog] =
    useState<SessionActionDialogState>(null)

  const startSession = async () => {
    if (!selectedCampaignId) return
    const clientSessionId = createClientMessageId()
    try {
      const result = await apiFetch<{ session_id: number }>(
        baseUrl,
        '/api/sessions/start',
        auth,
        {
          method: 'POST',
          body: JSON.stringify({ campaign_id: selectedCampaignId, client_session_id: clientSessionId }),
        },
      )
      setSelectedSessionId(result.session_id)
      await refreshCampaignWorkspace(selectedCampaignId)
    } catch (error) {
      pushError('persistence', `Could not start session: ${error instanceof Error ? error.message : String(error)}`)
    }
  }

  const downloadSessionJson = async () => {
    const warnings: string[] = []
    const [turnEventsResult, canonResult] = await Promise.allSettled([
      selectedSessionId
        ? apiFetch<SessionEventsResponse>(
            baseUrl,
            `/api/sessions/${selectedSessionId}/events?limit=1000`,
            auth,
          )
        : Promise.resolve<SessionEventsResponse | null>(null),
      selectedCampaignId
        ? apiFetch<CampaignCanon>(baseUrl, `/api/campaigns/${selectedCampaignId}/canon`, auth)
        : Promise.resolve<CampaignCanon | null>(null),
    ])

    const turnEvents =
      turnEventsResult.status === 'fulfilled' ? turnEventsResult.value : null
    const canon = canonResult.status === 'fulfilled' ? canonResult.value : null
    if (turnEventsResult.status === 'rejected') {
      warnings.push(`turn events unavailable: ${turnEventsResult.reason}`)
    }
    if (canonResult.status === 'rejected') {
      warnings.push(`canon unavailable: ${canonResult.reason}`)
    }

    const payload = {
      exportedAt: new Date().toISOString(),
      selectedIds: {
        campaignId: selectedCampaignId,
        sessionId: selectedSessionId,
        playerId: selectedPlayerId,
      },
      campaign,
      selectedSession: activeSession,
      players,
      selectedPlayer: playerDetail ?? selectedPlayer,
      sessionState,
      logEntries,
      turnEvents: turnEvents?.events ?? [],
      canon,
      maps,
      segments,
      metrics,
      warnings,
    }
    const blob = new Blob([JSON.stringify(payload, null, 2)], {
      type: 'application/json',
    })
    const url = URL.createObjectURL(blob)
    const link = document.createElement('a')
    link.href = url
    link.download = `aidm-session-${selectedSessionId ?? 'export'}.json`
    link.click()
    URL.revokeObjectURL(url)
    if (warnings.length) {
      pushError('workspace', `Export completed with missing live data: ${warnings.join('; ')}`)
    }
  }

  const downloadSessionChronicle = async () => {
    if (!selectedSessionId) {
      pushError('workspace', 'Choose a session before exporting a Chronicle.')
      return
    }
    try {
      await downloadHtmlResource(
        baseUrl,
        `/api/sessions/${selectedSessionId}/chronicle`,
        auth,
        `aidm-session-${selectedSessionId}-chronicle.html`,
      )
    } catch (error) {
      pushError('persistence', `Chronicle export failed: ${error instanceof Error ? error.message : String(error)}`)
    }
  }

  const downloadCampaignChronicle = async () => {
    if (!selectedCampaignId) {
      pushError('workspace', 'Choose a campaign before exporting a campaign Chronicle.')
      return
    }
    try {
      await downloadHtmlResource(
        baseUrl,
        `/api/campaigns/${selectedCampaignId}/chronicle`,
        auth,
        `aidm-campaign-${selectedCampaignId}-chronicle.html`,
      )
    } catch (error) {
      pushError('persistence', `Campaign Chronicle export failed: ${error instanceof Error ? error.message : String(error)}`)
    }
  }

  const importSessionJson = async (event: ChangeEvent<HTMLInputElement>) => {
    const file = event.currentTarget.files?.[0]
    event.currentTarget.value = ''
    if (!file) return
    setSessionImportPending(true)
    try {
      const text = await readFileText(file)
      const payload = JSON.parse(text) as JsonRecord
      const result = await apiFetch<SessionImportResponse>(baseUrl, '/api/sessions/import', auth, {
        method: 'POST',
        body: JSON.stringify(payload),
      })
      setSelectedCampaignId(result.session.campaign_id)
      setSelectedSessionId(result.session_id)
      setOptimisticEntries([])
      setStreamingTurn(null)
      setLogEntries([])
      setSessionState(null)
      setMainTab('turns')
      await refreshRoot()
      await refreshCampaignWorkspace(result.session.campaign_id)
      await loadSessionData(result.session_id)
      pushError('system', `Imported session "${result.session.display_name}".`)
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error)
      pushError('persistence', `Could not import session: ${message}`)
    } finally {
      setSessionImportPending(false)
    }
  }

  const shareSession = () => {
    const params = new URLSearchParams()
    if (selectedCampaignId) params.set('campaign', String(selectedCampaignId))
    if (selectedSessionId) params.set('session', String(selectedSessionId))
    if (baseUrl) params.set('backend', baseUrl)
    const shareUrl = `${window.location.origin}${window.location.pathname}?${params.toString()}`
    if (!navigator.clipboard) {
      setShareSessionUrl(shareUrl)
      pushError('system', 'Share link is ready to copy.')
      return
    }
    void navigator.clipboard.writeText(shareUrl).then(
      () => {
        setShareSessionUrl('')
        pushError('system', 'Session link copied.')
      },
      () => {
        setShareSessionUrl(shareUrl)
        pushError('system', 'Clipboard unavailable; copy the session link manually.')
      },
    )
  }

  const copyShareSessionUrl = () => {
    if (!shareSessionUrl) return
    if (!navigator.clipboard) {
      pushError('system', 'Clipboard unavailable; copy the session link manually.')
      return
    }
    void navigator.clipboard.writeText(shareSessionUrl).then(
      () => {
        setShareSessionUrl('')
        pushError('system', 'Session link copied.')
      },
      () => {
        pushError('system', 'Clipboard unavailable; copy the session link manually.')
      },
    )
  }

  const closeShareSessionDialog = () => {
    setShareSessionUrl('')
  }

  const openRenameSessionDialog = () => {
    if (!activeSession) return
    rememberDialogTrigger(sessionMenuButton())
    const currentName = sessionDisplayName(activeSession, sessionDisplayFallback)
    setSessionActionDialog({
      mode: 'rename',
      session: activeSession,
      name: currentName,
      error: '',
      pending: false,
    })
    setSessionMenuOpen(false)
  }

  const openDeleteSessionDialog = () => {
    if (!activeSession) return
    rememberDialogTrigger(sessionMenuButton())
    setSessionActionDialog({
      mode: 'delete',
      session: activeSession,
      name: sessionDisplayName(activeSession, sessionDisplayFallback),
      error: '',
      pending: false,
    })
    setSessionMenuOpen(false)
  }

  const closeSessionActionDialog = () => {
    if (sessionActionDialog?.pending) return
    setSessionActionDialog(null)
  }

  const submitSessionActionDialog = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    if (!sessionActionDialog) return
    const { mode, session } = sessionActionDialog
    const currentName = sessionDisplayName(session, sessionDisplayFallback)
    const nextName = sessionActionDialog.name.trim()

    if (mode === 'rename' && !nextName) {
      setSessionActionDialog((current) =>
        current ? { ...current, error: 'Session name is required.' } : current,
      )
      return
    }
    if (mode === 'rename' && nextName === currentName) {
      setSessionActionDialog(null)
      return
    }
    if (mode === 'delete' && !selectedCampaignId) {
      setSessionActionDialog((current) =>
        current
          ? {
              ...current,
              error: 'Select a campaign before deleting this session.',
            }
          : current,
      )
      return
    }

    setSessionActionDialog((current) =>
      current ? { ...current, pending: true, error: '' } : current,
    )

    try {
      if (mode === 'rename') {
        const updated = await apiFetch<SessionSummary>(
          baseUrl,
          `/api/sessions/${session.session_id}`,
          auth,
          {
            method: 'PATCH',
            body: JSON.stringify({
              name: nextName,
              expected_updated_at: session.updated_at ?? null,
            }),
          },
        )
        sessionUpserted(updated)
        setSessionActionDialog(null)
        if (selectedCampaignId) {
          await refreshCampaignWorkspace(selectedCampaignId)
        }
      } else if (mode === 'delete') {
        await apiFetch<{ deleted: boolean }>(
          baseUrl,
          `/api/sessions/${session.session_id}?hard=true`,
          auth,
          { method: 'DELETE' },
        )
        setSessionActionDialog(null)
        setOptimisticEntries([])
        setStreamingTurn(null)
        setLogEntries([])
        setSessionState(null)
        if (selectedCampaignId) {
          await refreshCampaignWorkspace(selectedCampaignId)
        }
      }
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error)
      setSessionActionDialog((current) =>
        current ? { ...current, pending: false, error: message } : current,
      )
      pushError(
        'persistence',
        `Could not ${mode === 'rename' ? 'rename' : 'delete'} session: ${message}`,
      )
    }
  }

  return {
    closeShareSessionDialog,
    closeSessionActionDialog,
    copyShareSessionUrl,
    downloadCampaignChronicle,
    downloadSessionChronicle,
    downloadSessionJson,
    importSessionJson,
    openDeleteSessionDialog,
    openRenameSessionDialog,
    sessionActionDialog,
    sessionImportPending,
    setSessionActionDialog,
    shareSession,
    shareSessionUrl,
    startSession,
    submitSessionActionDialog,
  }
}
