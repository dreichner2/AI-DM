import { Check, Search, X } from 'lucide-react'
import { useMemo, useState } from 'react'
import {
  CLASS_FILTERS,
  PLAYABLE_CLASSES,
  STARTER_CLASS_NAMES,
  classChoiceLabel,
  classSelectionFromValue,
  filterPlayableClasses,
  type ClassCategory,
  type PlayableClass,
  type PlayableSubclass,
} from './classCatalog'

type ClassSelectorProps = {
  selectedClass: string
  pending?: boolean
  onClassChange: (charClass: string) => void
}

const ALL_FILTER = 'All' as const
type ClassViewMode = 'starter' | 'all' | 'custom'

const starterClassNames = new Set<string>(STARTER_CLASS_NAMES)
const quickClassNames = new Set<string>(STARTER_CLASS_NAMES.slice(0, 8))

function ClassChips({ classEntry }: { classEntry: PlayableClass }) {
  return (
    <div className="race-trait-row class-trait-row" aria-label={`${classEntry.name} tags`}>
      {classEntry.categories.slice(0, 3).map((category) => (
        <span key={category}>{category}</span>
      ))}
    </div>
  )
}

function ClassDetailsModal({
  classEntry,
  selectedValue,
  pending,
  onClose,
  onSelect,
}: {
  classEntry: PlayableClass
  selectedValue: string
  pending: boolean
  onClose: () => void
  onSelect: (classEntry: PlayableClass, subclass?: PlayableSubclass) => void
}) {
  const selected = classSelectionFromValue(selectedValue)
  const selectedClassOnly =
    selected?.classEntry.key === classEntry.key && !selected.subclass && selectedValue.trim().length > 0
  return (
    <div
      className="race-details-backdrop"
      role="presentation"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) onClose()
      }}
    >
      <section
        className="race-details-modal class-details-modal"
        role="dialog"
        aria-modal="false"
        aria-labelledby="class-details-title"
        onMouseDown={(event) => event.stopPropagation()}
      >
        <header>
          <div>
            <span>Class Details</span>
            <h3 id="class-details-title">{classEntry.name}</h3>
          </div>
          <button type="button" aria-label={`Close ${classEntry.name} details`} onClick={onClose}>
            <X size={18} />
          </button>
        </header>
        <div className="race-details-body class-details-body">
          <section className="race-lore-panel class-summary-panel" aria-label={`${classEntry.name} summary`}>
            <span>{classEntry.powerSource}</span>
            <p>{classEntry.shortDescription}</p>
          </section>
          <p>{classEntry.longDescription}</p>
          <dl className="race-detail-grid class-detail-grid">
            <div>
              <dt>Role</dt>
              <dd>{classEntry.combatRole}</dd>
            </div>
            <div>
              <dt>Magic</dt>
              <dd>{classEntry.magicLevel}</dd>
            </div>
            <div>
              <dt>Difficulty</dt>
              <dd>{classEntry.difficulty}</dd>
            </div>
            <div>
              <dt>Tags</dt>
              <dd>{classEntry.categories.join(', ')}</dd>
            </div>
          </dl>
          <section className="class-subclass-panel" aria-label={`${classEntry.name} subclasses`}>
            <div className="class-subclass-heading">
              <span>Subclass Paths</span>
              <strong>{classEntry.subclasses.length}</strong>
            </div>
            <div className="class-subclass-grid">
              {classEntry.subclasses.map((subclass) => {
                const subclassSelected =
                  selected?.classEntry.key === classEntry.key && selected.subclass?.key === subclass.key
                return (
                  <button
                    key={subclass.key}
                    type="button"
                    className={`class-subclass-option${subclassSelected ? ' selected' : ''}`}
                    aria-label={`Select ${classChoiceLabel(classEntry, subclass)}`}
                    onClick={() => onSelect(classEntry, subclass)}
                    disabled={pending}
                  >
                    <strong>{subclass.name}</strong>
                    <span>{subclass.tagline}</span>
                  </button>
                )
              })}
            </div>
          </section>
        </div>
        <footer>
          <button type="button" className="secondary" onClick={onClose}>
            Close
          </button>
          <button type="button" disabled={pending || selectedClassOnly} onClick={() => onSelect(classEntry)}>
            {selectedClassOnly ? 'Selected' : `Select ${classEntry.name}`}
          </button>
        </footer>
      </section>
    </div>
  )
}

