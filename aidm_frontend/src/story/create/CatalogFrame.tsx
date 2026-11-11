import { Search } from 'lucide-react'
import { useEffect, useId, useRef, useState, type KeyboardEvent, type ReactNode, type RefObject } from 'react'
import '../../styles/story-catalog.css'

type CatalogFrameProps<View extends string, Tag extends string> = {
  /** `ancestries` or `callings`, for the controls' accessible names. */
  noun: string
  views: readonly (readonly [View, string])[]
  view: View
  onViewChange: (view: View) => void
  /** Whether the search field and the filter apply to this view. */
  searchable: boolean
  query: string
  onQueryChange: (query: string) => void
  tags: readonly Tag[]
  tag: Tag | null
  onTagChange: (tag: Tag | null) => void
  /** How many items the current view, search and filter leave; 0 shows the empty line. */
  count: number
  detailLabel: string
  detailRef: RefObject<HTMLElement | null>
  detail: ReactNode
  children: ReactNode
}

/**
 * The shared shape of the Story catalogs: text tabs and an underlined search on
 * one line, a closed-by-default tag filter, then the list beside a sticky detail
 * pane (one column, detail first, on narrow screens).
 */
export function CatalogFrame<View extends string, Tag extends string>({
  noun,
  views,
  view,
  onViewChange,
  searchable,
  query,
  onQueryChange,
  tags,
  tag,
  onTagChange,
  count,
  detailLabel,
  detailRef,
  detail,
  children,
}: CatalogFrameProps<View, Tag>) {
  const baseId = useId()
  const searchRef = useRef<HTMLInputElement | null>(null)
  const [filterOpen, setFilterOpen] = useState(false)
  const tabId = (target: View) => `${baseId}-tab-${target}`

  useEffect(() => {
    // The overlay opens on the search field (or, in a view without one, on the overlay itself).
    searchRef.current?.focus()
  }, [])

  const onTabKeyDown = (event: KeyboardEvent<HTMLButtonElement>) => {
    const index = views.findIndex(([target]) => target === view)
    const step: Record<string, number> = { ArrowRight: 1, ArrowLeft: -1 }
    if (!Object.hasOwn(step, event.key)) return
    event.preventDefault()
    const [next] = views[(index + step[event.key] + views.length) % views.length]
    onViewChange(next)
    document.getElementById(tabId(next))?.focus()
  }

  let empty: ReactNode = null
  if (searchable && count === 0) {
    empty = query.trim() ? (
      <p className="story-catalog-empty" role="status">
        Nothing by that name.{' '}
        <button type="button" className="story-link" onClick={() => onQueryChange('')}>
          Clear search
        </button>
      </p>
    ) : tag ? (
      <p className="story-catalog-empty" role="status">
        {`Nothing tagged ${tag} here.`}{' '}
        <button type="button" className="story-link" onClick={() => onTagChange(null)}>
          Clear
        </button>
      </p>
    ) : null
  }

  return (
    <div className="story-catalog">
      <div className="story-catalog-controls">
        <div className="story-catalog-tabs" role="tablist" aria-label={`Which ${noun}`}>
          {views.map(([target, label]) => (
            <button
              key={target}
              id={tabId(target)}
              type="button"
              role="tab"
              className="story-link"
              aria-selected={target === view}
              aria-controls={`${baseId}-panel`}
              tabIndex={target === view ? 0 : -1}
              onClick={() => onViewChange(target)}
              onKeyDown={onTabKeyDown}
            >
              {label}
            </button>
          ))}
        </div>
        {searchable ? (
          <label className="story-catalog-search">
            <Search size={16} strokeWidth={1.6} aria-hidden="true" />
            <input
              ref={searchRef}
              type="search"
              placeholder="Search"
              aria-label={`Search ${noun}`}
              autoComplete="off"
              spellCheck={false}
              value={query}
              onChange={(event) => onQueryChange(event.target.value)}
            />
          </label>
        ) : null}
      </div>

      {searchable ? (
        <div className="story-catalog-filter">
          <button
            type="button"
            className="story-link story-catalog-filter-toggle"
            aria-expanded={filterOpen}
            aria-controls={`${baseId}-tags`}
            onClick={() => setFilterOpen((open) => !open)}
          >
            {tag && !filterOpen ? `Filter: ${tag}` : 'Filter'}
          </button>
          {filterOpen ? (
            <div id={`${baseId}-tags`} className="story-catalog-tags" role="group" aria-label={`Filter ${noun} by tag`}>
              {tags.map((option) => (
                <button
                  key={option}
                  type="button"
                  className="story-link"
                  aria-pressed={option === tag}
                  onClick={() => onTagChange(option === tag ? null : option)}
                >
                  {option}
                </button>
              ))}
              <button type="button" className="story-link story-catalog-clear" disabled={!tag} onClick={() => onTagChange(null)}>
                Clear
              </button>
            </div>
          ) : null}
        </div>
      ) : null}

      <div
        id={`${baseId}-panel`}
        className="story-catalog-body"
        role="tabpanel"
        aria-labelledby={tabId(view)}
      >
        <div className="story-catalog-list">{empty ?? children}</div>
        <section ref={detailRef} className="story-catalog-detail" aria-label={detailLabel}>
          {detail}
        </section>
      </div>
    </div>
  )
}
