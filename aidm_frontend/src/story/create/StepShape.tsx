import { Minus, Plus } from 'lucide-react'
import type { RefObject } from 'react'
import { POINT_BUY_ABILITIES, POINT_BUY_BUDGET } from '../../characterStats'
import { skillName } from './creationRules'
import {
  MAX_SCORE,
  MIN_SCORE,
  backgroundOptions,
  classSkillOptions,
  countWord,
  expertiseCount,
  heroBackground,
  heroClass,
  heroNumbers,
  heroScores,
  modifier,
  needsExpertise,
  pointsRemaining,
  proficientSkills,
  signed,
  skillCountPhrase,
  stepScore,
  toggleExpertise,
  toggleSkill,
  withAbilityMode,
  withBackground,
  type HeroDraft,
} from './createHeroModel'

type StepShapeProps = {
  draft: HeroDraft
  headingRef: RefObject<HTMLHeadingElement | null>
  onChange: (change: (current: HeroDraft) => HeroDraft) => void
}

/** The live level-1 numbers for the current choices. */
export function HeroNumbersRow({ draft }: { draft: HeroDraft }) {
  const numbers = heroNumbers(draft)
  return (
    <dl className="story-create-numbers">
      <div data-tone="health">
        <dt className="story-label">HP</dt>
        <dd>{numbers.hp}</dd>
      </div>
      {numbers.armorClass !== null ? (
        <div>
          <dt className="story-label">AC</dt>
          <dd>{numbers.armorClass}</dd>
        </div>
      ) : null}
      {numbers.attack ? (
        <div>
          <dt className="story-label">{numbers.attack.weapon}</dt>
          <dd>{signed(numbers.attack.bonus)}</dd>
        </div>
      ) : null}
      {numbers.spellSaveDc !== null ? (
        <div data-tone="arcane">
          <dt className="story-label">Spell save DC</dt>
          <dd>{numbers.spellSaveDc}</dd>
        </div>
      ) : null}
    </dl>
  )
}