export function ClassSelector({ selectedClass, pending = false, onClassChange }: ClassSelectorProps) {
  const [expanded, setExpanded] = useState(false)
  const [query, setQuery] = useState('')
  const [selectedFilter, setSelectedFilter] = useState<ClassCategory | typeof ALL_FILTER>(ALL_FILTER)
  const [viewMode, setViewMode] = useState<ClassViewMode>('starter')
  const [detailsClass, setDetailsClass] = useState<PlayableClass | null>(null)
  const [customClass, setCustomClass] = useState('')

  const selected = classSelectionFromValue(selectedClass)
  const selectedClassLabel = selected ? classChoiceLabel(selected.classEntry, selected.subclass) : selectedClass.trim()

  const quickClasses = useMemo(
    () => PLAYABLE_CLASSES.filter((classEntry) => quickClassNames.has(classEntry.name)),
    [],
  )

  const visibleClasses = useMemo(() => {
    const filtered = filterPlayableClasses({ query, category: selectedFilter })
    if (viewMode === 'starter') return filtered.filter((classEntry) => starterClassNames.has(classEntry.name))
    if (viewMode === 'custom') return []
    return filtered
  }, [query, selectedFilter, viewMode])

  const chooseClass = (classEntry: PlayableClass, subclass?: PlayableSubclass) => {
    onClassChange(classChoiceLabel(classEntry, subclass))
    setDetailsClass(null)
  }

  const useCustomClass = () => {
    const trimmed = customClass.trim()
    if (!trimmed) return
    onClassChange(trimmed)
    setExpanded(false)
  }

  return (
    <section className="race-selector class-selector" aria-labelledby="class-selector-title">
      <div className="race-selector-heading class-selector-heading">
        <div>
          <span>Adventuring Path</span>
          <h3 id="class-selector-title">Class</h3>
        </div>
        {selectedClassLabel ? (
          <strong>
            <Check size={15} />
            {selectedClassLabel}
          </strong>
        ) : (
          <strong className="muted">Choose one</strong>
        )}
      </div>

      <div className="class-quick-grid" aria-label="Starter class paths">
        {quickClasses.map((classEntry) => {
          const classSelected = selected?.classEntry.key === classEntry.key
          return (
            <button
              key={classEntry.key}
              type="button"
              className={classSelected ? 'selected' : ''}
              aria-label={`Preview ${classEntry.name} class`}
              onClick={() => setDetailsClass(classEntry)}
              disabled={pending}
            >
              <strong>{classEntry.name}</strong>
              <span>{classEntry.combatRole}</span>
            </button>
          )
        })}
      </div>

      <button
        type="button"
        className="class-browse-toggle"
        onClick={() => setExpanded((current) => !current)}
        disabled={pending}
        aria-expanded={expanded}
      >
        {expanded ? 'Hide class catalog' : 'Browse all classes'}
      </button>

      {expanded ? (
        <>
          <div className="race-mode-tabs" aria-label="Class selection mode">
            {[
              ['starter', 'Starter'],
              ['all', 'All Classes'],
              ['custom', 'Custom'],
            ].map(([mode, label]) => (
              <button
                key={mode}
                type="button"
                className={viewMode === mode ? 'selected' : ''}
                onClick={() => setViewMode(mode as ClassViewMode)}
                disabled={pending}
              >
                {label}
              </button>
            ))}
          </div>

          {viewMode !== 'custom' ? (
            <>
              <label className="race-search-field">
                <span>Search classes</span>
                <div>
                  <Search size={16} aria-hidden="true" />
                  <input
                    type="search"
                    value={query}
                    placeholder="Search class, subclass, role"
                    onChange={(event) => setQuery(event.target.value)}
                    disabled={pending}
                  />
                </div>
              </label>

              <div className="race-filter-row" aria-label="Class categories">
                {[ALL_FILTER, ...CLASS_FILTERS].map((filter) => (
                  <button
                    key={filter}
                    type="button"
                    className={selectedFilter === filter ? 'selected' : ''}
                    onClick={() => setSelectedFilter(filter)}
                    disabled={pending}
                  >
                    {filter}
                  </button>
                ))}
              </div>

              <div className="class-card-grid" aria-label="Playable classes">
                {visibleClasses.map((classEntry) => {
                  const classSelected = selected?.classEntry.key === classEntry.key
                  return (
                    <button
                      key={classEntry.key}
                      type="button"
                      className={`class-card${classSelected ? ' selected' : ''}`}
                      aria-label={`View ${classEntry.name} details`}
                      onClick={() => setDetailsClass(classEntry)}
                      disabled={pending}
                    >
                      <span className="class-card-copy">
                        <strong>{classEntry.name}</strong>
                        <em>{classEntry.tagline}</em>
                        <ClassChips classEntry={classEntry} />
                        <small>
                          {classEntry.subclasses.length} subclasses: {classEntry.subclasses.slice(0, 3).map((subclass) => subclass.name).join(', ')}
                        </small>
                      </span>
                    </button>
                  )
                })}
              </div>

              {!visibleClasses.length ? (
                <div className="race-empty-state" role="status">
                  No classes match that search.
                </div>
              ) : null}
            </>
          ) : (
            <div className="custom-class-panel">
              <label className="custom-class-field">
                <span>Custom class or subclass</span>
                <input
                  value={customClass}
                  placeholder="Example: Dream Knight - Mirror Oath"
                  onChange={(event) => setCustomClass(event.target.value)}
                  disabled={pending}
                />
              </label>
              <button type="button" onClick={useCustomClass} disabled={pending || !customClass.trim()}>
                Use Custom Class
              </button>
            </div>
          )}
        </>
      ) : null}

      {detailsClass ? (
        <ClassDetailsModal
          classEntry={detailsClass}
          selectedValue={selectedClass}
          pending={pending}
          onClose={() => setDetailsClass(null)}
          onSelect={chooseClass}
        />
      ) : null}
    </section>
  )
}
