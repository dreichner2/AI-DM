import type { JsonRecord } from './types'

export class ApiClientError extends Error {
  status: number
  payload: unknown

  constructor(message: string, status: number, payload: unknown) {
    super(message)
    this.name = 'ApiClientError'
    this.status = status
    this.payload = payload
  }
}

export function normalizeBaseUrl(value: string) {
  const trimmed = value.trim()
  return trimmed.endsWith('/') ? trimmed.slice(0, -1) : trimmed
}

const NGROK_BROWSER_WARNING_HEADER = 'ngrok-skip-browser-warning'
export const WORKSPACE_TOKEN_HEADER = 'X-AIDM-Workspace-Token'
export const WORKSPACE_ID_HEADER = 'X-AIDM-Workspace-Id'
export const CSRF_HEADER = 'X-AIDM-CSRF-Token'
const CSRF_COOKIE_NAME = 'aidm_csrf_token'
const ACCOUNT_TOKEN_COOKIE_NAME = 'aidm_account_token'
const SAVED_BASE_URL_KEY = 'aidm:baseUrl'
const ACTIVE_BASE_URL_KEY = 'aidm:activeBaseUrl'
const TRUSTED_BACKEND_ORIGINS_KEY = 'aidm:trustedBackendOrigins'
const LEGACY_CREDENTIAL_ORIGIN_KEY = 'aidm:credentialOrigin'
const LEGACY_CSRF_ORIGIN_KEY = 'aidm:csrfOrigin'
const ORIGIN_SCOPED_CREDENTIAL_KEYS = [
  'aidm:authToken',
  'aidm:workspaceToken',
  'aidm:workspaceId',
  'aidm:accountTokenTransport',
  'aidm:account',
] as const

function browserLocationOrigin() {
  return typeof window === 'undefined' ? '' : window.location.origin
}

export function normalizedBackendOrigin(baseUrl: string) {
  try {
    const fallbackOrigin = browserLocationOrigin()
    const resolved = new URL(normalizeBaseUrl(baseUrl) || fallbackOrigin, fallbackOrigin || undefined)
    return resolved.origin
  } catch {
    return ''
  }
}

export function backendOriginsMatch(left: string, right: string) {
  const leftOrigin = normalizedBackendOrigin(left)
  return Boolean(leftOrigin) && leftOrigin === normalizedBackendOrigin(right)
}

function trustedBackendOrigins() {
  if (typeof localStorage === 'undefined') return new Set<string>()
  try {
    const parsed = JSON.parse(localStorage.getItem(TRUSTED_BACKEND_ORIGINS_KEY) ?? '[]') as unknown
    if (!Array.isArray(parsed)) return new Set<string>()
    return new Set(parsed.filter((value): value is string => typeof value === 'string' && Boolean(value)))
  } catch {
    return new Set<string>()
  }
}

export function trustBackendOrigin(baseUrl: string) {
  const origin = normalizedBackendOrigin(baseUrl)
  if (!origin || typeof localStorage === 'undefined') return
  const trustedOrigins = trustedBackendOrigins()
  trustedOrigins.add(origin)
  localStorage.setItem(TRUSTED_BACKEND_ORIGINS_KEY, JSON.stringify([...trustedOrigins].sort()))
}

export function isBackendOriginTrusted(baseUrl: string) {
  const origin = normalizedBackendOrigin(baseUrl)
  if (!origin) return false
  if (origin === browserLocationOrigin()) return true

  const savedBaseUrl = typeof localStorage === 'undefined' ? '' : localStorage.getItem(SAVED_BASE_URL_KEY) ?? ''
  return backendOriginsMatch(origin, savedBaseUrl) || trustedBackendOrigins().has(origin)
}

