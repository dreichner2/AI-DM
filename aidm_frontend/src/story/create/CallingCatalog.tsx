import { useRef, useState, type FormEvent, type KeyboardEvent } from 'react'
import {
  CLASS_FILTERS,
  PLAYABLE_CLASSES,
  classChoiceLabel,
  classSelectionFromValue,
  customClassChoice,
  filterPlayableClasses,
  type ClassCategory,
  type PlayableClass,
} from '../../classCatalog'
import { CatalogFrame } from './CatalogFrame'
import { moveListFocus } from './catalogKeys'
import type { CreationRules } from './creationRules'
import { coreClasses, type CoreClass } from './createHeroModel'
import { CallingStart } from './StepCalling'

export type CallingView = 'core' | 'all' | 'own'

const VIEWS = [
  ['core', 'Core'],
  ['all', 'All'],
  ['own', 'Your own'],
] as const

const MAX_PATHS = 6

type CallingCatalogProps = {
  /** The hero's calling now, such as `Fighter` or `Gunslinger - Sniper`. */
  current: string | null
  /** The rules in play: they name the twelve core callings, in the Calling step's order. */
  rules: CreationRules
  initialView?: CallingView
  onChoose: (calling: string) => void
}

/** `MARTIAL · FRONTLINE · 14 PATHS`: the first two role tags and how many paths it has. */
function metaLine(entry: PlayableClass) {
  const roles = entry.categories.filter((category) => category !== 'Beginner Friendly').slice(0, 2)
  const paths = `${entry.subclasses.length} ${entry.subclasses.length === 1 ? 'path' : 'paths'}`
  return [...roles, paths].join(' · ')
}

function pathsLine(entry: PlayableClass) {
  const names = entry.subclasses.slice(0, MAX_PATHS).map((subclass) => subclass.name).join(', ')
  const more = entry.subclasses.length - MAX_PATHS
  return more > 0 ? `${names} and ${more} more` : names
}

type RowProps = {
  entry: PlayableClass
  narrated: boolean
  chosen: boolean
  tabStop: boolean
  onPick: () => void
  onChoose: () => void
  onShowDetail: () => void
}

function CallingRow({ entry, narrated, chosen, tabStop, onPick, onChoose, onShowDetail }: RowProps) {
  return (
    <li>
      <button
        type="button"
        className="story-catalog-row"
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
        <span className="story-catalog-row-name">
          {entry.name}
          {narrated ? <span className="story-catalog-narrated">Narrated</span> : null}
        </span>
        <span className="story-catalog-row-text">{entry.tagline}</span>
        <span className="story-catalog-row-meta">{metaLine(entry)}</span>
      </button>
    </li>
  )
}

/**
 * "More callings" in the Story language: rows beside one detail pane, the twelve
 * core callings first, and a calling of the player's own. Uses the class catalog
 * the classic selector uses; core callings show what StepCalling shows.
 */
export function CallingCatalog({ current, rules, initialView = 'core', onChoose }: CallingCatalogProps) {
  const [view, setView] = useState<CallingView>(initialView)
  const [query, setQuery] = useState('')
  const [tag, setTag] = useState<ClassCategory | null>(null)
  const [picked, setPicked] = useState<PlayableClass | null>(null)
  const [ownName, setOwnName] = useState('')
  const detailRef = useRef<HTMLElement | null>(null)

  const core = new Map<string, CoreClass>(coreClasses(rules).map((entry) => [entry.id, entry]))
  const byKey = new Map(PLAYABLE_CLASSES.map((entry) => [entry.key, entry]))
  const coreEntries = [...core.keys()].flatMap((key) => byKey.get(key) ?? [])
  const ordered = view === 'core' ? coreEntries : [...coreEntries, ...PLAYABLE_CLASSES.filter((entry) => !core.has(entry.key))]
  const matching = new Set(filterPlayableClasses({ query, category: tag ?? 'All' }))
  const rows = view === 'own' ? [] : ordered.filter((entry) => matching.has(entry))

  const shown = picked ?? classSelectionFromValue(current)?.classEntry ?? null
  const tabStopKey = rows.some((entry) => entry.key === shown?.key) ? shown?.key : rows[0]?.key
  const choose = (entry: PlayableClass) => onChoose(classChoiceLabel(entry))
  const showDetail = () => detailRef.current?.scrollIntoView({ block: 'nearest' })

  const submitOwn = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    const choice = customClassChoice(ownName)
    if (choice) onChoose(choice)
  }

  const list =
    view === 'own' ? (
      <form className="story-catalog-form" aria-label="A calling of your own" onSubmit={submitOwn}>
        <label className="story-catalog-field">
          <span className="story-label">Your calling</span>
          <input
            value={ownName}
            placeholder="Dream Knight - Mirror Oath"
            autoComplete="off"
            onChange={(event) => setOwnName(event.target.value)}
          />
        </label>
        <p className="story-catalog-help">
          Name a calling, or a calling and its path. It is narrated: the Dungeon Master decides how its features play.
        </p>
        <div className="story-catalog-actions">
          <button type="submit" className="story-primary" disabled={!customClassChoice(ownName)}>
            Create and choose
          </button>
        </div>
      </form>
    ) : rows.length ? (
      <ul
        className="story-catalog-rows"
        aria-label="Callings"
        onKeyDown={(event: KeyboardEvent<HTMLUListElement>) => moveListFocus(event, 'column')}
      >
        {rows.map((entry) => (
          <CallingRow
            key={entry.key}
            entry={entry}
            narrated={!core.has(entry.key)}
            chosen={entry.key === shown?.key}
            tabStop={entry.key === tabStopKey}
            onPick={() => setPicked(entry)}
            onChoose={() => choose(entry)}
            onShowDetail={showDetail}
          />
        ))}
      </ul>
    ) : null

  const coreEntry = shown ? core.get(shown.key) : undefined
  const detail = shown ? (
    <>
      <div aria-live="polite">
        <h3 className="story-create-detail-name">{shown.name}</h3>
        <p className="story-create-prose">{shown.shortDescription}</p>
      </div>
      {coreEntry ? <CallingStart entry={coreEntry} /> : null}
      <h4 className="story-label">Paths</h4>
      <p className="story-catalog-line">{pathsLine(shown)}</p>
      {coreEntry ? null : (
        <p className="story-catalog-narrated-note">Narrated — the Dungeon Master decides how its features play.</p>
      )}
      <button type="button" className="story-primary story-catalog-choose" onClick={() => choose(shown)}>
        {`Choose ${shown.name}`}
      </button>
    </>
  ) : (
    <p className="story-catalog-empty-detail">Choose a calling to see what it can do.</p>
  )

  return (
    <CatalogFrame
      noun="callings"
      views={VIEWS}
      view={view}
      onViewChange={setView}
      searchable={view !== 'own'}
      query={query}
      onQueryChange={setQuery}
      tags={CLASS_FILTERS}
      tag={tag}
      onTagChange={setTag}
      count={rows.length}
      detailLabel="Calling details"
      detailRef={detailRef}
      detail={detail}
    >
      {list}
    </CatalogFrame>
  )
}
