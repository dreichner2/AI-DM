import { X } from 'lucide-react'
import {
  lazy,
  Suspense,
  useEffect,
  useId,
  useRef,
  useState,
  type KeyboardEvent,
  type RefObject,
} from 'react'
import { StoryAtmosphere } from '../StoryAtmosphere'
import { CatalogOverlay } from './CatalogOverlay'
import {
  CREATE_STEPS,
  STEP_NAMES,
  ancestryFromSelection,
  createPayload,
  emptyDraft,
  stepComplete,
  stepHint,
  withAncestry,
  withCalling,
  withRules,
  type CreateHeroPayload,
  type CreateStep,
  type HeroDraft,
} from './createHeroModel'
import { loadCreationRules } from './creationRules'
import { keepTabInside } from './focusLayer'
import { StepCalling } from './StepCalling'
import { StepReview } from './StepReview'
import { StepShape } from './StepShape'
import { StepWho } from './StepWho'
import type { CharacterRaceSelection } from '../../types'

// The catalogs are large; they load only when a "More…" overlay opens.
const AncestryCatalog = lazy(() => import('./AncestryCatalog').then((module) => ({ default: module.AncestryCatalog })))
const CallingCatalog = lazy(() => import('./CallingCatalog').then((module) => ({ default: module.CallingCatalog })))

type Overlay = 'ancestries' | 'callings'
type OverlayState = { kind: Overlay; opener: HTMLButtonElement | null; view?: 'all' | 'own' }

type CreateHeroProps = {
  auth: string
  baseUrl: string
  campaignTitle?: string | null
  dialogRef?: RefObject<HTMLElement | null>
  initialDraft?: HeroDraft
  initialStep?: CreateStep
  /** Opens with a "More…" overlay in front, in the given view (the preview harness). */
  initialOverlay?: Overlay
  initialOverlayView?: 'all' | 'own'
  /** Creates the character and joins the campaign; a rejection's message is shown above the button. */
  onCreate: (payload: CreateHeroPayload) => Promise<void>
  onClose: () => void
}

function errorMessage(error: unknown) {
  return error instanceof Error ? error.message : String(error)
}

/**
 * Making a hero in four calm steps (Who, Calling, Shape, Review) over the hearth
 * atmosphere. It leads with what the rules engine supports: the nine
 * recommended ancestries and the twelve core classes, with everything else one
 * "More…" away in the catalogs.
 */
