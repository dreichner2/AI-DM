import {
  type Dispatch,
  type FormEventHandler,
  type RefObject,
  type SetStateAction,
} from 'react'
import { X } from 'lucide-react'
import type { AccountWorkspace } from './types'
import {
  LEGACY_PASSWORD_SETUP_MESSAGE,
  type PendingBackendTrust,
  type RuntimeAccount,
  type RuntimeAuthIntent,
  type RuntimeAuthStep,
  type RuntimeSettingsForm,
  type RuntimeSettingsMode,
  type RuntimeWorkspaceAction,
  type RuntimeWorkspaceCreateAccessMode,
  type RuntimeWorkspaceJoinMethod,
} from './useRuntimeSettings'
import { ModalShell } from './ModalShell'
import { savedWorkspaceDisplayName, savedWorkspaceRoleLabel } from './workspaceLabels'

type BackendTrustDialogProps = {
  backend: PendingBackendTrust
  dialogRef: RefObject<HTMLElement | null>
  onConfirm: () => void
  onReject: () => void
}

export function BackendTrustDialog({
  backend,
  dialogRef,
  onConfirm,
  onReject,
}: BackendTrustDialogProps) {
  return (
    <ModalShell
      className="campaign-dialog runtime-dialog"
      describedBy="backend-trust-description"
      dialogRef={dialogRef}
      labelledBy="backend-trust-title"
      onClose={onReject}
    >
      <header>
        <div>
          <span>Security Check</span>
          <h2 id="backend-trust-title">Connect to Shared Backend</h2>
        </div>
        <button type="button" aria-label="Reject shared backend" onClick={onReject}>
          <X size={18} />
        </button>
      </header>
      <div className="dialog-warning">
        <strong>{backend.origin}</strong>
        {backend.baseUrl !== backend.origin ? <span>Backend URL: {backend.baseUrl}</span> : null}
      </div>
      <p id="backend-trust-description">
        Only continue if you recognize and trust this backend. AIDM will not contact it before you confirm.
      </p>
      <footer>
        <button type="button" className="secondary" data-autofocus onClick={onReject}>
          Cancel
        </button>
        <button type="button" onClick={onConfirm}>
          Trust and Connect
        </button>
      </footer>
    </ModalShell>
  )
}

export type RuntimeSettingsDialogProps = {
  defaultBaseUrl: string
  dialogRef: RefObject<HTMLElement | null>
  error: string
  form: RuntimeSettingsForm
  legacyPasswordSetupRequired: boolean
  mode: RuntimeSettingsMode
  onAuthIntentChange: (intent: RuntimeAuthIntent) => void
  onAuthStepChange: (step: RuntimeAuthStep) => void
  onClose: () => void
  onErrorChange: (message: string) => void
  onFormChange: Dispatch<SetStateAction<RuntimeSettingsForm>>
  onLegacyPasswordSetupRequiredChange: (required: boolean) => void
  onOpenSavedWorkspaceDelete: (workspace: AccountWorkspace) => void
  onSelectSavedWorkspace: (workspaceId: string) => void | Promise<void>
  onSubmit: FormEventHandler<HTMLFormElement>
  onWorkspaceActionChange: (action: RuntimeWorkspaceAction) => void
  onWorkspaceCreateAccessModeChange: (mode: RuntimeWorkspaceCreateAccessMode) => void
  onWorkspaceJoinMethodChange: (method: RuntimeWorkspaceJoinMethod) => void
  open: boolean
  runtimeAccount: RuntimeAccount
  runtimeCreatedWorkspaceToken: string
  runtimeAuthIntent: RuntimeAuthIntent
  runtimeAuthStep: RuntimeAuthStep
  runtimeWorkspaceAction: RuntimeWorkspaceAction
  runtimeWorkspaceCreateAccessMode: RuntimeWorkspaceCreateAccessMode
  runtimeWorkspaceJoinMethod: RuntimeWorkspaceJoinMethod
  workspaceId: string
}

