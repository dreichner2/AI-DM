// @vitest-environment jsdom
import type { ChangeEvent, FormEvent } from 'react'
import { act, cleanup, renderHook } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ApiClientError } from './api'
import type { SessionImportResponse, SessionSaveDocument, SessionSummary } from './types'
import { useSessionActions } from './useSessionActions'

const apiFetchMock = vi.hoisted(() => vi.fn())

vi.mock('./api', async () => {
  const actual = await vi.importActual<typeof import('./api')>('./api')
  return { ...actual, apiFetch: apiFetchMock }
})

function deferred<T>() {
  let resolve!: (value: T) => void
  const promise = new Promise<T>((resolvePromise) => {
    resolve = resolvePromise
  })
  return { promise, resolve }
}

function chapter(fields: Partial<SessionSummary> = {}): SessionSummary {
  return {
    session_id: 20,
    campaign_id: 7,
    name: null,
    display_name: 'Chapter 3',
    status: 'active',
    is_current: true,
    is_archived: false,
    chapter_number: 3,
    chapter_recap: '',
    ended_at: null,
    created_at: null,
    updated_at: '2026-11-10T10:00:00Z',
    latest_activity_at: null,
    turn_count: 4,
    scene_title: 'Ash Gate',
    summary_excerpt: '',
    imported: false,
    play_now: false,
    ...fields,
  }
}

function save(fields: Partial<SessionSaveDocument> = {}): SessionSaveDocument {
  return {
    format: 'aidm.save',
    version: 2,
    exported_at: '2026-11-10T10:00:00Z',
    campaign: { id: 7, title: 'The Lanterns of Bleakmoor!' },
    session: chapter(),
    state_snapshot: { flags: { gate_opened: true } },
    session_state: { rolling_summary: '', current_location: null, current_quest: null, memory_snippets: [] },
    log: [],
    characters: [],
    unsettled: { pending_roll: false, active_combat: false },
    ...fields,
  }
}

function importResponse(mode: 'archive' | 'restore', session: SessionSummary): SessionImportResponse {
  return {
    imported: true,
    mode,
    session_id: session.session_id,
    session,
    counts: { turn_events: 0, projected_log_entries: 0, log_entries: 0, session_state: 1 },
    restored_player_ids: [],
  }
}

function fileEvent(contents: unknown) {
  const file = new File([JSON.stringify(contents)], 'bleakmoor.aidm-save.json', { type: 'application/json' })
  return { currentTarget: { files: [file], value: 'bleakmoor.aidm-save.json' } } as unknown as ChangeEvent<HTMLInputElement>
}

function createOptions() {
  return {
    auth: 'token',
    baseUrl: 'https://backend.example.test',
    activeSession: null,
    selectedCampaignId: 7,
    selectedSessionId: null,
    canRestoreSaves: true,
    rememberDialogTrigger: vi.fn(),
    sessionMenuButton: vi.fn(() => null),
    loadSessionData: vi.fn(),
    refreshRoot: vi.fn(),
    refreshCampaignWorkspace: vi.fn(() => Promise.resolve()),
    sessionUpserted: vi.fn(),
    sessionRemoved: vi.fn(),
    setSelectedCampaignId: vi.fn(),
    setSelectedSessionId: vi.fn(),
    setLogEntries: vi.fn(),
    setSessionState: vi.fn(),
    setOptimisticEntries: vi.fn(),
    setStreamingTurn: vi.fn(),
    setMainTab: vi.fn(),
    setSessionMenuOpen: vi.fn(),
    pushError: vi.fn(),
  } as unknown as Parameters<typeof useSessionActions>[0]
}