export function CreateHero({
  auth,
  baseUrl,
  campaignTitle,
  dialogRef,
  initialDraft,
  initialStep = 'who',
  initialOverlay,
  initialOverlayView,
  onCreate,
  onClose,
}: CreateHeroProps) {
  const titleId = useId()
  const [draft, setDraft] = useState<HeroDraft>(() => initialDraft ?? emptyDraft())
  const [step, setStep] = useState<CreateStep>(initialStep)
  const [overlay, setOverlay] = useState<OverlayState | null>(() =>
    initialOverlay ? { kind: initialOverlay, opener: null, view: initialOverlayView } : null,
  )
  const [confirmingDiscard, setConfirmingDiscard] = useState(false)
  const [pending, setPending] = useState(false)
  const [error, setError] = useState('')
  const headingRef = useRef<HTMLHeadingElement | null>(null)
  const keepRef = useRef<HTMLButtonElement | null>(null)
  // The discard question in front of the stage, which Tab stays inside.
  const layerRef = useRef<HTMLDivElement | null>(null)
  // Where focus returns when the discard question closes.
  const returnFocusRef = useRef<HTMLElement | null>(null)
  const stepMovedRef = useRef(false)

  const stepIndex = CREATE_STEPS.indexOf(step)
  const reachable = (target: CreateStep) =>
    CREATE_STEPS.slice(0, CREATE_STEPS.indexOf(target)).every((earlier) => stepComplete(draft, earlier))
  const complete = stepComplete(draft, step)
  const hint = stepHint(draft, step)

  useEffect(() => {
    // The server's rules replace the built-in tables when they arrive; offline the tables stay.
    let cancelled = false
    void loadCreationRules(baseUrl, auth).then((rules) => {
      if (!cancelled && rules) setDraft((current) => withRules(current, rules))
    })
    return () => {
      cancelled = true
    }
  }, [baseUrl, auth])

  useEffect(() => {
    // Moving between steps puts keyboard focus on the new step's heading.
    if (stepMovedRef.current) headingRef.current?.focus()
    stepMovedRef.current = false
  }, [step])

  useEffect(() => {
    // Runs after the stage behind is no longer inert, so focus can land back on it.
    if (confirmingDiscard) keepRef.current?.focus()
    else {
      returnFocusRef.current?.focus()
      returnFocusRef.current = null
    }
  }, [confirmingDiscard])

  const update = (change: (current: HeroDraft) => HeroDraft) => {
    setDraft(change)
    setError('')
  }

  const goTo = (target: CreateStep) => {
    if (pending || target === step || !reachable(target)) return
    stepMovedRef.current = true
    setStep(target)
  }

  const continueOn = () => {
    if (!complete || stepIndex >= CREATE_STEPS.length - 1) return
    goTo(CREATE_STEPS[stepIndex + 1])
  }

  const askToDiscard = () => {
    if (pending) return
    returnFocusRef.current = document.activeElement instanceof HTMLElement ? document.activeElement : null
    setConfirmingDiscard(true)
  }

  const keepShaping = () => setConfirmingDiscard(false)

  const back = () => {
    if (stepIndex === 0) {
      askToDiscard()
      return
    }
    stepMovedRef.current = true
    setStep(CREATE_STEPS[stepIndex - 1])
  }

  const openOverlay = (kind: Overlay, opener: HTMLButtonElement) => setOverlay({ kind, opener })

  const closeOverlay = () => setOverlay(null)

  const chooseRace = async (selection: CharacterRaceSelection) => {
    const { playableRaceFromValue } = await import('../../raceCatalog')
    const catalogRace = selection.source === 'custom' ? null : playableRaceFromValue(selection.raceId)
    update((current) => withAncestry(current, ancestryFromSelection(selection, catalogRace)))
    closeOverlay()
  }

  const chooseCalling = (calling: string) => {
    update((current) => withCalling(current, calling))
    closeOverlay()
  }

  const begin = async () => {
    if (pending || !stepComplete(draft, 'review')) return
    setPending(true)
    setError('')
    try {
      await onCreate(createPayload(draft))
    } catch (failure) {
      setError(errorMessage(failure))
      setPending(false)
    }
  }

  const onKeyDown = (event: KeyboardEvent<HTMLElement>) => {
    if (event.key === 'Tab' && layerRef.current) {
      keepTabInside(event, layerRef.current)
      return
    }
    if (event.key !== 'Escape') return
    // Handled here so the app's dialog trap does not close the flow without asking.
    event.preventDefault()
    event.stopPropagation()
    if (confirmingDiscard) keepShaping()
    else askToDiscard()
  }

  const name = draft.name.trim()
  const covered = Boolean(overlay || confirmingDiscard)

  return (
    <section
      ref={dialogRef}
      className="story-shell story-create"
      data-mood="hearth"
      role="dialog"
      aria-modal="true"
      aria-labelledby={titleId}
      onKeyDown={onKeyDown}
    >
      <StoryAtmosphere mood="hearth" viewerHp={null} />
      <div className="story-create-stage" inert={covered}>
        <header className="story-create-header">
          <p id={titleId} className="story-label story-create-title">
            A new hero
            {campaignTitle ? (
              <>
                {' '}
                <span>{`· ${campaignTitle}`}</span>
              </>
            ) : null}
          </p>
          <nav aria-label="Hero creation steps">
            <ol className="story-create-steps">
              {CREATE_STEPS.map((target) => (
                <li key={target}>
                  <button
                    type="button"
                    className="story-link"
                    aria-current={target === step ? 'step' : undefined}
                    disabled={target !== step && (pending || !reachable(target))}
                    onClick={() => goTo(target)}
                  >
                    {STEP_NAMES[target]}
                  </button>
                </li>
              ))}
            </ol>
          </nav>
          <button type="button" className="story-icon-button" aria-label="Leave hero creation" onClick={askToDiscard}>
            <X size={18} strokeWidth={1.6} />
          </button>
        </header>

        <div className="story-create-body" key={step}>
          {step === 'who' ? (
            <StepWho
              draft={draft}
              headingRef={headingRef}
              onChange={update}
              onContinue={continueOn}
              onMoreAncestries={(trigger) => openOverlay('ancestries', trigger)}
            />
          ) : step === 'calling' ? (
            <StepCalling
              draft={draft}
              headingRef={headingRef}
              onChange={update}
              onMoreCallings={(trigger) => openOverlay('callings', trigger)}
            />
          ) : step === 'shape' ? (
            <StepShape draft={draft} headingRef={headingRef} onChange={update} />
          ) : (
            <StepReview draft={draft} headingRef={headingRef} />
          )}
        </div>

        <footer className="story-create-footer">
          <button type="button" className="story-link story-create-back" onClick={back} disabled={pending}>
            Back
          </button>
          <div className="story-create-next">
            {error ? (
              <p className="story-create-error" role="alert">
                {error}
              </p>
            ) : (
              <p className="story-create-hint" aria-live="polite">
                {hint}
              </p>
            )}
            {step === 'review' ? (
              <button
                type="button"
                className="story-primary"
                disabled={!complete || pending}
                aria-busy={pending || undefined}
                onClick={() => void begin()}
              >
                {pending ? 'Gathering your things…' : `Begin as ${name}`}
              </button>
            ) : (
              <button type="button" className="story-primary" disabled={!complete} onClick={continueOn}>
                Continue
              </button>
            )}
          </div>
        </footer>
      </div>

      {overlay ? (
        <CatalogOverlay
          label={overlay.kind === 'ancestries' ? 'More ancestries' : 'More callings'}
          note={
            overlay.kind === 'ancestries'
              ? 'Every playable ancestry, and ones you invent with the Dungeon Master.'
              : 'The twelve core callings play by the full rules. The rest are narrated.'
          }
          closeLabel={overlay.kind === 'ancestries' ? 'Close more ancestries' : 'Close more callings'}
          opener={overlay.opener}
          onClose={closeOverlay}
        >
          <Suspense fallback={<p className="story-create-overlay-note">Opening the catalog…</p>}>
            {overlay.kind === 'ancestries' ? (
              <AncestryCatalog
                auth={auth}
                baseUrl={baseUrl}
                current={draft.ancestry?.selection ?? null}
                sex={draft.sex}
                initialView={overlay.view}
                onChoose={(selection) => void chooseRace(selection)}
                onSexChange={(sex) => update((current) => ({ ...current, sex }))}
              />
            ) : (
              <CallingCatalog
                current={draft.calling}
                rules={draft.rules}
                initialView={overlay.view}
                onChoose={chooseCalling}
              />
            )}
          </Suspense>
        </CatalogOverlay>
      ) : null}

      {confirmingDiscard ? (
        <div ref={layerRef} className="story-create-confirm-scrim">
          <div
            className="story-create-confirm"
            role="alertdialog"
            aria-modal="true"
            aria-labelledby={`${titleId}-discard`}
            aria-describedby={`${titleId}-discard-detail`}
          >
            <p id={`${titleId}-discard`} className="story-create-confirm-title">
              Discard this hero?
            </p>
            <p id={`${titleId}-discard-detail`} className="story-create-confirm-detail">
              {name ? `${name} and every choice so far will be lost.` : 'Every choice so far will be lost.'}
            </p>
            <div className="story-create-confirm-actions">
              <button ref={keepRef} type="button" className="story-primary" onClick={keepShaping}>
                Keep shaping
              </button>
              <button type="button" className="story-link story-create-discard" onClick={onClose}>
                Discard
              </button>
            </div>
          </div>
        </div>
      ) : null}
    </section>
  )
}