export function bindLegacyCredentialsToBackend(baseUrl: string) {
  if (!isBackendOriginTrusted(baseUrl) || typeof localStorage === 'undefined') return
  if (!localStorage.getItem(LEGACY_CREDENTIAL_ORIGIN_KEY)) {
    localStorage.setItem(LEGACY_CREDENTIAL_ORIGIN_KEY, normalizedBackendOrigin(baseUrl))
    const storages = [localStorage, ...(typeof sessionStorage === 'undefined' ? [] : [sessionStorage])]
    for (const storage of storages) {
      for (const key of ORIGIN_SCOPED_CREDENTIAL_KEYS) {
        const legacyValue = storage.getItem(key)
        const scopedKey = originScopedStorageKey(key, baseUrl)
        if (legacyValue !== null && storage.getItem(scopedKey) === null) {
          storage.setItem(scopedKey, legacyValue)
        }
      }
    }
  }
  if (
    typeof document !== 'undefined'
    && readCookie(CSRF_COOKIE_NAME)
    && !localStorage.getItem(LEGACY_CSRF_ORIGIN_KEY)
  ) {
    localStorage.setItem(LEGACY_CSRF_ORIGIN_KEY, normalizedBackendOrigin(baseUrl))
  }
}

export function backendOwnsLegacyCredentials(baseUrl: string) {
  if (!isBackendOriginTrusted(baseUrl) || typeof localStorage === 'undefined') return false
  const credentialOrigin = localStorage.getItem(LEGACY_CREDENTIAL_ORIGIN_KEY) ?? ''
  return Boolean(credentialOrigin) && credentialOrigin === normalizedBackendOrigin(baseUrl)
}

function writeRawCookie(name: string, encodedValue: string) {
  if (typeof document === 'undefined') return
  const secure = browserLocationOrigin().startsWith('https://') ? '; Secure' : ''
  document.cookie = `${encodeURIComponent(name)}=${encodedValue}; Path=/; SameSite=Lax${secure}`
}

function clearBrowserCookie(name: string) {
  if (typeof document === 'undefined') return
  document.cookie = `${encodeURIComponent(name)}=; Max-Age=0; Path=/; SameSite=Lax`
}

export function activateBackendCredentialScope(baseUrl: string) {
  if (!isBackendOriginTrusted(baseUrl) || typeof localStorage === 'undefined') return
  const nextOrigin = normalizedBackendOrigin(baseUrl)
  const currentOrigin = localStorage.getItem(LEGACY_CREDENTIAL_ORIGIN_KEY) ?? ''
  const storages = [localStorage, ...(typeof sessionStorage === 'undefined' ? [] : [sessionStorage])]

  if (currentOrigin) {
    for (const storage of storages) {
      for (const key of ORIGIN_SCOPED_CREDENTIAL_KEYS) {
        const legacyValue = storage.getItem(key)
        const currentScopedKey = originScopedStorageKey(key, currentOrigin)
        if (legacyValue !== null && storage.getItem(currentScopedKey) === null) {
          storage.setItem(currentScopedKey, legacyValue)
        }
      }
    }
    const legacyAccountToken = readCookie(ACCOUNT_TOKEN_COOKIE_NAME)
    const currentScopedCookie = `${ACCOUNT_TOKEN_COOKIE_NAME}:${currentOrigin}`
    if (legacyAccountToken && !readCookie(currentScopedCookie)) {
      writeRawCookie(currentScopedCookie, legacyAccountToken)
    }
  }

  localStorage.setItem(LEGACY_CREDENTIAL_ORIGIN_KEY, nextOrigin)
  for (const storage of storages) {
    for (const key of ORIGIN_SCOPED_CREDENTIAL_KEYS) {
      const scopedValue = storage.getItem(originScopedStorageKey(key, nextOrigin))
      if (scopedValue === null) {
        storage.removeItem(key)
      } else {
        storage.setItem(key, scopedValue)
      }
    }
  }

  const nextScopedAccountToken = readCookie(`${ACCOUNT_TOKEN_COOKIE_NAME}:${nextOrigin}`)
  if (nextScopedAccountToken) {
    writeRawCookie(ACCOUNT_TOKEN_COOKIE_NAME, nextScopedAccountToken)
  } else {
    clearBrowserCookie(ACCOUNT_TOKEN_COOKIE_NAME)
  }
}

