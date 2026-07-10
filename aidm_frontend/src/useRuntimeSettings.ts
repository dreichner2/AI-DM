import { useCallback, useEffect, useRef, useState, type FormEvent } from 'react'
import {
  addCookieCsrfHeader,
  addNgrokBrowserWarningBypassHeader,
  activateBackendCredentialScope,
  backendOriginsMatch,
  backendOwnsLegacyCredentials,
  bindLegacyCredentialsToBackend,
  isBackendOriginTrusted,
  normalizeBaseUrl,
  normalizedBackendOrigin,
  readOriginScopedStorage,
  removeOriginScopedStorage,
  setActiveRuntimeBaseUrl,
  trustBackendOrigin,
  writeOriginScopedStorage,
} from './api'
import type { Account, AccountSession, AccountWorkspace } from './types'

export type RuntimeSettingsForm = {
  baseUrl: string
  workspaceToken: string
  workspaceName: string
  workspacePassword: string
  username: string
  firstName: string
  lastName: string
  password: string
  recoveryCode: string
}

export type RuntimeSettingsMode = 'settings' | 'auth'
export type RuntimeAuthIntent = 'login' | 'signup'
export type RuntimeAuthStep = 'account' | 'workspace'
export type RuntimeWorkspaceAction = 'join' | 'create'
export type RuntimeWorkspaceJoinMethod = 'token' | 'password'
export type RuntimeWorkspaceCreateAccessMode = 'password' | 'token'
export type DeleteSavedWorkspaceResult = { ok: true } | { ok: false; error: string }

export type RuntimeAccount = {
  accountId: number
  username: string
  firstName: string
  lastName: string
  displayName: string
  workspaceId: string | null
  workspaceRole: string | null
  isWorkspaceAdmin: boolean
  requiresPasswordSetup: boolean
  workspaces: AccountWorkspace[]
} | null

type RuntimeApiError = Error & {
  errorCode?: string
}

const ACCOUNT_TOKEN_COOKIE = 'aidm_account_token'
const ACCOUNT_TOKEN_COOKIE_MAX_AGE = 60 * 60 * 24 * 30
const ACCOUNT_TOKEN_TRANSPORT_KEY = 'aidm:accountTokenTransport'
const HTTP_ONLY_COOKIE_TRANSPORT = 'http_only_cookie'
const LEGACY_PASSWORD_SETUP_ERROR_CODE = 'legacy_password_setup_required'
export const LEGACY_PASSWORD_SETUP_MESSAGE =
  'Legacy account found. Use the saved account session or ask the AIDM operator for a recovery code, then set a new password.'

function readCookie(name: string) {
  const prefix = `${encodeURIComponent(name)}=`
  return document.cookie
    .split(';')
    .map((entry) => entry.trim())
    .find((entry) => entry.startsWith(prefix))
    ?.slice(prefix.length) ?? ''
}

function writeCookie(name: string, value: string, maxAgeSeconds: number) {
  const sameSite = 'SameSite=Lax'
  const secure = window.location.protocol === 'https:' ? '; Secure' : ''
  document.cookie = `${encodeURIComponent(name)}=${encodeURIComponent(value)}; Max-Age=${maxAgeSeconds}; Path=/; ${sameSite}${secure}`
}

function clearCookie(name: string) {
  document.cookie = `${encodeURIComponent(name)}=; Max-Age=0; Path=/; SameSite=Lax`
}

function accountTokenCookieName(baseUrl: string) {
  return `${ACCOUNT_TOKEN_COOKIE}:${normalizedBackendOrigin(baseUrl)}`
}

function loadSessionAuthToken(baseUrl: string) {
  const sessionToken = readOriginScopedStorage(sessionStorage, 'aidm:authToken', baseUrl)
  if (sessionToken !== null) return sessionToken
  const scopedCookieToken = decodeURIComponent(readCookie(accountTokenCookieName(baseUrl)))
  const legacyCookieToken = backendOwnsLegacyCredentials(baseUrl)
    ? decodeURIComponent(readCookie(ACCOUNT_TOKEN_COOKIE))
    : ''
  const cookieToken = scopedCookieToken || legacyCookieToken
  if (cookieToken) {
    writeOriginScopedStorage(sessionStorage, 'aidm:authToken', cookieToken, baseUrl)
    return cookieToken
  }
  const legacyToken = readOriginScopedStorage(localStorage, 'aidm:authToken', baseUrl) ?? ''
  if (legacyToken) {
    writeOriginScopedStorage(sessionStorage, 'aidm:authToken', legacyToken, baseUrl)
    removeOriginScopedStorage(localStorage, 'aidm:authToken', baseUrl)
    writeCookie(ACCOUNT_TOKEN_COOKIE, legacyToken, ACCOUNT_TOKEN_COOKIE_MAX_AGE)
    writeCookie(accountTokenCookieName(baseUrl), legacyToken, ACCOUNT_TOKEN_COOKIE_MAX_AGE)
  }
  return legacyToken
}

function storeSessionAuthToken(value: string, baseUrl: string) {
  const token = value.trim()
  removeOriginScopedStorage(localStorage, 'aidm:authToken', baseUrl)
  localStorage.removeItem('aidm:authToken')
  if (token) {
    writeOriginScopedStorage(sessionStorage, 'aidm:authToken', token, baseUrl)
    writeCookie(ACCOUNT_TOKEN_COOKIE, token, ACCOUNT_TOKEN_COOKIE_MAX_AGE)
    writeCookie(accountTokenCookieName(baseUrl), token, ACCOUNT_TOKEN_COOKIE_MAX_AGE)
  } else {
    removeOriginScopedStorage(sessionStorage, 'aidm:authToken', baseUrl)
    clearCookie(accountTokenCookieName(baseUrl))
    if (backendOwnsLegacyCredentials(baseUrl)) {
      clearCookie(ACCOUNT_TOKEN_COOKIE)
    }
  }
}

function loadAccountTokenTransport(baseUrl: string) {
  return readOriginScopedStorage(sessionStorage, ACCOUNT_TOKEN_TRANSPORT_KEY, baseUrl)
    ?? readOriginScopedStorage(localStorage, ACCOUNT_TOKEN_TRANSPORT_KEY, baseUrl)
    ?? ''
}

function storeAccountTokenTransport(value: string | null | undefined, baseUrl: string) {
  const transport = String(value || '').trim()
  if (transport) {
    writeOriginScopedStorage(sessionStorage, ACCOUNT_TOKEN_TRANSPORT_KEY, transport, baseUrl)
    writeOriginScopedStorage(localStorage, ACCOUNT_TOKEN_TRANSPORT_KEY, transport, baseUrl)
  } else {
    removeOriginScopedStorage(sessionStorage, ACCOUNT_TOKEN_TRANSPORT_KEY, baseUrl)
    removeOriginScopedStorage(localStorage, ACCOUNT_TOKEN_TRANSPORT_KEY, baseUrl)
  }
}

function accountSessionTokenTransport(session: AccountSession) {
  return String(session.account_token_transport || '').trim()
}

