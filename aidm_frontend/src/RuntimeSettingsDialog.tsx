import {
  type Dispatch,
  type FormEventHandler,
  type RefObject,
  type SetStateAction,
} from 'react'
import { type PendingBackendTrust, type RuntimeSettingsForm } from './useRuntimeSettings'
import { ModalShell } from './ModalShell'

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
      size="narrow"
      closeLabel="Reject shared backend"
      describedBy="backend-trust-description"
      dialogRef={dialogRef}
      labelledBy="backend-trust-title"
      onClose={onReject}
      title="Connect to Shared Backend"
    >
      <div className="story-dialog-body">
        <p className="story-dialog-subject">{backend.origin}</p>
        {backend.baseUrl !== backend.origin ? (
          <p className="story-dialog-help">Server address: {backend.baseUrl}</p>
        ) : null}
        <p id="backend-trust-description" className="story-dialog-note">
          Only continue if you know and trust this game server. Nothing is sent to it until you confirm.
        </p>
        <footer className="story-dialog-footer">
          <button type="button" className="story-link" data-autofocus onClick={onReject}>
            Cancel
          </button>
          <button type="button" className="story-primary" onClick={onConfirm}>
            Trust and Connect
          </button>
        </footer>
      </div>
    </ModalShell>
  )
}

export type RuntimeSettingsDialogProps = {
  defaultBaseUrl: string
  dialogRef: RefObject<HTMLElement | null>
  error: string
  form: RuntimeSettingsForm
  onClose: () => void
  onFormChange: Dispatch<SetStateAction<RuntimeSettingsForm>>
  onSubmit: FormEventHandler<HTMLFormElement>
  open: boolean
}

/** Connection settings: which game server this page talks to. */
export function RuntimeSettingsDialog({
  defaultBaseUrl,
  dialogRef,
  error,
  form,
  onClose,
  onFormChange,
  onSubmit,
  open,
}: RuntimeSettingsDialogProps) {
  if (!open) return null

  return (
    <ModalShell
      closeLabel="Close backend settings"
      dialogRef={dialogRef}
      labelledBy="runtime-settings-title"
      onClose={onClose}
      title="Backend Settings"
    >
      <form className="story-dialog-body" onSubmit={onSubmit}>
        <label className="story-dialog-field">
          <span className="story-label">Backend URL</span>
          <input
            autoFocus
            data-autofocus
            value={form.baseUrl}
            onChange={(event) =>
              onFormChange((current) => ({
                ...current,
                baseUrl: event.target.value,
              }))
            }
            placeholder="Leave blank to use this site"
          />
        </label>
        <p className="story-dialog-help">Only change this if you run the game server somewhere else.</p>
        {error ? <p className="story-dialog-error" role="alert">{error}</p> : null}
        <footer className="story-dialog-footer">
          <button
            type="button"
            className="story-link"
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
                recoveryCode: '',
              })
            }
          >
            Reset
          </button>
          <button type="button" className="story-link" onClick={onClose}>
            Cancel
          </button>
          <button type="submit" className="story-primary">
            Save Settings
          </button>
        </footer>
      </form>
    </ModalShell>
  )
}
