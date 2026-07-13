import {
  type Dispatch,
  type FormEvent,
  type RefObject,
  type SetStateAction,
} from 'react'
import { X } from 'lucide-react'
import { ClassSelector } from './ClassSelector'
import {
  POINT_BUY_ABILITIES,
  POINT_BUY_BUDGET,
  abilityModifier,
  clampPointBuyScore,
  pointBuySpent,
} from './characterStats'
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
  const pointBuySpentTotal = pointBuySpent(dialog.abilityScores)
  const pointBuyRemaining = POINT_BUY_BUDGET - pointBuySpentTotal
  const title = dialog.mode === 'create' ? 'Create Character' : 'Edit Character'

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
            <h2 id="player-edit-title">{title}</h2>
          </div>
          <button
            type="button"
            aria-label={dialog.mode === 'create' ? 'Close character creator' : 'Close character editor'}
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
          {dialog.mode === 'create' ? (
            <section className="point-buy-panel" aria-label="Ability score point buy">
              <div className="point-buy-summary">
                <strong>Ability Scores</strong>
                <span className={pointBuyRemaining < 0 ? 'over-budget' : ''}>
                  {pointBuyRemaining} / {POINT_BUY_BUDGET} left
                </span>
              </div>
              <div className="point-buy-grid">
                {POINT_BUY_ABILITIES.map((ability) => {
                  const score = dialog.abilityScores[ability.key]
                  return (
                    <label key={ability.key}>
                      <span>
                        {ability.label}
                        <small>{abilityModifier(score)}</small>
                      </span>
                      <input
                        type="number"
                        min={8}
                        max={15}
                        value={score}
                        aria-label={ability.name}
                        onChange={(event) =>
                          setDialog((current) =>
                            current
                              ? {
                                  ...current,
                                  abilityScores: {
                                    ...current.abilityScores,
                                    [ability.key]: clampPointBuyScore(Number(event.target.value)),
                                  },
                                }
                              : current,
                          )
                        }
                      />
                    </label>
                  )
                })}
              </div>
            </section>
          ) : null}
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
              {dialog.pending
                ? dialog.mode === 'create'
                  ? 'Creating...'
                  : 'Saving...'
                : dialog.mode === 'create'
                  ? 'Create Character'
                  : 'Save Character'}
            </button>
          </footer>
        </form>
    </ModalShell>
  )
}
