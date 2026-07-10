// @vitest-environment jsdom
import { beforeEach, describe, expect, it, vi } from 'vitest'
import {
  CSRF_HEADER,
  WORKSPACE_ID_HEADER,
  WORKSPACE_TOKEN_HEADER,
  activateBackendCredentialScope,
  apiFetch,
  bindLegacyCredentialsToBackend,
  trustBackendOrigin,
  storedAuthToken,
  storedWorkspaceToken,
  writeOriginScopedStorage,
} from './api'

function clearCookies() {
  for (const cookie of document.cookie.split(';')) {
    const name = cookie.split('=', 1)[0]?.trim()
    if (name) document.cookie = `${name}=; Max-Age=0; Path=/; SameSite=Lax`
  }
}

describe('backend origin credential boundary', () => {
  beforeEach(() => {
    localStorage.clear()
    sessionStorage.clear()
    clearCookies()
  })

  it('strips every stored or caller-provided credential from an untrusted backend request', async () => {
    const savedBackend = 'https://saved-backend.example.test'
    localStorage.setItem('aidm:baseUrl', savedBackend)
    sessionStorage.setItem('aidm:authToken', 'saved-account-token')
    sessionStorage.setItem('aidm:workspaceToken', 'saved-workspace-token')
    localStorage.setItem('aidm:workspaceId', 'saved-workspace')
    bindLegacyCredentialsToBackend(savedBackend)
    document.cookie = 'aidm_csrf_token=saved-csrf-token; Path=/; SameSite=Lax'

    const fetchMock = vi.fn<typeof fetch>()
    fetchMock.mockResolvedValue(
      new Response(JSON.stringify({ ok: true }), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      }),
    )
    vi.stubGlobal('fetch', fetchMock)

    await apiFetch('https://untrusted.example.test', '/api/health', 'saved-account-token', {
      headers: {
        Authorization: 'Bearer caller-account-token',
        [WORKSPACE_TOKEN_HEADER]: 'caller-workspace-token',
        [WORKSPACE_ID_HEADER]: 'caller-workspace',
        [CSRF_HEADER]: 'caller-csrf-token',
      },
    })

    const request = fetchMock.mock.calls[0]?.[1]
    const headers = new Headers(request?.headers)
    expect(headers.get('Authorization')).toBeNull()
    expect(headers.get(WORKSPACE_TOKEN_HEADER)).toBeNull()
    expect(headers.get(WORKSPACE_ID_HEADER)).toBeNull()
    expect(headers.get(CSRF_HEADER)).toBeNull()
    expect(request?.credentials).toBe('omit')
  })

  it('sends credentials that belong to an explicitly trusted backend origin', async () => {
    const backend = 'https://trusted-backend.example.test/path'
    trustBackendOrigin(backend)
    writeOriginScopedStorage(sessionStorage, 'aidm:workspaceToken', 'trusted-workspace-token', backend)
    document.cookie = 'aidm_csrf_token=trusted-csrf-token; Path=/; SameSite=Lax'
    localStorage.setItem('aidm:csrfOrigin', 'https://trusted-backend.example.test')

    const fetchMock = vi.fn<typeof fetch>()
    fetchMock.mockResolvedValue(
      new Response(JSON.stringify({ ok: true }), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      }),
    )
    vi.stubGlobal('fetch', fetchMock)

    await apiFetch(backend, '/api/health', 'trusted-account-token')

    const headers = new Headers(fetchMock.mock.calls[0]?.[1]?.headers)
    expect(headers.get('Authorization')).toBe('Bearer trusted-account-token')
    expect(headers.get(WORKSPACE_TOKEN_HEADER)).toBe('trusted-workspace-token')
    expect(headers.get(CSRF_HEADER)).toBe('trusted-csrf-token')
    expect(fetchMock.mock.calls[0]?.[1]?.credentials).toBeUndefined()
  })

  it('restores only the credentials scoped to the selected trusted origin', () => {
    const firstBackend = 'https://first.example.test'
    const secondBackend = 'https://second.example.test'
    trustBackendOrigin(firstBackend)
    writeOriginScopedStorage(sessionStorage, 'aidm:authToken', 'first-account-token', firstBackend)
    writeOriginScopedStorage(sessionStorage, 'aidm:workspaceToken', 'first-workspace-token', firstBackend)

    trustBackendOrigin(secondBackend)
    activateBackendCredentialScope(secondBackend)
    expect(storedAuthToken(secondBackend)).toBe('')
    expect(storedWorkspaceToken(secondBackend)).toBe('')
    writeOriginScopedStorage(sessionStorage, 'aidm:authToken', 'second-account-token', secondBackend)
    writeOriginScopedStorage(sessionStorage, 'aidm:workspaceToken', 'second-workspace-token', secondBackend)

    activateBackendCredentialScope(firstBackend)
    expect(storedAuthToken(firstBackend)).toBe('first-account-token')
    expect(storedWorkspaceToken(firstBackend)).toBe('first-workspace-token')
    expect(storedAuthToken(secondBackend)).toBe('second-account-token')
    expect(storedWorkspaceToken(secondBackend)).toBe('second-workspace-token')
  })
})