export function originScopedStorageKey(key: string, baseUrl: string) {
  return `${key}:${encodeURIComponent(normalizedBackendOrigin(baseUrl))}`
}

export function readOriginScopedStorage(storage: Storage, key: string, baseUrl: string) {
  if (!isBackendOriginTrusted(baseUrl)) return null
  const scopedKey = originScopedStorageKey(key, baseUrl)
  const scopedValue = storage.getItem(scopedKey)
  if (scopedValue !== null) return scopedValue
  if (!backendOwnsLegacyCredentials(baseUrl)) return null

  const legacyValue = storage.getItem(key)
  if (legacyValue !== null) {
    storage.setItem(scopedKey, legacyValue)
  }
  return legacyValue
}

export function writeOriginScopedStorage(storage: Storage, key: string, value: string, baseUrl: string) {
  if (!isBackendOriginTrusted(baseUrl)) return
  activateBackendCredentialScope(baseUrl)
  storage.setItem(originScopedStorageKey(key, baseUrl), value)
  storage.setItem(key, value)
}

export function removeOriginScopedStorage(storage: Storage, key: string, baseUrl: string) {
  storage.removeItem(originScopedStorageKey(key, baseUrl))
  if (backendOwnsLegacyCredentials(baseUrl)) {
    storage.removeItem(key)
  }
}

function readCookie(name: string) {
  const prefix = `${encodeURIComponent(name)}=`
  return document.cookie
    .split(';')
    .map((entry) => entry.trim())
    .find((entry) => entry.startsWith(prefix))
    ?.slice(prefix.length) ?? ''
}

function activeStoredBaseUrl() {
  if (typeof sessionStorage !== 'undefined') {
    const activeBaseUrl = sessionStorage.getItem(ACTIVE_BASE_URL_KEY)
    if (activeBaseUrl !== null) return activeBaseUrl
  }
  return typeof localStorage === 'undefined' ? '' : localStorage.getItem(SAVED_BASE_URL_KEY) ?? ''
}

export function setActiveRuntimeBaseUrl(baseUrl: string) {
  if (typeof sessionStorage === 'undefined') return
  sessionStorage.setItem(ACTIVE_BASE_URL_KEY, normalizeBaseUrl(baseUrl))
}

export function storedWorkspaceToken(baseUrl = activeStoredBaseUrl()) {
  return readOriginScopedStorage(sessionStorage, 'aidm:workspaceToken', baseUrl) ?? ''
}

export function storedAuthToken(baseUrl = activeStoredBaseUrl()) {
  return readOriginScopedStorage(sessionStorage, 'aidm:authToken', baseUrl) ?? ''
}

export function storedWorkspaceId(baseUrl = activeStoredBaseUrl()) {
  return readOriginScopedStorage(localStorage, 'aidm:workspaceId', baseUrl)
    ?? readOriginScopedStorage(sessionStorage, 'aidm:workspaceId', baseUrl)
    ?? ''
}

export function storedRuntimeAccessSnapshot(authToken = storedAuthToken()) {
  return JSON.stringify([authToken.trim(), storedWorkspaceToken().trim(), storedWorkspaceId().trim()])
}

function shouldBypassNgrokBrowserWarning(baseUrl: string) {
  try {
    const hostname = new URL(normalizeBaseUrl(baseUrl)).hostname
    return hostname.endsWith('.ngrok-free.app') || hostname.endsWith('.ngrok.app')
  } catch {
    return baseUrl.includes('.ngrok-free.app') || baseUrl.includes('.ngrok.app')
  }
}

export function ngrokBrowserWarningBypassHeaders(baseUrl: string): Record<string, string> | undefined {
  if (!shouldBypassNgrokBrowserWarning(baseUrl)) return undefined
  return { [NGROK_BROWSER_WARNING_HEADER]: 'true' }
}

export function addNgrokBrowserWarningBypassHeader(headers: Headers, baseUrl: string) {
  const bypassHeaders = ngrokBrowserWarningBypassHeaders(baseUrl)
  if (!bypassHeaders) return
  for (const [name, value] of Object.entries(bypassHeaders)) {
    headers.set(name, value)
  }
}