function hasCookieAccountSession(transport: string) {
  return transport === HTTP_ONLY_COOKIE_TRANSPORT
}

function loadSessionWorkspaceToken(baseUrl: string) {
  const sessionToken = readOriginScopedStorage(sessionStorage, 'aidm:workspaceToken', baseUrl)
  if (sessionToken !== null) return sessionToken
  const legacyToken = readOriginScopedStorage(localStorage, 'aidm:workspaceToken', baseUrl) ?? ''
  if (legacyToken) {
    writeOriginScopedStorage(sessionStorage, 'aidm:workspaceToken', legacyToken, baseUrl)
    removeOriginScopedStorage(localStorage, 'aidm:workspaceToken', baseUrl)
  }
  return legacyToken
}

function storeSessionWorkspaceToken(value: string, baseUrl: string) {
  const token = value.trim()
  removeOriginScopedStorage(localStorage, 'aidm:workspaceToken', baseUrl)
  localStorage.removeItem('aidm:workspaceToken')
  if (token) {
    writeOriginScopedStorage(sessionStorage, 'aidm:workspaceToken', token, baseUrl)
  } else {
    removeOriginScopedStorage(sessionStorage, 'aidm:workspaceToken', baseUrl)
  }
}

function loadStoredWorkspaceId(baseUrl: string) {
  return readOriginScopedStorage(localStorage, 'aidm:workspaceId', baseUrl)
    ?? readOriginScopedStorage(sessionStorage, 'aidm:workspaceId', baseUrl)
    ?? ''
}

function storeWorkspaceId(value: string | null | undefined, baseUrl: string) {
  const workspaceId = String(value || '').trim()
  if (workspaceId) {
    writeOriginScopedStorage(localStorage, 'aidm:workspaceId', workspaceId, baseUrl)
    writeOriginScopedStorage(sessionStorage, 'aidm:workspaceId', workspaceId, baseUrl)
  } else {
    removeOriginScopedStorage(localStorage, 'aidm:workspaceId', baseUrl)
    removeOriginScopedStorage(sessionStorage, 'aidm:workspaceId', baseUrl)
  }
}

function loadSessionAccount(baseUrl: string): RuntimeAccount {
  const raw = readOriginScopedStorage(sessionStorage, 'aidm:account', baseUrl)
    ?? readOriginScopedStorage(localStorage, 'aidm:account', baseUrl)
  if (!raw) return null
  try {
    const parsed = JSON.parse(raw) as Partial<NonNullable<RuntimeAccount>>
    if (!parsed || typeof parsed.username !== 'string') return null
    const workspaces = Array.isArray(parsed.workspaces) ? parsed.workspaces : []
    return {
      accountId: typeof parsed.accountId === 'number' ? parsed.accountId : 0,
      username: parsed.username,
      firstName: typeof parsed.firstName === 'string' ? parsed.firstName : '',
      lastName: typeof parsed.lastName === 'string' ? parsed.lastName : '',
      displayName: typeof parsed.displayName === 'string' ? parsed.displayName : parsed.username,
      workspaceId: typeof parsed.workspaceId === 'string' ? parsed.workspaceId : null,
      workspaceRole: typeof parsed.workspaceRole === 'string' ? parsed.workspaceRole : null,
      isWorkspaceAdmin: parsed.isWorkspaceAdmin === true,
      requiresPasswordSetup: parsed.requiresPasswordSetup === true,
      workspaces,
    }
  } catch {
    return null
  }
}

function storeSessionAccount(value: RuntimeAccount, baseUrl: string) {
  if (!value) {
    removeOriginScopedStorage(sessionStorage, 'aidm:account', baseUrl)
    removeOriginScopedStorage(localStorage, 'aidm:account', baseUrl)
    return
  }
  const serialized = JSON.stringify(value)
  writeOriginScopedStorage(sessionStorage, 'aidm:account', serialized, baseUrl)
  writeOriginScopedStorage(localStorage, 'aidm:account', serialized, baseUrl)
}

function isHttpBaseUrl(value: string) {
  try {
    const url = new URL(value)
    return ['http:', 'https:'].includes(url.protocol)
  } catch {
    return false
  }
}

function queryRuntimeBaseUrl() {
  const params = new URLSearchParams(window.location.search)
  const value = params.get('backend') ?? params.get('api')
  const baseUrl = value ? normalizeBaseUrl(value) : ''
  return baseUrl && isHttpBaseUrl(baseUrl) ? baseUrl : ''
}

export type PendingBackendTrust = {
  baseUrl: string
  origin: string
}

function removeRuntimeBackendQuery() {
  const params = new URLSearchParams(window.location.search)
  params.delete('backend')
  params.delete('api')
  const query = params.toString()
  window.history.replaceState(
    null,
    '',
    `${window.location.pathname}${query ? `?${query}` : ''}${window.location.hash}`,
  )
}

export function useShareBackendTrust(defaultBaseUrl: string) {
  const [pendingBackendTrust, setPendingBackendTrust] = useState<PendingBackendTrust | null>(() => {
    const baseUrl = queryRuntimeBaseUrl()
    const origin = normalizedBackendOrigin(baseUrl)
    if (!baseUrl || !origin || isBackendOriginTrusted(baseUrl)) return null
    return { baseUrl, origin }
  })

  const rejectPendingBackendTrust = useCallback(() => {
    removeRuntimeBackendQuery()
    setPendingBackendTrust(null)
  }, [])

  const confirmPendingBackendTrust = useCallback(() => {
    if (!pendingBackendTrust) return
    const savedBaseUrl = normalizeBaseUrl(localStorage.getItem('aidm:baseUrl') ?? '')
    const legacyCredentialOwnerBaseUrl = savedBaseUrl || normalizeBaseUrl(defaultBaseUrl)
    trustBackendOrigin(legacyCredentialOwnerBaseUrl)
    bindLegacyCredentialsToBackend(legacyCredentialOwnerBaseUrl)
    trustBackendOrigin(pendingBackendTrust.baseUrl)
    localStorage.setItem('aidm:baseUrl', pendingBackendTrust.baseUrl)
    removeRuntimeBackendQuery()
    setPendingBackendTrust(null)
  }, [defaultBaseUrl, pendingBackendTrust])

  return {
    confirmPendingBackendTrust,
    pendingBackendTrust,
    rejectPendingBackendTrust,
  }
}

function loadInitialBaseUrl(defaultBaseUrl: string) {
  const normalizedDefaultBaseUrl = normalizeBaseUrl(defaultBaseUrl)
  trustBackendOrigin(normalizedDefaultBaseUrl)
  const savedBaseUrl = normalizeBaseUrl(localStorage.getItem('aidm:baseUrl') ?? '')
  const queryBaseUrl = queryRuntimeBaseUrl()
  const trustedQueryBaseUrl = queryBaseUrl && isBackendOriginTrusted(queryBaseUrl) ? queryBaseUrl : ''
  const initialBaseUrl = trustedQueryBaseUrl || savedBaseUrl || normalizedDefaultBaseUrl
  const legacyCredentialBaseUrl = savedBaseUrl || (!trustedQueryBaseUrl ? normalizedDefaultBaseUrl : '')
  if (savedBaseUrl || !trustedQueryBaseUrl) {
    bindLegacyCredentialsToBackend(legacyCredentialBaseUrl)
  }
  if (isBackendOriginTrusted(initialBaseUrl)) {
    activateBackendCredentialScope(initialBaseUrl)
  }
  setActiveRuntimeBaseUrl(initialBaseUrl)
  return initialBaseUrl
}

