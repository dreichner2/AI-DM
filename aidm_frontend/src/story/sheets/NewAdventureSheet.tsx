import {
  useEffect,
  useRef,
  useState,
  type Dispatch,
  type RefObject,
  type SetStateAction,
} from 'react'
import { flushSync } from 'react-dom'
import { ModalShell } from '../../ModalShell'
import type { World } from '../../types'
import type { CampaignPackExample, CreateCampaignForm } from '../../useCampaignActions'
import { useLatest } from '../../useLatest'

type NewAdventureSheetProps = {
  dialogRef: RefObject<HTMLElement | null>
  error: string
  form: CreateCampaignForm
  onClose: () => void
  /** Creates the adventure from the current form (useCampaignActions.submitCreateCampaign). */
  onSubmit: () => void
  packOptions: CampaignPackExample[]
  packOptionsPending: boolean
  pending: boolean
  setForm: Dispatch<SetStateAction<CreateCampaignForm>>
  worlds: World[]
}

/**
 * New adventure in two steps: choose a beginning (a bundled adventure starts
 * at once), or take a blank page and name it.
 */
export function NewAdventureSheet({
  dialogRef,
  error,
  form,
  onClose,
  onSubmit,
  packOptions,
  packOptionsPending,
  pending,
  setForm,
  worlds,
}: NewAdventureSheetProps) {
  const [step, setStep] = useState<'beginning' | 'details'>('beginning')
  const [beginningPackId, setBeginningPackId] = useState<string | null>(null)
  const submitRef = useLatest(onSubmit)
  const blankPageRef = useRef<HTMLButtonElement | null>(null)
  const returnedRef = useRef(false)

  useEffect(() => {
    if (step === 'beginning' && returnedRef.current) blankPageRef.current?.focus()
  }, [step])

  const beginPack = (pack: CampaignPackExample) => {
    // The form must hold the pack before the submit handler reads it.
    flushSync(() => {
      setBeginningPackId(pack.pack_id)
      setForm((current) => ({
        ...current,
        packId: pack.pack_id,
        title: pack.title,
        description: pack.description,
        worldId: '',
        worldName: '',
      }))
    })
    submitRef.current()
  }

  const beginBlankPage = () => {
    setForm((current) => {
      // A bundled adventure that could not begin leaves its title behind; a blank page starts empty.
      const title = current.packId ? '' : current.title
      const description = current.packId ? '' : current.description
      return { ...current, packId: '', title, description, worldId: '', worldName: title }
    })
    setStep('details')
  }

  const newWorld = !form.worldId
  const changeName = (title: string) =>
    setForm((current) => ({ ...current, title, worldName: current.worldId ? '' : title }))
  const chooseWorld = (worldId: string) =>
    setForm((current) => ({ ...current, worldId, worldName: worldId ? '' : current.title }))

  return (
    <ModalShell
      closeDisabled={pending}
      closeLabel="Close create campaign"
      dialogRef={dialogRef}
      labelledBy="new-adventure-title"
      onClose={onClose}
      title="New adventure"
    >
      {step === 'beginning' ? (
        <div className="story-dialog-body">
          <h3 className="story-label">Choose a beginning</h3>
          <ul className="story-dialog-list" aria-label="Beginnings">
            {packOptionsPending && !packOptions.length ? (
              <li className="story-dialog-row">
                <span className="story-dialog-row-copy">
                  <span className="story-dialog-row-meta" role="status">
                    Gathering the ready-made adventures
                    <span className="story-dots" aria-hidden="true">
                      <i />
                      <i />
                      <i />
                    </span>
                  </span>
                </span>
              </li>
            ) : null}
            {packOptions.map((pack, index) => (
              <li key={pack.pack_id} className="story-dialog-row">
                <span className="story-dialog-row-copy">
                  <span className="story-dialog-row-name">{pack.title}</span>
                  <span className="story-dialog-row-meta story-dialog-row-summary">
                    {pack.short_description || pack.description}
                  </span>
                </span>
                <span className="story-dialog-row-actions">
                  <button
                    type="button"
                    className="story-link"
                    aria-label={`Begin ${pack.title}`}
                    data-autofocus={index === 0 ? true : undefined}
                    disabled={pending}
                    onClick={() => beginPack(pack)}
                  >
                    {pending && beginningPackId === pack.pack_id ? 'Beginning…' : 'Begin'}
                  </button>
                </span>
              </li>
            ))}
            <li className="story-dialog-row">
              <span className="story-dialog-row-copy">
                <span className="story-dialog-row-name">A blank page</span>
                <span className="story-dialog-row-meta">You and the Dungeon Master make it up.</span>
              </span>
              <span className="story-dialog-row-actions">
                <button
                  ref={blankPageRef}
                  type="button"
                  className="story-link"
                  aria-label="Begin with a blank page"
                  data-autofocus={packOptions.length ? undefined : true}
                  disabled={pending}
                  onClick={beginBlankPage}
                >
                  Begin
                </button>
              </span>
            </li>
          </ul>
          {error ? <p className="story-dialog-error" role="alert">{error}</p> : null}
        </div>
      ) : (
        <form
          className="story-dialog-body"
          onSubmit={(event) => {
            event.preventDefault()
            onSubmit()
          }}
        >
          <label className="story-dialog-field">
            <span className="story-label">Name</span>
            <input
              autoFocus
              required
              value={form.title}
              onChange={(event) => changeName(event.target.value)}
              placeholder="Ashes Beyond the Gate"
              disabled={pending}
            />
          </label>
          <label className="story-dialog-field">
            <span className="story-label">What is it about?</span>
            <textarea
              value={form.description}
              onChange={(event) => setForm((current) => ({ ...current, description: event.target.value }))}
              rows={3}
              placeholder="A premise, a goal, a mood — or leave it to the Dungeon Master."
              disabled={pending}
            />
          </label>
          {worlds.length ? (
            <section className="story-dialog-section" aria-labelledby="new-adventure-world">
              <h3 id="new-adventure-world" className="story-label">World</h3>
              <div className="story-dialog-options" role="radiogroup" aria-labelledby="new-adventure-world">
                <label className="story-dialog-option">
                  <input
                    type="radio"
                    name="new-adventure-world"
                    checked={newWorld}
                    disabled={pending}
                    onChange={() => chooseWorld('')}
                  />
                  <span>
                    A new world
                    <small>Named after the adventure.</small>
                  </span>
                </label>
                {worlds.map((world) => (
                  <label key={world.world_id} className="story-dialog-option">
                    <input
                      type="radio"
                      name="new-adventure-world"
                      checked={form.worldId === String(world.world_id)}
                      disabled={pending}
                      onChange={() => chooseWorld(String(world.world_id))}
                    />
                    <span>
                      {world.name}
                      {world.description ? <small>{world.description}</small> : null}
                    </span>
                  </label>
                ))}
              </div>
            </section>
          ) : null}
          {error ? <p className="story-dialog-error" role="alert">{error}</p> : null}
          <footer className="story-dialog-footer">
            <button
              type="button"
              className="story-link"
              disabled={pending}
              onClick={() => {
                returnedRef.current = true
                setStep('beginning')
              }}
            >
              Back
            </button>
            <button type="submit" className="story-primary" disabled={pending || !form.title.trim()}>
              {pending ? 'Beginning…' : 'Begin the adventure'}
            </button>
          </footer>
        </form>
      )}
    </ModalShell>
  )
}
