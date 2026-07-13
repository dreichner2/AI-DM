import {
  type Dispatch,
  type FormEvent,
  type RefObject,
  type SetStateAction,
} from 'react'
import { X } from 'lucide-react'
import { ModalShell } from './ModalShell'
import type { World } from './types'
import type { WorldDeleteDialogState, WorldFormState } from './worldDialogState'

type WorldManagerDialogProps = {
  deleteDialogOpen: boolean
  dialogRef: RefObject<HTMLElement | null>
  form: WorldFormState
  onClose: () => void
  onEditWorld: (world: World) => void
  onOpenDelete: (world: World) => void
  onResetForm: () => void
  onSubmit: (event: FormEvent<HTMLFormElement>) => void
  setForm: Dispatch<SetStateAction<WorldFormState>>
  worlds: World[]
}

type WorldDeleteDialogProps = {
  dialog: NonNullable<WorldDeleteDialogState>
  dialogRef: RefObject<HTMLElement | null>
  onClose: () => void
  onDelete: () => void
  onForceDelete: () => void
}

export function WorldManagerDialog({
  deleteDialogOpen,
  dialogRef,
  form,
  onClose,
  onEditWorld,
  onOpenDelete,
  onResetForm,
  onSubmit,
  setForm,
  worlds,
}: WorldManagerDialogProps) {
  return (
    <ModalShell
      className="campaign-dialog world-manager-dialog"
      dialogRef={dialogRef}
      labelledBy="world-manager-title"
      onClose={onClose}
    >
        <header>
          <div>
            <span>Worlds</span>
            <h2 id="world-manager-title">Manage Worlds</h2>
          </div>
          <button
            type="button"
            aria-label="Close world manager"
            onClick={onClose}
            disabled={form.pending || deleteDialogOpen}
          >
            <X size={18} />
          </button>
        </header>
        <div className="world-manager-list" aria-label="World list">
          {worlds.length ? (
            worlds.map((world) => {
              const isEditing = form.mode === 'edit' && form.worldId === world.world_id
              return (
                <div
                  key={world.world_id}
                  className={`world-manager-row ${isEditing ? 'active' : ''}`}
                >
                  <span>
                    <strong>{world.name}</strong>
                    <small>{world.description || 'No description yet'}</small>
                  </span>
                  <div>
                    <button
                      type="button"
                      onClick={() => onEditWorld(world)}
                      disabled={form.pending || deleteDialogOpen}
                    >
                      Edit
                    </button>
                    <button
                      type="button"
                      className="danger"
                      onClick={() => onOpenDelete(world)}
                      disabled={form.pending || deleteDialogOpen}
                    >
                      Delete
                    </button>
                  </div>
                </div>
              )
            })
          ) : (
            <div className="dialog-warning">
              <strong>No worlds yet.</strong>
              <span>Create a world below, then attach campaigns to it.</span>
            </div>
          )}
        </div>
        <form className="world-manager-form" onSubmit={onSubmit}>
          <div className="world-manager-form-heading">
            <strong>{form.mode === 'edit' ? 'Edit World' : 'Create World'}</strong>
            {form.mode === 'edit' ? (
              <button
                type="button"
                className="secondary"
                onClick={onResetForm}
                disabled={form.pending}
              >
                New World
              </button>
            ) : null}
          </div>
          <label>
            World Name
            <input
              data-autofocus
              value={form.name}
              onChange={(event) =>
                setForm((current) => ({
                  ...current,
                  name: event.target.value,
                  error: '',
                }))
              }
              placeholder="Crystal Reach"
              disabled={form.pending}
            />
          </label>
          <label>
            Description
            <textarea
              value={form.description}
              onChange={(event) =>
                setForm((current) => ({
                  ...current,
                  description: event.target.value,
                  error: '',
                }))
              }
              rows={3}
              placeholder="Realm premise, tone, or key conflicts..."
              disabled={form.pending}
            />
          </label>
          {form.error ? <div className="dialog-error" role="alert">{form.error}</div> : null}
          <footer>
            <button
              type="button"
              className="secondary"
              onClick={onClose}
              disabled={form.pending || deleteDialogOpen}
            >
              Close
            </button>
            <button type="submit" disabled={form.pending}>
              {form.pending
                ? form.mode === 'edit'
                  ? 'Saving...'
                  : 'Creating...'
                : form.mode === 'edit'
                  ? 'Save World'
                  : 'Create World'}
            </button>
          </footer>
        </form>
    </ModalShell>
  )
}

export function WorldDeleteDialog({
  dialog,
  dialogRef,
  onClose,
  onDelete,
  onForceDelete,
}: WorldDeleteDialogProps) {
  return (
    <ModalShell
      dialogRef={dialogRef}
      labelledBy="world-delete-title"
      describedBy="world-delete-description"
      onClose={onClose}
    >
        <header>
          <div>
            <span>World</span>
            <h2 id="world-delete-title">Delete World</h2>
          </div>
          <button
            type="button"
            aria-label="Close delete world"
            onClick={onClose}
            disabled={dialog.pending}
          >
            <X size={18} />
          </button>
        </header>
        <div className="dialog-body">
          <div id="world-delete-description" className="dialog-warning">
            <strong>{dialog.world.name}</strong>
            <span>
              This world can be deleted directly when nothing is using it.
              If campaigns are linked, force delete removes those linked campaigns first.
            </span>
          </div>
          {dialog.error ? <div className="dialog-error" role="alert">{dialog.error}</div> : null}
          <footer>
            <button
              type="button"
              className="secondary"
              onClick={onClose}
              disabled={dialog.pending}
            >
              Cancel
            </button>
            <button
              type="button"
              className="danger"
              data-autofocus
              onClick={onDelete}
              disabled={dialog.pending}
            >
              {dialog.pending ? 'Deleting...' : 'Delete World'}
            </button>
            {dialog.canForce ? (
              <button
                type="button"
                className="danger"
                onClick={onForceDelete}
                disabled={dialog.pending}
              >
                {dialog.pending ? 'Deleting...' : 'Delete World and Campaigns'}
              </button>
            ) : null}
          </footer>
        </div>
    </ModalShell>
  )
}
