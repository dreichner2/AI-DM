import { useRef, useState, type FormEvent, type KeyboardEvent } from 'react'
import {
  customRaceBalance,
  customRacePortraitKey,
  customRaceTraits,
  customRaceWarnings,
  useCustomRaces,
} from '../../customRaces'
import { profileIconSrcForCharacter, type SexKey } from '../../profileIcons'
import {
  RACE_FILTERS,
  filterPlayableRaces,
  isRecommendedRace,
  playableRaceFromValue,
  profileIconSrcForRace,
  raceSelectionFromPlayableRace,
  type PlayableRace,
  type RaceCategory,
} from '../../raceCatalog'
import type { CharacterRaceSelection, RaceDefinition, RaceSummary } from '../../types'
import { CatalogFrame } from './CatalogFrame'
import { moveListFocus } from './catalogKeys'
import { PortraitToggle } from './PortraitToggle'

export type AncestryView = 'recommended' | 'all' | 'own'

const VIEWS = [
  ['recommended', 'Recommended'],
  ['all', 'All'],
  ['own', 'Your own'],
] as const

type AncestryCatalogProps = {
  auth: string
  baseUrl: string
  /** The hero's ancestry now; it is the one shown until another is picked. */
  current: CharacterRaceSelection | null
  sex: SexKey
  initialView?: AncestryView
  onChoose: (selection: CharacterRaceSelection) => void
  onSexChange: (sex: SexKey) => void
}

/** One ancestry as the medallions and the detail pane show it, whatever it came from. */
type Entry = {
  key: string
  name: string
  description: string
  portrait: (sex: SexKey) => string | null
  traits: { name: string; text: string }[]
  goodWith: string[]
}

function playableEntry(race: PlayableRace): Entry {
  return {
    key: `race:${race.key}`,
    name: race.name,
    description: race.shortDescription,
    portrait: (sex) => profileIconSrcForRace(race.key, sex),
    traits: race.traits.map((name) => ({ name, text: '' })),
    goodWith: race.recommendedClasses,
  }
}

function customPortrait(race: RaceDefinition | RaceSummary) {
  return (sex: SexKey) => profileIconSrcForCharacter({ race: customRacePortraitKey(race), sex })
}

function savedEntry(race: RaceSummary): Entry {
  return {
    key: `custom:${race.id}`,
    name: race.name,
    description: race.descriptionShort,
    portrait: customPortrait(race),
    traits: race.traits.map((name) => ({ name, text: '' })),
    goodWith: race.recommendedClasses,
  }
}

function definedEntry(race: RaceDefinition): Entry {
  return {
    key: `custom:${race.id}`,
    name: race.name,
    description: race.descriptionShort,
    portrait: customPortrait(race),
    traits: customRaceTraits(race),
    goodWith: Array.isArray(race.recommendedClasses) ? race.recommendedClasses : [],
  }
}

/** One line per trait when they have text, else the names on one line. */
function TraitLines({ traits }: { traits: Entry['traits'] }) {
  if (!traits.length) return null
  return (
    <>
      <h4 className="story-label">Traits</h4>
      {traits.some((trait) => trait.text) ? (
        <ul className="story-catalog-lines">
          {traits.map((trait) => (
            <li key={trait.name}>{trait.text ? `${trait.name} — ${trait.text}` : trait.name}</li>
          ))}
        </ul>
      ) : (
        <p className="story-catalog-line">{traits.map((trait) => trait.name).join(' · ')}</p>
      )}
    </>
  )
}

type MedallionProps = {
  entry: Entry
  sex: SexKey
  chosen: boolean
  tabStop: boolean
  onPick: () => void
  onChoose: () => void
  onShowDetail: () => void
}

