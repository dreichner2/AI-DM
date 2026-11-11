import {
  type Dispatch,
  type FormEventHandler,
  type RefObject,
  type SetStateAction,
} from 'react'
import { ModalShell } from '../../ModalShell'
import type { AccountWorkspace } from '../../types'
import {
  LEGACY_PASSWORD_SETUP_MESSAGE,
  type RuntimeAccount,
  type RuntimeAuthIntent,
  type RuntimeAuthStep,
  type RuntimeSettingsForm,
  type RuntimeWorkspaceAction,
  type RuntimeWorkspaceCreateAccessMode,
  type RuntimeWorkspaceJoinMethod,
} from '../../useRuntimeSettings'
import { savedWorkspaceDisplayName, savedWorkspaceRoleLabel } from '../../workspaceLabels'

/** The log-in, sign-up and table forms, driven by useRuntimeSettings. */
export type AccountAccessProps = {
  error: string
  form: RuntimeSettingsForm
  legacyPasswordSetupRequired: boolean
  onAuthIntentChange: (intent: RuntimeAuthIntent) => void
  onAuthStepChange: (step: RuntimeAuthStep) => void
  onErrorChange: (message: string) => void
  onFormChange: Dispatch<SetStateAction<RuntimeSettingsForm>>
  onLegacyPasswordSetupRequiredChange: (required: boolean) => void
  onOpenSavedWorkspaceDelete: (workspace: AccountWorkspace) => void
  onSelectSavedWorkspace: (workspaceId: string) => void | Promise<void>
  onSubmit: FormEventHandler<HTMLFormElement>
  onWorkspaceActionChange: (action: RuntimeWorkspaceAction) => void
  onWorkspaceCreateAccessModeChange: (mode: RuntimeWorkspaceCreateAccessMode) => void
  onWorkspaceJoinMethodChange: (method: RuntimeWorkspaceJoinMethod) => void
  runtimeAccount: RuntimeAccount
  runtimeAuthIntent: RuntimeAuthIntent
  runtimeAuthStep: RuntimeAuthStep
  runtimeCreatedWorkspaceToken: string
  runtimeWorkspaceAction: RuntimeWorkspaceAction
  runtimeWorkspaceCreateAccessMode: RuntimeWorkspaceCreateAccessMode
  runtimeWorkspaceJoinMethod: RuntimeWorkspaceJoinMethod
  workspaceId: string
}

/** Who is playing and where. */
export type AccountSummary = {
  name: string
  table: string
  role: string | null
}

/** `summary`: who you are and where (only with an account); `access`: the log-in and table forms. */
export type AccountSheetView = { view: 'summary'; account: AccountSummary } | { view: 'access' }

type StoryAccountSheetProps = AccountSheetView & {
  dialogRef: RefObject<HTMLElement | null>
  onClose: () => void
  onSwitchTable: () => void
  onLogOut: () => void
  access: AccountAccessProps
}

type TableChoice = 'join-code' | 'join-password' | 'create'

const TABLE_CHOICES: ReadonlyArray<[TableChoice, string]> = [
  ['join-code', 'Join a table with a code'],
  ['join-password', 'Join with a password'],
  ['create', 'Create a table'],
]

function accessHelp(access: AccountAccessProps) {
  if (access.runtimeAuthStep === 'account') {
    if (access.legacyPasswordSetupRequired) return LEGACY_PASSWORD_SETUP_MESSAGE
    return access.runtimeAuthIntent === 'signup'
      ? 'Create your player account. A password is required.'
      : 'Log in with your username. Use your password if one is set.'
  }
  if (access.runtimeCreatedWorkspaceToken) {
    return 'Save this table code now. You will not be able to see it after you leave this page.'
  }
  if (access.runtimeWorkspaceAction === 'create') return 'Create a table with a password, or with a code to share.'
  return access.runtimeWorkspaceJoinMethod === 'password'
    ? "Enter the table's name and password."
    : 'Enter the code for the table you want to join.'
}

function Choice({
  pressed,
  onSelect,
  children,
}: {
  pressed: boolean
  onSelect: () => void
  children: string
}) {
  return (
    <button type="button" className="story-link story-dialog-choice" aria-pressed={pressed} onClick={onSelect}>
      {children}
    </button>
  )
}

