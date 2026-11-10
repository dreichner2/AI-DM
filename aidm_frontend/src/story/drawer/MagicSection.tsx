import { Sparkle } from 'lucide-react'
import type { SpellSummary } from '../../gameSelectors'
import type { InspectorPanelProps } from '../../InspectorPanel'
import type { SessionBoardProps } from '../../SessionBoard'

function ordinal(level: number) {
  const suffix = level === 1 ? 'st' : level === 2 ? 'nd' : level === 3 ? 'rd' : 'th'
  return `${level}${suffix}`
}

function spellNameKey(value: string) {
  return value.toLowerCase().replace(/[^a-z0-9]+/g, ' ').trim()
}

function SlotPips({ label, current, max }: { label: string; current: number; max: number }) {
  return (
    <div className="story-slot" role="img" aria-label={`${label}: ${current} of ${max} available`}>
      <span className="story-slot-level" aria-hidden="true">{label}</span>
      <span className="story-pips" aria-hidden="true">
        {Array.from({ length: Math.min(max, 10) }, (_, index) => (
          <i key={index} data-spent={index >= current || undefined} />
        ))}
      </span>
    </div>
  )
}

type MagicSectionProps = {
  board: SessionBoardProps
  inspector: InspectorPanelProps
}

export function MagicSection({ board, inspector }: MagicSectionProps) {
  const { spellResources, spellbook, characterTraits } = inspector
  const resolvedIds = new Set(board.gameplayControls.spells.map((spell) => spell.id))
  const resolvedNames = new Set(board.gameplayControls.spells.map((spell) => spellNameKey(spell.name)))
  const resolved = (spell: SpellSummary) => resolvedIds.has(spell.id) || resolvedNames.has(spellNameKey(spell.name))
  const levels = [...new Set(spellbook.knownSpells.map((spell) => spell.level))].sort((left, right) => left - right)
  const hasSlots = Boolean(spellResources.slots.length || spellResources.pactSlot || spellResources.arcanum.length)
  const anyResolved = spellbook.knownSpells.some(resolved)

  return (
    <div className="story-sheet story-sheet-magic">
      {hasSlots ? (
        <section className="story-slots-section" aria-labelledby="story-magic-slots">
          <h3 id="story-magic-slots" className="story-label">Spell slots</h3>
          <div className="story-slots">
            {spellResources.slots.map((slot) => (
              <SlotPips key={`slot-${slot.level}`} label={ordinal(slot.level)} current={slot.current} max={slot.max} />
            ))}
            {spellResources.pactSlot ? (
              <SlotPips
                label={`Pact ${ordinal(spellResources.pactSlot.level)}`}
                current={spellResources.pactSlot.current}
                max={spellResources.pactSlot.max}
              />
            ) : null}
            {spellResources.arcanum.map((use) => (
              <SlotPips key={`arcanum-${use.level}`} label={`Arcanum ${ordinal(use.level)}`} current={use.current} max={use.max} />
            ))}
          </div>
        </section>
      ) : null}
      {spellResources.concentration ? (
        <p className="story-sheet-meta">Concentrating on {spellResources.concentration}</p>
      ) : null}

      {levels.length ? (
        levels.map((level) => (
          <section key={level} className="story-spell-group" aria-label={level ? `${ordinal(level)} level` : 'Cantrips'}>
            <h3 className="story-label">{level ? `${ordinal(level)} level` : 'Cantrips'}</h3>
            <ul className="story-spell-list">
              {spellbook.knownSpells.filter((spell) => spell.level === level).map((spell) => {
                const tags = [
                  spell.tags.includes('concentration') ? 'conc.' : '',
                  spell.tags.includes('ritual') ? 'ritual' : '',
                ].filter(Boolean)
                return (
                  <li key={spell.id || spell.name}>
                    {resolved(spell) ? (
                      <Sparkle className="story-spark" size={12} aria-label="Resolved by the rules engine" />
                    ) : (
                      <span className="story-spark-space" aria-hidden="true" />
                    )}
                    <span className="story-spell-name">{spell.name}</span>
                    {tags.map((tag) => <span key={tag} className="story-tag">{tag}</span>)}
                  </li>
                )
              })}
            </ul>
          </section>
        ))
      ) : (
        <p className="story-sheet-empty">No spells known.</p>
      )}
      {anyResolved ? (
        <p className="story-sheet-legend">
          <Sparkle className="story-spark" size={12} aria-hidden="true" />
          The rules engine resolves these exactly; the Dungeon Master narrates the rest.
        </p>
      ) : null}

      {characterTraits.length ? (
        <>
          <hr className="story-rule" />
          <h3 className="story-label">Abilities and traits</h3>
          <ul className="story-trait-list">
            {characterTraits.map((trait) => (
              <li key={trait.id || trait.name}>
                <span className="story-trait-name">{trait.name}</span>
                {trait.description ? <span className="story-trait-summary">{trait.description}</span> : null}
              </li>
            ))}
          </ul>
        </>
      ) : null}
    </div>
  )
}
