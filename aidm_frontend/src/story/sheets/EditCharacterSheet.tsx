import { Minus, Plus } from 'lucide-react'
import {
  lazy,
  Suspense,
  useEffect,
  useId,
  useState,
  type Dispatch,
  type FormEvent,
  type RefObject,
  type SetStateAction,
} from 'react'
import { backgroundFromValue, PLAYABLE_BACKGROUNDS } from '../../backgroundCatalog'
import { customRacePortraitKey } from '../../customRaces'
import { ModalShell } from '../../ModalShell'
import { profileIconSrcForCharacter, type SexKey } from '../../profileIcons'
import { playableRaceFromValue, raceSelectionFromPlayableRace } from '../../raceCatalog'
import type { CharacterRaceSelection } from '../../types'
import type { PlayerEditDialogState } from '../../usePlayerProfileActions'
import { BackgroundChoices } from '../create/BackgroundChoices'
import { CatalogOverlay } from '../create/CatalogOverlay'
import { ancestryById, callingName } from '../create/createHeroModel'
import { HeroTiesFields } from '../create/HeroTiesFields'
import { heroTiesFromSheet, tiesDraft, tiesPayload, tiesProblem } from '../heroTies'
import { FALLBACK_RULES, loadCreationRules, type CreationRules } from '../create/creationRules'
import { PortraitToggle } from '../create/PortraitToggle'
import '../../styles/story-edit-character.css'

// The catalogs load only when a Change opens one.
const AncestryCatalog = lazy(() => import('../create/AncestryCatalog').then((module) => ({ default: module.AncestryCatalog })))
const CallingCatalog = lazy(() => import('../create/CallingCatalog').then((module) => ({ default: module.CallingCatalog })))

const MIN_LEVEL = 1
const MAX_LEVEL = 20

/** The ties as edited, else as the hero's sheet holds them. */
function tiesDraftOf(dialog: NonNullable<PlayerEditDialogState>) {
  return dialog.ties?.draft ?? tiesDraft(heroTiesFromSheet(dialog.sheet))
}

type EditDialog = NonNullable<PlayerEditDialogState>
type Overlay = { kind: 'ancestries' | 'callings'; opener: HTMLButtonElement }

type EditCharacterSheetProps = {
  auth: string
  baseUrl: string
  dialog: EditDialog
  dialogRef: RefObject<HTMLElement | null>
  onClose: () => void
  onSubmit: (event: FormEvent<HTMLFormElement>) => void
  setDialog: Dispatch<SetStateAction<PlayerEditDialogState>>
}

/**
 * The Story view's character editor: the classic editor's fields (name,
 * ancestry, calling, background, portrait, level) and the hero's own threads,
 * its state, validation and save, as one quiet sheet. Ancestry and calling open the Story catalogs over
 * the whole window; the background list opens under its row.
 */