function AccountForms({ access, onClose }: { access: AccountAccessProps; onClose: () => void }) {
  const {
    form,
    legacyPasswordSetupRequired,
    onErrorChange,
    onFormChange,
    runtimeAccount,
    runtimeAuthIntent,
    runtimeCreatedWorkspaceToken,
  } = access
  const accountStep = access.runtimeAuthStep === 'account'
  const savedTables = runtimeAccount?.workspaces ?? []
  const tableChoice: TableChoice =
    access.runtimeWorkspaceAction === 'create'
      ? 'create'
      : access.runtimeWorkspaceJoinMethod === 'password'
        ? 'join-password'
        : 'join-code'
  const field = (key: keyof RuntimeSettingsForm) => (value: string) =>
    onFormChange((current) => ({ ...current, [key]: value }))

  const chooseTable = (choice: TableChoice) => {
    access.onWorkspaceActionChange(choice === 'create' ? 'create' : 'join')
    if (choice !== 'create') access.onWorkspaceJoinMethodChange(choice === 'join-password' ? 'password' : 'token')
    onErrorChange('')
  }

  return (
    <form className="story-dialog-body" onSubmit={access.onSubmit}>
      {accountStep ? (
        <>
          <div className="story-dialog-choices story-dialog-tabs" role="group" aria-label="Account action">
            <Choice
              pressed={runtimeAuthIntent === 'login'}
              onSelect={() => {
                access.onAuthIntentChange('login')
                access.onLegacyPasswordSetupRequiredChange(false)
                onErrorChange('')
              }}
            >
              Log in
            </Choice>
            <Choice
              pressed={runtimeAuthIntent === 'signup'}
              onSelect={() => {
                access.onAuthIntentChange('signup')
                access.onLegacyPasswordSetupRequiredChange(legacyPasswordSetupRequired)
                onErrorChange(legacyPasswordSetupRequired ? LEGACY_PASSWORD_SETUP_MESSAGE : '')
              }}
            >
              Create account
            </Choice>
          </div>
          <div className="story-dialog-field-pair">
            <label className="story-dialog-field">
              <span className="story-label">Username</span>
              <input
                autoFocus
                data-autofocus
                value={form.username}
                onChange={(event) => {
                  onFormChange((current) => ({ ...current, username: event.target.value, recoveryCode: '' }))
                  if (legacyPasswordSetupRequired) {
                    access.onLegacyPasswordSetupRequiredChange(false)
                    onErrorChange('')
                  }
                }}
                autoComplete="username"
              />
            </label>
            <label className="story-dialog-field">
              <span className="story-label">{legacyPasswordSetupRequired ? 'New Password' : 'Password'}</span>
              <input
                value={form.password}
                onChange={(event) => field('password')(event.target.value)}
                type="password"
                autoComplete={
                  runtimeAuthIntent === 'signup' || legacyPasswordSetupRequired ? 'new-password' : 'current-password'
                }
              />
            </label>
          </div>
          {runtimeAuthIntent === 'signup' && !legacyPasswordSetupRequired ? (
            <div className="story-dialog-field-pair">
              <label className="story-dialog-field">
                <span className="story-label">First Name</span>
                <input
                  value={form.firstName}
                  onChange={(event) => field('firstName')(event.target.value)}
                  autoComplete="given-name"
                />
              </label>
              <label className="story-dialog-field">
                <span className="story-label">Last Name</span>
                <input
                  value={form.lastName}
                  onChange={(event) => field('lastName')(event.target.value)}
                  autoComplete="family-name"
                />
              </label>
            </div>
          ) : null}
          {legacyPasswordSetupRequired ? (
            <label className="story-dialog-field">
              <span className="story-label">Recovery Code</span>
              <input
                value={form.recoveryCode}
                onChange={(event) => field('recoveryCode')(event.target.value)}
                placeholder="From the person who runs the game"
                type="password"
                autoComplete="one-time-code"
              />
            </label>
          ) : null}
        </>
      ) : runtimeCreatedWorkspaceToken ? (
        <label className="story-dialog-field">
          <span className="story-label">Table code</span>
          <input
            aria-label="Generated table code"
            className="story-dialog-link-field"
            readOnly
            value={runtimeCreatedWorkspaceToken}
            onFocus={(event) => event.currentTarget.select()}
          />
        </label>
      ) : (
        <>
          {savedTables.length ? (
            <section className="story-dialog-section" aria-labelledby="account-tables-heading">
              <h3 id="account-tables-heading" className="story-label">Your tables</h3>
              <ul className="story-dialog-list" role="group" aria-label="Saved tables">
                {savedTables.map((workspace) => {
                  const tableName = savedWorkspaceDisplayName(workspace)
                  const deletesTable = workspace.is_workspace_admin && workspace.access_mode !== 'configured'
                  const current = workspace.workspace_id === access.workspaceId
                  return (
                    <li key={workspace.workspace_id} className="story-dialog-row" data-current={current ? '' : undefined}>
                      <span className="story-dialog-row-copy">
                        <span className="story-dialog-row-name">{tableName}</span>
                        <span className="story-dialog-row-meta">{savedWorkspaceRoleLabel(workspace)}</span>
                      </span>
                      <span className="story-dialog-row-actions">
                        {current ? (
                          <span className="story-dialog-here">Here now</span>
                        ) : (
                          <button
                            type="button"
                            className="story-link"
                            aria-label={`Play at ${tableName}`}
                            onClick={() => void access.onSelectSavedWorkspace(workspace.workspace_id)}
                          >
                            Play here
                          </button>
                        )}
                        <button
                          type="button"
                          className={deletesTable ? 'story-link story-link-danger' : 'story-link story-dialog-quiet'}
                          aria-label={`${deletesTable ? 'Delete' : 'Remove'} ${tableName}`}
                          onClick={() => access.onOpenSavedWorkspaceDelete(workspace)}
                        >
                          {deletesTable ? 'Delete' : 'Remove'}
                        </button>
                      </span>
                    </li>
                  )
                })}
              </ul>
            </section>
          ) : null}
          <div className="story-dialog-choices story-dialog-tabs" role="group" aria-label="Table action">
            {TABLE_CHOICES.map(([choice, label]) => (
              <Choice key={choice} pressed={tableChoice === choice} onSelect={() => chooseTable(choice)}>
                {label}
              </Choice>
            ))}
          </div>
          {tableChoice === 'join-code' ? (
            <label className="story-dialog-field">
              <span className="story-label">Table code</span>
              <input
                autoFocus={!savedTables.length}
                data-autofocus={!savedTables.length ? true : undefined}
                value={form.workspaceToken}
                onChange={(event) => field('workspaceToken')(event.target.value)}
                type="password"
                autoComplete="off"
              />
            </label>
          ) : (
            <div className="story-dialog-field-pair">
              <label className="story-dialog-field">
                <span className="story-label">Table Name</span>
                <input
                  autoFocus={tableChoice === 'create' || !savedTables.length}
                  data-autofocus={tableChoice === 'create' || !savedTables.length ? true : undefined}
                  value={form.workspaceName}
                  onChange={(event) => field('workspaceName')(event.target.value)}
                  autoComplete="off"
                />
              </label>
              {tableChoice === 'join-password' || access.runtimeWorkspaceCreateAccessMode === 'password' ? (
                <label className="story-dialog-field">
                  <span className="story-label">Table Password</span>
                  <input
                    value={form.workspacePassword}
                    onChange={(event) => field('workspacePassword')(event.target.value)}
                    type="password"
                    autoComplete={tableChoice === 'create' ? 'new-password' : 'off'}
                  />
                </label>
              ) : null}
            </div>
          )}
          {tableChoice === 'create' ? (
            <div className="story-dialog-setting">
              <span className="story-dialog-setting-name">Others join with</span>
              <span className="story-dialog-choices" role="group" aria-label="Table access">
                <Choice
                  pressed={access.runtimeWorkspaceCreateAccessMode === 'password'}
                  onSelect={() => {
                    access.onWorkspaceCreateAccessModeChange('password')
                    onErrorChange('')
                  }}
                >
                  Password
                </Choice>
                <Choice
                  pressed={access.runtimeWorkspaceCreateAccessMode === 'token'}
                  onSelect={() => {
                    access.onWorkspaceCreateAccessModeChange('token')
                    onErrorChange('')
                  }}
                >
                  Code
                </Choice>
              </span>
            </div>
          ) : null}
        </>
      )}
      {access.error ? <p className="story-dialog-error" role="alert">{access.error}</p> : null}
      <footer className="story-dialog-footer">
        {!accountStep && !runtimeCreatedWorkspaceToken ? (
          <button
            type="button"
            className="story-link"
            onClick={() => {
              access.onAuthStepChange('account')
              onErrorChange('')
            }}
          >
            Back
          </button>
        ) : null}
        {runtimeCreatedWorkspaceToken ? null : (
          <button type="button" className="story-link" onClick={onClose}>
            Cancel
          </button>
        )}
        <button type="submit" className="story-primary">
          {accountStep
            ? 'Continue'
            : runtimeCreatedWorkspaceToken
              ? 'Done'
              : tableChoice === 'create'
                ? 'Create table'
                : 'Join table'}
        </button>
      </footer>
    </form>
  )
}

/** Account: who you are and which table you are at, or the forms to get there. */
export function StoryAccountSheet(props: StoryAccountSheetProps) {
  const { access, dialogRef, onClose, onLogOut, onSwitchTable } = props
  return (
    <ModalShell
      size="narrow"
      closeLabel={props.view === 'access' ? 'Close account prompt' : 'Close account'}
      description={props.view === 'access' ? accessHelp(access) : undefined}
      dialogRef={dialogRef}
      labelledBy="story-account-title"
      onClose={onClose}
      title="Account"
    >
      {props.view === 'access' ? (
        <AccountForms access={access} onClose={onClose} />
      ) : (
        <div className="story-dialog-body">
          <div className="story-dialog-identity">
            <p className="story-dialog-subject">{props.account.name}</p>
            <p className="story-dialog-row-meta">
              At {props.account.table}
              {props.account.role ? ` · ${props.account.role}` : ''}
            </p>
          </div>
          <div className="story-dialog-links">
            <button type="button" className="story-link" data-autofocus onClick={onSwitchTable}>
              Switch table
            </button>
            <button type="button" className="story-link" onClick={onLogOut}>
              Log out
            </button>
          </div>
        </div>
      )}
    </ModalShell>
  )
}