function Medallion({ entry, sex, chosen, tabStop, onPick, onChoose, onShowDetail }: MedallionProps) {
  const src = entry.portrait(sex)
  return (
    <li>
      <button
        type="button"
        className="story-catalog-medallion"
        data-catalog-item
        aria-pressed={chosen}
        tabIndex={tabStop ? 0 : -1}
        onFocus={onPick}
        onClick={() => {
          onPick()
          onShowDetail()
        }}
        onKeyDown={(event) => {
          if (event.key !== 'Enter') return
          event.preventDefault()
          onChoose()
        }}
      >
        <span className="story-catalog-ring" aria-hidden="true">
          {src ? (
            <img src={src} alt="" loading="lazy" decoding="async" draggable={false} />
          ) : (
            <span className="story-catalog-initial">{entry.name.slice(0, 1)}</span>
          )}
        </span>
        <span className="story-catalog-medallion-name">{entry.name}</span>
      </button>
    </li>
  )
}

/**
 * "More ancestries" in the Story language: medallions beside one detail pane,
 * and the player's own ancestries drafted with the Dungeon Master. Uses the race
 * catalog and the custom-race calls the classic selector uses.
 */
export function AncestryCatalog({
  auth,
  baseUrl,
  current,
  sex,
  initialView = 'recommended',
  onChoose,
  onSexChange,
}: AncestryCatalogProps) {
  const [view, setView] = useState<AncestryView>(initialView)
  const [query, setQuery] = useState('')
  const [tag, setTag] = useState<RaceCategory | null>(null)
  const [picked, setPicked] = useState<{ entry: Entry; choose: () => void } | null>(null)
  const detailRef = useRef<HTMLElement | null>(null)
  const custom = useCustomRaces({ auth, baseUrl, listing: view === 'own', onChosen: onChoose })

  const currentRace = current && current.source !== 'custom' ? playableRaceFromValue(current.raceId || current.raceName) : null
  const currentCustom = current?.source === 'custom' ? current.customRaceDefinition : undefined
  const shown =
    picked ??
    (currentRace
      ? { entry: playableEntry(currentRace), choose: () => onChoose(raceSelectionFromPlayableRace(currentRace)) }
      : currentCustom && current
        ? { entry: definedEntry(currentCustom), choose: () => onChoose(current) }
        : null)

  const races =
    view === 'own'
      ? []
      : filterPlayableRaces({ query, category: tag ?? 'All' }).filter((race) => view === 'all' || isRecommendedRace(race))
  const items =
    view === 'own'
      ? custom.saved.map((race) => ({ entry: savedEntry(race), choose: () => void custom.chooseSaved(race) }))
      : races.map((race) => ({ entry: playableEntry(race), choose: () => onChoose(raceSelectionFromPlayableRace(race)) }))
  const tabStopKey = items.some((item) => item.entry.key === shown?.entry.key) ? shown?.entry.key : items[0]?.entry.key

  const showDetail = () => detailRef.current?.scrollIntoView({ block: 'nearest' })

  const medallions = items.length ? (
    <ul
      className="story-catalog-medallions"
      aria-label={view === 'own' ? 'Your ancestries' : 'Ancestries'}
      onKeyDown={(event: KeyboardEvent<HTMLUListElement>) => moveListFocus(event, 'grid')}
    >
      {items.map((item) => (
        <Medallion
          key={item.entry.key}
          entry={item.entry}
          sex={sex}
          chosen={item.entry.key === shown?.entry.key}
          tabStop={item.entry.key === tabStopKey}
          onPick={() => setPicked(item)}
          onChoose={item.choose}
          onShowDetail={showDetail}
        />
      ))}
    </ul>
  ) : null

  const submitDraft = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    void (custom.draft ? custom.accept() : custom.generate())
  }

  const draft = custom.draft
  const balance = draft ? customRaceBalance(draft) : null
  const warnings = draft ? customRaceWarnings(draft) : []

  const ownView = (
    <div className="story-catalog-own">
      <div>
        {custom.savedLoading ? <p className="story-catalog-quiet">Gathering your ancestries…</p> : null}
        {custom.savedError ? (
          <p className="story-catalog-error" role="alert">
            {custom.savedError}
          </p>
        ) : null}
        {medallions ??
          (custom.savedLoading || custom.savedError ? null : (
            <p className="story-catalog-quiet">None yet. Describe one below and the Dungeon Master drafts it.</p>
          ))}
      </div>

      <hr className="story-rule" />

      <form className="story-catalog-form" aria-label="A new ancestry" onSubmit={submitDraft}>
        <label className="story-catalog-field">
          <span className="story-label">Your idea</span>
          <textarea
            value={custom.prompt}
            placeholder="Emberborn are people descended from fire spirits, with glowing veins and a once-per-rest flame burst."
            disabled={custom.pending}
            onChange={(event) => custom.setPrompt(event.target.value)}
          />
        </label>
        <p className="story-catalog-help">
          The Dungeon Master drafts an ancestry from your idea. You can rename it, balance it and draft it again before you choose it.
        </p>

        {draft ? (
          <>
            <label className="story-catalog-field">
              <span className="story-label">Name</span>
              <input
                value={draft.name}
                disabled={custom.pending}
                onChange={(event) => custom.setDraft((existing) => (existing ? { ...existing, name: event.target.value } : existing))}
              />
            </label>
            <label className="story-catalog-field">
              <span className="story-label">Short description</span>
              <textarea
                value={draft.descriptionShort}
                disabled={custom.pending}
                onChange={(event) =>
                  custom.setDraft((existing) => (existing ? { ...existing, descriptionShort: event.target.value } : existing))
                }
              />
            </label>
            <TraitLines traits={customRaceTraits(draft)} />
            {balance ? (
              <p className="story-catalog-help">{`Balance ${balance.spent} of ${balance.budget} · ${balance.tier.replace('_', ' ')}`}</p>
            ) : null}
            {warnings.length ? <p className="story-catalog-help">{warnings.join(' ')}</p> : null}
          </>
        ) : null}

        {custom.error ? (
          <p className="story-catalog-error" role="alert">
            {custom.error}
          </p>
        ) : null}

        <div className="story-catalog-actions">
          {draft ? (
            <>
              <button type="submit" className="story-primary" disabled={custom.pending} aria-busy={custom.pending || undefined}>
                Create and choose
              </button>
              <button type="button" className="story-link" disabled={custom.pending} onClick={() => void custom.generate('balanced')}>
                Balance it
              </button>
              <button type="button" className="story-link" disabled={custom.pending} onClick={() => void custom.generate()}>
                Draft again
              </button>
              <button type="button" className="story-link" disabled={custom.pending} onClick={() => custom.setDraft(null)}>
                Start over
              </button>
            </>
          ) : (
            <button
              type="submit"
              className="story-primary"
              disabled={custom.pending || !custom.prompt.trim()}
              aria-busy={custom.pending || undefined}
            >
              {custom.pending ? 'Drafting…' : 'Draft it'}
            </button>
          )}
        </div>
      </form>
    </div>
  )

  const portrait = shown?.entry.portrait(sex) ?? null
  const detail = shown ? (
    <>
      {portrait ? (
        <span className="story-catalog-portrait">
          <img key={portrait} src={portrait} alt="" draggable={false} />
        </span>
      ) : null}
      <div aria-live="polite">
        <h3 className="story-create-detail-name">{shown.entry.name}</h3>
        {shown.entry.description ? <p className="story-create-prose">{shown.entry.description}</p> : null}
      </div>
      <TraitLines traits={shown.entry.traits} />
      {shown.entry.goodWith.length ? (
        <>
          <h4 className="story-label">Good with</h4>
          <p className="story-catalog-line">{shown.entry.goodWith.join(', ')}</p>
        </>
      ) : null}
      <PortraitToggle sex={sex} onChange={onSexChange} />
      <button
        type="button"
        className="story-primary story-catalog-choose"
        disabled={custom.pending}
        aria-busy={custom.pending || undefined}
        onClick={shown.choose}
      >
        {`Choose ${shown.entry.name}`}
      </button>
    </>
  ) : (
    <p className="story-catalog-empty-detail">Choose an ancestry to meet them.</p>
  )

  return (
    <CatalogFrame
      noun="ancestries"
      views={VIEWS}
      view={view}
      onViewChange={setView}
      searchable={view !== 'own'}
      query={query}
      onQueryChange={setQuery}
      tags={RACE_FILTERS}
      tag={tag}
      onTagChange={setTag}
      count={items.length}
      detailLabel="Ancestry details"
      detailRef={detailRef}
      detail={detail}
    >
      {view === 'own' ? ownView : medallions}
    </CatalogFrame>
  )
}
