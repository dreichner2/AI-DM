import { useId } from 'react'
import { BURDEN_MAX, OWN_DESIRE, OWN_DESIRE_MAX, TIE_NAME_LENGTH, type HeroTiesDraft } from '../heroTies'
import { suggestName, type Ancestry } from './createHeroModel'
import '../../styles/story-ties.css'

/** The desires a player may choose, in the order the choices show, then a line of their own. */
const DESIRE_CHOICES: ReadonlyArray<{ kind: string; words: string }> = [
  { kind: 'find_someone', words: 'to find someone' },
  { kind: 'be_recognised', words: 'to be recognised' },
  { kind: 'clear_debt', words: 'to clear a debt' },
  { kind: 'keep_duty', words: 'to keep a duty' },
  { kind: 'learn_forbidden', words: 'to learn what is forbidden' },
  { kind: 'prove_wrong', words: 'to prove someone wrong' },
  { kind: OWN_DESIRE, words: 'in your own words' },
]
const FEMALE_RELATIONS = new Set(['sister', 'mother', 'daughter'])
const MALE_RELATIONS = new Set(['brother', 'father', 'son'])
const NOT_PEOPLE = new Set(['a place', 'an order', 'a keepsake'])
/** Who or what the hero would not abandon: people first, then a place, an order or a keepsake. */
const RELATIONS: readonly string[] = [
  'sister', 'brother', 'mother', 'father', 'daughter', 'son', 'friend', 'mentor', 'old comrade', 'the one who wronged you',
  ...NOT_PEOPLE,
]

/** One sentence a hero of each background might carry, offered by `Suggest`. */
const BURDEN_SUGGESTIONS: Record<string, string> = {
  acolyte: 'A temple key you were told to throw into the river.',
  criminal: 'The share of a job that got a friend hanged.',
  folk_hero: "A village's thanks you have never felt you earned.",
  guild_artisan: 'An unfinished commission, paid for in full.',
  sage: 'A book you borrowed from a dead man and never returned.',
  soldier: 'A discharge paper you never showed anyone.',
}

type HeroTiesFieldsProps = {
  draft: HeroTiesDraft
  heroName: string
  ancestry: Ancestry | null
  backgroundId: string | null
  disabled?: boolean
  onChange: (change: Partial<HeroTiesDraft>) => void
}

/**
 * A name for the one the hero would not abandon, from their ancestry's names (a sister's from its women's
 * names, a brother's from its men's), never the hero's own.
 */
function suggestTieName(ancestry: Ancestry | null, relation: string, heroName: string, current: string) {
  const own = heroName.trim().toLowerCase()
  const order = FEMALE_RELATIONS.has(relation)
    ? (['female'] as const)
    : MALE_RELATIONS.has(relation)
      ? (['male'] as const)
      : (['female', 'male'] as const)
  let candidate = current
  for (let tries = 0; tries < 12; tries += 1) {
    const pick = suggestName(ancestry, order[tries % order.length], candidate)
    if (pick.trim().toLowerCase() !== own && pick !== current) return pick
    candidate = pick
  }
  return current
}

/**
 * `Your own threads`: what the hero wants, who or what they would not abandon, and what they carry. Every line is
 * optional; choices are text links, the chosen one underlined in ember.
 */
export function HeroTiesFields({ draft, heroName, ancestry, backgroundId, disabled = false, onChange }: HeroTiesFieldsProps) {
  const headingId = useId()
  const wantId = useId()
  const relationId = useId()
  const own = draft.desireKind === OWN_DESIRE
  const burdenSuggestion = backgroundId ? BURDEN_SUGGESTIONS[backgroundId] : undefined

  return (
    <section className="story-ties" aria-labelledby={headingId}>
      <h3 id={headingId} className="story-label">Your own threads</h3>

      <div className="story-ties-line">
        <p id={wantId} className="story-ties-label">What you want</p>
        <div className="story-ties-choices" role="group" aria-labelledby={wantId}>
          {DESIRE_CHOICES.map((choice) => (
            <button
              key={choice.kind}
              type="button"
              className="story-link"
              aria-pressed={draft.desireKind === choice.kind}
              disabled={disabled}
              onClick={() => onChange({ desireKind: draft.desireKind === choice.kind ? '' : choice.kind })}
            >
              {choice.words}
            </button>
          ))}
        </div>
        {own ? (
          <input
            className="story-ties-input"
            aria-label="What you want, in your own words"
            placeholder="One line of your own"
            maxLength={OWN_DESIRE_MAX}
            autoComplete="off"
            disabled={disabled}
            value={draft.ownDesire}
            onChange={(event) => onChange({ ownDesire: event.target.value })}
          />
        ) : null}
      </div>

      <div className="story-ties-line">
        <label className="story-ties-label" htmlFor={`${relationId}-name`}>
          Who or what you would not abandon
        </label>
        <div className="story-ties-row">
          <input
            id={`${relationId}-name`}
            className="story-ties-input"
            placeholder="A name"
            maxLength={TIE_NAME_LENGTH.max}
            autoComplete="off"
            spellCheck={false}
            disabled={disabled}
            value={draft.attachmentName}
            onChange={(event) => onChange({ attachmentName: event.target.value })}
          />
          {!NOT_PEOPLE.has(draft.relation) ? (
            <button
              type="button"
              className="story-link"
              aria-label="Suggest a name"
              disabled={disabled}
              onClick={() => onChange({ attachmentName: suggestTieName(ancestry, draft.relation, heroName, draft.attachmentName) })}
            >
              Suggest
            </button>
          ) : null}
        </div>
        <div className="story-ties-choices" role="group" aria-label="Who they are to you">
          {RELATIONS.map((relation) => (
            <button
              key={relation}
              type="button"
              className="story-link"
              aria-pressed={draft.relation === relation}
              disabled={disabled}
              onClick={() => onChange({ relation })}
            >
              {relation}
            </button>
          ))}
        </div>
      </div>

      <div className="story-ties-line">
        <label className="story-ties-label" htmlFor={`${relationId}-burden`}>
          What you carry
        </label>
        <div className="story-ties-row">
          <input
            id={`${relationId}-burden`}
            className="story-ties-input"
            placeholder="One sentence"
            maxLength={BURDEN_MAX}
            autoComplete="off"
            disabled={disabled}
            value={draft.burden}
            onChange={(event) => onChange({ burden: event.target.value })}
          />
          {burdenSuggestion ? (
            <button
              type="button"
              className="story-link"
              aria-label="Suggest what you carry"
              disabled={disabled}
              onClick={() => onChange({ burden: burdenSuggestion })}
            >
              Suggest
            </button>
          ) : null}
        </div>
      </div>
    </section>
  )
}
