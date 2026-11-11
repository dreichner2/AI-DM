import {
  type Dispatch,
  type FormEvent,
  type RefObject,
  type SetStateAction,
} from 'react'
import { X } from 'lucide-react'
import { BackgroundSelector } from './BackgroundSelector'
import { ClassSelector } from './ClassSelector'
import { ModalShell } from './ModalShell'
import { RaceSelector } from './RaceSelector'
import type { PlayerEditDialogState } from './usePlayerProfileActions'

type PlayerEditDialogProps = {
  auth: string
  baseUrl: string
  dialog: NonNullable<PlayerEditDialogState>
  dialogRef: RefObject<HTMLElement | null>
  onClose: () => void
  onSubmit: (event: FormEvent<HTMLFormElement>) => void
  setDialog: Dispatch<SetStateAction<PlayerEditDialogState>>
}

export function PlayerEditDialog({
  auth,
  baseUrl,
  dialog,
  dialogRef,
  onClose,
  onSubmit,
  setDialog,
}: PlayerEditDialogProps) {
  return (
    <ModalShell
      className="campaign-dialog player-edit-dialog"
      closeDisabled={dialog.pending}
      dialogRef={dialogRef}
      labelledBy="player-edit-title"
      onClose={onClose}
    >
        <header>
          <div>
            <span>Character</span>
            <h2 id="player-edit-title">Edit Character</h2>
          </div>
          <button
            type="button"
            aria-label="Close character editor"
            onClick={onClose}
            disabled={dialog.pending}
          >
            <X size={18} />
          </button>
        </header>
        <form onSubmit={onSubmit}>
          <label>
            Character Name
            <input
              autoFocus
              data-autofocus
              value={dialog.characterName}
              onChange={(event) =>
                setDialog((current) =>
                  current ? { ...current, characterName: event.target.value } : current,
                )
              }
            />
          </label>
          <RaceSelector
              auth={auth}
              baseUrl={baseUrl}
              selectedRace={dialog.race}
              selectedRaceSelection={dialog.raceSelection}
              selectedSex={dialog.sex}
              pending={dialog.pending}
              onRaceChange={(race) =>
                setDialog((current) => (current ? { ...current, race } : current))
              }
              onRaceSelectionChange={(raceSelection) =>
                setDialog((current) => (current ? { ...current, raceSelection } : current))
              }
              onSexChange={(sex) =>
                setDialog((current) => (current ? { ...current, sex } : current))
              }
          />
          <ClassSelector
              selectedClass={dialog.charClass}
              pending={dialog.pending}
              onClassChange={(charClass) =>
                setDialog((current) => (current ? { ...current, charClass } : current))
              }
          />
          <BackgroundSelector
            selectedBackgroundId={dialog.backgroundId}
            pending={dialog.pending}
            onBackgroundChange={(backgroundId) =>
              setDialog((current) => (current ? { ...current, backgroundId } : current))
            }
          />
          <div className="dialog-grid two character-level-grid">
            <label>
              Level
              <input
                type="number"
                min={1}
                max={20}
                value={dialog.level}
                onChange={(event) =>
                  setDialog((current) => (current ? { ...current, level: event.target.value } : current))
                }
              />
            </label>
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
            <button type="submit" disabled={dialog.pending}>
              {dialog.pending ? 'Saving...' : 'Save Character'}
            </button>
          </footer>
        </form>
    </ModalShell>
  )
}
