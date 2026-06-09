// @vitest-environment jsdom
import { act, renderHook, waitFor } from '@testing-library/react'
import type { FormEvent } from 'react'
import { describe, expect, it, vi, beforeEach } from 'vitest'
import { useRuntimeSettings } from './useRuntimeSettings'

function submitEvent() {
  return {
    preventDefault: vi.fn(),
  } as unknown as FormEvent<HTMLFormElement>
}

const emptyAccountFields = {
  workspaceToken: '',
  username: '',
  firstName: '',
  lastName: '',
  password: '',
}

function createStorageMock(): Storage {
  const store = new Map<string, string>()
  return {
    get length() {
      return store.size
    },
    clear: vi.fn(() => store.clear()),
    getItem: vi.fn((key: string) => store.get(key) ?? null),
    key: vi.fn((index: number) => [...store.keys()][index] ?? null),
    removeItem: vi.fn((key: string) => {
      store.delete(key)
    }),
    setItem: vi.fn((key: string, value: string) => {
      store.set(key, value)
    }),
  }
}

describe('useRuntimeSettings', () => {
  beforeEach(() => {
    vi.stubGlobal('localStorage', createStorageMock())
    vi.stubGlobal('sessionStorage', createStorageMock())
    document.cookie = 'aidm_account_token=; Max-Age=0; Path=/; SameSite=Lax'
    window.history.replaceState(null, '', '/')
  })

  it('validates and persists backend settings', () => {
    const resetRuntimeState = vi.fn()
    const reconnectSocket = vi.fn()
    const { result } = renderHook(() =>
      useRuntimeSettings({
        defaultBaseUrl: 'http://127.0.0.1:5050',
        resetRuntimeState,
        reconnectSocket,
      }),
    )

    act(() => {
      result.current.openRuntimeSettings()
      result.current.setRuntimeSettingsForm({
        baseUrl: 'https://backend.example.test/',
        ...emptyAccountFields,
      })
    })
    act(() => {
      void result.current.submitRuntimeSettings(submitEvent())
    })

    expect(result.current.baseUrl).toBe('https://backend.example.test')
    expect(result.current.authToken).toBe('')
    expect(localStorage.getItem('aidm:baseUrl')).toBe('https://backend.example.test')
    expect(sessionStorage.getItem('aidm:workspaceToken')).toBeNull()
    expect(resetRuntimeState).toHaveBeenCalledOnce()
    expect(reconnectSocket).toHaveBeenCalledOnce()
    expect(result.current.runtimeSettingsOpen).toBe(false)
  })

  it('rejects non-http backend URLs', () => {
    const { result } = renderHook(() =>
      useRuntimeSettings({
        defaultBaseUrl: 'http://127.0.0.1:5050',
        resetRuntimeState: vi.fn(),
        reconnectSocket: vi.fn(),
      }),
    )

    act(() => {
      result.current.setRuntimeSettingsForm({ baseUrl: 'file:///tmp/aidm', ...emptyAccountFields })
    })
    act(() => {
      void result.current.submitRuntimeSettings(submitEvent())
    })

    expect(result.current.runtimeSettingsError).toMatch(/http/)
    expect(result.current.baseUrl).toBe('http://127.0.0.1:5050')
  })

  it('allows a blank backend URL for same-origin mode', () => {
    localStorage.setItem('aidm:baseUrl', 'https://backend.example.test')
    const resetRuntimeState = vi.fn()
    const reconnectSocket = vi.fn()
    const { result } = renderHook(() =>
      useRuntimeSettings({
        defaultBaseUrl: '',
        resetRuntimeState,
        reconnectSocket,
      }),
    )

    act(() => {
      result.current.openRuntimeSettings()
      result.current.setRuntimeSettingsForm({ baseUrl: '', ...emptyAccountFields })
    })
    act(() => {
      void result.current.submitRuntimeSettings(submitEvent())
    })

    expect(result.current.baseUrl).toBe('')
    expect(localStorage.getItem('aidm:baseUrl')).toBeNull()
    expect(resetRuntimeState).toHaveBeenCalledOnce()
    expect(reconnectSocket).toHaveBeenCalledOnce()
    expect(result.current.runtimeSettingsOpen).toBe(false)
  })

  it('opens a focused account prompt that saves same-origin credentials', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: RequestInfo | URL) => {
        const path = new URL(String(input), 'http://localhost').pathname
        const workspacePayload = path === '/api/accounts/workspace'
        const workspaces = workspacePayload
          ? [
              {
                workspace_id: 'owner',
                workspace_role: 'player',
                is_workspace_admin: false,
                created_at: null,
                updated_at: null,
              },
            ]
          : []
        if (path === '/api/accounts/me') {
          return new Response(
            JSON.stringify({
              account_id: 1,
              username: 'danny',
              first_name: 'Danny',
              last_name: 'Reichner',
              display_name: 'Danny Reichner',
              workspace_id: 'owner',
              workspace_role: 'player',
              is_workspace_admin: false,
              workspaces: [
                {
                  workspace_id: 'owner',
                  workspace_role: 'player',
                  is_workspace_admin: false,
                  created_at: null,
                  updated_at: null,
                },
              ],
            }),
            { status: 200, headers: { 'Content-Type': 'application/json' } },
          )
        }
        return new Response(
            JSON.stringify({
              account: {
                account_id: 1,
                username: 'danny',
                first_name: 'Danny',
                last_name: 'Reichner',
                display_name: 'Danny Reichner',
                workspace_id: workspacePayload ? 'owner' : null,
                workspace_role: workspacePayload ? 'player' : null,
                is_workspace_admin: false,
                workspaces,
              },
              account_token: 'account-token',
              workspace_id: workspacePayload ? 'owner' : null,
              workspace_role: workspacePayload ? 'player' : null,
              is_workspace_admin: false,
              claimed_player_ids: [],
              workspaces,
            }),
            { status: 200, headers: { 'Content-Type': 'application/json' } },
          )
      }),
    )
    const resetRuntimeState = vi.fn()
    const reconnectSocket = vi.fn()
    const { result } = renderHook(() =>
      useRuntimeSettings({
        defaultBaseUrl: '',
        resetRuntimeState,
        reconnectSocket,
      }),
    )

    act(() => {
      result.current.openAuthTokenPrompt()
    })

    expect(result.current.runtimeSettingsOpen).toBe(true)
    expect(result.current.runtimeSettingsMode).toBe('auth')
    expect(result.current.runtimeAuthStep).toBe('account')
    expect(result.current.runtimeSettingsForm.baseUrl).toBe('')

    act(() => {
      result.current.setRuntimeAuthIntent('signup')
      result.current.setRuntimeSettingsForm({
        baseUrl: '',
        workspaceToken: '',
        username: 'Danny',
        firstName: 'Danny',
        lastName: 'Reichner',
        password: '',
      })
    })
    await act(async () => {
      await result.current.submitRuntimeSettings(submitEvent())
    })

    expect(result.current.authToken).toBe('')
    expect(sessionStorage.getItem('aidm:authToken')).toBe('account-token')
    expect(document.cookie).toContain('aidm_account_token=account-token')
    expect(sessionStorage.getItem('aidm:workspaceToken')).toBeNull()
    expect(result.current.runtimeAccount?.displayName).toBe('Danny Reichner')
    expect(result.current.runtimeAccount?.workspaceId).toBeNull()
    expect(result.current.runtimeSettingsOpen).toBe(true)
    expect(result.current.runtimeAuthStep).toBe('workspace')
    expect(result.current.runtimeSettingsForm.workspaceToken).toBe('')
    expect(resetRuntimeState).not.toHaveBeenCalled()
    expect(reconnectSocket).not.toHaveBeenCalled()

    act(() => {
      result.current.setRuntimeSettingsForm((current) => ({ ...current, workspaceToken: ' workspace-token ' }))
    })
    await act(async () => {
      await result.current.submitRuntimeSettings(submitEvent())
    })

    expect(result.current.authToken).toBe('account-token')
    expect(sessionStorage.getItem('aidm:workspaceToken')).toBe('workspace-token')
    expect(localStorage.getItem('aidm:workspaceId')).toBe('owner')
    expect(result.current.runtimeAccount?.displayName).toBe('Danny Reichner')
    expect(result.current.runtimeAccount?.workspaceId).toBe('owner')
    expect(result.current.runtimeAccount?.workspaces.map((workspace) => workspace.workspace_id)).toEqual(['owner'])
    expect(result.current.runtimeSettingsOpen).toBe(false)
    expect(result.current.runtimeSettingsMode).toBe('settings')
    expect(resetRuntimeState).toHaveBeenCalledOnce()
    expect(reconnectSocket).toHaveBeenCalledOnce()
  })

  it('selects a saved account workspace without re-entering its token', async () => {
    const fetchCalls: Array<{ path: string; body: unknown }> = []
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const path = new URL(String(input), 'http://localhost').pathname
        const body = init?.body ? JSON.parse(String(init.body)) : null
        fetchCalls.push({ path, body })
        const workspacePayload = path === '/api/accounts/workspace/select'
        const workspaces = [
          {
            workspace_id: 'owner',
            workspace_role: 'admin',
            is_workspace_admin: true,
            created_at: null,
            updated_at: null,
          },
          {
            workspace_id: 'friend',
            workspace_role: 'player',
            is_workspace_admin: false,
            created_at: null,
            updated_at: null,
          },
        ]
        if (path === '/api/accounts/me') {
          return new Response(
            JSON.stringify({
              account_id: 1,
              username: 'danny',
              first_name: 'Danny',
              last_name: 'Reichner',
              display_name: 'Danny Reichner',
              workspace_id: null,
              workspace_role: null,
              is_workspace_admin: false,
              workspaces,
            }),
            { status: 200, headers: { 'Content-Type': 'application/json' } },
          )
        }
        return new Response(
          JSON.stringify({
            account: {
              account_id: 1,
              username: 'danny',
              first_name: 'Danny',
              last_name: 'Reichner',
              display_name: 'Danny Reichner',
              workspace_id: workspacePayload ? body.workspace_id : null,
              workspace_role: workspacePayload ? 'admin' : null,
              is_workspace_admin: workspacePayload,
              workspaces,
            },
            account_token: 'account-token',
            workspace_id: workspacePayload ? body.workspace_id : null,
            workspace_role: workspacePayload ? 'admin' : null,
            is_workspace_admin: workspacePayload,
            claimed_player_ids: [],
            workspaces,
          }),
          { status: 200, headers: { 'Content-Type': 'application/json' } },
        )
      }),
    )
    const resetRuntimeState = vi.fn()
    const reconnectSocket = vi.fn()
    const { result } = renderHook(() =>
      useRuntimeSettings({
        defaultBaseUrl: '',
        resetRuntimeState,
        reconnectSocket,
      }),
    )

    act(() => {
      result.current.openAuthTokenPrompt()
      result.current.setRuntimeSettingsForm({
        baseUrl: '',
        workspaceToken: '',
        username: 'Danny',
        firstName: '',
        lastName: '',
        password: '',
      })
    })
    await act(async () => {
      await result.current.submitRuntimeSettings(submitEvent())
    })

    expect(result.current.runtimeAuthStep).toBe('workspace')
    expect(result.current.runtimeAccount?.workspaces.map((workspace) => workspace.workspace_id)).toEqual(['owner', 'friend'])

    await act(async () => {
      await result.current.selectSavedWorkspace('owner')
    })

    expect(fetchCalls).toEqual(
      expect.arrayContaining([
        expect.objectContaining({
          path: '/api/accounts/workspace/select',
          body: { workspace_id: 'owner' },
        }),
      ]),
    )
    expect(result.current.authToken).toBe('account-token')
    expect(result.current.workspaceId).toBe('owner')
    expect(sessionStorage.getItem('aidm:workspaceToken')).toBeNull()
    expect(localStorage.getItem('aidm:workspaceId')).toBe('owner')
    expect(result.current.runtimeSettingsOpen).toBe(false)
    expect(resetRuntimeState).toHaveBeenCalledOnce()
    expect(reconnectSocket).toHaveBeenCalledOnce()
  })

  it('refreshes saved workspaces for a remembered account session', async () => {
    sessionStorage.setItem('aidm:authToken', 'account-token')
    sessionStorage.setItem(
      'aidm:account',
      JSON.stringify({
        accountId: 1,
        username: 'danny',
        displayName: 'Danny Reichner',
        workspaceId: null,
        workspaceRole: null,
        isWorkspaceAdmin: false,
        workspaces: [],
      }),
    )
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const path = new URL(String(input), 'http://localhost').pathname
      expect(path).toBe('/api/accounts/me')
      expect(new Headers(init?.headers).get('Authorization')).toBe('Bearer account-token')
      return new Response(
        JSON.stringify({
          account_id: 1,
          username: 'danny',
          first_name: 'Danny',
          last_name: 'Reichner',
          display_name: 'Danny Reichner',
          workspace_id: null,
          workspace_role: null,
          is_workspace_admin: false,
          workspaces: [
            {
              workspace_id: 'owner',
              workspace_role: 'admin',
              is_workspace_admin: true,
              created_at: null,
              updated_at: null,
            },
            {
              workspace_id: 'friend',
              workspace_role: 'player',
              is_workspace_admin: false,
              created_at: null,
              updated_at: null,
            },
          ],
        }),
        { status: 200, headers: { 'Content-Type': 'application/json' } },
      )
    })
    vi.stubGlobal('fetch', fetchMock)

    const { result } = renderHook(() =>
      useRuntimeSettings({
        defaultBaseUrl: '',
        resetRuntimeState: vi.fn(),
        reconnectSocket: vi.fn(),
      }),
    )

    await waitFor(() =>
      expect(result.current.runtimeAccount?.workspaces.map((workspace) => workspace.workspace_id)).toEqual([
        'owner',
        'friend',
      ]),
    )
    expect(fetchMock).toHaveBeenCalledOnce()
    expect(JSON.parse(String(localStorage.getItem('aidm:account'))).workspaces).toHaveLength(2)
  })

  it('refreshes a remembered workspace role even when workspaces were cached', async () => {
    sessionStorage.setItem('aidm:authToken', 'account-token')
    localStorage.setItem('aidm:workspaceId', 'aidan_test')
    sessionStorage.setItem(
      'aidm:account',
      JSON.stringify({
        accountId: 1,
        username: 'danny',
        displayName: 'Danny Reichner',
        workspaceId: 'aidan_test',
        workspaceRole: 'admin',
        isWorkspaceAdmin: true,
        workspaces: [
          {
            workspace_id: 'aidan_test',
            workspace_role: 'admin',
            is_workspace_admin: true,
            created_at: null,
            updated_at: null,
          },
        ],
      }),
    )
    const fetchMock = vi.fn(async () =>
      new Response(
        JSON.stringify({
          account_id: 1,
          username: 'danny',
          first_name: 'Danny',
          last_name: 'Reichner',
          display_name: 'Danny Reichner',
          workspace_id: null,
          workspace_role: null,
          is_workspace_admin: false,
          workspaces: [
            {
              workspace_id: 'aidan_test',
              workspace_role: 'player',
              is_workspace_admin: false,
              created_at: null,
              updated_at: null,
            },
          ],
        }),
        { status: 200, headers: { 'Content-Type': 'application/json' } },
      ),
    )
    vi.stubGlobal('fetch', fetchMock)

    const { result } = renderHook(() =>
      useRuntimeSettings({
        defaultBaseUrl: '',
        resetRuntimeState: vi.fn(),
        reconnectSocket: vi.fn(),
      }),
    )

    await waitFor(() => expect(result.current.runtimeAccount?.workspaceRole).toBe('player'))
    expect(result.current.runtimeAccount?.isWorkspaceAdmin).toBe(false)
    expect(JSON.parse(String(localStorage.getItem('aidm:account'))).workspaceRole).toBe('player')
  })

  it('loads and persists a backend URL from a share-link query parameter', () => {
    window.history.replaceState(null, '', '/?api=https%3A%2F%2Fbackend.example.test%2F')

    const { result } = renderHook(() =>
      useRuntimeSettings({
        defaultBaseUrl: 'http://127.0.0.1:5050',
        resetRuntimeState: vi.fn(),
        reconnectSocket: vi.fn(),
      }),
    )

    expect(result.current.baseUrl).toBe('https://backend.example.test')
    expect(result.current.runtimeSettingsForm.baseUrl).toBe('https://backend.example.test')
    expect(localStorage.getItem('aidm:baseUrl')).toBe('https://backend.example.test')
  })
})
