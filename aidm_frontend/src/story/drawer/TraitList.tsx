import type { InspectorPanelProps } from '../../InspectorPanel'

/** The hero's abilities and traits: on the Magic tab, or on Character for a hero without spells. */
export function TraitList({ traits }: { traits: InspectorPanelProps['characterTraits'] }) {
  if (!traits.length) return null
  return (
    <>
      <hr className="story-rule" />
      <h3 className="story-label">Abilities and traits</h3>
      <ul className="story-trait-list">
        {traits.map((trait) => (
          <li key={trait.id || trait.name}>
            <span className="story-trait-name">{trait.name}</span>
            {trait.description ? <span className="story-trait-summary">{trait.description}</span> : null}
          </li>
        ))}
      </ul>
    </>
  )
}
