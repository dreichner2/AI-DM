import { useId } from 'react'
import {
  PLAYABLE_BACKGROUNDS,
  backgroundFromValue,
  proficiencyLabel,
  type PlayableBackground,
} from './backgroundCatalog'

type BackgroundSelectorProps = {
  selectedBackgroundId: string
  pending: boolean
  onBackgroundChange: (backgroundId: string) => void
}

/** What a background grants, in one line: skills, then tools and languages when it has them. */
function grants(background: PlayableBackground) {
  return [
    background.skillProficiencies.map(proficiencyLabel).join(', '),
    background.toolProficiencies.map(proficiencyLabel).join(', '),
    background.languages.join(', '),
  ].filter(Boolean).join(' · ')
}

/** The character editor's background choice: one text row per background, like every choice in a sheet. */
export function BackgroundSelector({
  selectedBackgroundId,
  pending,
  onBackgroundChange,
}: BackgroundSelectorProps) {
  const headingId = useId()
  const selected = backgroundFromValue(selectedBackgroundId)

  return (
    <section className="background-selector story-dialog-section" aria-labelledby={headingId}>
      <h3 id={headingId} className="story-label">Background</h3>
      <div className="story-dialog-options" role="radiogroup" aria-labelledby={headingId}>
        {PLAYABLE_BACKGROUNDS.map((background) => (
          <label key={background.id} className="story-dialog-option">
            <input
              type="radio"
              name="character-background"
              value={background.id}
              checked={selected?.id === background.id}
              disabled={pending}
              onChange={() => onBackgroundChange(background.id)}
            />
            <span>
              {background.name}
              <small>{grants(background)}</small>
            </span>
          </label>
        ))}
      </div>
    </section>
  )
}
