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
import {
  CREATE_STEPS,
  STEP_NAMES,
  ancestryFromSelection,
  createPayload,
  emptyDraft,
  isCoreClassName,
  NARRATED_CLASS_NOTE,
  stepComplete,
  stepHint,
  withAncestry,
  withCalling,
  type CreateHeroPayload,
  type CreateStep,
  type HeroDraft,
} from './createHeroModel'
import { StepCalling } from './StepCalling'
import { StepReview } from './StepReview'
import { StepShape } from './StepShape'
import { StepWho } from './StepWho'
import type { CharacterRaceSelection } from '../../types'

// The full selectors are large; they load only when a "More…" overlay opens.
const RaceSelector = lazy(() => import('../../RaceSelector').then((module) => ({ default: module.RaceSelector })))
const ClassSelector = lazy(() => import('../../ClassSelector').then((module) => ({ default: module.ClassSelector })))

type Overlay = 'ancestries' | 'callings'

const FOCUSABLE = 'button:not([disabled]), input:not([disabled]), textarea:not([disabled]), select:not([disabled]), [tabindex]:not([tabindex="-1"])'

type CreateHeroProps = {
  auth: string
  baseUrl: string
  campaignTitle?: string | null
  dialogRef?: RefObject<HTMLElement | null>
  initialDraft?: HeroDraft
  initialStep?: CreateStep
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
 * "More…" away in the full selectors.
 */
export function CreateHero({
  auth,
  baseUrl,
  campaignTitle,
  dialogRef,
  initialDraft,
  initialStep = 'who',
  onCreate,
  onClose,
}: CreateHeroProps) {
  const titleId = useId()
  const [draft, setDraft] = useState<HeroDraft>(() => initialDraft ?? emptyDraft())
  const [step, setStep] = useState<CreateStep>(initialStep)
  const [overlay, setOverlay] = useState<Overlay | null>(null)
  const [confirmingDiscard, setConfirmingDiscard] = useState(false)
  const [pending, setPending] = useState(false)
  const [error, setError] = useState('')
  const headingRef = useRef<HTMLHeadingElement | null>(null)
  const overlayCloseRef = useRef<HTMLButtonElement | null>(null)
  const keepRef = useRef<HTMLButtonElement | null>(null)
  // The overlay or discard question in front of the stage, which Tab stays inside.
  const layerRef = useRef<HTMLDivElement | null>(null)
  // Where focus returns when an overlay or the discard question closes.
  const returnFocusRef = useRef<HTMLElement | null>(null)
  const stepMovedRef = useRef(false)

  const stepIndex = CREATE_STEPS.indexOf(step)
  const reachable = (target: CreateStep) =>
    CREATE_STEPS.slice(0, CREATE_STEPS.indexOf(target)).every((earlier) => stepComplete(draft, earlier))
  const complete = stepComplete(draft, step)
  const hint = stepHint(draft, step)

  useEffect(() => {
    // Moving between steps puts keyboard focus on the new step's heading.
    if (stepMovedRef.current) headingRef.current?.focus()
    stepMovedRef.current = false
  }, [step])

  useEffect(() => {
    // Runs after the stage behind is no longer inert, so focus can land back on it.
    if (overlay) overlayCloseRef.current?.focus()
    else if (confirmingDiscard) keepRef.current?.focus()
    else {
      returnFocusRef.current?.focus()
      returnFocusRef.current = null
    }
  }, [overlay, confirmingDiscard])

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

  const openOverlay = (next: Overlay, trigger: HTMLButtonElement) => {
    returnFocusRef.current = trigger
    setOverlay(next)
  }

  const closeOverlay = () => setOverlay(null)

  const chooseRace = async (selection: CharacterRaceSelection | null) => {
    if (!selection) return
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
      // The stage behind is inert, so the app's dialog trap would wrap focus onto nothing.
      event.stopPropagation()
      const focusable = Array.from(layerRef.current.querySelectorAll<HTMLElement>(FOCUSABLE))
      const [first, last] = [focusable[0], focusable[focusable.length - 1]]
      if (first && document.activeElement === (event.shiftKey ? first : last)) {
        event.preventDefault()
        const wrapTo = event.shiftKey ? last : first
        wrapTo.focus()
      }
      return
    }
    if (event.key !== 'Escape') return
    // Handled here so the app's dialog trap does not close the flow without asking.
    event.preventDefault()
    event.stopPropagation()
    if (overlay) closeOverlay()
    else if (confirmingDiscard) keepShaping()
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
        <div
          ref={layerRef}
          className="story-create-overlay"
          role="dialog"
          aria-modal="true"
          aria-label={overlay === 'ancestries' ? 'More ancestries' : 'More callings'}
        >
          <header className="story-create-overlay-head">
            <div>
              <p className="story-label">{overlay === 'ancestries' ? 'More ancestries' : 'More callings'}</p>
              <p className="story-create-overlay-note">
                {overlay === 'ancestries'
                  ? 'Every playable ancestry, and ones you invent with the Dungeon Master.'
                  : 'The twelve core callings play by the full rules. The rest are narrated.'}
              </p>
            </div>
            <button
              ref={overlayCloseRef}
              type="button"
              className="story-icon-button"
              aria-label={overlay === 'ancestries' ? 'Close more ancestries' : 'Close more callings'}
              onClick={closeOverlay}
            >
              <X size={18} strokeWidth={1.6} />
            </button>
          </header>
          <div className="story-create-overlay-body">
            <Suspense fallback={<p className="story-create-overlay-note">Opening the catalog…</p>}>
              {overlay === 'ancestries' ? (
                <RaceSelector
                  auth={auth}
                  baseUrl={baseUrl}
                  selectedRace={draft.ancestry?.selection.raceName ?? ''}
                  selectedRaceSelection={draft.ancestry?.selection ?? null}
                  selectedSex={draft.sex}
                  onRaceChange={() => undefined}
                  onRaceSelectionChange={(selection) => void chooseRace(selection)}
                  onSexChange={(sex) => update((current) => ({ ...current, sex }))}
                />
              ) : (
                <ClassSelector
                  selectedClass={draft.calling ?? ''}
                  initialView="all"
                  classNote={(classEntry) => (isCoreClassName(classEntry.name) ? null : NARRATED_CLASS_NOTE)}
                  onClassChange={chooseCalling}
                />
              )}
            </Suspense>
          </div>
        </div>
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