export function EditCharacterSheet({
  auth,
  baseUrl,
  dialog,
  dialogRef,
  onClose,
  onSubmit,
  setDialog,
}: EditCharacterSheetProps) {
  const titleId = useId()
  const backgroundsId = useId()
  const levelId = useId()
  const [overlay, setOverlay] = useState<Overlay | null>(null)
  const [backgroundsOpen, setBackgroundsOpen] = useState(false)
  const [rules, setRules] = useState<CreationRules>(FALLBACK_RULES)
  const { pending } = dialog

  useEffect(() => {
    // The calling catalog shows the server's rules for the core callings once they arrive.
    let cancelled = false
    void loadCreationRules(baseUrl, auth).then((loaded) => {
      if (!cancelled && loaded) setRules(loaded)
    })
    return () => {
      cancelled = true
    }
  }, [baseUrl, auth])

  const update = (change: Partial<EditDialog>) => setDialog((current) => (current ? { ...current, ...change } : current))

  const sex: SexKey = dialog.sex === 'female' ? 'female' : 'male'
  const selection = dialog.raceSelection
  const ancestryName = selection?.raceName || dialog.race
  const custom = selection?.source === 'custom'
  const playable = custom ? null : playableRaceFromValue(selection?.raceId || dialog.race)
  const current = selection ?? (playable ? raceSelectionFromPlayableRace(playable) : null)
  const portraitRace = custom ? (customRacePortraitKey(selection.customRaceDefinition) ?? ancestryName) : (playable?.key ?? ancestryName)
  const portrait = profileIconSrcForCharacter({ race: portraitRace, sex })
  const background = backgroundFromValue(dialog.backgroundId)
  const level = Number(dialog.level)
  const levelKnown = Number.isInteger(level)

  const stepLevel = (delta: 1 | -1) => {
    const next = Math.min(MAX_LEVEL, Math.max(MIN_LEVEL, (levelKnown ? level : MIN_LEVEL) + delta))
    update({ level: String(next) })
  }

  const chooseAncestry = (chosen: CharacterRaceSelection) => {
    update({ raceSelection: chosen, race: chosen.raceName })
    setOverlay(null)
  }

  const chooseCalling = (charClass: string) => {
    update({ charClass })
    setOverlay(null)
  }

  return (
    <>
      <ModalShell
        className="story-edit-character"
        closeDisabled={pending}
        closeLabel="Close character editor"
        dialogRef={dialogRef}
        labelledBy={titleId}
        busy={pending}
        onClose={onClose}
        title="Edit character"
      >
        <form className="story-dialog-body" inert={overlay ? true : undefined} onSubmit={onSubmit}>
          <div className="story-edit-hero">
            <span className="story-edit-portrait" aria-hidden="true">
              {portrait ? <img src={portrait} alt="" draggable={false} /> : <span>{(ancestryName || dialog.characterName).slice(0, 1)}</span>}
            </span>
            <label className="story-dialog-field story-edit-name">
              <span className="story-label">Name</span>
              <input
                data-autofocus
                autoComplete="off"
                spellCheck={false}
                value={dialog.characterName}
                disabled={pending}
                onChange={(event) => update({ characterName: event.target.value })}
              />
            </label>
          </div>

          <dl className="story-edit-rows">
            <div className="story-edit-row">
              <dt className="story-label">Ancestry</dt>
              <dd className="story-edit-value">{ancestryName || 'None yet'}</dd>
              <dd>
                <button
                  type="button"
                  className="story-link"
                  aria-label="Change ancestry"
                  disabled={pending}
                  onClick={(event) => setOverlay({ kind: 'ancestries', opener: event.currentTarget })}
                >
                  Change
                </button>
              </dd>
            </div>
            <div className="story-edit-row">
              <dt className="story-label">Calling</dt>
              <dd className="story-edit-value">{dialog.charClass.trim() ? callingName(dialog.charClass) : 'None yet'}</dd>
              <dd>
                <button
                  type="button"
                  className="story-link"
                  aria-label="Change calling"
                  disabled={pending}
                  onClick={(event) => setOverlay({ kind: 'callings', opener: event.currentTarget })}
                >
                  Change
                </button>
              </dd>
            </div>
            <div className="story-edit-row">
              <dt className="story-label">Background</dt>
              <dd className="story-edit-value">{background?.name ?? 'None yet'}</dd>
              <dd>
                <button
                  type="button"
                  className="story-link"
                  aria-label="Change background"
                  aria-expanded={backgroundsOpen}
                  aria-controls={backgroundsId}
                  disabled={pending}
                  onClick={() => setBackgroundsOpen((open) => !open)}
                >
                  Change
                </button>
              </dd>
              {backgroundsOpen ? (
                <dd id={backgroundsId} className="story-edit-row-more">
                  <BackgroundChoices
                    options={PLAYABLE_BACKGROUNDS}
                    chosenId={background?.id}
                    name="story-edit-background"
                    disabled={pending}
                    onChoose={(backgroundId) => update({ backgroundId })}
                  />
                </dd>
              ) : null}
            </div>
            <div className="story-edit-row">
              <dt className="story-label">Portrait</dt>
              <dd className="story-edit-value">
                <PortraitToggle sex={sex} labelled={false} disabled={pending} onChange={(next) => update({ sex: next })} />
              </dd>
            </div>
          </dl>

          <div className="story-edit-stats">
            <div className="story-edit-stat">
              <input
                id={levelId}
                type="number"
                inputMode="numeric"
                min={MIN_LEVEL}
                max={MAX_LEVEL}
                value={dialog.level}
                disabled={pending}
                onChange={(event) => update({ level: event.target.value })}
              />
              <label htmlFor={levelId} className="story-label">
                Level
              </label>
              <span className="story-edit-stepper">
                <button
                  type="button"
                  aria-label="Lower level"
                  disabled={pending || (levelKnown && level <= MIN_LEVEL)}
                  onClick={() => stepLevel(-1)}
                >
                  <Minus size={14} strokeWidth={1.8} aria-hidden="true" />
                </button>
                <button
                  type="button"
                  aria-label="Raise level"
                  disabled={pending || (levelKnown && level >= MAX_LEVEL)}
                  onClick={() => stepLevel(1)}
                >
                  <Plus size={14} strokeWidth={1.8} aria-hidden="true" />
                </button>
              </span>
            </div>
          </div>

          <HeroTiesFields
            draft={tiesDraftOf(dialog)}
            heroName={dialog.characterName}
            ancestry={ancestryById(current?.raceId)}
            backgroundId={background?.id ?? null}
            disabled={pending}
            onChange={(change) =>
              setDialog((latest) => {
                if (!latest) return latest
                const draft = { ...tiesDraftOf(latest), ...change }
                return { ...latest, ties: { draft, payload: tiesPayload(draft), problem: tiesProblem(draft) } }
              })
            }
          />

          {dialog.error ? (
            <p className="story-dialog-error" role="alert">
              {dialog.error}
            </p>
          ) : null}

          <footer className="story-dialog-footer">
            <button type="button" className="story-link" disabled={pending} onClick={onClose}>
              Cancel
            </button>
            <button type="submit" className="story-primary" disabled={pending} aria-busy={pending || undefined}>
              {pending ? 'Saving…' : 'Save'}
            </button>
          </footer>
        </form>
      </ModalShell>

      {overlay ? (
        <CatalogOverlay
          fullWindow
          label={overlay.kind === 'ancestries' ? 'Change ancestry' : 'Change calling'}
          note={
            overlay.kind === 'ancestries'
              ? 'Every playable ancestry, and ones you invent with the Dungeon Master.'
              : 'The twelve core callings play by the full rules. The rest are narrated.'
          }
          closeLabel={overlay.kind === 'ancestries' ? 'Close ancestries' : 'Close callings'}
          opener={overlay.opener}
          onClose={() => setOverlay(null)}
        >
          <Suspense fallback={<p className="story-create-overlay-note">Opening the catalog…</p>}>
            {overlay.kind === 'ancestries' ? (
              <AncestryCatalog
                auth={auth}
                baseUrl={baseUrl}
                current={current}
                sex={sex}
                onChoose={chooseAncestry}
                onSexChange={(next) => update({ sex: next })}
              />
            ) : (
              <CallingCatalog current={dialog.charClass} rules={rules} onChoose={chooseCalling} />
            )}
          </Suspense>
        </CatalogOverlay>
      ) : null}
    </>
  )
}
