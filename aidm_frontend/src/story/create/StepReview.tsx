import type { RefObject } from 'react'
import { skillName } from './creationRules'
import {
  FIGHTING_STYLES,
  heroClass,
  heroDescriptor,
  heroFightingStyle,
  heroLanguages,
  heroSentence,
  portraitSrc,
  proficientSkills,
  type HeroDraft,
} from './createHeroModel'
import { HeroNumbersRow } from './StepShape'

type StepReviewProps = {
  draft: HeroDraft
  headingRef: RefObject<HTMLHeadingElement | null>
}

/** Step 4: the finished hero, read back before they join the campaign. */
export function StepReview({ draft, headingRef }: StepReviewProps) {
  const entry = heroClass(draft)
  const style = heroFightingStyle(draft)
  const expertise = new Set(draft.expertise)
  const skills = proficientSkills(draft).map((skill) =>
    expertise.has(skill) ? `${skillName(skill)} (expertise)` : skillName(skill),
  )

  return (
    <div className="story-create-step story-create-review">
      <div className="story-create-review-hero story-create-rise">
        <span className="story-create-ring story-create-review-portrait">
          <img src={portraitSrc(draft.ancestry, draft.sex)} alt="" draggable={false} />
        </span>
        <div>
          <h2 ref={headingRef} tabIndex={-1} className="story-create-review-name">
            {draft.name.trim()}
          </h2>
          <p className="story-label story-create-review-kind">{heroDescriptor(draft)}</p>
        </div>
        <p className="story-create-review-sentence">{heroSentence(draft)}</p>
        <HeroNumbersRow draft={draft} />
      </div>

      <dl className="story-create-sheet story-create-rise">
        <div>
          <dt className="story-label">Skills</dt>
          <dd>{skills.length ? skills.join(', ') : 'None yet'}</dd>
        </div>
        <div>
          <dt className="story-label">Languages</dt>
          <dd>{heroLanguages(draft).join(', ')}</dd>
        </div>
        {style && entry?.fightingStyle ? (
          <div>
            <dt className="story-label">Fighting style</dt>
            <dd>
              {FIGHTING_STYLES[style].name}
              {entry.fightingStyle.fromLevel > 1 ? `, from level ${entry.fightingStyle.fromLevel}` : ''}
            </dd>
          </div>
        ) : null}
        <div>
          <dt className="story-label">Starting kit</dt>
          <dd>{entry ? entry.kit.join(', ') : 'Set for your calling when you begin.'}</dd>
        </div>
        {entry?.cantrips.length || entry?.spells.length ? (
          <div className="story-create-sheet-spells">
            <dt className="story-label">Spells</dt>
            {entry.cantrips.length ? (
              <dd>
                <span className="story-create-spell-level">Cantrips</span> {entry.cantrips.join(', ')}
              </dd>
            ) : null}
            {entry.spells.length ? (
              <dd>
                <span className="story-create-spell-level">1st level</span> {entry.spells.join(', ')}
              </dd>
            ) : null}
          </div>
        ) : null}
      </dl>
    </div>
  )
}
