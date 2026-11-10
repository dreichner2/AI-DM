import type { RefObject } from 'react'
import type { SexKey } from '../../profileIcons'
import {
  ANCESTRIES,
  portraitSrc,
  suggestName,
  withAncestry,
  type Ancestry,
  type HeroDraft,
} from './createHeroModel'

type StepWhoProps = {
  draft: HeroDraft
  headingRef: RefObject<HTMLHeadingElement | null>
  onChange: (change: (current: HeroDraft) => HeroDraft) => void
  onContinue: () => void
  onMoreAncestries: (trigger: HTMLButtonElement) => void
}

const PORTRAITS: { sex: SexKey; label: string }[] = [
  { sex: 'male', label: 'Male' },
  { sex: 'female', label: 'Female' },
]

/** Step 1: the hero's name, ancestry and portrait. */
export function StepWho({ draft, headingRef, onChange, onContinue, onMoreAncestries }: StepWhoProps) {
  const chosen = draft.ancestry
  // An ancestry picked from the full selector joins the row so the choice stays visible.
  const row: Ancestry[] = chosen && !ANCESTRIES.includes(chosen) ? [...ANCESTRIES, chosen] : ANCESTRIES

  return (
    <div className="story-create-step story-create-who">
      <h2 ref={headingRef} tabIndex={-1} className="story-sr-only">
        Who
      </h2>
      <div className="story-create-name-row story-create-rise">
        <input
          className="story-create-name"
          aria-label="Hero name"
          placeholder="Name your hero"
          autoComplete="off"
          spellCheck={false}
          maxLength={80}
          data-autofocus
          value={draft.name}
          onChange={(event) => {
            const name = event.target.value
            onChange((current) => ({ ...current, name }))
          }}
          onKeyDown={(event) => {
            if (event.key === 'Enter') {
              event.preventDefault()
              onContinue()
            }
          }}
        />
        <button
          type="button"
          className="story-link story-create-suggest"
          onClick={() => onChange((current) => ({ ...current, name: suggestName(current.ancestry, current.sex, current.name) }))}
        >
          Suggest
        </button>
      </div>

      <fieldset className="story-create-ancestries story-create-rise">
        <legend className="story-label">Ancestry</legend>
        <div className="story-create-ancestry-row">
          {row.map((entry) => (
            <label key={entry.id} className="story-create-ancestry">
              <input
                type="radio"
                name="story-create-ancestry"
                className="story-create-radio"
                checked={chosen?.id === entry.id}
                onChange={() => onChange((current) => withAncestry(current, entry))}
              />
              <span className="story-create-ring">
                <img src={portraitSrc(entry, draft.sex)} alt="" draggable={false} />
              </span>
              <span className="story-create-ancestry-name">{entry.name}</span>
            </label>
          ))}
        </div>
      </fieldset>

      <div className="story-create-who-detail story-create-rise">
        <div className="story-create-detail">
          <div aria-live="polite">
            {chosen ? (
              <>
                <h3 className="story-create-detail-name">{chosen.name}</h3>
                <p className="story-create-prose">{chosen.summary}</p>
                {chosen.traits.length ? (
                  <ul className="story-create-traits" aria-label={`${chosen.name} traits in play`}>
                    {chosen.traits.slice(0, 3).map((trait) => (
                      <li key={trait}>{trait}</li>
                    ))}
                  </ul>
                ) : null}
              </>
            ) : (
              <p className="story-create-quiet">Choose an ancestry to meet them.</p>
            )}
          </div>
          <button
            type="button"
            className="story-link story-create-more"
            onClick={(event) => onMoreAncestries(event.currentTarget)}
          >
            More ancestries
          </button>
        </div>

        {chosen ? (
          <fieldset className="story-create-portraits">
            <legend className="story-label">Portrait</legend>
            <div className="story-create-portrait-row">
              {PORTRAITS.map(({ sex, label }) => (
                <label key={sex} className="story-create-portrait">
                  <input
                    type="radio"
                    name="story-create-portrait"
                    className="story-create-radio"
                    checked={draft.sex === sex}
                    onChange={() => onChange((current) => ({ ...current, sex }))}
                  />
                  <span className="story-create-ring">
                    <img src={portraitSrc(chosen, sex)} alt="" draggable={false} />
                  </span>
                  <span className="story-sr-only">{`${label} ${chosen.name}`}</span>
                </label>
              ))}
            </div>
          </fieldset>
        ) : null}
      </div>
    </div>
  )
}
