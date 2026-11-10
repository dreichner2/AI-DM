import { useCallback, useId, useState, type CSSProperties } from 'react'
import { EmberMark } from './story/glyphs'
import { StoryAtmosphere } from './story/StoryAtmosphere'
import { TitleHeroes, type TitleHero, type TitleHeroesStatus } from './story/TitleHeroes'

/** The most recently played adventure, for the "Continue your adventure" block. */
export type TitleResume = {
  title: string
  chapterNumber: number | null
  scene: string | null
  lastPlayedAt: string | null
  recap: string | null
}

export type TitleScreenProps = {
  pending: boolean
  accountReady: boolean
  heroes: TitleHero[]
  heroesStatus: TitleHeroesStatus
  selectedHeroId: string | null
  onSelectHero: (heroId: string) => void
  resume: TitleResume | null
  /** No AI provider is active, so the scripted narrator runs. */
  safeMode: boolean
  onPlayNow: () => void
  onContinue: () => void
  onCreateCampaign: () => void
  onOpenAdventures: () => void
  onLogIn: () => void
  onCreateAccount: () => void
}

// The server's default ready-made hero; named when the hero list cannot be loaded.
const DEFAULT_HERO_NAME = 'Arden Vale'

const MINUTE = 60_000
const HOUR = 60 * MINUTE
const DAY = 24 * HOUR

function countOf(value: number, unit: string) {
  return `${value} ${unit}${value === 1 ? '' : 's'}`
}

function playedAgo(timestamp: string | null, now: number) {
  if (!timestamp) return null
  const time = Date.parse(timestamp)
  if (!Number.isFinite(time)) return null
  const elapsed = Math.max(0, now - time)
  if (elapsed < MINUTE) return 'played just now'
  if (elapsed < HOUR) return `played ${countOf(Math.floor(elapsed / MINUTE), 'minute')} ago`
  if (elapsed < DAY) return `played ${countOf(Math.floor(elapsed / HOUR), 'hour')} ago`
  const days = Math.floor(elapsed / DAY)
  if (days === 1) return 'played yesterday'
  if (days < 7) return `played ${days} days ago`
  if (days < 30) return `played ${countOf(Math.floor(days / 7), 'week')} ago`
  if (days < 365) return `played ${countOf(Math.floor(days / 30), 'month')} ago`
  return `played ${countOf(Math.floor(days / 365), 'year')} ago`
}

/** Entrance order: each step rises 60 ms after the one before it. */
function rise(step: number) {
  return { '--rise': step } as CSSProperties
}

/** Hands out consecutive entrance steps from `first`. */
function riseFrom(first: number) {
  let step = first
  return () => rise(step++)
}

