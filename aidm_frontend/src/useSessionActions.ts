import { useLayoutEffect, useRef, useState, type ChangeEvent, type Dispatch, type FormEvent, type SetStateAction } from 'react'
import {
  addCookieCsrfHeader,
  addNgrokBrowserWarningBypassHeader,
  addWorkspaceTokenHeader,
  ApiClientError,
  apiFetch,
  normalizeBaseUrl,
} from './api'
import { isRecord } from './gameSelectors'
import { createClientMessageId } from './gameActions'
import type { MainTab } from './SessionBoard'
import type {
  JsonRecord,
  SessionImportMode,
  SessionImportResponse,
  SessionLogEntry,
  SessionSaveDocument,
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

export type StartChapterOptions = {
  /** Start over from the adventure's opening instead of carrying the world forward. */
  fresh?: boolean
  /** Drop an unfinished fight or roll in the current chapter (operators only). */
  force?: boolean
}

export type StartChapterDialogState = {
  fresh: boolean
  force: boolean
  /** The server refused because the current chapter has an unfinished fight or roll. */
  unsettled: boolean
  error: string
  pending: boolean
} | null

/** A save the open campaign's operator may keep as an archive or restore as a new chapter. */
export type SaveImportDialogState = {
  fileName: string
  save: SessionSaveDocument
  mode: SessionImportMode
  restoreCharacters: boolean
  error: string
  pending: boolean
} | null

function chapterStartErrorCode(error: unknown) {
  return error instanceof ApiClientError && isRecord(error.payload) ? error.payload.error_code : null
}

function isSaveDocument(value: JsonRecord): value is JsonRecord & SessionSaveDocument {
  return value.format === 'aidm.save' && value.version === 2 && isRecord(value.campaign)
}

/** The name a rename starts from: the stored name, else the name the server shows. */
export function sessionEditableName(session: SessionSummary) {
  return session.name?.trim() || session.display_name
}

function fileNamePart(value: string, fallback: string) {
  const slug = value.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '')
  return slug || fallback
}

/** `{campaign}-{chapter}.aidm-save.json` */
export function saveFileName(save: SessionSaveDocument) {
  const campaign = fileNamePart(save.campaign.title, 'campaign')
  return `${campaign}-chapter-${save.session.chapter_number}.aidm-save.json`
}

