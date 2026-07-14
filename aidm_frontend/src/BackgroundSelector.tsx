import {
  PLAYABLE_BACKGROUNDS,
  backgroundFromValue,
  proficiencyLabel,
} from './backgroundCatalog'

type BackgroundSelectorProps = {
  selectedBackgroundId: string
  pending: boolean
  onBackgroundChange: (backgroundId: string) => void
}

export function BackgroundSelector({
  selectedBackgroundId,
  pending,
  onBackgroundChange,
}: BackgroundSelectorProps) {
  const selected = backgroundFromValue(selectedBackgroundId)

  return (
    <section className="background-selector" aria-label="Character background">
      <label>
        Background
        <select
          aria-label="Background"
          value={selected?.id ?? ''}
          disabled={pending}
          onChange={(event) => onBackgroundChange(event.target.value)}
        >
          <option value="">Choose a background</option>
          {PLAYABLE_BACKGROUNDS.map((background) => (
            <option key={background.id} value={background.id}>
              {background.name}
            </option>
          ))}
        </select>
      </label>
      {selected ? (
        <dl className="background-mechanics">
          <div>
            <dt>Skills</dt>
            <dd>{selected.skillProficiencies.map(proficiencyLabel).join(', ')}</dd>
          </div>
          <div>
            <dt>Tools</dt>
            <dd>{selected.toolProficiencies.map(proficiencyLabel).join(', ') || 'None'}</dd>
          </div>
          <div>
            <dt>Languages</dt>
            <dd>{selected.languages.join(', ') || 'None'}</dd>
          </div>
        </dl>
      ) : null}
    </section>
  )
}