export function TitleScreen({
  pending,
  accountReady,
  heroes,
  heroesStatus,
  selectedHeroId,
  onSelectHero,
  resume,
  safeMode,
  onPlayNow,
  onContinue,
  onCreateCampaign,
  onOpenAdventures,
  onLogIn,
  onCreateAccount,
}: TitleScreenProps) {
  const idPrefix = useId()
  const [now] = useState(Date.now)
  const [readyMadeOpen, setReadyMadeOpen] = useState(false)
  const returning = resume !== null
  const showReadyMade = !returning || readyMadeOpen
  const readyMadeId = `${idPrefix}-ready-made`

  // Bring the ready-made block into view when a returning player opens it below their adventure.
  const revealReadyMade = useCallback((node: HTMLElement | null) => {
    node?.scrollIntoView?.({ block: 'nearest', behavior: 'smooth' })
  }, [])

  const heroName =
    heroesStatus === 'ready'
      ? heroes.find((hero) => hero.id === selectedHeroId)?.name ?? null
      : heroesStatus === 'failed'
        ? DEFAULT_HERO_NAME
        : null

  const resumeMeta = resume
    ? [
        resume.chapterNumber ? `Chapter ${resume.chapterNumber}` : null,
        resume.scene,
        playedAgo(resume.lastPlayedAt, now),
      ]
        .filter(Boolean)
        .join(' · ')
    : ''

  const continueStep = riseFrom(3)
  // A block revealed later starts its own entrance instead of continuing the page's.
  const readyMadeStep = riseFrom(returning ? 0 : 3)

  return (
    <section className="title-screen story-shell" data-mood="hearth" aria-labelledby="title-screen-heading">
      <StoryAtmosphere mood="hearth" viewerHp={null} />
      <div className="story-title-stage">
        <div className="story-title-content">
          <header className="story-title-brand">
            <EmberMark size={48} className="story-title-ember story-title-rise" />
            <h1 id="title-screen-heading" className="story-title-wordmark story-title-rise" style={rise(1)}>
              AI-DM
            </h1>
            <p className="story-title-tagline story-title-rise" style={rise(2)}>
              A Dungeon Master that remembers.
            </p>
          </header>

          {resume ? (
            <section className="story-title-block" aria-labelledby={`${idPrefix}-continue`}>
              <h2 id={`${idPrefix}-continue`} className="story-label story-title-rise" style={continueStep()}>
                Continue your adventure
              </h2>
              <p className="story-title-campaign story-title-rise" style={continueStep()}>
                {resume.title}
              </p>
              {resumeMeta ? (
                <p className="story-title-meta story-title-rise" style={continueStep()}>
                  {resumeMeta}
                </p>
              ) : null}
              {resume.recap ? (
                <p className="story-title-recap story-title-rise" style={continueStep()}>
                  {resume.recap}
                </p>
              ) : null}
              <div className="story-title-actions story-title-rise" style={continueStep()}>
                <button type="button" className="story-primary" disabled={pending} onClick={onContinue}>
                  Continue
                </button>
              </div>
              <div className="story-title-links story-title-rise" style={continueStep()}>
                <button
                  type="button"
                  className="story-link"
                  aria-expanded={readyMadeOpen}
                  aria-controls={readyMadeId}
                  disabled={pending}
                  onClick={() => setReadyMadeOpen((open) => !open)}
                >
                  Play a ready-made adventure
                </button>
                <span className="story-title-link-group">
                  <button type="button" className="story-link" disabled={pending} onClick={onCreateCampaign}>
                    New adventure
                  </button>
                  <button type="button" className="story-link" disabled={pending} onClick={onOpenAdventures}>
                    All adventures
                  </button>
                </span>
              </div>
            </section>
          ) : null}

          {showReadyMade ? (
            <section
              ref={returning ? revealReadyMade : undefined}
              id={readyMadeId}
              className="story-title-block"
              data-revealed={returning ? '' : undefined}
              aria-labelledby={`${readyMadeId}-heading`}
            >
              <h2 id={`${readyMadeId}-heading`} className="story-label story-title-rise" style={readyMadeStep()}>
                The Road of Unremembered Kings
              </h2>
              <p className="story-title-pitch story-title-rise" style={readyMadeStep()}>
                An empty caravan rolls into a roadside inn at dusk. Its cargo is untouched. Every name in its
                ledger has been scratched out.
              </p>
              {heroesStatus === 'failed' ? null : (
                <div className="story-title-rise" style={readyMadeStep()}>
                  <TitleHeroes
                    heroes={heroes}
                    loading={heroesStatus === 'loading'}
                    selectedId={selectedHeroId}
                    disabled={pending}
                    onSelect={onSelectHero}
                  />
                </div>
              )}
              <div className="story-title-actions story-title-rise" style={readyMadeStep()}>
                <button
                  type="button"
                  className="story-primary story-title-play"
                  disabled={pending}
                  aria-busy={pending || undefined}
                  onClick={onPlayNow}
                >
                  {pending ? (
                    <>
                      Preparing the adventure
                      <span className="story-dots" aria-hidden="true">
                        <i />
                        <i />
                        <i />
                      </span>
                    </>
                  ) : heroName ? (
                    `Play now as ${heroName}`
                  ) : (
                    'Play now'
                  )}
                </button>
              </div>
              {returning && accountReady ? null : (
                <div className="story-title-links story-title-rise" style={readyMadeStep()}>
                  {returning ? null : (
                    <button type="button" className="story-link" disabled={pending} onClick={onCreateCampaign}>
                      Create your own adventure
                    </button>
                  )}
                  {accountReady ? null : (
                    <span className="story-title-link-group">
                      <button type="button" className="story-link" disabled={pending} onClick={onLogIn}>
                        Log in
                      </button>
                      <button type="button" className="story-link" disabled={pending} onClick={onCreateAccount}>
                        Create account
                      </button>
                    </span>
                  )}
                </div>
              )}
            </section>
          ) : null}
        </div>

        {safeMode ? (
          <p className="story-title-footnote">
            Scripted narrator — no AI provider is connected. You can add one in Settings.
          </p>
        ) : null}
      </div>
    </section>
  )
}
