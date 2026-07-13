// @vitest-environment jsdom
import { act, renderHook, waitFor } from '@testing-library/react'
import type { FormEvent } from 'react'
import { describe, expect, it, vi, beforeEach } from 'vitest'
import { normalizedBackendOrigin, originScopedStorageKey } from './api'
import {
  LEGACY_PASSWORD_SETUP_MESSAGE,
  useRuntimeSettings,
  useShareBackendTrust,
} from './useRuntimeSettings'

function submitEvent() {
  return {
    preventDefault: vi.fn(),
  } as unknown as FormEvent<HTMLFormElement>
}

const emptyAccountFields = {
  workspaceToken: '',
  workspaceName: '',
  workspacePassword: '',
  username: '',
  firstName: '',
  lastName: '',
  password: '',
  recoveryCode: '',
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
    for (const cookie of document.cookie.split(';')) {
      const name = cookie.split('=', 1)[0]?.trim()
      if (name) document.cookie = `${name}=; Max-Age=0; Path=/; SameSite=Lax`
    }
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
    const accountRequestBodies: Array<Record<string, unknown>> = []
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const path = new URL(String(input), 'http://localhost').pathname
        const body = init?.body ? JSON.parse(String(init.body)) as Record<string, unknown> : null
        if (path === '/api/accounts/login' && body) {
          accountRequestBodies.push(body)
        }
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
        workspaceName: '',
        workspacePassword: '',
        username: 'Danny',
        firstName: 'Danny',
        lastName: 'Reichner',
        password: 'secret',
        recoveryCode: '',
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
    expect(accountRequestBodies[0]).toMatchObject({
      username: 'Danny',
      password: 'secret',
      intent: 'signup',
    })
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

  it('allows http-only cookie account sessions to join a table without a bearer token', async () => {
    const fetchCalls: Array<{ method: string; path: string; authorization: string | null; csrf: string | null }> = []
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const path = new URL(String(input), 'http://localhost').pathname
        const method = init?.method ?? 'GET'
        const headers = new Headers(init?.headers)
        fetchCalls.push({
          method,
          path,
          authorization: headers.get('Authorization'),
          csrf: headers.get('X-AIDM-CSRF-Token'),
        })
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
              username: 'cookie-player',
              first_name: 'Cookie',
              last_name: 'Player',
              display_name: 'Cookie Player',
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
              username: 'cookie-player',
              first_name: 'Cookie',
              last_name: 'Player',
              display_name: 'Cookie Player',
              workspace_id: workspacePayload ? 'owner' : null,
              workspace_role: workspacePayload ? 'player' : null,
              is_workspace_admin: false,
              workspaces,
            },
            account_token: '',
            account_token_transport: 'http_only_cookie',
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
      result.current.setRuntimeAuthIntent('signup')
      result.current.setRuntimeSettingsForm({
        baseUrl: '',
        workspaceToken: '',
        workspaceName: '',
        workspacePassword: '',
        username: 'CookiePlayer',
        firstName: 'Cookie',
        lastName: 'Player',
        password: 'secret',
        recoveryCode: '',
      })
    })
    await act(async () => {
      await result.current.submitRuntimeSettings(submitEvent())
    })

    expect(result.current.authToken).toBe('')
    expect(sessionStorage.getItem('aidm:authToken')).toBeNull()
    expect(sessionStorage.getItem('aidm:accountTokenTransport')).toBe('http_only_cookie')
    expect(result.current.runtimeAuthStep).toBe('workspace')
    document.cookie = 'aidm_csrf_token=csrf-token; Path=/; SameSite=Lax'

    act(() => {
      result.current.setRuntimeSettingsForm((current) => ({ ...current, workspaceToken: ' owner-token ' }))
    })
    await act(async () => {
      await result.current.submitRuntimeSettings(submitEvent())
    })

    const workspaceCall = fetchCalls.find((call) => call.path === '/api/accounts/workspace')
    expect(workspaceCall?.authorization).toBeNull()
    expect(workspaceCall?.csrf).toBe('csrf-token')
    expect(result.current.workspaceId).toBe('owner')
    expect(result.current.runtimeSettingsOpen).toBe(false)
    expect(resetRuntimeState).toHaveBeenCalledOnce()
    expect(reconnectSocket).toHaveBeenCalledOnce()

    act(() => {
      result.current.clearAuthToken()
    })

    await waitFor(() =>
      expect(fetchCalls).toEqual(
        expect.arrayContaining([
          expect.objectContaining({ method: 'DELETE', path: '/api/accounts/session', csrf: 'csrf-token' }),
        ]),
      ),
    )
    expect(sessionStorage.getItem('aidm:accountTokenTransport')).toBeNull()
  })

  it.each([
    { label: 'http-only cookie', accountToken: '', transport: 'http_only_cookie' },
    { label: 'bearer token', accountToken: 'guest-bearer-token', transport: 'bearer' },
  ])('adopts a Play Now $label session and reconnects the selected table', ({ accountToken, transport }) => {
    const resetRuntimeState = vi.fn()
    const reconnectSocket = vi.fn()
    const { result } = renderHook(() =>
      useRuntimeSettings({
        defaultBaseUrl: 'https://backend.example.test',
        resetRuntimeState,
        reconnectSocket,
      }),
    )
    const workspace = {
      workspace_id: 'guest-table',
      workspace_name: 'Guest Table',
      table_name: 'Guest Table',
      access_mode: 'password' as const,
      workspace_role: 'player',
      is_workspace_admin: false,
      created_at: null,
      updated_at: null,
    }

    act(() => {
      result.current.adoptAccountSession({
        account: {
          account_id: 41,
          username: 'guest-41',
          first_name: 'Guest',
          last_name: 'Adventurer',
          display_name: 'Guest Adventurer',
          workspace_id: 'guest-table',
          workspace_role: 'player',
          is_workspace_admin: false,
          requires_password_setup: false,
          workspaces: [workspace],
        },
        account_token: accountToken,
        account_token_transport: transport,
        workspace_id: 'guest-table',
        workspace_role: 'player',
        is_workspace_admin: false,
        claimed_player_ids: [30],
        workspaces: [workspace],
      })
    })

    expect(result.current.authToken).toBe(accountToken)
    expect(result.current.accountTokenTransport).toBe(transport)
    expect(result.current.workspaceId).toBe('guest-table')
    expect(result.current.runtimeAccount?.username).toBe('guest-41')
    expect(sessionStorage.getItem('aidm:authToken')).toBe(accountToken || null)
    expect(sessionStorage.getItem('aidm:accountTokenTransport')).toBe(transport)
    expect(localStorage.getItem('aidm:workspaceId')).toBe('guest-table')
    expect(
      sessionStorage.getItem(originScopedStorageKey('aidm:authToken', 'https://backend.example.test')),
    ).toBe(accountToken || null)
    expect(
      sessionStorage.getItem(originScopedStorageKey('aidm:accountTokenTransport', 'https://backend.example.test')),
    ).toBe(transport)
    expect(
      localStorage.getItem(originScopedStorageKey('aidm:workspaceId', 'https://backend.example.test')),
    ).toBe('guest-table')
    expect(resetRuntimeState).toHaveBeenCalledOnce()
    expect(reconnectSocket).toHaveBeenCalledOnce()
  })

  it('sends the selected auth intent and displays username intent errors', async () => {
    const requestBodies: Array<Record<string, unknown>> = []
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const path = new URL(String(input), 'http://localhost').pathname
        const body = init?.body ? JSON.parse(String(init.body)) as Record<string, unknown> : {}
        requestBodies.push(body)
        if (path === '/api/accounts/login' && body.intent === 'login') {
          return new Response(
            JSON.stringify({
              error_code: 'username_not_found',
              error: 'Username not found. Please sign up.',
            }),
            { status: 404, headers: { 'Content-Type': 'application/json' } },
          )
        }
        return new Response(
          JSON.stringify({
            error_code: 'username_taken',
            error: 'Username is already taken. Please sign in.',
          }),
          { status: 409, headers: { 'Content-Type': 'application/json' } },
        )
      }),
    )

    const { result } = renderHook(() =>
      useRuntimeSettings({
        defaultBaseUrl: '',
        resetRuntimeState: vi.fn(),
        reconnectSocket: vi.fn(),
      }),
    )

    act(() => {
      result.current.openAuthTokenPrompt()
      result.current.setRuntimeSettingsForm({
        baseUrl: '',
        workspaceToken: '',
        workspaceName: '',
        workspacePassword: '',
        username: 'Missing',
        firstName: '',
        lastName: '',
        password: 'secret',
        recoveryCode: '',
      })
    })
    await act(async () => {
      await result.current.submitRuntimeSettings(submitEvent())
    })

    expect(requestBodies[0]).toMatchObject({
      username: 'Missing',
      intent: 'login',
    })
    expect(requestBodies[0].first_name).toBeUndefined()
    expect(requestBodies[0].last_name).toBeUndefined()
    expect(result.current.runtimeSettingsError).toBe('Username not found. Please sign up.')

    act(() => {
      result.current.setRuntimeAuthIntent('signup')
      result.current.setRuntimeSettingsForm({
        baseUrl: '',
        workspaceToken: '',
        workspaceName: '',
        workspacePassword: '',
        username: 'Danny',
        firstName: 'Danny',
        lastName: 'Reichner',
        password: 'secret',
        recoveryCode: '',
      })
    })
    await act(async () => {
      await result.current.submitRuntimeSettings(submitEvent())
    })

    expect(requestBodies[1]).toMatchObject({
      username: 'Danny',
      first_name: 'Danny',
      last_name: 'Reichner',
      intent: 'signup',
    })
    expect(result.current.runtimeSettingsError).toBe('Username is already taken. Please sign in.')
  })

  it('uses an out-of-band recovery code without placing it in the request body or storage', async () => {
    const requestBodies: Array<Record<string, unknown>> = []
    const requestHeaders: Headers[] = []
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const path = new URL(String(input), 'http://localhost').pathname
        const body = init?.body ? JSON.parse(String(init.body)) as Record<string, unknown> : {}
        requestBodies.push(body)
        requestHeaders.push(new Headers(init?.headers))
        if (path === '/api/accounts/login' && body.intent === 'login') {
          return new Response(
            JSON.stringify({
              error_code: 'legacy_password_setup_required',
              error: LEGACY_PASSWORD_SETUP_MESSAGE,
            }),
            { status: 401, headers: { 'Content-Type': 'application/json' } },
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
              workspace_id: null,
              workspace_role: null,
              is_workspace_admin: false,
              workspaces: [],
            },
            account_token: 'upgraded-account-token',
            workspace_id: null,
            workspace_role: null,
            is_workspace_admin: false,
            claimed_player_ids: [],
            workspaces: [],
          }),
          { status: 200, headers: { 'Content-Type': 'application/json' } },
        )
      }),
    )

    const { result } = renderHook(() =>
      useRuntimeSettings({
        defaultBaseUrl: '',
        resetRuntimeState: vi.fn(),
        reconnectSocket: vi.fn(),
      }),
    )

    act(() => {
      result.current.openAuthTokenPrompt()
      result.current.setRuntimeSettingsForm({
        baseUrl: '',
        workspaceToken: '',
        workspaceName: '',
        workspacePassword: '',
        username: 'Danny',
        firstName: '',
        lastName: '',
        password: '',
        recoveryCode: '',
      })
    })

    await act(async () => {
      await result.current.submitRuntimeSettings(submitEvent())
    })

    expect(result.current.runtimeSettingsError).toBe(LEGACY_PASSWORD_SETUP_MESSAGE)
    expect(result.current.legacyPasswordSetupRequired).toBe(true)
    expect(result.current.runtimeAuthIntent).toBe('signup')
    expect(result.current.runtimeAuthStep).toBe('account')
    expect(requestBodies[0]).toMatchObject({
      username: 'Danny',
      password: '',
      intent: 'login',
    })
    expect(requestBodies[0]).not.toHaveProperty('legacy_claim')

    act(() => {
      result.current.setRuntimeSettingsForm((current) => ({
        ...current,
        password: 'new-secret',
        recoveryCode: 'operator-recovery-code',
      }))
    })
    await act(async () => {
      await result.current.submitRuntimeSettings(submitEvent())
    })

    expect(requestBodies[1]).toMatchObject({
      username: 'Danny',
      password: 'new-secret',
      intent: 'signup',
      legacy_claim: true,
      legacy_recovery: true,
    })
    expect(requestBodies[1]).not.toHaveProperty('first_name')
    expect(requestBodies[1]).not.toHaveProperty('last_name')
    expect(requestBodies[1]).not.toHaveProperty('recovery_code')
    expect(JSON.stringify(requestBodies)).not.toContain('operator-recovery-code')
    expect(requestHeaders[1].get('Authorization')).toBe('Bearer operator-recovery-code')
    expect(sessionStorage.getItem('aidm:authToken')).toBe('upgraded-account-token')
    expect(sessionStorage.setItem).not.toHaveBeenCalledWith(
      expect.anything(),
      expect.stringContaining('operator-recovery-code'),
    )
    expect(localStorage.setItem).not.toHaveBeenCalledWith(
      expect.anything(),
      expect.stringContaining('operator-recovery-code'),
    )
    expect(result.current.runtimeSettingsForm.recoveryCode).toBe('')
    expect(result.current.legacyPasswordSetupRequired).toBe(false)
    expect(result.current.runtimeSettingsError).toBe('')
    expect(result.current.runtimeAuthStep).toBe('workspace')
  })

  it('prompts saved passwordless account sessions before workspace access', async () => {
    sessionStorage.setItem('aidm:authToken', 'legacy-account-token')
    sessionStorage.setItem('aidm:workspaceToken', 'owner-token')
    localStorage.setItem('aidm:workspaceId', 'owner')

    vi.stubGlobal(
      'fetch',
      vi.fn(async () =>
        new Response(
          JSON.stringify({
            account_id: 5,
            username: 'aidan',
            first_name: 'Aidan',
            last_name: 'Fernandez',
            display_name: 'Aidan Fernandez',
            workspace_id: 'owner',
            workspace_role: 'player',
            is_workspace_admin: false,
            requires_password_setup: true,
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
        ),
      ),
    )

    const { result } = renderHook(() =>
      useRuntimeSettings({
        defaultBaseUrl: '',
        resetRuntimeState: vi.fn(),
        reconnectSocket: vi.fn(),
      }),
    )

    await waitFor(() => expect(result.current.legacyPasswordSetupRequired).toBe(true))

    expect(result.current.runtimeSettingsOpen).toBe(true)
    expect(result.current.runtimeSettingsMode).toBe('auth')
    expect(result.current.runtimeAuthIntent).toBe('signup')
    expect(result.current.runtimeAuthStep).toBe('account')
    expect(result.current.runtimeSettingsError).toBe(LEGACY_PASSWORD_SETUP_MESSAGE)
    expect(result.current.runtimeSettingsForm.username).toBe('aidan')
    expect(result.current.runtimeSettingsForm.firstName).toBe('Aidan')
    expect(result.current.runtimeSettingsForm.lastName).toBe('Fernandez')
    expect(result.current.runtimeAccount?.requiresPasswordSetup).toBe(true)
    expect(result.current.workspaceToken).toBe('')
    expect(result.current.workspaceId).toBe('')
    expect(sessionStorage.getItem('aidm:workspaceToken')).toBeNull()
    expect(localStorage.getItem('aidm:workspaceId')).toBeNull()
  })

  it('does not reset an already-open auth dialog back to join table', async () => {
    sessionStorage.setItem('aidm:authToken', 'account-token')

    vi.stubGlobal(
      'fetch',
      vi.fn(async () =>
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
            requires_password_setup: false,
            workspaces: [],
          }),
          { status: 200, headers: { 'Content-Type': 'application/json' } },
        ),
      ),
    )

    const { result } = renderHook(() =>
      useRuntimeSettings({
        defaultBaseUrl: '',
        resetRuntimeState: vi.fn(),
        reconnectSocket: vi.fn(),
      }),
    )

    await waitFor(() => expect(result.current.runtimeAccount?.username).toBe('danny'))

    act(() => {
      result.current.openAuthTokenPrompt()
    })
    expect(result.current.runtimeAuthStep).toBe('workspace')

    act(() => {
      result.current.setRuntimeWorkspaceAction('create')
      result.current.setRuntimeSettingsForm((current) => ({
        ...current,
        workspaceName: 'Draft Table',
        workspacePassword: 'draft-table-secret',
      }))
      result.current.openAuthTokenPrompt()
    })
    expect(result.current.runtimeAuthStep).toBe('workspace')
    expect(result.current.runtimeWorkspaceAction).toBe('create')
    expect(result.current.runtimeSettingsForm.workspaceName).toBe('Draft Table')
    expect(result.current.runtimeSettingsForm.workspacePassword).toBe('draft-table-secret')

    act(() => {
      result.current.setRuntimeAuthStep('account')
      result.current.setRuntimeSettingsForm((current) => ({
        ...current,
        username: 'other-user',
        password: 'draft-password',
      }))
      result.current.openAuthTokenPrompt()
    })
    expect(result.current.runtimeAuthStep).toBe('account')
    expect(result.current.runtimeWorkspaceAction).toBe('create')
    expect(result.current.runtimeSettingsForm.username).toBe('other-user')
    expect(result.current.runtimeSettingsForm.password).toBe('draft-password')
  })

  it('selects a saved account workspace without re-entering its token', async () => {
    const fetchCalls: Array<{ method: string; path: string; body: unknown }> = []
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const path = new URL(String(input), 'http://localhost').pathname
        const method = init?.method ?? 'GET'
        const body = init?.body ? JSON.parse(String(init.body)) : null
        fetchCalls.push({ method, path, body })
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
        if (method === 'DELETE' && path === '/api/accounts/workspaces/friend') {
          const remainingWorkspaces = workspaces.filter((workspace) => workspace.workspace_id !== 'friend')
          return new Response(
            JSON.stringify({
              account: {
                account_id: 1,
                username: 'danny',
                first_name: 'Danny',
                last_name: 'Reichner',
                display_name: 'Danny Reichner',
                workspace_id: null,
                workspace_role: null,
                is_workspace_admin: false,
                workspaces: remainingWorkspaces,
              },
              account_token: 'account-token',
              workspace_id: null,
              workspace_role: null,
              is_workspace_admin: false,
              claimed_player_ids: [],
              workspaces: remainingWorkspaces,
              workspace_action: 'removed',
              workspace_id_removed: 'friend',
            }),
            { status: 200, headers: { 'Content-Type': 'application/json' } },
          )
        }
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
        workspaceName: '',
        workspacePassword: '',
        username: 'Danny',
        firstName: '',
        lastName: '',
        password: '',
        recoveryCode: '',
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
          method: 'POST',
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

    await act(async () => {
      await result.current.deleteSavedWorkspace('friend')
    })

    expect(fetchCalls).toEqual(
      expect.arrayContaining([
        expect.objectContaining({
          method: 'DELETE',
          path: '/api/accounts/workspaces/friend',
        }),
      ]),
    )
    expect(result.current.workspaceId).toBe('owner')
    expect(result.current.runtimeAccount?.workspaces.map((workspace) => workspace.workspace_id)).toEqual(['owner'])
  })

  it('shows a request-status error when saved table delete returns non-json HTML', async () => {
    sessionStorage.setItem('aidm:authToken', 'account-token')
    const fetchCalls: Array<{ method: string; path: string }> = []
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const path = new URL(String(input), 'http://localhost').pathname
        const method = init?.method ?? 'GET'
        fetchCalls.push({ method, path })
        if (method === 'DELETE' && path === '/api/accounts/workspaces/owner') {
          return new Response('<!doctype html><title>405 Method Not Allowed</title>', {
            status: 405,
            headers: { 'Content-Type': 'text/html' },
          })
        }
        return new Response(
          JSON.stringify({
            account_id: 1,
            username: 'danny',
            first_name: 'Danny',
            last_name: 'Reichner',
            display_name: 'Danny Reichner',
            workspace_id: 'owner',
            workspace_role: 'admin',
            is_workspace_admin: true,
            workspaces: [
              {
                workspace_id: 'owner',
                table_name: 'Test',
                access_mode: 'token',
                workspace_role: 'admin',
                is_workspace_admin: true,
                created_at: null,
                updated_at: null,
              },
            ],
          }),
          { status: 200, headers: { 'Content-Type': 'application/json' } },
        )
      }),
    )
    const { result } = renderHook(() =>
      useRuntimeSettings({
        defaultBaseUrl: '',
        resetRuntimeState: vi.fn(),
        reconnectSocket: vi.fn(),
      }),
    )

    await waitFor(() => expect(result.current.runtimeAccount?.username).toBe('danny'))

    let deleteResult: Awaited<ReturnType<typeof result.current.deleteSavedWorkspace>> | undefined
    await act(async () => {
      deleteResult = await result.current.deleteSavedWorkspace('owner')
    })

    expect(deleteResult).toEqual({ ok: false, error: 'Table delete request failed with status 405' })
    expect(result.current.runtimeSettingsError).toBe('Table delete request failed with status 405')
    expect(fetchCalls).toEqual(
      expect.arrayContaining([
        expect.objectContaining({ method: 'DELETE', path: '/api/accounts/workspaces/owner' }),
      ]),
    )
  })

  it('joins a table with its name and password without storing a table token', async () => {
    sessionStorage.setItem('aidm:authToken', 'account-token')
    const fetchCalls: Array<{ path: string; body: unknown }> = []
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const path = new URL(String(input), 'http://localhost').pathname
        const body = init?.body ? JSON.parse(String(init.body)) : null
        fetchCalls.push({ path, body })
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
              requires_password_setup: false,
              workspaces: [],
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
              workspace_id: 'Friday_Night',
              workspace_role: 'player',
              is_workspace_admin: false,
              requires_password_setup: false,
              workspaces: [
                {
                  workspace_id: 'Friday_Night',
                  workspace_name: 'Friday Night',
                  table_name: 'Friday Night',
                  access_mode: 'password',
                  workspace_role: 'player',
                  is_workspace_admin: false,
                  created_at: null,
                  updated_at: null,
                },
              ],
            },
            account_token: 'account-token',
            workspace_id: 'Friday_Night',
            workspace_role: 'player',
            is_workspace_admin: false,
            claimed_player_ids: [],
            workspaces: [],
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

    await waitFor(() => expect(result.current.runtimeAccount?.username).toBe('danny'))

    act(() => {
      result.current.openAuthTokenPrompt()
      result.current.setRuntimeWorkspaceJoinMethod('password')
      result.current.setRuntimeSettingsForm((current) => ({
        ...current,
        workspaceName: 'Friday Night',
        workspacePassword: 'table-secret',
      }))
    })
    await act(async () => {
      await result.current.submitRuntimeSettings(submitEvent())
    })

    expect(fetchCalls).toEqual(
      expect.arrayContaining([
        expect.objectContaining({
          path: '/api/accounts/workspace',
          body: { table_name: 'Friday Night', table_password: 'table-secret' },
        }),
      ]),
    )
    expect(result.current.workspaceId).toBe('Friday_Night')
    expect(sessionStorage.getItem('aidm:workspaceToken')).toBeNull()
    expect(localStorage.getItem('aidm:workspaceId')).toBe('Friday_Night')
    expect(resetRuntimeState).toHaveBeenCalledOnce()
    expect(reconnectSocket).toHaveBeenCalledOnce()
  })

  it('creates a generated-token table and keeps the raw token only in dialog state', async () => {
    sessionStorage.setItem('aidm:authToken', 'account-token')
    const fetchCalls: Array<{ path: string; body: unknown }> = []
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const path = new URL(String(input), 'http://localhost').pathname
        const body = init?.body ? JSON.parse(String(init.body)) : null
        fetchCalls.push({ path, body })
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
              requires_password_setup: false,
              workspaces: [],
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
              workspace_id: 'Token_Table',
              workspace_role: 'admin',
              is_workspace_admin: true,
              requires_password_setup: false,
              workspaces: [
                {
                  workspace_id: 'Token_Table',
                  workspace_name: 'Token Table',
                  table_name: 'Token Table',
                  access_mode: 'token',
                  workspace_role: 'admin',
                  is_workspace_admin: true,
                  created_at: null,
                  updated_at: null,
                },
              ],
            },
            account_token: 'account-token',
            workspace_id: 'Token_Table',
            workspace_role: 'admin',
            is_workspace_admin: true,
            claimed_player_ids: [],
            workspaces: [],
            workspace_token: 'new-table-token',
          }),
          { status: 201, headers: { 'Content-Type': 'application/json' } },
        )
      }),
    )
    const { result } = renderHook(() =>
      useRuntimeSettings({
        defaultBaseUrl: '',
        resetRuntimeState: vi.fn(),
        reconnectSocket: vi.fn(),
      }),
    )

    await waitFor(() => expect(result.current.runtimeAccount?.username).toBe('danny'))

    act(() => {
      result.current.openAuthTokenPrompt()
      result.current.setRuntimeWorkspaceAction('create')
      result.current.setRuntimeWorkspaceCreateAccessMode('token')
      result.current.setRuntimeSettingsForm((current) => ({
        ...current,
        workspaceName: 'Token Table',
      }))
    })
    await act(async () => {
      await result.current.submitRuntimeSettings(submitEvent())
    })

    expect(fetchCalls).toEqual(
      expect.arrayContaining([
        expect.objectContaining({
          path: '/api/accounts/workspaces',
          body: { table_name: 'Token Table', access_mode: 'token' },
        }),
      ]),
    )
    expect(result.current.runtimeSettingsOpen).toBe(true)
    expect(result.current.runtimeCreatedWorkspaceToken).toBe('new-table-token')
    expect(result.current.workspaceId).toBe('Token_Table')
    expect(sessionStorage.getItem('aidm:workspaceToken')).toBeNull()

    await act(async () => {
      await result.current.submitRuntimeSettings(submitEvent())
    })

    expect(result.current.runtimeSettingsOpen).toBe(false)
    expect(result.current.runtimeCreatedWorkspaceToken).toBe('')
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

  it('does not activate an untrusted share-link backend when the runtime hook mounts directly', () => {
    localStorage.setItem('aidm:baseUrl', 'https://saved.example.test')
    window.history.replaceState(null, '', '/?backend=https%3A%2F%2Fshared.example.test')

    const { result } = renderHook(() =>
      useRuntimeSettings({
        defaultBaseUrl: '',
        resetRuntimeState: vi.fn(),
        reconnectSocket: vi.fn(),
      }),
    )

    expect(result.current.baseUrl).toBe('https://saved.example.test')
    expect(result.current.authToken).toBe('')
    expect(result.current.workspaceToken).toBe('')
    expect(result.current.workspaceId).toBe('')
    expect(result.current.runtimeAccount).toBeNull()
    expect(localStorage.getItem('aidm:baseUrl')).toBe('https://saved.example.test')
  })

  it('records explicit trust before activating a normalized share-link backend', () => {
    window.history.replaceState(
      null,
      '',
      '/?campaign=10&session=20&api=https%3A%2F%2FBackend.Example.Test%2F',
    )

    const { result } = renderHook(() => useShareBackendTrust(''))

    expect(result.current.pendingBackendTrust).toEqual({
      baseUrl: 'https://Backend.Example.Test',
      origin: 'https://backend.example.test',
    })
    expect(localStorage.getItem('aidm:baseUrl')).toBeNull()

    act(() => {
      result.current.confirmPendingBackendTrust()
    })

    expect(result.current.pendingBackendTrust).toBeNull()
    expect(localStorage.getItem('aidm:baseUrl')).toBe('https://Backend.Example.Test')
    expect(JSON.parse(String(localStorage.getItem('aidm:trustedBackendOrigins')))).toContain(
      'https://backend.example.test',
    )
    expect(window.location.search).toBe('?campaign=10&session=20')
  })

  it('rejects a share-link backend without losing the shared campaign and session', () => {
    window.history.replaceState(
      null,
      '',
      '/?campaign=10&session=20&backend=https%3A%2F%2Fbackend.example.test',
    )

    const { result } = renderHook(() => useShareBackendTrust(''))

    act(() => {
      result.current.rejectPendingBackendTrust()
    })

    expect(result.current.pendingBackendTrust).toBeNull()
    expect(localStorage.getItem('aidm:baseUrl')).toBeNull()
    expect(localStorage.getItem('aidm:trustedBackendOrigins')).toBeNull()
    expect(window.location.search).toBe('?campaign=10&session=20')
  })

  it('keeps saved and same-origin share-link backends seamless', () => {
    localStorage.setItem('aidm:baseUrl', 'https://saved.example.test')
    window.history.replaceState(
      null,
      '',
      '/?backend=https%3A%2F%2Fsaved.example.test%2Fshared-api',
    )

    const saved = renderHook(() => useShareBackendTrust(''))
    expect(saved.result.current.pendingBackendTrust).toBeNull()
    saved.unmount()

    localStorage.clear()
    window.history.replaceState(
      null,
      '',
      `/?backend=${encodeURIComponent(`${window.location.origin}/shared-api`)}`,
    )
    const sameOrigin = renderHook(() => useShareBackendTrust(''))
    expect(sameOrigin.result.current.pendingBackendTrust).toBeNull()
  })

  it('binds no-marker legacy credentials to the saved owner before confirming another origin', () => {
    const savedBaseUrl = 'https://saved.example.test/api'
    const confirmedBaseUrl = 'https://confirmed.example.test/api'
    localStorage.setItem('aidm:baseUrl', savedBaseUrl)
    sessionStorage.setItem('aidm:authToken', 'legacy-account-token')
    sessionStorage.setItem('aidm:workspaceToken', 'legacy-workspace-token')
    window.history.replaceState(
      null,
      '',
      `/?backend=${encodeURIComponent(confirmedBaseUrl)}`,
    )

    const { result } = renderHook(() => useShareBackendTrust('https://default.example.test'))

    act(() => {
      result.current.confirmPendingBackendTrust()
    })

    expect(localStorage.getItem('aidm:credentialOrigin')).toBe(normalizedBackendOrigin(savedBaseUrl))
    expect(sessionStorage.getItem(originScopedStorageKey('aidm:authToken', savedBaseUrl))).toBe(
      'legacy-account-token',
    )
    expect(sessionStorage.getItem(originScopedStorageKey('aidm:workspaceToken', savedBaseUrl))).toBe(
      'legacy-workspace-token',
    )
    expect(sessionStorage.getItem(originScopedStorageKey('aidm:authToken', confirmedBaseUrl))).toBeNull()
    expect(sessionStorage.getItem(originScopedStorageKey('aidm:workspaceToken', confirmedBaseUrl))).toBeNull()
    expect(localStorage.getItem('aidm:baseUrl')).toBe(confirmedBaseUrl)
  })

  it.each([
    {
      label: 'saved',
      savedBaseUrl: 'https://saved.example.test/api',
      defaultBaseUrl: 'https://default.example.test/api',
      expectedBaseUrl: 'https://saved.example.test/api',
    },
    {
      label: 'default',
      savedBaseUrl: '',
      defaultBaseUrl: 'https://default.example.test/api',
      expectedBaseUrl: 'https://default.example.test/api',
    },
  ])('preserves normal $label-backend legacy credential migration', async ({
    savedBaseUrl,
    defaultBaseUrl,
    expectedBaseUrl,
  }) => {
    if (savedBaseUrl) localStorage.setItem('aidm:baseUrl', savedBaseUrl)
    sessionStorage.setItem('aidm:authToken', 'legacy-account-token')
    sessionStorage.setItem('aidm:workspaceToken', 'legacy-workspace-token')
    const fetchMock = vi.fn<
      (input: RequestInfo | URL, init?: RequestInit) => Promise<Response>
    >(async () =>
      new Response(
        JSON.stringify({
          account_id: 1,
          username: 'legacy-user',
          first_name: 'Legacy',
          last_name: 'User',
          display_name: 'Legacy User',
          workspace_id: 'owner',
          workspace_role: 'player',
          is_workspace_admin: false,
          requires_password_setup: false,
          workspaces: [],
        }),
        { status: 200, headers: { 'Content-Type': 'application/json' } },
      ),
    )
    vi.stubGlobal('fetch', fetchMock)

    const { result } = renderHook(() =>
      useRuntimeSettings({
        defaultBaseUrl,
        resetRuntimeState: vi.fn(),
        reconnectSocket: vi.fn(),
      }),
    )

    expect(result.current.baseUrl).toBe(expectedBaseUrl)
    expect(result.current.authToken).toBe('legacy-account-token')
    expect(result.current.workspaceToken).toBe('legacy-workspace-token')
    expect(sessionStorage.getItem(originScopedStorageKey('aidm:authToken', expectedBaseUrl))).toBe(
      'legacy-account-token',
    )
    expect(sessionStorage.getItem(originScopedStorageKey('aidm:workspaceToken', expectedBaseUrl))).toBe(
      'legacy-workspace-token',
    )
    await waitFor(() => expect(fetchMock).toHaveBeenCalledOnce())
    const [requestUrl, requestInit] = fetchMock.mock.calls[0]
    expect(String(requestUrl)).toBe(`${expectedBaseUrl}/api/accounts/me`)
    const headers = new Headers(requestInit?.headers)
    expect(headers.get('Authorization')).toBe('Bearer legacy-account-token')
    expect(headers.get('X-AIDM-Workspace-Token')).toBe('legacy-workspace-token')
  })
})
