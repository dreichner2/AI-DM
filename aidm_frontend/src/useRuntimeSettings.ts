import { useCallback, useEffect, useRef, useState, type FormEvent } from 'react'
import { addNgrokBrowserWarningBypassHeader, normalizeBaseUrl } from './api'
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
const LEGACY_PASSWORD_SETUP_ERROR_CODE = 'legacy_password_setup_required'
export const LEGACY_PASSWORD_SETUP_MESSAGE = 'Passwords are required now. Please set one now.'

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

function loadSessionAuthToken() {
  const sessionToken = sessionStorage.getItem('aidm:authToken')
  if (sessionToken !== null) return sessionToken
  const cookieToken = decodeURIComponent(readCookie(ACCOUNT_TOKEN_COOKIE))
  if (cookieToken) {
    sessionStorage.setItem('aidm:authToken', cookieToken)
    return cookieToken
  }
  const legacyToken = localStorage.getItem('aidm:authToken') ?? ''
  if (legacyToken) {
    sessionStorage.setItem('aidm:authToken', legacyToken)
    localStorage.removeItem('aidm:authToken')
    writeCookie(ACCOUNT_TOKEN_COOKIE, legacyToken, ACCOUNT_TOKEN_COOKIE_MAX_AGE)
  }
  return legacyToken
}

function storeSessionAuthToken(value: string) {
  const token = value.trim()
  localStorage.removeItem('aidm:authToken')
  if (token) {
    sessionStorage.setItem('aidm:authToken', token)
    writeCookie(ACCOUNT_TOKEN_COOKIE, token, ACCOUNT_TOKEN_COOKIE_MAX_AGE)
  } else {
    sessionStorage.removeItem('aidm:authToken')
    clearCookie(ACCOUNT_TOKEN_COOKIE)
  }
}

function loadSessionWorkspaceToken() {
  const sessionToken = sessionStorage.getItem('aidm:workspaceToken')
  if (sessionToken !== null) return sessionToken
  const legacyToken = localStorage.getItem('aidm:workspaceToken') ?? ''
  if (legacyToken) {
    sessionStorage.setItem('aidm:workspaceToken', legacyToken)
    localStorage.removeItem('aidm:workspaceToken')
  }
  return legacyToken
}

function storeSessionWorkspaceToken(value: string) {
  const token = value.trim()
  localStorage.removeItem('aidm:workspaceToken')
  if (token) {
    sessionStorage.setItem('aidm:workspaceToken', token)
  } else {
    sessionStorage.removeItem('aidm:workspaceToken')
  }
}

function loadStoredWorkspaceId() {
  return localStorage.getItem('aidm:workspaceId') ?? sessionStorage.getItem('aidm:workspaceId') ?? ''
}

function storeWorkspaceId(value: string | null | undefined) {
  const workspaceId = String(value || '').trim()
  if (workspaceId) {
    localStorage.setItem('aidm:workspaceId', workspaceId)
    sessionStorage.setItem('aidm:workspaceId', workspaceId)
  } else {
    localStorage.removeItem('aidm:workspaceId')
    sessionStorage.removeItem('aidm:workspaceId')
  }
}