function accountFromSession(session: AccountSession): NonNullable<RuntimeAccount> {
  return {
    accountId: session.account.account_id,
    username: session.account.username,
    firstName: session.account.first_name,
    lastName: session.account.last_name,
    displayName: session.account.display_name,
    workspaceId: session.workspace_id,
    workspaceRole: session.workspace_role,
    isWorkspaceAdmin: session.is_workspace_admin,
    requiresPasswordSetup: session.account.requires_password_setup,
    workspaces: session.workspaces ?? session.account.workspaces ?? [],
  }
}

function accountFromPayload(account: Account): NonNullable<RuntimeAccount> {
  return {
    accountId: account.account_id,
    username: account.username,
    firstName: account.first_name,
    lastName: account.last_name,
    displayName: account.display_name,
    workspaceId: account.workspace_id,
    workspaceRole: account.workspace_role,
    isWorkspaceAdmin: account.is_workspace_admin,
    requiresPasswordSetup: account.requires_password_setup,
    workspaces: account.workspaces ?? [],
  }
}

function mergeAccountWorkspaceState(
  account: NonNullable<RuntimeAccount>,
  currentAccount: RuntimeAccount,
  currentWorkspaceId: string,
): NonNullable<RuntimeAccount> {
  if (account.workspaceId) return account

  const fallbackWorkspaceId = [currentAccount?.workspaceId, currentWorkspaceId]
    .map((value) => String(value || '').trim())
    .find((value) => account.workspaces.some((workspace) => workspace.workspace_id === value))
  if (!fallbackWorkspaceId) return account

  const fallbackWorkspace = account.workspaces.find((workspace) => workspace.workspace_id === fallbackWorkspaceId)
  return {
    ...account,
    workspaceId: fallbackWorkspaceId,
    workspaceRole: fallbackWorkspace?.workspace_role ?? currentAccount?.workspaceRole ?? null,
    isWorkspaceAdmin: fallbackWorkspace?.is_workspace_admin ?? currentAccount?.isWorkspaceAdmin ?? false,
  }
}

function responseErrorMessage(payload: unknown, fallback: string) {
  if (payload && typeof payload === 'object') {
    const record = payload as Record<string, unknown>
    if (typeof record.error === 'string') return record.error
    if (typeof record.message === 'string') return record.message
  }
  return fallback
}

function responseError(payload: unknown, fallback: string): RuntimeApiError {
  const error = new Error(responseErrorMessage(payload, fallback)) as RuntimeApiError
  if (payload && typeof payload === 'object') {
    const record = payload as Record<string, unknown>
    if (typeof record.error_code === 'string') {
      error.errorCode = record.error_code
    }
  }
  return error
}

async function submitAccountSession(
  baseUrl: string,
  form: RuntimeSettingsForm,
  accountToken: string,
  options: { intent: RuntimeAuthIntent; legacyClaim?: boolean },
) {
  const headers = new Headers({ 'Content-Type': 'application/json' })
  const trustedBackend = isBackendOriginTrusted(baseUrl)
  const recoveryCode = options.legacyClaim ? form.recoveryCode.trim() : ''
  const accountCredential = recoveryCode || accountToken.trim()
  if (trustedBackend && accountCredential) {
    headers.set('Authorization', `Bearer ${accountCredential}`)
  }
  addCookieCsrfHeader(headers, baseUrl)
  addNgrokBrowserWarningBypassHeader(headers, baseUrl)

  const response = await fetch(`${normalizeBaseUrl(baseUrl)}${'/api/accounts/login'}`, {
    method: 'POST',
    headers,
    ...(!trustedBackend ? { credentials: 'omit' as const } : {}),
    body: JSON.stringify({
      username: form.username.trim(),
      password: form.password,
      intent: options.intent,
      ...(options.intent === 'signup' && !options.legacyClaim
        ? {
            first_name: form.firstName.trim(),
            last_name: form.lastName.trim(),
          }
        : {}),
      ...(options.legacyClaim
        ? {
            legacy_claim: true,
            ...(recoveryCode ? { legacy_recovery: true } : {}),
          }
        : {}),
    }),
  })
  const text = await response.text()
  const payload = text ? JSON.parse(text) as unknown : null
  if (!response.ok) {
    throw responseError(payload, `Account request failed with status ${response.status}`)
  }
  return payload as AccountSession
}

async function fetchAccountSnapshot(baseUrl: string, accountToken: string, workspaceToken: string) {
  const headers = new Headers()
  const trustedBackend = isBackendOriginTrusted(baseUrl)
  if (trustedBackend && accountToken.trim()) {
    headers.set('Authorization', `Bearer ${accountToken.trim()}`)
  }
  if (trustedBackend && workspaceToken.trim()) {
    headers.set('X-AIDM-Workspace-Token', workspaceToken.trim())
  }
  addCookieCsrfHeader(headers, baseUrl)
  addNgrokBrowserWarningBypassHeader(headers, baseUrl)

  const response = await fetch(`${normalizeBaseUrl(baseUrl)}${'/api/accounts/me'}`, {
    headers,
    ...(!trustedBackend ? { credentials: 'omit' as const } : {}),
  })
  const text = await response.text()
  const payload = text ? JSON.parse(text) as unknown : null
  if (!response.ok) {
    throw new Error(responseErrorMessage(payload, `Account refresh failed with status ${response.status}`))
  }
  return accountFromPayload(payload as Account)
}

async function submitWorkspaceSession(
  baseUrl: string,
  accountToken: string,
  payload: { workspace_token?: string; table_name?: string; table_password?: string },
) {
  const headers = new Headers({ 'Content-Type': 'application/json' })
  const trustedBackend = isBackendOriginTrusted(baseUrl)
  if (trustedBackend && accountToken.trim()) {
    headers.set('Authorization', `Bearer ${accountToken.trim()}`)
  }
  if (trustedBackend && payload.workspace_token?.trim()) {
    headers.set('X-AIDM-Workspace-Token', payload.workspace_token.trim())
  }
  addCookieCsrfHeader(headers, baseUrl)
  addNgrokBrowserWarningBypassHeader(headers, baseUrl)

  const response = await fetch(`${normalizeBaseUrl(baseUrl)}${'/api/accounts/workspace'}`, {
    method: 'POST',
    headers,
    ...(!trustedBackend ? { credentials: 'omit' as const } : {}),
    body: JSON.stringify(payload),
  })
  const text = await response.text()
  const responsePayload = text ? JSON.parse(text) as unknown : null
  if (!response.ok) {
    throw responseError(responsePayload, `Workspace request failed with status ${response.status}`)
  }
  return responsePayload as AccountSession
}