export function addWorkspaceTokenHeader(
  headers: Headers,
  baseUrl = activeStoredBaseUrl(),
  workspaceToken = storedWorkspaceToken(baseUrl),
) {
  if (!isBackendOriginTrusted(baseUrl)) return
  if (headers.has(WORKSPACE_TOKEN_HEADER) || headers.has(WORKSPACE_ID_HEADER)) return
  const token = workspaceToken.trim()
  if (token) {
    headers.set(WORKSPACE_TOKEN_HEADER, token)
    return
  }
  const workspaceId = storedWorkspaceId(baseUrl).trim()
  if (workspaceId) {
    headers.set(WORKSPACE_ID_HEADER, workspaceId)
  }
}

export function addCookieCsrfHeader(headers: Headers, baseUrl = activeStoredBaseUrl()) {
  if (!isBackendOriginTrusted(baseUrl)) return
  if (headers.has(CSRF_HEADER)) return
  const origin = normalizedBackendOrigin(baseUrl)
  const csrfOrigin = localStorage.getItem(LEGACY_CSRF_ORIGIN_KEY) ?? ''
  if (!csrfOrigin && origin === browserLocationOrigin() && readCookie(CSRF_COOKIE_NAME)) {
    localStorage.setItem(LEGACY_CSRF_ORIGIN_KEY, origin)
  }
  const scopedCookieName = `${CSRF_COOKIE_NAME}:${origin}`
  const scopedToken = decodeURIComponent(readCookie(scopedCookieName))
  const legacyToken = (csrfOrigin ? csrfOrigin === origin : origin === browserLocationOrigin())
    ? decodeURIComponent(readCookie(CSRF_COOKIE_NAME))
    : ''
  const token = scopedToken || legacyToken
  if (token) {
    headers.set(CSRF_HEADER, token)
    if (!scopedToken) {
      writeRawCookie(scopedCookieName, encodeURIComponent(token))
    }
  }
}

function isRecord(value: unknown): value is JsonRecord {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function errorMessage(payload: unknown, fallback: string) {
  if (isRecord(payload)) {
    if (typeof payload.error === 'string') return payload.error
    if (typeof payload.message === 'string') return payload.message
  }
  return fallback
}

function parseResponsePayload(text: string, response: Response) {
  if (!text) return null

  const contentType = response.headers.get('Content-Type') ?? ''
  if (!contentType.toLowerCase().includes('json')) {
    return { raw: text }
  }

  try {
    return JSON.parse(text) as unknown
  } catch {
    return { raw: text }
  }
}

export async function apiFetch<T>(
  baseUrl: string,
  path: string,
  token: string,
  options: RequestInit = {},
): Promise<T> {
  const headers = new Headers(options.headers)
  const trustedBackend = isBackendOriginTrusted(baseUrl)
  if (trustedBackend) {
    if (token.trim()) {
      headers.set('Authorization', `Bearer ${token.trim()}`)
    }
    addWorkspaceTokenHeader(headers, baseUrl)
    addCookieCsrfHeader(headers, baseUrl)
  } else {
    headers.delete('Authorization')
    headers.delete(WORKSPACE_TOKEN_HEADER)
    headers.delete(WORKSPACE_ID_HEADER)
    headers.delete(CSRF_HEADER)
  }
  if (options.body && !headers.has('Content-Type')) {
    headers.set('Content-Type', 'application/json')
  }
  addNgrokBrowserWarningBypassHeader(headers, baseUrl)

  const response = await fetch(`${normalizeBaseUrl(baseUrl)}${path}`, {
    ...options,
    ...(!trustedBackend ? { credentials: 'omit' as const } : {}),
    headers,
  })
  const text = await response.text()
  const payload = parseResponsePayload(text, response)

  if (!response.ok) {
    throw new ApiClientError(
      errorMessage(payload, `Request failed with status ${response.status}`),
      response.status,
      payload,
    )
  }

  return payload as T
}