export function RuntimeSettingsDialog({
  defaultBaseUrl,
  dialogRef,
  error,
  form,
  legacyPasswordSetupRequired,
  mode,
  onAuthIntentChange,
  onAuthStepChange,
  onClose,
  onErrorChange,
  onFormChange,
  onLegacyPasswordSetupRequiredChange,
  onOpenSavedWorkspaceDelete,
  onSelectSavedWorkspace,
  onSubmit,
  onWorkspaceActionChange,
  onWorkspaceCreateAccessModeChange,
  onWorkspaceJoinMethodChange,
  open,
  runtimeAccount,
  runtimeCreatedWorkspaceToken,
  runtimeAuthIntent,
  runtimeAuthStep,
  runtimeWorkspaceAction,
  runtimeWorkspaceCreateAccessMode,
  runtimeWorkspaceJoinMethod,
  workspaceId,
}: RuntimeSettingsDialogProps) {
  if (!open) return null

  const isAuthPrompt = mode === 'auth'
  const isAccountStep = isAuthPrompt && runtimeAuthStep === 'account'
  const isWorkspaceStep = isAuthPrompt && runtimeAuthStep === 'workspace'
  const eyebrow = isAuthPrompt ? 'Access' : 'Runtime'
  const title = isWorkspaceStep
    ? runtimeCreatedWorkspaceToken
      ? 'Save Table Token'
      : runtimeWorkspaceAction === 'create'
        ? 'Create Table'
        : 'Join Table'
    : isAccountStep
      ? runtimeAuthIntent === 'signup'
        ? 'Sign Up'
        : 'Log In'
      : 'Backend Settings'
  const closeLabel = isAuthPrompt ? 'Close account prompt' : 'Close backend settings'
  const helpText = isWorkspaceStep
    ? runtimeCreatedWorkspaceToken
      ? 'Save this token now. You will not be able to view it after you leave this page.'
      : runtimeWorkspaceAction === 'create'
        ? 'Create a table with a shared password or a generated token.'
        : runtimeWorkspaceJoinMethod === 'password'
          ? 'Enter the table name and password.'
          : 'Enter the table token for the table you want to join.'
    : isAccountStep
      ? legacyPasswordSetupRequired
        ? LEGACY_PASSWORD_SETUP_MESSAGE
        : runtimeAuthIntent === 'signup'
          ? 'Create your player account first. Password is required.'
          : 'Log in with your username. Use your password if one is set.'
      : 'Leave Backend URL blank when the frontend and backend share one origin.'

  return (
    <div
      className="modal-backdrop"
      role="presentation"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) {
          onClose()
        }
      }}
    >
      <section
        ref={dialogRef}
        className="campaign-dialog runtime-dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby="runtime-settings-title"
      >
        <header>
          <div>
            <span>{eyebrow}</span>
            <h2 id="runtime-settings-title">{title}</h2>
          </div>
          <button type="button" aria-label={closeLabel} onClick={onClose}>
            <X size={18} />
          </button>
        </header>
        <form onSubmit={onSubmit}>
          {isAuthPrompt ? null : (
            <label>
              Backend URL
              <input
                autoFocus={!isAuthPrompt}
                data-autofocus={!isAuthPrompt ? true : undefined}
                value={form.baseUrl}
                onChange={(event) =>
                  onFormChange((current) => ({
                    ...current,
                    baseUrl: event.target.value,
                  }))
                }
                placeholder="Leave blank for same origin"
              />
            </label>
          )}
          {isAccountStep ? (
            <>
              <div className="runtime-auth-choice" role="group" aria-label="Account action">
                <button
                  type="button"
                  aria-pressed={runtimeAuthIntent === 'login'}
                  onClick={() => {
                    onAuthIntentChange('login')
                    onLegacyPasswordSetupRequiredChange(false)
                    onErrorChange('')
                  }}
                >
                  Log In
                </button>
                <button
                  type="button"
                  aria-pressed={runtimeAuthIntent === 'signup'}
                  onClick={() => {
                    onAuthIntentChange('signup')
                    onLegacyPasswordSetupRequiredChange(legacyPasswordSetupRequired)
                    onErrorChange(legacyPasswordSetupRequired ? LEGACY_PASSWORD_SETUP_MESSAGE : '')
                  }}
                >
                  Sign Up
                </button>
              </div>
              <div className="dialog-grid two">
                <label>
                  Username
                  <input
                    autoFocus
                    data-autofocus
                    value={form.username}
                    onChange={(event) => {
                      onFormChange((current) => ({
                        ...current,
                        username: event.target.value,
                      }))
                      if (legacyPasswordSetupRequired) {
                        onLegacyPasswordSetupRequiredChange(false)
                        onErrorChange('')
                      }
                    }}
                    placeholder="Username"
                    autoComplete="username"
                  />
                </label>
                <label>
                  {legacyPasswordSetupRequired ? 'New Password' : 'Password'}
                  <input
                    value={form.password}
                    onChange={(event) =>
                      onFormChange((current) => ({
                        ...current,
                        password: event.target.value,
                      }))
                    }
                    placeholder="Password"
                    type="password"
                    autoComplete={
                      runtimeAuthIntent === 'signup' || legacyPasswordSetupRequired
                        ? 'new-password'
                        : 'current-password'
                    }
                  />
                </label>
              </div>
              {runtimeAuthIntent === 'signup' ? (
                <div className="dialog-grid two">
                  <label>
                    First Name
                    <input
                      value={form.firstName}
                      onChange={(event) =>
                        onFormChange((current) => ({
                          ...current,
                          firstName: event.target.value,
                        }))
                      }
                      autoComplete="given-name"
                    />
                  </label>
                  <label>
                    Last Name
                    <input
                      value={form.lastName}
                      onChange={(event) =>
                        onFormChange((current) => ({
                          ...current,
                          lastName: event.target.value,
                        }))
                      }
                      autoComplete="family-name"
                    />
                  </label>
                </div>
              ) : null}
            </>
          ) : null}
          {isWorkspaceStep ? (
            <>
              {runtimeCreatedWorkspaceToken ? (
                <div className="dialog-warning">
                  <strong>Save this table token now.</strong>
                  <input
                    aria-label="Generated table token"
                    readOnly
                    value={runtimeCreatedWorkspaceToken}
                    onFocus={(event) => event.currentTarget.select()}
                  />
                  <span>You will not be able to view it after you leave this page.</span>
                </div>
              ) : (
                <>
                  <div className="runtime-auth-choice" role="group" aria-label="Table action">
                    <button
                      type="button"
                      aria-pressed={runtimeWorkspaceAction === 'join'}
                      onClick={() => {
                        onWorkspaceActionChange('join')
                        onErrorChange('')
                      }}
                    >
                      Join
                    </button>
                    <button
                      type="button"
                      aria-pressed={runtimeWorkspaceAction === 'create'}
                      onClick={() => {
                        onWorkspaceActionChange('create')
                        onErrorChange('')
                      }}
                    >
                      Create
                    </button>
                  </div>
                  {runtimeWorkspaceAction === 'join' && runtimeAccount?.workspaces.length ? (
                    <div className="saved-workspace-list" role="group" aria-label="Saved tables">
                      <span>Saved Tables</span>
                      {runtimeAccount.workspaces.map((workspace) => {
                        const tableName = savedWorkspaceDisplayName(workspace)
                        const deletesTable =
                          workspace.is_workspace_admin && workspace.access_mode !== 'configured'
                        return (
                          <div className="saved-workspace-row" key={workspace.workspace_id}>
                            <button
                              type="button"
                              className="saved-workspace-option"
                              aria-label={`${tableName} ${savedWorkspaceRoleLabel(workspace)}`}
                              aria-pressed={workspace.workspace_id === workspaceId}
                              onClick={() => void onSelectSavedWorkspace(workspace.workspace_id)}
                            >
                              <strong>{tableName}</strong>
                              <span>{savedWorkspaceRoleLabel(workspace)}</span>
                            </button>
                            <button
                              type="button"
                              className="saved-workspace-delete"
                              aria-label={`${deletesTable ? 'Delete' : 'Remove'} ${tableName}`}
                              onClick={() => onOpenSavedWorkspaceDelete(workspace)}
                            >
                              {deletesTable ? 'Delete' : 'Remove'}
                            </button>
                          </div>
                        )
                      })}
                    </div>
                  ) : null}
                  {runtimeWorkspaceAction === 'join' ? (
                    <>
                      <div className="runtime-auth-choice" role="group" aria-label="Join method">
                        <button
                          type="button"
                          aria-pressed={runtimeWorkspaceJoinMethod === 'token'}
                          onClick={() => {
                            onWorkspaceJoinMethodChange('token')
                            onErrorChange('')
                          }}
                        >
                          Token
                        </button>
                        <button
                          type="button"
                          aria-pressed={runtimeWorkspaceJoinMethod === 'password'}
                          onClick={() => {
                            onWorkspaceJoinMethodChange('password')
                            onErrorChange('')
                          }}
                        >
                          Password
                        </button>
                      </div>
                      {runtimeWorkspaceJoinMethod === 'password' ? (
                        <div className="dialog-grid two">
                          <label>
                            Table Name
                            <input
                              autoFocus={!runtimeAccount?.workspaces.length}
                              data-autofocus={!runtimeAccount?.workspaces.length ? true : undefined}
                              value={form.workspaceName}
                              onChange={(event) =>
                                onFormChange((current) => ({
                                  ...current,
                                  workspaceName: event.target.value,
                                }))
                              }
                              autoComplete="off"
                            />
                          </label>
                          <label>
                            Table Password
                            <input
                              value={form.workspacePassword}
                              onChange={(event) =>
                                onFormChange((current) => ({
                                  ...current,
                                  workspacePassword: event.target.value,
                                }))
                              }
                              type="password"
                              autoComplete="off"
                            />
                          </label>
                        </div>
                      ) : (
                        <label>
                          Table Token
                          <input
                            autoFocus={!runtimeAccount?.workspaces.length}
                            data-autofocus={!runtimeAccount?.workspaces.length ? true : undefined}
                            value={form.workspaceToken}
                            onChange={(event) =>
                              onFormChange((current) => ({
                                ...current,
                                workspaceToken: event.target.value,
                              }))
                            }
                            placeholder="Token for a table"
                            type="password"
                            autoComplete="off"
                          />
                        </label>
                      )}
                    </>
                  ) : (
                    <>
                      <label>
                        Table Name
                        <input
                          autoFocus
                          data-autofocus
                          value={form.workspaceName}
                          onChange={(event) =>
                            onFormChange((current) => ({
                              ...current,
                              workspaceName: event.target.value,
                            }))
                          }
                          autoComplete="off"
                        />
                      </label>
                      <div className="runtime-auth-choice" role="group" aria-label="Table access">
                        <button
                          type="button"
                          aria-pressed={runtimeWorkspaceCreateAccessMode === 'password'}
                          onClick={() => {
                            onWorkspaceCreateAccessModeChange('password')
                            onErrorChange('')
                          }}
                        >
                          Password
                        </button>
                        <button
                          type="button"
                          aria-pressed={runtimeWorkspaceCreateAccessMode === 'token'}
                          onClick={() => {
                            onWorkspaceCreateAccessModeChange('token')
                            onErrorChange('')
                          }}
                        >
                          Token
                        </button>
                      </div>
                      {runtimeWorkspaceCreateAccessMode === 'password' ? (
                        <label>
                          Table Password
                          <input
                            value={form.workspacePassword}
                            onChange={(event) =>
                              onFormChange((current) => ({
                                ...current,
                                workspacePassword: event.target.value,
                              }))
                            }
                            type="password"
                            autoComplete="new-password"
                          />
                        </label>
                      ) : null}
                    </>
                  )}
                </>
              )}
            </>
          ) : null}
          <p>{helpText}</p>
          {error ? <div className="dialog-error">{error}</div> : null}
          <footer>
            {isAuthPrompt ? null : (
              <button
                type="button"
                className="secondary"
                onClick={() =>
                  onFormChange({
                    baseUrl: defaultBaseUrl,
                    workspaceToken: '',
                    workspaceName: '',
                    workspacePassword: '',
                    username: '',
                    firstName: '',
                    lastName: '',
                    password: '',
                  })
                }
              >
                Reset
              </button>
            )}
            {isWorkspaceStep && !runtimeCreatedWorkspaceToken ? (
              <button
                type="button"
                className="secondary"
                onClick={() => {
                  onAuthStepChange('account')
                  onErrorChange('')
                }}
              >
                Back
              </button>
            ) : null}
            <button type="button" className="secondary" onClick={onClose}>
              Cancel
            </button>
            <button type="submit">
              {isWorkspaceStep
                ? runtimeCreatedWorkspaceToken
                  ? 'Done'
                  : runtimeWorkspaceAction === 'create'
                    ? 'Create Table'
                    : 'Join Table'
                : isAccountStep
                  ? 'Continue'
                  : 'Save Settings'}
            </button>
          </footer>
        </form>
      </section>
    </div>
  )
}