describe('useSessionActions', () => {
  beforeEach(() => apiFetchMock.mockReset())
  afterEach(() => cleanup())

  it('starts a session synchronously single-flight and unlocks after completion', async () => {
    const firstStart = deferred<{ session_id: number }>()
    apiFetchMock.mockReturnValueOnce(firstStart.promise).mockResolvedValueOnce({ session_id: 12 })
    const options = createOptions()
    const { result } = renderHook(() => useSessionActions(options))

    let firstRequest!: Promise<number | null>
    let duplicateRequest!: Promise<number | null>
    act(() => {
      firstRequest = result.current.startSession()
      duplicateRequest = result.current.startSession()
    })

    expect(duplicateRequest).toBe(firstRequest)
    expect(apiFetchMock).toHaveBeenCalledTimes(1)

    firstStart.resolve({ session_id: 11 })
    await act(async () => {
      // Resolves to the new chapter once the workspace has been read again.
      await expect(firstRequest).resolves.toBe(11)
    })

    expect(options.setSelectedSessionId).toHaveBeenCalledWith(11)
    expect(options.refreshCampaignWorkspace).toHaveBeenCalledWith(7)

    await act(async () => {
      await expect(result.current.startSession()).resolves.toBe(12)
    })

    expect(apiFetchMock).toHaveBeenCalledTimes(2)
    expect(options.setSelectedSessionId).toHaveBeenLastCalledWith(12)
  })

  it('scopes in-flight starts by campaign and ignores a late response after navigation', async () => {
    const firstStart = deferred<{ session_id: number }>()
    const secondStart = deferred<{ session_id: number }>()
    apiFetchMock.mockReturnValueOnce(firstStart.promise).mockReturnValueOnce(secondStart.promise)
    const options = createOptions()
    const { result, rerender } = renderHook(
      ({ selectedCampaignId }) =>
        useSessionActions({ ...options, selectedCampaignId }),
      { initialProps: { selectedCampaignId: 7 } },
    )

    let firstRequest!: Promise<number | null>
    act(() => {
      firstRequest = result.current.startSession()
    })
    rerender({ selectedCampaignId: 8 })
    let secondRequest!: Promise<number | null>
    act(() => {
      secondRequest = result.current.startSession()
    })

    expect(apiFetchMock).toHaveBeenCalledTimes(2)
    expect(JSON.parse(apiFetchMock.mock.calls[0][3].body)).toMatchObject({ campaign_id: 7 })
    expect(JSON.parse(apiFetchMock.mock.calls[1][3].body)).toMatchObject({ campaign_id: 8 })

    secondStart.resolve({ session_id: 12 })
    await act(async () => secondRequest)
    expect(options.setSelectedSessionId).toHaveBeenCalledWith(12)
    expect(options.refreshCampaignWorkspace).toHaveBeenCalledWith(8)

    firstStart.resolve({ session_id: 11 })
    // Moved on to another campaign: no chapter of the first one is opened.
    await act(async () => {
      await expect(firstRequest).resolves.toBeNull()
    })
    expect(options.setSelectedSessionId).toHaveBeenCalledTimes(1)
    expect(options.refreshCampaignWorkspace).toHaveBeenCalledTimes(1)
  })

  it('resolves to no chapter and reports it when the chapter cannot start', async () => {
    apiFetchMock.mockRejectedValueOnce(new Error('timeout'))
    const options = createOptions()
    const { result } = renderHook(() => useSessionActions(options))

    await act(async () => {
      await expect(result.current.startSession()).resolves.toBeNull()
    })
    expect(options.pushError).toHaveBeenCalledWith('persistence', 'Could not start the next chapter: timeout')
    expect(options.setSelectedSessionId).not.toHaveBeenCalled()
  })

  describe('the next-chapter dialog', () => {
    const submitEvent = () => ({ preventDefault: vi.fn() }) as unknown as FormEvent<HTMLFormElement>
    const startBody = (call: number) => JSON.parse(apiFetchMock.mock.calls[call][3].body)
    const unsettledError = () =>
      new ApiClientError('Finish the fight or roll first.', 409, {
        error: 'Finish the fight or roll first.',
        error_code: 'chapter_unsettled',
        details: { session_id: 11, reason: 'active_combat' },
      })

    it('continues the story by default and closes once the chapter starts', async () => {
      apiFetchMock.mockResolvedValueOnce({ session_id: 12 })
      const options = createOptions()
      const { result } = renderHook(() => useSessionActions(options))

      act(() => result.current.openStartChapterDialog())
      expect(result.current.startChapterDialog).toMatchObject({ fresh: false, force: false, unsettled: false })
      await act(async () => result.current.submitStartChapterDialog(submitEvent()))

      expect(startBody(0)).toMatchObject({ campaign_id: 7, continuity: 'continue' })
      expect(startBody(0)).not.toHaveProperty('force')
      expect(result.current.startChapterDialog).toBeNull()
      expect(options.setSelectedSessionId).toHaveBeenCalledWith(12)
    })

    it('starts over from the beginning when that choice is made', async () => {
      apiFetchMock.mockResolvedValueOnce({ session_id: 12 })
      const { result } = renderHook(() => useSessionActions(createOptions()))

      act(() => result.current.openStartChapterDialog())
      act(() => result.current.updateStartChapterDialog({ fresh: true }))
      await act(async () => result.current.submitStartChapterDialog(submitEvent()))

      expect(startBody(0)).toMatchObject({ continuity: 'fresh' })
    })

    it('shows an unsettled refusal in the dialog and forces only after it is acknowledged', async () => {
      apiFetchMock.mockRejectedValueOnce(unsettledError()).mockResolvedValueOnce({ session_id: 12 })
      const options = createOptions()
      const { result } = renderHook(() => useSessionActions(options))

      act(() => result.current.openStartChapterDialog())
      act(() => result.current.updateStartChapterDialog({ force: true }))
      await act(async () => result.current.submitStartChapterDialog(submitEvent()))

      expect(startBody(0)).not.toHaveProperty('force')
      expect(result.current.startChapterDialog).toMatchObject({
        unsettled: true,
        pending: false,
        error: 'Finish the fight or roll first.',
      })
      expect(options.pushError).not.toHaveBeenCalled()

      await act(async () => result.current.submitStartChapterDialog(submitEvent()))
      expect(startBody(1)).toMatchObject({ continuity: 'continue', force: true })
      expect(result.current.startChapterDialog).toBeNull()
    })

    it('keeps other failures in the dialog and reports them', async () => {
      apiFetchMock.mockRejectedValueOnce(new Error('Network down'))
      const options = createOptions()
      const { result } = renderHook(() => useSessionActions(options))

      act(() => result.current.openStartChapterDialog())
      await act(async () => result.current.submitStartChapterDialog(submitEvent()))

      expect(result.current.startChapterDialog).toMatchObject({ error: 'Network down', unsettled: false, pending: false })
      expect(options.pushError).toHaveBeenCalledWith('persistence', 'Could not start the next chapter: Network down')
    })
  })

  it('renames from the stored name and deletes by the server display name', () => {
    const { result, rerender } = renderHook(
      ({ activeSession }) => useSessionActions({ ...createOptions(), activeSession }),
      { initialProps: { activeSession: chapter({ name: 'Old save', display_name: 'Archive — Old save' }) } },
    )

    act(() => result.current.openRenameSessionDialog())
    expect(result.current.sessionActionDialog).toMatchObject({ mode: 'rename', name: 'Old save' })

    rerender({ activeSession: chapter() })
    act(() => result.current.openRenameSessionDialog())
    expect(result.current.sessionActionDialog).toMatchObject({ mode: 'rename', name: 'Chapter 3' })
    act(() => result.current.openDeleteSessionDialog())
    expect(result.current.sessionActionDialog).toMatchObject({ mode: 'delete', name: 'Chapter 3' })
  })

  it('downloads the server save document unchanged under the campaign and chapter name', async () => {
    const document = save()
    apiFetchMock.mockResolvedValueOnce(document)
    const blobs: Blob[] = []
    const downloads: string[] = []
    vi.stubGlobal('URL', {
      ...URL,
      createObjectURL: vi.fn((blob: Blob) => {
        blobs.push(blob)
        return 'blob:save'
      }),
      revokeObjectURL: vi.fn(),
    })
    const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function (this: HTMLAnchorElement) {
      downloads.push(this.download)
    })
    const { result } = renderHook(() => useSessionActions({ ...createOptions(), selectedSessionId: 20 }))

    await act(async () => result.current.downloadSessionJson())

    expect(apiFetchMock).toHaveBeenCalledWith('https://backend.example.test', '/api/sessions/20/export', 'token')
    expect(downloads).toEqual(['the-lanterns-of-bleakmoor-chapter-3.aidm-save.json'])
    expect(JSON.parse(await blobs[0].text())).toEqual(document)
    click.mockRestore()
    vi.unstubAllGlobals()
  })

  describe('importing a save', () => {
    const submitEvent = () => ({ preventDefault: vi.fn() }) as unknown as FormEvent<HTMLFormElement>
    const importBody = () => JSON.parse(apiFetchMock.mock.calls[0][3].body)

    it('posts other files straight in as a read-only archive', async () => {
      const archive = chapter({ session_id: 31, status: 'ended', is_current: false, imported: true, display_name: 'Archive — Old' })
      apiFetchMock.mockResolvedValueOnce(importResponse('archive', archive))
      const options = createOptions()
      const { result } = renderHook(() => useSessionActions(options))

      await act(async () => result.current.importSessionJson(fileEvent({ selectedSession: {}, campaign_id: 7 })))

      expect(importBody()).toMatchObject({ mode: 'archive', campaign_id: 7 })
      expect(result.current.saveImportDialog).toBeNull()
      expect(options.setSelectedSessionId).toHaveBeenCalledWith(31)
      expect(options.pushError).toHaveBeenCalledWith('system', 'Imported "Archive — Old" as a read-only archive.')
    })

    it('archives a save of another campaign, or any save for a player, without asking', async () => {
      const archive = chapter({ session_id: 31, imported: true })
      apiFetchMock.mockResolvedValue(importResponse('archive', archive))
      const { result: operator } = renderHook(() => useSessionActions(createOptions()))
      const { result: player } = renderHook(() => useSessionActions({ ...createOptions(), canRestoreSaves: false }))

      await act(async () => operator.current.importSessionJson(fileEvent(save({ campaign: { id: 99, title: 'Other' } }))))
      await act(async () => player.current.importSessionJson(fileEvent(save())))

      expect(operator.current.saveImportDialog).toBeNull()
      expect(player.current.saveImportDialog).toBeNull()
      expect(apiFetchMock.mock.calls.map((call) => JSON.parse(call[3].body).mode)).toEqual(['archive', 'archive'])
    })

    it('offers an operator the choice for a save of the open campaign and keeps it as an archive by default', async () => {
      const archive = chapter({ session_id: 31, imported: true })
      apiFetchMock.mockResolvedValueOnce(importResponse('archive', archive))
      const { result } = renderHook(() => useSessionActions(createOptions()))

      await act(async () => result.current.importSessionJson(fileEvent(save())))
      expect(apiFetchMock).not.toHaveBeenCalled()
      expect(result.current.saveImportDialog).toMatchObject({
        fileName: 'bleakmoor.aidm-save.json',
        mode: 'archive',
        restoreCharacters: false,
      })

      await act(async () => result.current.submitSaveImportDialog(submitEvent()))
      expect(importBody()).toMatchObject({ mode: 'archive', format: 'aidm.save' })
      expect(importBody()).not.toHaveProperty('restore_characters')
      expect(result.current.saveImportDialog).toBeNull()
    })

    it('restores as a new chapter, with characters only when asked, and selects it', async () => {
      const restored = chapter({ session_id: 32, display_name: 'Chapter 4', chapter_number: 4 })
      apiFetchMock.mockResolvedValueOnce(importResponse('restore', restored))
      const options = createOptions()
      const { result } = renderHook(() => useSessionActions(options))

      await act(async () => result.current.importSessionJson(fileEvent(save())))
      act(() => result.current.updateSaveImportDialog({ mode: 'restore' }))
      act(() => result.current.updateSaveImportDialog({ restoreCharacters: true }))
      await act(async () => result.current.submitSaveImportDialog(submitEvent()))

      expect(importBody()).toMatchObject({ mode: 'restore', restore_characters: true, campaign_id: 7 })
      expect(options.setSelectedSessionId).toHaveBeenCalledWith(32)
      expect(options.refreshCampaignWorkspace).toHaveBeenCalledWith(7)
      expect(options.pushError).toHaveBeenCalledWith('system', 'Restored the save as Chapter 4.')
      expect(result.current.saveImportDialog).toBeNull()
    })

    it("shows the server's reason when a save cannot be restored", async () => {
      apiFetchMock.mockRejectedValueOnce(
        new ApiClientError('This save belongs to a different campaign.', 409, {
          error: 'This save belongs to a different campaign.',
          error_code: 'save_not_restorable',
        }),
      )
      const options = createOptions()
      const { result } = renderHook(() => useSessionActions(options))

      await act(async () => result.current.importSessionJson(fileEvent(save())))
      act(() => result.current.updateSaveImportDialog({ mode: 'restore' }))
      await act(async () => result.current.submitSaveImportDialog(submitEvent()))

      expect(importBody()).toMatchObject({ mode: 'restore', restore_characters: false })
      expect(result.current.saveImportDialog).toMatchObject({
        pending: false,
        error: 'This save belongs to a different campaign.',
      })
      expect(options.setSelectedSessionId).not.toHaveBeenCalled()
    })
  })
})
