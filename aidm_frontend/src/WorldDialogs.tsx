import {
  type Dispatch,
  type FormEvent,
  type RefObject,
  type SetStateAction,
} from 'react'
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
  const locked = form.pending || deleteDialogOpen
  return (
    <ModalShell
      closeDisabled={locked}
      closeLabel="Close world manager"
      description="Worlds hold the places and history that adventures share."
      dialogRef={dialogRef}
      labelledBy="world-manager-title"
      onClose={onClose}
      title="Manage worlds"
    >
      <div className="story-dialog-body">
        {worlds.length ? (
          <ul className="story-dialog-list" aria-label="World list">
            {worlds.map((world) => {
              const isEditing = form.mode === 'edit' && form.worldId === world.world_id
              return (
                <li
                  key={world.world_id}
                  className="story-dialog-row"
                  data-current={isEditing ? '' : undefined}
                >
                  <span className="story-dialog-row-copy">
                    <span className="story-dialog-row-name">{world.name}</span>
                    <span className="story-dialog-row-meta">{world.description || 'No description yet'}</span>
                  </span>
                  <span className="story-dialog-row-actions">
                    <button
                      type="button"
                      className="story-link"
                      onClick={() => onEditWorld(world)}
                      disabled={locked}
                    >
                      Edit
                    </button>
                    <button
                      type="button"
                      className="story-link story-link-danger"
                      onClick={() => onOpenDelete(world)}
                      disabled={locked}
                    >
                      Delete
                    </button>
                  </span>
                </li>
              )
            })}
          </ul>
        ) : (
          <p className="story-dialog-empty">No worlds yet. Create one below, then set adventures in it.</p>
        )}
      </div>
      <hr className="story-rule" />
      <form className="story-dialog-body" onSubmit={onSubmit}>
        <div className="story-dialog-section-head">
          <h3 className="story-label">{form.mode === 'edit' ? 'Edit the world' : 'A new world'}</h3>
          {form.mode === 'edit' ? (
            <button type="button" className="story-link" onClick={onResetForm} disabled={form.pending}>
              New World
            </button>
          ) : null}
        </div>
        <label className="story-dialog-field">
          <span className="story-label">World name</span>
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
        <label className="story-dialog-field">
          <span className="story-label">Description</span>
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
            placeholder="The land, its mood, the trouble brewing…"
            disabled={form.pending}
          />
        </label>
        {form.error ? <p className="story-dialog-error" role="alert">{form.error}</p> : null}
        <footer className="story-dialog-footer">
          <button type="button" className="story-link" onClick={onClose} disabled={locked}>
            Close
          </button>
          <button type="submit" className="story-primary" disabled={form.pending}>
            {form.pending
              ? form.mode === 'edit'
                ? 'Saving...'
                : 'Creating...'
              : form.mode === 'edit'
                ? 'Save'
                : 'Create the world'}
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
      size="narrow"
      closeDisabled={dialog.pending}
      closeLabel="Close delete world"
      dialogRef={dialogRef}
      labelledBy="world-delete-title"
      describedBy="world-delete-description"
      onClose={onClose}
      title="Delete this world"
    >
      <div className="story-dialog-body">
        <p className="story-dialog-subject">{dialog.world.name}</p>
        <p id="world-delete-description" className="story-dialog-note">
          A world nothing uses can be deleted straight away. If adventures are set in it, deleting
          the world and its adventures removes those adventures first.
        </p>
        {dialog.error ? <p className="story-dialog-error" role="alert">{dialog.error}</p> : null}
        <footer className="story-dialog-footer">
          <button type="button" className="story-link" onClick={onClose} disabled={dialog.pending}>
            Cancel
          </button>
          <button
            type="button"
            className="story-link story-link-danger"
            data-autofocus
            onClick={onDelete}
            disabled={dialog.pending}
          >
            {dialog.pending ? 'Deleting...' : 'Delete this world'}
          </button>
          {dialog.canForce ? (
            <button
              type="button"
              className="story-link story-link-danger"
              onClick={onForceDelete}
              disabled={dialog.pending}
            >
              {dialog.pending ? 'Deleting...' : 'Delete the world and its adventures'}
            </button>
          ) : null}
        </footer>
      </div>
    </ModalShell>
  )
}