/** Step 3: ability scores with live numbers, background, class skills and Rogue expertise. */
export function StepShape({ draft, headingRef, onChange }: StepShapeProps) {
  const entry = heroClass(draft)
  const adjusting = draft.abilityMode === 'adjust'
  const scores = heroScores(draft)
  const remaining = pointsRemaining(scores)
  const background = heroBackground(draft)
  const options = classSkillOptions(draft)
  const owned = new Set(background?.skillProficiencies ?? [])
  const skillsFull = entry ? draft.skills.length >= entry.skillCount : true
  const expertiseOptions = needsExpertise(draft) ? proficientSkills(draft) : []
  const expertiseLimit = expertiseCount(draft)

  return (
    <div className="story-create-step story-create-shape">
      <h2 ref={headingRef} tabIndex={-1} className="story-sr-only">
        Shape
      </h2>
      <div className="story-create-shape-main story-create-rise">
        <section className="story-create-abilities" aria-labelledby="story-create-abilities-label">
          <div className="story-create-section-head">
            <h3 id="story-create-abilities-label" className="story-label">
              Abilities
            </h3>
            <div className="story-create-modes" role="group" aria-label="How to set abilities">
              <button
                type="button"
                className="story-link"
                aria-pressed={!adjusting}
                onClick={() => onChange((current) => withAbilityMode(current, 'recommended'))}
              >
                Recommended
              </button>
              <button
                type="button"
                className="story-link"
                aria-pressed={adjusting}
                onClick={() => onChange((current) => withAbilityMode(current, 'adjust'))}
              >
                Adjust
              </button>
            </div>
          </div>
          <ul className="story-create-scores" data-adjusting={adjusting || undefined}>
            {POINT_BUY_ABILITIES.map((ability) => {
              const score = scores[ability.key]
              return (
                <li key={ability.key}>
                  <span className="story-create-score-mod">{signed(modifier(score))}</span>
                  <span className="story-create-score">
                    {adjusting ? (
                      <button
                        type="button"
                        className="story-create-step-button"
                        aria-label={`Lower ${ability.name}`}
                        disabled={score <= MIN_SCORE}
                        onClick={() => onChange((current) => stepScore(current, ability.key, -1))}
                      >
                        <Minus size={13} strokeWidth={1.8} />
                      </button>
                    ) : null}
                    <span>{score}</span>
                    {adjusting ? (
                      <button
                        type="button"
                        className="story-create-step-button"
                        aria-label={`Raise ${ability.name}`}
                        disabled={score >= MAX_SCORE}
                        onClick={() => onChange((current) => stepScore(current, ability.key, 1))}
                      >
                        <Plus size={13} strokeWidth={1.8} />
                      </button>
                    ) : null}
                  </span>
                  <span className="story-label" aria-hidden="true">
                    {ability.label}
                  </span>
                  <span className="story-sr-only">{ability.name}</span>
                </li>
              )
            })}
          </ul>
          <p className={`story-create-points${remaining < 0 ? ' is-over' : ''}`} aria-live="polite">
            {adjusting
              ? remaining < 0
                ? `${-remaining} ${-remaining === 1 ? 'point' : 'points'} over the ${POINT_BUY_BUDGET}-point budget`
                : `${remaining} of ${POINT_BUY_BUDGET} points left`
              : `The standard array, 15 to 8, laid out for ${entry ? `a ${entry.name}` : 'your calling'}.`}
          </p>
        </section>

        <section className="story-create-at-start" aria-labelledby="story-create-numbers-label">
          <h3 id="story-create-numbers-label" className="story-label">
            At level 1
          </h3>
          <HeroNumbersRow draft={draft} />
        </section>
      </div>

      <div className="story-create-shape-side story-create-rise">
        <fieldset className="story-create-backgrounds">
          <legend className="story-label">Background</legend>
          <div className="story-create-background-grid">
            {backgroundOptions(draft.rules).map((option) => (
              <label key={option.id} className="story-create-choice">
                <input
                  type="radio"
                  name="story-create-background"
                  className="story-create-radio"
                  checked={background?.id === option.id}
                  onChange={() => onChange((current) => withBackground(current, option.id))}
                />
                <span className="story-create-choice-name">{option.name}</span>
                <span className="story-create-choice-meta">
                  {option.skillProficiencies.map(skillName).join(', ')}
                </span>
              </label>
            ))}
          </div>
        </fieldset>

        {entry ? (
          <fieldset className="story-create-skills">
            <legend className="story-label">
              Class skills <span className="story-create-count">{`Choose ${entry.skillCount}`}</span>
            </legend>
            <div className="story-create-check-grid">
              {options.map((skill) => {
                const checked = draft.skills.includes(skill)
                return (
                  <label key={skill} className="story-create-check">
                    <input
                      type="checkbox"
                      className="story-create-radio"
                      checked={checked}
                      disabled={!checked && skillsFull}
                      onChange={() => onChange((current) => toggleSkill(current, skill))}
                    />
                    <span>{skillName(skill)}</span>
                  </label>
                )
              })}
            </div>
            {owned.size ? (
              <p className="story-create-quiet">{`${background?.name} already gives you ${[...owned].map(skillName).join(' and ')}.`}</p>
            ) : null}
          </fieldset>
        ) : (
          <p className="story-create-note">
            A narrated calling has no class skill list; your background&rsquo;s skills are yours.
          </p>
        )}

        {needsExpertise(draft) ? (
          <fieldset className="story-create-skills story-create-expertise">
            <legend className="story-label">
              Expertise{' '}
              <span className="story-create-count">{`Mark ${countWord(expertiseLimit)}`}</span>
            </legend>
            <div className="story-create-check-grid">
              {expertiseOptions.map((skill) => {
                const checked = draft.expertise.includes(skill)
                return (
                  <label key={skill} className="story-create-check">
                    <input
                      type="checkbox"
                      className="story-create-radio"
                      checked={checked}
                      disabled={!checked && draft.expertise.length >= expertiseLimit}
                      onChange={() => onChange((current) => toggleExpertise(current, skill))}
                    />
                    <span>{skillName(skill)}</span>
                  </label>
                )
              })}
            </div>
            <p className="story-create-quiet">{`Double proficiency in ${skillCountPhrase(expertiseLimit)} you already have.`}</p>
          </fieldset>
        ) : null}
      </div>
    </div>
  )
}