type UseSessionActionsOptions = {
  auth: string
  baseUrl: string
  activeSession: SessionSummary | null
  selectedCampaignId: number | null
  selectedSessionId: number | null
  /** Operators may restore a save of the open campaign as a new chapter. */
  canRestoreSaves: boolean
  rememberDialogTrigger: (fallback?: HTMLElement | null) => void
  sessionMenuButton: () => HTMLElement | null
  loadSessionData: (sessionId: number) => Promise<void>
  refreshRoot: () => Promise<void>
  refreshCampaignWorkspace: (campaignId: number) => Promise<void>
  sessionUpserted: (session: SessionSummary) => void
  sessionRemoved: (sessionId: number) => void
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
  activeSession,
  selectedCampaignId,
  selectedSessionId,
  canRestoreSaves,
  rememberDialogTrigger,
  sessionMenuButton,
  loadSessionData,
  refreshRoot,
  refreshCampaignWorkspace,
  sessionUpserted,
  sessionRemoved,
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
  const [startChapterDialog, setStartChapterDialog] =
    useState<StartChapterDialogState>(null)
  const [saveImportDialog, setSaveImportDialog] = useState<SaveImportDialogState>(null)
  const selectedCampaignIdRef = useRef(selectedCampaignId)
  const startSessionPromisesRef = useRef<Map<number, Promise<void>>>(new Map())

  useLayoutEffect(() => {
    selectedCampaignIdRef.current = selectedCampaignId
  }, [selectedCampaignId])

  const startSession = ({ fresh = false, force = false }: StartChapterOptions = {}) => {
    if (!selectedCampaignId) return Promise.resolve()

    const campaignId = selectedCampaignId
    const existingOperation = startSessionPromisesRef.current.get(campaignId)
    if (existingOperation) return existingOperation
    const clientSessionId = createClientMessageId()
    const operation = (async () => {
      try {
        const result = await apiFetch<{ session_id: number }>(
          baseUrl,
          '/api/sessions/start',
          auth,
          {
            method: 'POST',
            body: JSON.stringify({
              campaign_id: campaignId,
              client_session_id: clientSessionId,
              continuity: fresh ? 'fresh' : 'continue',
              ...(force ? { force: true } : {}),
            }),
          },
        )
        setStartChapterDialog(null)
        if (selectedCampaignIdRef.current !== campaignId) return
        setSelectedSessionId(result.session_id)
        await refreshCampaignWorkspace(campaignId)
      } catch (error) {
        const message = error instanceof Error ? error.message : String(error)
        const unsettled = chapterStartErrorCode(error) === 'chapter_unsettled'
        setStartChapterDialog((current) =>
          current ? { ...current, pending: false, error: message, unsettled: current.unsettled || unsettled } : current,
        )
        if (!unsettled) pushError('persistence', `Could not start the next chapter: ${message}`)
      } finally {
        startSessionPromisesRef.current.delete(campaignId)
      }
    })()
    startSessionPromisesRef.current.set(campaignId, operation)
    return operation
  }

  const openStartChapterDialog = () => {
    if (!selectedCampaignId) return
    rememberDialogTrigger()
    setStartChapterDialog({ fresh: false, force: false, unsettled: false, error: '', pending: false })
  }

  const closeStartChapterDialog = () => {
    if (startChapterDialog?.pending) return
    setStartChapterDialog(null)
  }

  const updateStartChapterDialog = (changes: Partial<Pick<NonNullable<StartChapterDialogState>, 'fresh' | 'force'>>) => {
    setStartChapterDialog((current) => (current ? { ...current, ...changes } : current))
  }

  const submitStartChapterDialog = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    if (!startChapterDialog || startChapterDialog.pending) return Promise.resolve()
    const { fresh, force, unsettled } = startChapterDialog
    setStartChapterDialog((current) => (current ? { ...current, pending: true, error: '' } : current))
    return startSession({ fresh, force: unsettled && force })
  }

  const downloadSessionJson = async () => {
    if (!selectedSessionId) {
      pushError('workspace', 'Choose a chapter before exporting a save.')
      return
    }
    try {
      const save = await apiFetch<SessionSaveDocument>(
        baseUrl,
        `/api/sessions/${selectedSessionId}/export`,
        auth,
      )
      const blob = new Blob([JSON.stringify(save, null, 2)], {
        type: 'application/json',
      })
      const url = URL.createObjectURL(blob)
      const link = document.createElement('a')
      link.href = url
      link.download = saveFileName(save)
      link.click()
      URL.revokeObjectURL(url)
    } catch (error) {
      pushError('persistence', `Save export failed: ${error instanceof Error ? error.message : String(error)}`)
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

  const postSessionImport = async (
    save: JsonRecord,
    mode: SessionImportMode,
    restoreCharacters = false,
  ) => {
    const result = await apiFetch<SessionImportResponse>(baseUrl, '/api/sessions/import', auth, {
      method: 'POST',
      body: JSON.stringify({
        ...save,
        mode,
        ...(mode === 'restore' ? { campaign_id: selectedCampaignId, restore_characters: restoreCharacters } : {}),
      }),
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
    pushError(
      'system',
      result.mode === 'restore'
        ? `Restored the save as ${result.session.display_name}.`
        : `Imported "${result.session.display_name}" as a read-only archive.`,
    )
  }

  const importSessionJson = async (event: ChangeEvent<HTMLInputElement>) => {
    const file = event.currentTarget.files?.[0]
    event.currentTarget.value = ''
    if (!file) return
    setSessionImportPending(true)
    try {
      const text = await readFileText(file)
      const payload = JSON.parse(text) as JsonRecord
      if (canRestoreSaves && isSaveDocument(payload) && payload.campaign.id === selectedCampaignId) {
        rememberDialogTrigger(sessionMenuButton())
        setSaveImportDialog({
          fileName: file.name,
          save: payload,
          mode: 'archive',
          restoreCharacters: false,
          error: '',
          pending: false,
        })
        return
      }
      await postSessionImport(payload, 'archive')
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error)
      pushError('persistence', `Could not import session: ${message}`)
    } finally {
      setSessionImportPending(false)
    }
  }

  const closeSaveImportDialog = () => {
    if (saveImportDialog?.pending) return
    setSaveImportDialog(null)
  }

  const updateSaveImportDialog = (
    changes: Partial<Pick<NonNullable<SaveImportDialogState>, 'mode' | 'restoreCharacters'>>,
  ) => {
    setSaveImportDialog((current) => (current ? { ...current, ...changes, error: '' } : current))
  }

  const submitSaveImportDialog = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    if (!saveImportDialog || saveImportDialog.pending) return
    const { save, mode, restoreCharacters } = saveImportDialog
    setSaveImportDialog((current) => (current ? { ...current, pending: true, error: '' } : current))
    try {
      await postSessionImport(save, mode, mode === 'restore' && restoreCharacters)
      setSaveImportDialog(null)
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error)
      setSaveImportDialog((current) => (current ? { ...current, pending: false, error: message } : current))
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
    setSessionActionDialog({
      mode: 'rename',
      session: activeSession,
      name: sessionEditableName(activeSession),
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
      name: activeSession.display_name,
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
    const currentName = sessionEditableName(session)
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
        sessionRemoved(session.session_id)
        setSelectedSessionId((current) =>
          current === session.session_id ? null : current,
        )
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
    closeSaveImportDialog,
    closeSessionActionDialog,
    closeStartChapterDialog,
    copyShareSessionUrl,
    downloadCampaignChronicle,
    downloadSessionChronicle,
    downloadSessionJson,
    importSessionJson,
    openDeleteSessionDialog,
    openRenameSessionDialog,
    openStartChapterDialog,
    saveImportDialog,
    sessionActionDialog,
    sessionImportPending,
    setSessionActionDialog,
    shareSession,
    shareSessionUrl,
    startChapterDialog,
    startSession,
    submitSaveImportDialog,
    submitSessionActionDialog,
    submitStartChapterDialog,
    updateSaveImportDialog,
    updateStartChapterDialog,
  }
}