function loadSessionAccount(): RuntimeAccount {
  const raw = sessionStorage.getItem('aidm:account') ?? localStorage.getItem('aidm:account')
  if (!raw) return null
  try {
    const parsed = JSON.parse(raw) as Partial<NonNullable<RuntimeAccount>>
    if (!parsed || typeof parsed.username !== 'string') return null
    const workspaces = Array.isArray(parsed.workspaces) ? parsed.workspaces : []
    return {
      accountId: typeof parsed.accountId === 'number' ? parsed.accountId : 0,
      username: parsed.username,
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

function storeSessionAccount(value: RuntimeAccount) {
  if (!value) {
    sessionStorage.removeItem('aidm:account')
    localStorage.removeItem('aidm:account')
    return
  }
  const serialized = JSON.stringify(value)
  sessionStorage.setItem('aidm:account', serialized)
  localStorage.setItem('aidm:account', serialized)
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

function loadInitialBaseUrl(defaultBaseUrl: string) {
  const queryBaseUrl = queryRuntimeBaseUrl()
  if (queryBaseUrl) {
    localStorage.setItem('aidm:baseUrl', queryBaseUrl)
    return queryBaseUrl
  }
  return normalizeBaseUrl(localStorage.getItem('aidm:baseUrl') ?? defaultBaseUrl)
}

function accountFromSession(session: AccountSession): NonNullable<RuntimeAccount> {
  return {
    accountId: session.account.account_id,
    username: session.account.username,
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
  if (accountToken.trim()) {
    headers.set('Authorization', `Bearer ${accountToken.trim()}`)
  }
  addNgrokBrowserWarningBypassHeader(headers, baseUrl)

  const response = await fetch(`${normalizeBaseUrl(baseUrl)}${'/api/accounts/login'}`, {
    method: 'POST',
    headers,
    body: JSON.stringify({
      username: form.username.trim(),
      first_name: form.firstName.trim(),
      last_name: form.lastName.trim(),
      password: form.password,
      intent: options.intent,
      ...(options.legacyClaim ? { legacy_claim: true } : {}),
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
  if (accountToken.trim()) {
    headers.set('Authorization', `Bearer ${accountToken.trim()}`)
  }
  if (workspaceToken.trim()) {
    headers.set('X-AIDM-Workspace-Token', workspaceToken.trim())
  }
  addNgrokBrowserWarningBypassHeader(headers, baseUrl)

  const response = await fetch(`${normalizeBaseUrl(baseUrl)}${'/api/accounts/me'}`, { headers })
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
  if (accountToken.trim()) {
    headers.set('Authorization', `Bearer ${accountToken.trim()}`)
  }
  if (payload.workspace_token?.trim()) {
    headers.set('X-AIDM-Workspace-Token', payload.workspace_token.trim())
  }
  addNgrokBrowserWarningBypassHeader(headers, baseUrl)

  const response = await fetch(`${normalizeBaseUrl(baseUrl)}${'/api/accounts/workspace'}`, {
    method: 'POST',
    headers,
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
  if (accountToken.trim()) {
    headers.set('Authorization', `Bearer ${accountToken.trim()}`)
  }
  addNgrokBrowserWarningBypassHeader(headers, baseUrl)

  const response = await fetch(`${normalizeBaseUrl(baseUrl)}${'/api/accounts/workspaces'}`, {
    method: 'POST',
    headers,
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
  if (accountToken.trim()) {
    headers.set('Authorization', `Bearer ${accountToken.trim()}`)
  }
  addNgrokBrowserWarningBypassHeader(headers, baseUrl)

  const response = await fetch(`${normalizeBaseUrl(baseUrl)}${'/api/accounts/workspace/select'}`, {
    method: 'POST',
    headers,
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
  if (accountToken.trim()) {
    headers.set('Authorization', `Bearer ${accountToken.trim()}`)
  }
  addNgrokBrowserWarningBypassHeader(headers, baseUrl)

  const response = await fetch(
    `${normalizeBaseUrl(baseUrl)}${`/api/accounts/workspaces/${encodeURIComponent(workspaceId.trim())}`}`,
    {
      method: 'DELETE',
      headers,
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
  const [baseUrl, setBaseUrl] = useState(() => loadInitialBaseUrl(defaultBaseUrl))
  const [authToken, setAuthToken] = useState(() => loadSessionAuthToken())
  const [pendingAuthToken, setPendingAuthToken] = useState('')
  const [workspaceToken, setWorkspaceToken] = useState(() => loadSessionWorkspaceToken())
  const [workspaceId, setWorkspaceId] = useState(() => loadStoredWorkspaceId())
  const [runtimeAccount, setRuntimeAccount] = useState<RuntimeAccount>(() => loadSessionAccount())
  const [runtimeSettingsOpen, setRuntimeSettingsOpen] = useState(false)
  const [runtimeSettingsMode, setRuntimeSettingsMode] = useState<RuntimeSettingsMode>('settings')
  const [runtimeAuthIntent, setRuntimeAuthIntent] = useState<RuntimeAuthIntent>('login')
  const [runtimeAuthStep, setRuntimeAuthStep] = useState<RuntimeAuthStep>(() => (loadSessionAuthToken() ? 'workspace' : 'account'))
  const [runtimeWorkspaceAction, setRuntimeWorkspaceAction] = useState<RuntimeWorkspaceAction>('join')
  const [runtimeWorkspaceJoinMethod, setRuntimeWorkspaceJoinMethod] = useState<RuntimeWorkspaceJoinMethod>('token')
  const [runtimeWorkspaceCreateAccessMode, setRuntimeWorkspaceCreateAccessMode] =
    useState<RuntimeWorkspaceCreateAccessMode>('password')
  const [runtimeCreatedWorkspaceToken, setRuntimeCreatedWorkspaceToken] = useState('')
  const [runtimeSettingsError, setRuntimeSettingsError] = useState('')
  const [legacyPasswordSetupRequired, setLegacyPasswordSetupRequired] = useState(false)
  const accountRefreshTokenRef = useRef('')
  const [runtimeSettingsForm, setRuntimeSettingsForm] = useState<RuntimeSettingsForm>(() => ({
    baseUrl: loadInitialBaseUrl(defaultBaseUrl),
    workspaceToken: loadSessionWorkspaceToken(),
    workspaceName: '',
    workspacePassword: '',
    username: loadSessionAccount()?.username ?? '',
    firstName: '',
    lastName: '',
    password: '',
  }))

  const promptForLegacyPasswordSetup = useCallback((account?: NonNullable<RuntimeAccount>) => {
    setRuntimeSettingsForm((current) => ({
      ...current,
      username: account?.username || current.username,
      password: '',
    }))
    setRuntimeAuthIntent('login')
    setRuntimeAuthStep('account')
    setRuntimeCreatedWorkspaceToken('')
    setRuntimeSettingsMode('auth')
    setRuntimeSettingsOpen(true)
    setLegacyPasswordSetupRequired(true)
    setRuntimeSettingsError(LEGACY_PASSWORD_SETUP_MESSAGE)
  }, [])

  const refreshRuntimeAccount = useCallback(
    async (options: { reportError?: boolean } = {}) => {
      const accountStepToken = pendingAuthToken.trim() || authToken.trim() || loadSessionAuthToken().trim()
      if (!accountStepToken) return null

      try {
        const nextBaseUrl = normalizeBaseUrl(runtimeSettingsForm.baseUrl || baseUrl)
        const accountSnapshot = await fetchAccountSnapshot(nextBaseUrl, accountStepToken, workspaceToken)
        const account = mergeAccountWorkspaceState(accountSnapshot, runtimeAccount, workspaceId)
        storeSessionAuthToken(accountStepToken)
        storeSessionAccount(account)
        setRuntimeAccount(account)
        setRuntimeSettingsForm((current) => ({
          ...current,
          username: account.username || current.username,
        }))
        if (account.requiresPasswordSetup) {
          storeSessionWorkspaceToken('')
          storeWorkspaceId('')
          setWorkspaceToken('')
          setWorkspaceId('')
          promptForLegacyPasswordSetup(account)
          return account
        }
        if (account.workspaceId) {
          storeWorkspaceId(account.workspaceId)
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
    if (!accountStepToken) {
      accountRefreshTokenRef.current = ''
      return
    }
    if (accountRefreshTokenRef.current === accountStepToken) return

    accountRefreshTokenRef.current = accountStepToken
    void refreshRuntimeAccount()
  }, [authToken, refreshRuntimeAccount])

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
    }))
    setRuntimeWorkspaceAction('join')
    setRuntimeWorkspaceJoinMethod('token')
    setRuntimeCreatedWorkspaceToken('')
    setRuntimeAuthStep(
      needsPasswordSetup || !(mode === 'auth' && (authToken.trim() || pendingAuthToken.trim()))
        ? 'account'
        : 'workspace',
    )
    if (needsPasswordSetup) {
      setRuntimeAuthIntent('login')
    }
    setRuntimeSettingsMode(mode)
    setRuntimeSettingsError(needsPasswordSetup ? LEGACY_PASSWORD_SETUP_MESSAGE : '')
    setLegacyPasswordSetupRequired(needsPasswordSetup)
    setRuntimeSettingsOpen(true)
    if (mode === 'auth' && (authToken.trim() || pendingAuthToken.trim())) {
      void refreshRuntimeAccount({ reportError: true })
    }
  }, [
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
  }, [])

  const submitRuntimeSettings = useCallback(
    async (event: FormEvent<HTMLFormElement>) => {
      event.preventDefault()
      const nextBaseUrl = normalizeBaseUrl(runtimeSettingsForm.baseUrl)
      const nextAuthToken = authToken.trim()
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
        const legacyPasswordSetupAttempt = runtimeAuthIntent === 'login' && legacyPasswordSetupRequired
        if (legacyPasswordSetupAttempt && !runtimeSettingsForm.password.trim()) {
          setRuntimeSettingsError(LEGACY_PASSWORD_SETUP_MESSAGE)
          return
        }
        if (runtimeAuthIntent === 'signup' && (!runtimeSettingsForm.firstName.trim() || !runtimeSettingsForm.lastName.trim())) {
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
          const account = accountFromSession(accountSession)
          if (nextBaseUrl) {
            localStorage.setItem('aidm:baseUrl', nextBaseUrl)
          } else {
            localStorage.removeItem('aidm:baseUrl')
          }
          storeSessionAuthToken(accountToken)
          storeSessionWorkspaceToken('')
          storeWorkspaceId(account.workspaceId)
          storeSessionAccount(account)
          setBaseUrl(nextBaseUrl)
          setPendingAuthToken(accountToken)
          setWorkspaceToken('')
          setWorkspaceId(account.workspaceId ?? '')
          setRuntimeAccount(account)
          setRuntimeSettingsForm((current) => ({ ...current, workspaceToken: '', workspacePassword: '' }))
          setRuntimeAuthStep('workspace')
          setLegacyPasswordSetupRequired(false)
          setRuntimeSettingsError('')
          return
        } catch (error) {
          const runtimeError = error as RuntimeApiError
          if (runtimeAuthIntent === 'login' && runtimeError.errorCode === LEGACY_PASSWORD_SETUP_ERROR_CODE) {
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
        if (!accountStepToken) {
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
          const account = accountFromSession(accountSession)
          if (nextBaseUrl) {
            localStorage.setItem('aidm:baseUrl', nextBaseUrl)
          } else {
            localStorage.removeItem('aidm:baseUrl')
          }
          storeSessionAuthToken(accountToken)
          storeSessionWorkspaceToken(storedWorkspaceToken)
          storeWorkspaceId(account.workspaceId)
          storeSessionAccount(account)
          setBaseUrl(nextBaseUrl)
          setAuthToken(accountToken)
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

        setBaseUrl('')
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

      localStorage.setItem('aidm:baseUrl', nextBaseUrl)

      setBaseUrl(nextBaseUrl)
      resetRuntimeState()
      reconnectSocket()
      setRuntimeSettingsOpen(false)
      setRuntimeSettingsMode('settings')
      setRuntimeSettingsError('')
      setLegacyPasswordSetupRequired(false)
    },
    [
      authToken,
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
    storeSessionAuthToken('')
    storeSessionWorkspaceToken('')
    storeWorkspaceId('')
    storeSessionAccount(null)
    accountRefreshTokenRef.current = ''
    setAuthToken('')
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
    }))
    reconnectSocket()
  }, [reconnectSocket])

  const selectSavedWorkspace = useCallback(
    async (nextWorkspaceId: string) => {
      const cleanWorkspaceId = nextWorkspaceId.trim()
      const accountStepToken = pendingAuthToken.trim() || authToken.trim()
      if (!accountStepToken) {
        setRuntimeSettingsError('Log in or sign up before choosing a workspace.')
        setRuntimeAuthStep('account')
        return
      }
      if (!cleanWorkspaceId) {
        setRuntimeSettingsError('Choose a saved workspace.')
        return
      }
      try {
        const nextBaseUrl = normalizeBaseUrl(runtimeSettingsForm.baseUrl)
        if (nextBaseUrl && !isHttpBaseUrl(nextBaseUrl)) {
          setRuntimeSettingsError('Backend URL must start with http:// or https://.')
          return
        }
        const accountSession = await selectWorkspaceSession(nextBaseUrl, cleanWorkspaceId, accountStepToken)
        const accountToken = accountSession.account_token.trim()
        const account = accountFromSession(accountSession)
        if (nextBaseUrl) {
          localStorage.setItem('aidm:baseUrl', nextBaseUrl)
        } else {
          localStorage.removeItem('aidm:baseUrl')
        }
        storeSessionAuthToken(accountToken)
        storeSessionWorkspaceToken('')
        storeWorkspaceId(account.workspaceId)
        storeSessionAccount(account)
        setBaseUrl(nextBaseUrl)
        setAuthToken(accountToken)
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
      authToken,
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
      const accountStepToken = pendingAuthToken.trim() || authToken.trim()
      if (!accountStepToken) {
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
        const nextBaseUrl = normalizeBaseUrl(runtimeSettingsForm.baseUrl)
        if (nextBaseUrl && !isHttpBaseUrl(nextBaseUrl)) {
          const message = 'Backend URL must start with http:// or https://.'
          setRuntimeSettingsError(message)
          return { ok: false, error: message }
        }
        const accountSession = await deleteWorkspaceSession(nextBaseUrl, cleanWorkspaceId, accountStepToken)
        const accountToken = accountSession.account_token.trim()
        const removedCurrentWorkspace =
          cleanWorkspaceId === workspaceId.trim() || cleanWorkspaceId === runtimeAccount?.workspaceId
        const account = removedCurrentWorkspace
          ? accountFromSession(accountSession)
          : mergeAccountWorkspaceState(accountFromSession(accountSession), runtimeAccount, workspaceId)
        const nextWorkspaceToken = removedCurrentWorkspace ? '' : workspaceToken
        if (nextBaseUrl) {
          localStorage.setItem('aidm:baseUrl', nextBaseUrl)
        } else {
          localStorage.removeItem('aidm:baseUrl')
        }
        storeSessionAuthToken(accountToken)
        storeSessionWorkspaceToken(nextWorkspaceToken)
        storeWorkspaceId(account.workspaceId)
        storeSessionAccount(account)
        setBaseUrl(nextBaseUrl)
        setAuthToken(accountToken)
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
      authToken,
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
