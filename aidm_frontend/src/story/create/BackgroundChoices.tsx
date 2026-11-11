import type { PlayableBackground } from '../../backgroundCatalog'
import { skillName } from './creationRules'

type BackgroundChoicesProps = {
  options: PlayableBackground[]
  chosenId: string | null | undefined
  /** The radio group's name, unique on the page. */
  name: string
  disabled?: boolean
  onChoose: (backgroundId: string) => void
}

/** The backgrounds as radio rows with the skills each grants (hero creation's Shape step, the character editor). */
export function BackgroundChoices({ options, chosenId, name, disabled = false, onChoose }: BackgroundChoicesProps) {
  return (
    <fieldset className="story-create-backgrounds">
      <legend className="story-label">Background</legend>
      <div className="story-create-background-grid">
        {options.map((option) => (
          <label key={option.id} className="story-create-choice">
            <input
              type="radio"
              name={name}
              className="story-create-radio"
              checked={chosenId === option.id}
              disabled={disabled}
              onChange={() => onChoose(option.id)}
            />
            <span className="story-create-choice-name">{option.name}</span>
            <span className="story-create-choice-meta">{option.skillProficiencies.map(skillName).join(', ')}</span>
          </label>
        ))}
      </div>
    </fieldset>
  )
}
