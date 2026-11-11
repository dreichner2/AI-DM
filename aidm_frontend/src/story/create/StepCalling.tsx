import type { RefObject } from 'react'
import { hitDieForClass } from '../../characterStats'
import {
  FIGHTING_STYLES,
  NARRATED_CLASS_NOTE,
  callingName,
  coreClasses,
  countWord,
  heroClass,
  heroFightingStyle,
  savesLabel,
  withCalling,
  type CoreClass,
  type HeroDraft,
} from './createHeroModel'

type StepCallingProps = {
  draft: HeroDraft
  headingRef: RefObject<HTMLHeadingElement | null>
  onChange: (change: (current: HeroDraft) => HeroDraft) => void
  onMoreCallings: (trigger: HTMLButtonElement) => void
}

function listPhrase(items: readonly string[]) {
  return items.length > 1 ? `${items.slice(0, -1).join(', ')} and ${items[items.length - 1]}` : items.join('')
}

function counted(count: number, noun: string) {
  return `${count} ${noun}${count === 1 ? '' : 's'}`
}

/** `6 cantrips and 13 spells`, leaving out an empty list. */
function spellCounts(entry: CoreClass) {
  return [
    entry.cantrips.length ? counted(entry.cantrips.length, 'cantrip') : '',
    entry.spells.length ? counted(entry.spells.length, 'spell') : '',
  ].filter(Boolean).join(' and ')
}

function ClassDetail({ entry, draft, onChange }: { entry: CoreClass; draft: HeroDraft; onChange: StepCallingProps['onChange'] }) {
  const style = heroFightingStyle(draft)
  const magic = entry.spellcasting
  return (
    <>
      <h3 className="story-create-detail-name">{entry.name}</h3>
      <p className="story-create-meta">{`Hit die d${entry.hitDie} · Saves ${savesLabel(entry)}`}</p>

      <h4 className="story-label">What you can do from the start</h4>
      <ul className="story-create-features">
        {entry.start.map((feature) => (
          <li key={feature.name}>
            <strong>{feature.name}</strong> {feature.text}
          </li>
        ))}
        {magic ? (
          <li className="story-create-feature-magic">
            <strong>Spells</strong>
            {` ${spellCounts(entry)}, such as ${listPhrase(magic.examples)}.`}
          </li>
        ) : null}
      </ul>
      {entry.note ? <p className="story-create-note">{entry.note}</p> : null}

      {entry.fightingStyle ? (
        <fieldset className="story-create-styles">
          <legend className="story-label">Fighting style</legend>
          {entry.fightingStyle.fromLevel > 1 ? (
            <p className="story-create-quiet">{`Applies from level ${entry.fightingStyle.fromLevel}.`}</p>
          ) : null}
          {entry.fightingStyle.options.map((option) => (
            <label key={option} className="story-create-choice story-create-choice-line">
              <input
                type="radio"
                name="story-create-style"
                className="story-create-radio"
                checked={style === option}
                onChange={() => onChange((current) => ({ ...current, fightingStyle: option }))}
              />
              <span className="story-create-choice-name">{FIGHTING_STYLES[option].name}</span>
              <span className="story-create-choice-text">{FIGHTING_STYLES[option].effect}</span>
            </label>
          ))}
        </fieldset>
      ) : null}
      {entry.expertise ? (
        <p className="story-create-note">{`You mark ${countWord(entry.expertise)} of your skills for Expertise in the next step.`}</p>
      ) : null}

      {entry.later.length ? (
        <>
          <h4 className="story-label">Later</h4>
          <ol className="story-create-later">
            {entry.later.map((feature) => (
              <li key={`${feature.level}-${feature.name}`}>
                <span className="story-create-later-level">{`Level ${feature.level}`}</span>
                <span>
                  <strong>{feature.name}</strong> {feature.text}
                </span>
              </li>
            ))}
          </ol>
        </>
      ) : null}
    </>
  )
}

/** Step 2: the twelve core callings, what each does from the start and later, and its choices. */
export function StepCalling({ draft, headingRef, onChange, onMoreCallings }: StepCallingProps) {
  const entry = heroClass(draft)
  const narrated = draft.calling && !entry ? draft.calling : null

  return (
    <div className="story-create-step story-create-calling">
      <h2 ref={headingRef} tabIndex={-1} className="story-sr-only">
        Calling
      </h2>
      <fieldset className="story-create-callings story-create-rise">
        <legend className="story-label">Calling</legend>
        <div className="story-create-calling-grid">
          {coreClasses(draft.rules).map((option) => (
            <label key={option.id} className="story-create-choice">
              <input
                type="radio"
                name="story-create-calling"
                className="story-create-radio"
                checked={entry?.id === option.id}
                onChange={() => onChange((current) => withCalling(current, option.name))}
              />
              <span className="story-create-choice-name">{option.name}</span>
              <span className="story-create-choice-text">{option.feel}</span>
              <span className="story-create-choice-meta">{`Hit die d${option.hitDie} · Saves ${savesLabel(option)}`}</span>
            </label>
          ))}
        </div>
        {narrated ? (
          <label className="story-create-choice">
            <input type="radio" name="story-create-calling" className="story-create-radio" checked readOnly />
            <span className="story-create-choice-name">{callingName(narrated)}</span>
            <span className="story-create-choice-text">{NARRATED_CLASS_NOTE}</span>
            <span className="story-create-choice-meta">{`Hit die d${hitDieForClass(narrated)}`}</span>
          </label>
        ) : null}
        <button
          type="button"
          className="story-link story-create-more"
          onClick={(event) => onMoreCallings(event.currentTarget)}
        >
          More callings
        </button>
      </fieldset>

      <section className="story-create-detail story-create-calling-detail story-create-rise" aria-label="Calling details">
        {entry ? (
          <ClassDetail entry={entry} draft={draft} onChange={onChange} />
        ) : narrated ? (
          <>
            <h3 className="story-create-detail-name">{callingName(narrated)}</h3>
            <p className="story-create-meta">{`Hit die d${hitDieForClass(narrated)}`}</p>
            <p className="story-create-note">{NARRATED_CLASS_NOTE}</p>
            <p className="story-create-quiet">
              Its starting kit is set when you begin, and the Dungeon Master brings its abilities into the story.
            </p>
          </>
        ) : (
          <p className="story-create-quiet">Choose a calling to see what it can do.</p>
        )}
      </section>
    </div>
  )
}