async function createWorkspaceSession(
  baseUrl: string,
  accountToken: string,
  payload: { table_name: string; access_mode: RuntimeWorkspaceCreateAccessMode; table_password?: string },
) {
  const headers = new Headers({ 'Content-Type': 'application/json' })
  const trustedBackend = isBackendOriginTrusted(baseUrl)
  if (trustedBackend && accountToken.trim()) {
    headers.set('Authorization', `Bearer ${accountToken.trim()}`)
  }
  addCookieCsrfHeader(headers, baseUrl)
  addNgrokBrowserWarningBypassHeader(headers, baseUrl)

  const response = await fetch(`${normalizeBaseUrl(baseUrl)}${'/api/accounts/workspaces'}`, {
    method: 'POST',
    headers,
    ...(!trustedBackend ? { credentials: 'omit' as const } : {}),
    body: JSON.stringify(payload),
  })
  const text = await response.text()
  const responsePayload = text ? JSON.parse(text) as unknown : null
  if (!response.ok) {
    throw responseError(responsePayload, `Table request failed with status ${response.status}`)
  }
  return responsePayload as AccountSession
}

async function selectWorkspaceSession(baseUrl: string, workspaceId: string, accountToken: string) {
  const headers = new Headers({ 'Content-Type': 'application/json' })
  const trustedBackend = isBackendOriginTrusted(baseUrl)
  if (trustedBackend && accountToken.trim()) {
    headers.set('Authorization', `Bearer ${accountToken.trim()}`)
  }
  addCookieCsrfHeader(headers, baseUrl)
  addNgrokBrowserWarningBypassHeader(headers, baseUrl)

  const response = await fetch(`${normalizeBaseUrl(baseUrl)}${'/api/accounts/workspace/select'}`, {
    method: 'POST',
    headers,
    ...(!trustedBackend ? { credentials: 'omit' as const } : {}),
    body: JSON.stringify({
      workspace_id: workspaceId.trim(),
    }),
  })
  const text = await response.text()
  const payload = text ? JSON.parse(text) as unknown : null
  if (!response.ok) {
    throw responseError(payload, `Workspace request failed with status ${response.status}`)
  }
  return payload as AccountSession
}

async function deleteWorkspaceSession(baseUrl: string, workspaceId: string, accountToken: string) {
  const headers = new Headers()
  const trustedBackend = isBackendOriginTrusted(baseUrl)
  if (trustedBackend && accountToken.trim()) {
    headers.set('Authorization', `Bearer ${accountToken.trim()}`)
  }
  addCookieCsrfHeader(headers, baseUrl)
  addNgrokBrowserWarningBypassHeader(headers, baseUrl)

  const response = await fetch(
    `${normalizeBaseUrl(baseUrl)}${`/api/accounts/workspaces/${encodeURIComponent(workspaceId.trim())}`}`,
    {
      method: 'DELETE',
      headers,
      ...(!trustedBackend ? { credentials: 'omit' as const } : {}),
    },
  )
  const text = await response.text()
  let payload: unknown = null
  if (text) {
    try {
      payload = JSON.parse(text) as unknown
    } catch {
      if (response.ok) {
        throw new Error('Table delete response was not valid JSON.')
      }
    }
  }
  if (!response.ok) {
    throw responseError(payload, `Table delete request failed with status ${response.status}`)
  }
  return payload as AccountSession
}

async function deleteAccountSessionCookie(baseUrl: string) {
  const headers = new Headers()
  addNgrokBrowserWarningBypassHeader(headers, baseUrl)
  addCookieCsrfHeader(headers, baseUrl)
  await fetch(`${normalizeBaseUrl(baseUrl)}${'/api/accounts/session'}`, {
    method: 'DELETE',
    headers,
    ...(!isBackendOriginTrusted(baseUrl) ? { credentials: 'omit' as const } : {}),
  })
}

type UseRuntimeSettingsOptions = {
  defaultBaseUrl: string
  resetRuntimeState: () => void
  reconnectSocket: () => void
}

