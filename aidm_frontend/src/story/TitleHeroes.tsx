import { useId, useRef, type KeyboardEvent } from 'react'

export type TitleHero = {
  id: string
  name: string
  race: string
  charClass: string
  tagline: string
  portrait: string
}

export type TitleHeroesStatus = 'loading' | 'ready' | 'failed'

type TitleHeroesProps = {
  heroes: TitleHero[]
  loading: boolean
  selectedId: string | null
  disabled: boolean
  onSelect: (heroId: string) => void
}

const PLACEHOLDER_COUNT = 4

/** The ready-made heroes as a row of portraits; one is always chosen. */
export function TitleHeroes({ heroes, loading, selectedId, disabled, onSelect }: TitleHeroesProps) {
  const idPrefix = useId()
  const radioRefs = useRef<Array<HTMLButtonElement | null>>([])

  if (loading) {
    // Empty rings that hold the row's place, so nothing below moves when the heroes arrive.
    return (
      <div className="story-title-heroes" data-loading="" aria-hidden="true">
        <div className="story-title-hero-row">
          {Array.from({ length: PLACEHOLDER_COUNT }, (_, index) => (
            <span key={index} className="story-title-hero">
              <span className="story-title-hero-portrait" />
              <span className="story-title-hero-name">{' '}</span>
              <span className="story-label story-title-hero-kind">{' '}</span>
            </span>
          ))}
        </div>
        <p className="story-title-hero-tagline">{' '}</p>
      </div>
    )
  }

  const selectedIndex = heroes.findIndex((hero) => hero.id === selectedId)
  const selected = selectedIndex >= 0 ? heroes[selectedIndex] : null
  const tabStop = Math.max(selectedIndex, 0)

  const choose = (index: number) => {
    const next = (index + heroes.length) % heroes.length
    onSelect(heroes[next].id)
    radioRefs.current[next]?.focus()
  }

  const onKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    const current = radioRefs.current.findIndex((radio) => radio === event.target)
    if (current < 0) return
    const target =
      event.key === 'ArrowRight' || event.key === 'ArrowDown'
        ? current + 1
        : event.key === 'ArrowLeft' || event.key === 'ArrowUp'
          ? current - 1
          : event.key === 'Home'
            ? 0
            : event.key === 'End'
              ? heroes.length - 1
              : null
    if (target === null) return
    event.preventDefault()
    choose(target)
  }

  return (
    <div className="story-title-heroes">
      <div
        role="radiogroup"
        aria-label="Choose your hero"
        className="story-title-hero-row"
        onKeyDown={onKeyDown}
      >
        {heroes.map((hero, index) => {
          const checked = index === selectedIndex
          return (
            <button
              key={hero.id}
              ref={(node) => {
                radioRefs.current[index] = node
              }}
              type="button"
              role="radio"
              aria-checked={checked}
              aria-labelledby={`${idPrefix}-${index}-name`}
              aria-describedby={`${idPrefix}-${index}-kind`}
              tabIndex={index === tabStop ? 0 : -1}
              className="story-title-hero"
              disabled={disabled}
              onClick={() => onSelect(hero.id)}
            >
              <span className="story-title-hero-portrait">
                <img src={hero.portrait} alt="" draggable={false} />
              </span>
              <span id={`${idPrefix}-${index}-name`} className="story-title-hero-name">
                {hero.name}
              </span>
              <span id={`${idPrefix}-${index}-kind`} className="story-label story-title-hero-kind">
                {`${hero.race} ${hero.charClass}`}
              </span>
            </button>
          )
        })}
      </div>
      {selected?.tagline ? (
        <p key={selected.id} className="story-title-hero-tagline">
          {selected.tagline}
        </p>
      ) : null}
    </div>
  )
}
