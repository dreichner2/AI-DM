// @vitest-environment jsdom
import { beforeEach, describe, expect, it } from 'vitest'
import { trustBackendOrigin, writeOriginScopedStorage } from './api'
import { buildSessionSocketConnection } from './useSessionSocket'

describe('session socket backend origin credential boundary', () => {
  beforeEach(() => {
    localStorage.clear()
    sessionStorage.clear()
  })

  it('omits all Socket.IO auth and browser credentials for an untrusted backend', () => {
    const savedBackend = 'https://saved-backend.example.test'
    localStorage.setItem('aidm:baseUrl', savedBackend)
    trustBackendOrigin(savedBackend)
    writeOriginScopedStorage(sessionStorage, 'aidm:workspaceToken', 'saved-workspace-token', savedBackend)
    writeOriginScopedStorage(localStorage, 'aidm:workspaceId', 'saved-workspace', savedBackend)

    const { socketOptions } = buildSessionSocketConnection(
      'https://untrusted-tunnel.ngrok-free.app',
      'saved-account-token',
    )

    expect(socketOptions.auth).toBeUndefined()
    expect(socketOptions.withCredentials).toBe(false)
    expect(socketOptions.extraHeaders).toEqual({ 'ngrok-skip-browser-warning': 'true' })
  })

  it('includes only credentials scoped to a trusted backend and retains ngrok support', () => {
    const backend = 'https://trusted-tunnel.ngrok-free.app'
    trustBackendOrigin(backend)
    writeOriginScopedStorage(sessionStorage, 'aidm:workspaceToken', 'trusted-workspace-token', backend)
    writeOriginScopedStorage(localStorage, 'aidm:workspaceId', 'trusted-workspace', backend)

    const { socketOptions } = buildSessionSocketConnection(backend, 'trusted-account-token')

    expect(socketOptions.auth).toEqual({
      account_token: 'trusted-account-token',
      workspace_token: 'trusted-workspace-token',
    })
    expect(socketOptions.withCredentials).toBeUndefined()
    expect(socketOptions.extraHeaders).toEqual({ 'ngrok-skip-browser-warning': 'true' })
  })
})