export function useRuntimeSettings({
  defaultBaseUrl,
  resetRuntimeState,
  reconnectSocket,
}: UseRuntimeSettingsOptions) {
  const [initialRuntime] = useState(() => {
    const initialBaseUrl = loadInitialBaseUrl(defaultBaseUrl)
    return {
      account: loadSessionAccount(initialBaseUrl),
      accountTokenTransport: loadAccountTokenTransport(initialBaseUrl),
      authToken: loadSessionAuthToken(initialBaseUrl),
      baseUrl: initialBaseUrl,
      workspaceId: loadStoredWorkspaceId(initialBaseUrl),
      workspaceToken: loadSessionWorkspaceToken(initialBaseUrl),
    }
  })
  const [baseUrl, setBaseUrl] = useState(initialRuntime.baseUrl)
  const [authToken, setAuthToken] = useState(initialRuntime.authToken)
  const [accountTokenTransport, setAccountTokenTransport] = useState(initialRuntime.accountTokenTransport)
  const [pendingAuthToken, setPendingAuthToken] = useState('')
  const [workspaceToken, setWorkspaceToken] = useState(initialRuntime.workspaceToken)
  const [workspaceId, setWorkspaceId] = useState(initialRuntime.workspaceId)
  const [runtimeAccount, setRuntimeAccount] = useState<RuntimeAccount>(initialRuntime.account)
  const [runtimeSettingsOpen, setRuntimeSettingsOpen] = useState(false)
  const [runtimeSettingsMode, setRuntimeSettingsMode] = useState<RuntimeSettingsMode>('settings')
  const [runtimeAuthIntent, setRuntimeAuthIntent] = useState<RuntimeAuthIntent>('login')
  const [runtimeAuthStep, setRuntimeAuthStep] = useState<RuntimeAuthStep>(() =>
    initialRuntime.authToken || hasCookieAccountSession(initialRuntime.accountTokenTransport)
      ? 'workspace'
      : 'account',
  )
  const [runtimeWorkspaceAction, setRuntimeWorkspaceAction] = useState<RuntimeWorkspaceAction>('join')
  const [runtimeWorkspaceJoinMethod, setRuntimeWorkspaceJoinMethod] = useState<RuntimeWorkspaceJoinMethod>('token')
  const [runtimeWorkspaceCreateAccessMode, setRuntimeWorkspaceCreateAccessMode] =
    useState<RuntimeWorkspaceCreateAccessMode>('password')
  const [runtimeCreatedWorkspaceToken, setRuntimeCreatedWorkspaceToken] = useState('')
  const [runtimeSettingsError, setRuntimeSettingsError] = useState('')
  const [legacyPasswordSetupRequired, setLegacyPasswordSetupRequired] = useState(false)
  const accountRefreshTokenRef = useRef('')
  const [runtimeSettingsForm, setRuntimeSettingsForm] = useState<RuntimeSettingsForm>(() => ({
    baseUrl: initialRuntime.baseUrl,
    workspaceToken: initialRuntime.workspaceToken,
    workspaceName: '',
    workspacePassword: '',
    username: initialRuntime.account?.username ?? '',
    firstName: '',
    lastName: '',
    password: '',
    recoveryCode: '',
  }))

  useEffect(() => {
    setActiveRuntimeBaseUrl(baseUrl)
  }, [baseUrl])

  const promptForLegacyPasswordSetup = useCallback((account?: NonNullable<RuntimeAccount>) => {
    setRuntimeSettingsForm((current) => ({
      ...current,
      username: account?.username || current.username,
      firstName: account?.firstName || current.firstName,
      lastName: account?.lastName || current.lastName,
      password: '',
      recoveryCode: '',
    }))
    setRuntimeAuthIntent('signup')
    setRuntimeAuthStep('account')
    setRuntimeCreatedWorkspaceToken('')
    setRuntimeSettingsMode('auth')
    setRuntimeSettingsOpen(true)
    setLegacyPasswordSetupRequired(true)
    setRuntimeSettingsError(LEGACY_PASSWORD_SETUP_MESSAGE)
  }, [])

  const refreshRuntimeAccount = useCallback(
    async (options: { reportError?: boolean } = {}) => {
      const nextBaseUrl = normalizeBaseUrl(runtimeSettingsForm.baseUrl || baseUrl)
      const sameBackend = backendOriginsMatch(nextBaseUrl, baseUrl)
      const accountStepToken = (
        sameBackend ? pendingAuthToken.trim() || authToken.trim() : loadSessionAuthToken(nextBaseUrl).trim()
      ) || loadSessionAuthToken(nextBaseUrl).trim()
      const nextAccountTokenTransport = sameBackend
        ? accountTokenTransport || loadAccountTokenTransport(nextBaseUrl)
        : loadAccountTokenTransport(nextBaseUrl)
      const cookieAuthAvailable = hasCookieAccountSession(nextAccountTokenTransport)
      if (!accountStepToken && !cookieAuthAvailable) return null

      try {
        const nextWorkspaceToken = sameBackend ? workspaceToken : loadSessionWorkspaceToken(nextBaseUrl)
        const accountSnapshot = await fetchAccountSnapshot(nextBaseUrl, accountStepToken, nextWorkspaceToken)
        const account = mergeAccountWorkspaceState(accountSnapshot, runtimeAccount, workspaceId)
        storeSessionAuthToken(accountStepToken, nextBaseUrl)
        if (cookieAuthAvailable) {
          storeAccountTokenTransport(HTTP_ONLY_COOKIE_TRANSPORT, nextBaseUrl)
          setAccountTokenTransport(HTTP_ONLY_COOKIE_TRANSPORT)
        }
        storeSessionAccount(account, nextBaseUrl)
        setRuntimeAccount(account)
        setRuntimeSettingsForm((current) => ({
          ...current,
          username: account.username || current.username,
        }))
        if (account.requiresPasswordSetup) {
          storeSessionWorkspaceToken('', nextBaseUrl)
          storeWorkspaceId('', nextBaseUrl)
          setWorkspaceToken('')
          setWorkspaceId('')
          promptForLegacyPasswordSetup(account)
          return account
        }
        if (account.workspaceId) {
          storeWorkspaceId(account.workspaceId, nextBaseUrl)
          setWorkspaceId(account.workspaceId)
        }
        return account
      } catch (error) {
        if (options.reportError) {
          setRuntimeSettingsError(error instanceof Error ? error.message : String(error))
        }
        return null
      }
    },
    [
      accountTokenTransport,
      authToken,
      baseUrl,
      pendingAuthToken,
      promptForLegacyPasswordSetup,
      runtimeAccount,
      runtimeSettingsForm.baseUrl,
      workspaceId,
      workspaceToken,
    ],
  )

  useEffect(() => {
    const accountStepToken = authToken.trim()
    if (!accountStepToken && !hasCookieAccountSession(accountTokenTransport)) {
      accountRefreshTokenRef.current = ''
      return
    }
    const refreshKey = accountStepToken || accountTokenTransport
    if (accountRefreshTokenRef.current === refreshKey) return

    accountRefreshTokenRef.current = refreshKey
    void refreshRuntimeAccount()
  }, [accountTokenTransport, authToken, refreshRuntimeAccount])

  const openRuntimeSettings = useCallback((mode: RuntimeSettingsMode = 'settings') => {
    const needsPasswordSetup = runtimeAccount?.requiresPasswordSetup === true
    const preserveOpenAuthFlow = mode === 'auth' && runtimeSettingsOpen && runtimeSettingsMode === 'auth'
    if (preserveOpenAuthFlow && !needsPasswordSetup) {
      setRuntimeSettingsMode('auth')
      setRuntimeSettingsOpen(true)
      return
    }
    setRuntimeSettingsForm((current) => ({
      baseUrl,
      workspaceToken,
      workspaceName: current.workspaceName,
      workspacePassword: '',
      username: runtimeAccount?.username ?? current.username,
      firstName: current.firstName,
      lastName: current.lastName,
      password: '',
      recoveryCode: '',
    }))
    setRuntimeWorkspaceAction('join')
    setRuntimeWorkspaceJoinMethod('token')
    setRuntimeCreatedWorkspaceToken('')
    const accountSessionAvailable =
      authToken.trim() || pendingAuthToken.trim() || hasCookieAccountSession(accountTokenTransport)
    setRuntimeAuthStep(needsPasswordSetup || !(mode === 'auth' && accountSessionAvailable) ? 'account' : 'workspace')
    if (needsPasswordSetup) {
      setRuntimeAuthIntent('signup')
    }
    setRuntimeSettingsMode(mode)
    setRuntimeSettingsError(needsPasswordSetup ? LEGACY_PASSWORD_SETUP_MESSAGE : '')
    setLegacyPasswordSetupRequired(needsPasswordSetup)
    setRuntimeSettingsOpen(true)
    if (mode === 'auth' && accountSessionAvailable) {
      void refreshRuntimeAccount({ reportError: true })
    }
  }, [
    accountTokenTransport,
    authToken,
    baseUrl,
    pendingAuthToken,
    refreshRuntimeAccount,
    runtimeAccount?.requiresPasswordSetup,
    runtimeAccount?.username,
    runtimeSettingsMode,
    runtimeSettingsOpen,
    workspaceToken,
  ])

  const openAuthTokenPrompt = useCallback(() => {
    openRuntimeSettings('auth')
  }, [openRuntimeSettings])

  const closeRuntimeSettings = useCallback(() => {
    setRuntimeSettingsOpen(false)
    setRuntimeSettingsMode('settings')
    setRuntimeSettingsError('')
    setRuntimeCreatedWorkspaceToken('')
    setLegacyPasswordSetupRequired(false)
    setRuntimeSettingsForm((current) => ({ ...current, recoveryCode: '' }))
  }, [])

  const activateBackendCredentials = useCallback((nextBaseUrl: string) => {
    activateBackendCredentialScope(nextBaseUrl)
    const nextAuthToken = loadSessionAuthToken(nextBaseUrl)
    const nextAccountTokenTransport = loadAccountTokenTransport(nextBaseUrl)
    const nextWorkspaceToken = loadSessionWorkspaceToken(nextBaseUrl)
    const nextWorkspaceId = loadStoredWorkspaceId(nextBaseUrl)
    const nextAccount = loadSessionAccount(nextBaseUrl)
    accountRefreshTokenRef.current = ''
    setBaseUrl(nextBaseUrl)
    setAuthToken(nextAuthToken)
    setAccountTokenTransport(nextAccountTokenTransport)
    setPendingAuthToken('')
    setWorkspaceToken(nextWorkspaceToken)
    setWorkspaceId(nextWorkspaceId)
    setRuntimeAccount(nextAccount)
    setRuntimeAuthStep(
      nextAuthToken || hasCookieAccountSession(nextAccountTokenTransport) ? 'workspace' : 'account',
    )
    setRuntimeSettingsForm((current) => ({
      ...current,
      baseUrl: nextBaseUrl,
      workspaceToken: nextWorkspaceToken,
      username: nextAccount?.username ?? '',
    }))
  }, [])

  const submitRuntimeSettings = useCallback(
    async (event: FormEvent<HTMLFormElement>) => {
      event.preventDefault()
      const nextBaseUrl = normalizeBaseUrl(runtimeSettingsForm.baseUrl)
      const sameBackend = backendOriginsMatch(nextBaseUrl, baseUrl)
      const nextAuthToken = (
        sameBackend ? authToken : loadSessionAuthToken(nextBaseUrl)
      ).trim()
      const nextWorkspaceToken = runtimeSettingsForm.workspaceToken.trim()

      if (nextBaseUrl && !isHttpBaseUrl(nextBaseUrl)) {
        setRuntimeSettingsError('Backend URL must start with http:// or https://.')
        return
      }

      if (runtimeSettingsMode === 'auth' && runtimeAuthStep === 'account') {
        if (!runtimeSettingsForm.username.trim()) {
          setRuntimeSettingsError('Username is required.')
          return
        }
        const legacyPasswordSetupAttempt = legacyPasswordSetupRequired
        if (legacyPasswordSetupAttempt && !runtimeSettingsForm.password.trim()) {
          setRuntimeSettingsError(LEGACY_PASSWORD_SETUP_MESSAGE)
          return
        }
        if (
          runtimeAuthIntent === 'signup'
          && !legacyPasswordSetupAttempt
          && (!runtimeSettingsForm.firstName.trim() || !runtimeSettingsForm.lastName.trim())
        ) {
          setRuntimeSettingsError('First and last name are required.')
          return
        }
        if (runtimeAuthIntent === 'signup' && !runtimeSettingsForm.password.trim()) {
          setRuntimeSettingsError('Password is required.')
          return
        }
        try {
          const accountSession = await submitAccountSession(
            nextBaseUrl,
            runtimeSettingsForm,
            nextAuthToken,
            { intent: runtimeAuthIntent, legacyClaim: legacyPasswordSetupAttempt },
          )
          const accountToken = accountSession.account_token.trim()
          const tokenTransport = accountSessionTokenTransport(accountSession)
          const account = accountFromSession(accountSession)
          trustBackendOrigin(nextBaseUrl)
          if (nextBaseUrl) {
            localStorage.setItem('aidm:baseUrl', nextBaseUrl)
          } else {
            localStorage.removeItem('aidm:baseUrl')
          }
          storeSessionAuthToken(accountToken, nextBaseUrl)
          storeAccountTokenTransport(tokenTransport, nextBaseUrl)
          storeSessionWorkspaceToken('', nextBaseUrl)
          storeWorkspaceId(account.workspaceId, nextBaseUrl)
          storeSessionAccount(account, nextBaseUrl)
          setBaseUrl(nextBaseUrl)
          setAccountTokenTransport(tokenTransport)
          setPendingAuthToken(accountToken)
          setWorkspaceToken('')
          setWorkspaceId(account.workspaceId ?? '')
          setRuntimeAccount(account)
          setRuntimeSettingsForm((current) => ({
            ...current,
            workspaceToken: '',
            workspacePassword: '',
            recoveryCode: '',
          }))
          setRuntimeAuthStep('workspace')
          setLegacyPasswordSetupRequired(false)
          setRuntimeSettingsError('')
          return
        } catch (error) {
          const runtimeError = error as RuntimeApiError
          if (runtimeAuthIntent === 'login' && runtimeError.errorCode === LEGACY_PASSWORD_SETUP_ERROR_CODE) {
            setRuntimeAuthIntent('signup')
            setRuntimeSettingsForm((current) => ({ ...current, password: '', recoveryCode: '' }))
            setLegacyPasswordSetupRequired(true)
            setRuntimeSettingsError(LEGACY_PASSWORD_SETUP_MESSAGE)
            return
          }
          setRuntimeSettingsError(error instanceof Error ? error.message : String(error))
          return
        }
      }

      if (runtimeSettingsMode === 'auth' && runtimeAuthStep === 'workspace') {
        if (runtimeCreatedWorkspaceToken) {
          setRuntimeSettingsOpen(false)
          setRuntimeSettingsMode('settings')
          setRuntimeCreatedWorkspaceToken('')
          setRuntimeAuthStep('account')
          setRuntimeSettingsError('')
          return
        }
        const accountStepToken = pendingAuthToken.trim() || nextAuthToken
        const nextAccountTokenTransport = sameBackend
          ? accountTokenTransport
          : loadAccountTokenTransport(nextBaseUrl)
        const cookieAuthAvailable = hasCookieAccountSession(nextAccountTokenTransport)
        if (!accountStepToken && !cookieAuthAvailable) {
          setRuntimeSettingsError('Log in or sign up before joining a table.')
          setRuntimeAuthStep('account')
          return
        }
        try {
          let accountSession: AccountSession
          let storedWorkspaceToken = ''
          let createdWorkspaceToken = ''
          if (runtimeWorkspaceAction === 'create') {
            const tableName = runtimeSettingsForm.workspaceName.trim()
            if (!tableName) {
              setRuntimeSettingsError('Table name is required.')
              return
            }
            const tablePassword = runtimeSettingsForm.workspacePassword
            if (runtimeWorkspaceCreateAccessMode === 'password' && !tablePassword.trim()) {
              setRuntimeSettingsError('Table password is required.')
              return
            }
            accountSession = await createWorkspaceSession(nextBaseUrl, accountStepToken, {
              table_name: tableName,
              access_mode: runtimeWorkspaceCreateAccessMode,
              ...(runtimeWorkspaceCreateAccessMode === 'password' ? { table_password: tablePassword } : {}),
            })
            createdWorkspaceToken = accountSession.workspace_token?.trim() ?? ''
          } else if (runtimeWorkspaceJoinMethod === 'password') {
            const tableName = runtimeSettingsForm.workspaceName.trim()
            const tablePassword = runtimeSettingsForm.workspacePassword
            if (!tableName) {
              setRuntimeSettingsError('Table name is required.')
              return
            }
            if (!tablePassword.trim()) {
              setRuntimeSettingsError('Table password is required.')
              return
            }
            accountSession = await submitWorkspaceSession(nextBaseUrl, accountStepToken, {
              table_name: tableName,
              table_password: tablePassword,
            })
          } else {
            if (!nextWorkspaceToken) {
              setRuntimeSettingsError('Table token is required.')
              return
            }
            storedWorkspaceToken = nextWorkspaceToken
            accountSession = await submitWorkspaceSession(nextBaseUrl, accountStepToken, {
              workspace_token: nextWorkspaceToken,
            })
          }
          const accountToken = accountSession.account_token.trim()
          const tokenTransport = accountSessionTokenTransport(accountSession) || nextAccountTokenTransport
          const account = accountFromSession(accountSession)
          trustBackendOrigin(nextBaseUrl)
          if (nextBaseUrl) {
            localStorage.setItem('aidm:baseUrl', nextBaseUrl)
          } else {
            localStorage.removeItem('aidm:baseUrl')
          }
          storeSessionAuthToken(accountToken, nextBaseUrl)
          storeAccountTokenTransport(tokenTransport, nextBaseUrl)
          storeSessionWorkspaceToken(storedWorkspaceToken, nextBaseUrl)
          storeWorkspaceId(account.workspaceId, nextBaseUrl)
          storeSessionAccount(account, nextBaseUrl)
          setBaseUrl(nextBaseUrl)
          setAuthToken(accountToken)
          setAccountTokenTransport(tokenTransport)
          setPendingAuthToken('')
          setWorkspaceToken(storedWorkspaceToken)
          setWorkspaceId(account.workspaceId ?? '')
          setRuntimeAccount(account)
          setRuntimeSettingsForm((current) => ({
            ...current,
            workspacePassword: '',
            workspaceToken: storedWorkspaceToken,
          }))
          resetRuntimeState()
          reconnectSocket()
          setRuntimeCreatedWorkspaceToken(createdWorkspaceToken)
          if (createdWorkspaceToken) {
            setRuntimeSettingsOpen(true)
            setRuntimeSettingsMode('auth')
            setRuntimeAuthStep('workspace')
            setLegacyPasswordSetupRequired(false)
            setRuntimeSettingsError('')
            return
          }
          setRuntimeSettingsOpen(false)
          setRuntimeSettingsMode('settings')
          setRuntimeAuthStep('account')
          setLegacyPasswordSetupRequired(false)
          setRuntimeSettingsError('')
          return
        } catch (error) {
          const runtimeError = error as RuntimeApiError
          if (runtimeError.errorCode === LEGACY_PASSWORD_SETUP_ERROR_CODE) {
            promptForLegacyPasswordSetup(runtimeAccount ?? undefined)
            return
          }
          setRuntimeSettingsError(error instanceof Error ? error.message : String(error))
          return
        }
      }

      if (!nextBaseUrl) {
        localStorage.removeItem('aidm:baseUrl')

        activateBackendCredentials('')
        resetRuntimeState()
        reconnectSocket()
        setRuntimeSettingsOpen(false)
        setRuntimeSettingsMode('settings')
        setRuntimeSettingsError('')
        setLegacyPasswordSetupRequired(false)
        return
      }

      if (!isHttpBaseUrl(nextBaseUrl)) {
        setRuntimeSettingsError('Backend URL must start with http:// or https://.')
        return
      }

      trustBackendOrigin(nextBaseUrl)
      localStorage.setItem('aidm:baseUrl', nextBaseUrl)

      activateBackendCredentials(nextBaseUrl)
      resetRuntimeState()
      reconnectSocket()
      setRuntimeSettingsOpen(false)
      setRuntimeSettingsMode('settings')
      setRuntimeSettingsError('')
      setLegacyPasswordSetupRequired(false)
    },
    [
      accountTokenTransport,
      activateBackendCredentials,
      authToken,
      baseUrl,
      pendingAuthToken,
      reconnectSocket,
      resetRuntimeState,
      runtimeSettingsForm,
      runtimeSettingsMode,
      runtimeAuthIntent,
      runtimeAuthStep,
      runtimeCreatedWorkspaceToken,
      runtimeWorkspaceAction,
      runtimeWorkspaceCreateAccessMode,
      runtimeWorkspaceJoinMethod,
      legacyPasswordSetupRequired,
      promptForLegacyPasswordSetup,
      runtimeAccount,
    ],
  )

  const clearAuthToken = useCallback(() => {
    if (hasCookieAccountSession(accountTokenTransport)) {
      void deleteAccountSessionCookie(baseUrl).catch(() => undefined)
    }
    storeSessionAuthToken('', baseUrl)
    storeAccountTokenTransport('', baseUrl)
    storeSessionWorkspaceToken('', baseUrl)
    storeWorkspaceId('', baseUrl)
    storeSessionAccount(null, baseUrl)
    accountRefreshTokenRef.current = ''
    setAuthToken('')
    setAccountTokenTransport('')
    setPendingAuthToken('')
    setWorkspaceToken('')
    setWorkspaceId('')
    setRuntimeAccount(null)
    setRuntimeAuthIntent('login')
    setRuntimeAuthStep('account')
    setRuntimeWorkspaceAction('join')
    setRuntimeWorkspaceJoinMethod('token')
    setRuntimeWorkspaceCreateAccessMode('password')
    setRuntimeCreatedWorkspaceToken('')
    setLegacyPasswordSetupRequired(false)
    setRuntimeSettingsForm((current) => ({
      ...current,
      workspaceToken: '',
      workspacePassword: '',
      password: '',
      recoveryCode: '',
    }))
    reconnectSocket()
  }, [accountTokenTransport, baseUrl, reconnectSocket])

  const selectSavedWorkspace = useCallback(
    async (nextWorkspaceId: string) => {
      const cleanWorkspaceId = nextWorkspaceId.trim()
      const nextBaseUrl = normalizeBaseUrl(runtimeSettingsForm.baseUrl)
      const sameBackend = backendOriginsMatch(nextBaseUrl, baseUrl)
      const accountStepToken = sameBackend
        ? pendingAuthToken.trim() || authToken.trim()
        : loadSessionAuthToken(nextBaseUrl).trim()
      const nextAccountTokenTransport = sameBackend
        ? accountTokenTransport
        : loadAccountTokenTransport(nextBaseUrl)
      const cookieAuthAvailable = hasCookieAccountSession(nextAccountTokenTransport)
      if (!accountStepToken && !cookieAuthAvailable) {
        setRuntimeSettingsError('Log in or sign up before choosing a workspace.')
        setRuntimeAuthStep('account')
        return
      }
      if (!cleanWorkspaceId) {
        setRuntimeSettingsError('Choose a saved workspace.')
        return
      }
      try {
        if (nextBaseUrl && !isHttpBaseUrl(nextBaseUrl)) {
          setRuntimeSettingsError('Backend URL must start with http:// or https://.')
          return
        }
        const accountSession = await selectWorkspaceSession(nextBaseUrl, cleanWorkspaceId, accountStepToken)
        const accountToken = accountSession.account_token.trim()
        const tokenTransport = accountSessionTokenTransport(accountSession) || nextAccountTokenTransport
        const account = accountFromSession(accountSession)
        trustBackendOrigin(nextBaseUrl)
        if (nextBaseUrl) {
          localStorage.setItem('aidm:baseUrl', nextBaseUrl)
        } else {
          localStorage.removeItem('aidm:baseUrl')
        }
        storeSessionAuthToken(accountToken, nextBaseUrl)
        storeAccountTokenTransport(tokenTransport, nextBaseUrl)
        storeSessionWorkspaceToken('', nextBaseUrl)
        storeWorkspaceId(account.workspaceId, nextBaseUrl)
        storeSessionAccount(account, nextBaseUrl)
        setBaseUrl(nextBaseUrl)
        setAuthToken(accountToken)
        setAccountTokenTransport(tokenTransport)
        setPendingAuthToken('')
        setWorkspaceToken('')
        setWorkspaceId(account.workspaceId ?? '')
        setRuntimeAccount(account)
        resetRuntimeState()
        reconnectSocket()
        setRuntimeSettingsOpen(false)
        setRuntimeSettingsMode('settings')
        setRuntimeAuthStep('account')
        setLegacyPasswordSetupRequired(false)
        setRuntimeSettingsError('')
      } catch (error) {
        const runtimeError = error as RuntimeApiError
        if (runtimeError.errorCode === LEGACY_PASSWORD_SETUP_ERROR_CODE) {
          promptForLegacyPasswordSetup(runtimeAccount ?? undefined)
          return
        }
        setRuntimeSettingsError(error instanceof Error ? error.message : String(error))
      }
    },
    [
      accountTokenTransport,
      authToken,
      baseUrl,
      pendingAuthToken,
      promptForLegacyPasswordSetup,
      reconnectSocket,
      resetRuntimeState,
      runtimeAccount,
      runtimeSettingsForm.baseUrl,
    ],
  )

  const deleteSavedWorkspace = useCallback(
    async (nextWorkspaceId: string): Promise<DeleteSavedWorkspaceResult> => {
      const cleanWorkspaceId = nextWorkspaceId.trim()
      const nextBaseUrl = normalizeBaseUrl(runtimeSettingsForm.baseUrl)
      const sameBackend = backendOriginsMatch(nextBaseUrl, baseUrl)
      const accountStepToken = sameBackend
        ? pendingAuthToken.trim() || authToken.trim()
        : loadSessionAuthToken(nextBaseUrl).trim()
      const nextAccountTokenTransport = sameBackend
        ? accountTokenTransport
        : loadAccountTokenTransport(nextBaseUrl)
      const cookieAuthAvailable = hasCookieAccountSession(nextAccountTokenTransport)
      if (!accountStepToken && !cookieAuthAvailable) {
        const message = 'Log in or sign up before deleting a saved table.'
        setRuntimeSettingsError(message)
        setRuntimeAuthStep('account')
        return { ok: false, error: message }
      }
      if (!cleanWorkspaceId) {
        const message = 'Choose a saved table to delete.'
        setRuntimeSettingsError(message)
        return { ok: false, error: message }
      }
      try {
        if (nextBaseUrl && !isHttpBaseUrl(nextBaseUrl)) {
          const message = 'Backend URL must start with http:// or https://.'
          setRuntimeSettingsError(message)
          return { ok: false, error: message }
        }
        const accountSession = await deleteWorkspaceSession(nextBaseUrl, cleanWorkspaceId, accountStepToken)
        const accountToken = accountSession.account_token.trim()
        const tokenTransport = accountSessionTokenTransport(accountSession) || nextAccountTokenTransport
        const removedCurrentWorkspace =
          cleanWorkspaceId === workspaceId.trim() || cleanWorkspaceId === runtimeAccount?.workspaceId
        const account = removedCurrentWorkspace
          ? accountFromSession(accountSession)
          : mergeAccountWorkspaceState(accountFromSession(accountSession), runtimeAccount, workspaceId)
        const nextWorkspaceToken = removedCurrentWorkspace ? '' : workspaceToken
        trustBackendOrigin(nextBaseUrl)
        if (nextBaseUrl) {
          localStorage.setItem('aidm:baseUrl', nextBaseUrl)
        } else {
          localStorage.removeItem('aidm:baseUrl')
        }
        storeSessionAuthToken(accountToken, nextBaseUrl)
        storeAccountTokenTransport(tokenTransport, nextBaseUrl)
        storeSessionWorkspaceToken(nextWorkspaceToken, nextBaseUrl)
        storeWorkspaceId(account.workspaceId, nextBaseUrl)
        storeSessionAccount(account, nextBaseUrl)
        setBaseUrl(nextBaseUrl)
        setAuthToken(accountToken)
        setAccountTokenTransport(tokenTransport)
        setPendingAuthToken('')
        setWorkspaceToken(nextWorkspaceToken)
        setWorkspaceId(account.workspaceId ?? '')
        setRuntimeAccount(account)
        setRuntimeSettingsError('')
        if (removedCurrentWorkspace) {
          resetRuntimeState()
          reconnectSocket()
        }
        return { ok: true }
      } catch (error) {
        const runtimeError = error as RuntimeApiError
        if (runtimeError.errorCode === LEGACY_PASSWORD_SETUP_ERROR_CODE) {
          promptForLegacyPasswordSetup(runtimeAccount ?? undefined)
          return { ok: false, error: LEGACY_PASSWORD_SETUP_MESSAGE }
        }
        const message = error instanceof Error ? error.message : String(error)
        setRuntimeSettingsError(message)
        return { ok: false, error: message }
      }
    },
    [
      accountTokenTransport,
      authToken,
      baseUrl,
      pendingAuthToken,
      promptForLegacyPasswordSetup,
      reconnectSocket,
      resetRuntimeState,
      runtimeAccount,
      runtimeSettingsForm.baseUrl,
      workspaceId,
      workspaceToken,
    ],
  )

  return {
    authToken,
    baseUrl,
    clearAuthToken,
    closeRuntimeSettings,
    openAuthTokenPrompt,
    openRuntimeSettings,
    runtimeAuthIntent,
    runtimeAuthStep,
    runtimeAccount,
    runtimeCreatedWorkspaceToken,
    runtimeWorkspaceAction,
    runtimeWorkspaceCreateAccessMode,
    runtimeWorkspaceJoinMethod,
    legacyPasswordSetupRequired,
    runtimeSettingsError,
    runtimeSettingsForm,
    runtimeSettingsMode,
    runtimeSettingsOpen,
    setRuntimeAuthIntent,
    setRuntimeAuthStep,
    setRuntimeCreatedWorkspaceToken,
    setRuntimeWorkspaceAction,
    setRuntimeWorkspaceCreateAccessMode,
    setRuntimeWorkspaceJoinMethod,
    setLegacyPasswordSetupRequired,
    setRuntimeSettingsError,
    setRuntimeSettingsForm,
    submitRuntimeSettings,
    deleteSavedWorkspace,
    selectSavedWorkspace,
    workspaceToken,
    workspaceId,
  }
}
