import { AlertTriangle, Check, Search, Sparkles, X } from 'lucide-react'
import { useMemo, useState } from 'react'
import { apiFetch } from './api'
import {
  PLAYABLE_RACES,
  RACE_FILTERS,
  filterPlayableRaces,
  playableRaceFromValue,
  raceSelectionFromPlayableRace,
  profileIconSrcForRace,
  type PlayableRace,
  type RaceCategory,
} from './raceCatalog'
import { profileIconSrcForCharacter, type SexKey } from './profileIcons'
import type {
  CharacterRaceSelection,
  CustomRaceGenerateResponse,
  CustomRaceSaveResponse,
  JsonRecord,
  RaceDefinition,
  RaceSummary,
} from './types'

type RaceSelectorProps = {
  auth: string
  baseUrl: string
  selectedRace: string
  selectedRaceSelection?: CharacterRaceSelection | null
  selectedSex: string
  pending?: boolean
  onRaceChange: (race: string) => void
  onRaceSelectionChange?: (raceSelection: CharacterRaceSelection | null) => void
  onSexChange: (sex: SexKey) => void
}

const ALL_FILTER = 'All' as const
type RaceViewMode = 'recommended' | 'all' | 'custom'
const sexOptions: { key: SexKey; label: string }[] = [
  { key: 'male', label: 'Male' },
  { key: 'female', label: 'Female' },
]
const recommendedRaceNames = new Set(['Human', 'Elf', 'Dwarf', 'Halfling', 'Gnome', 'Dragonborn', 'Orc', 'Tabaxi'])

function sexValue(value: string): SexKey {
  return value === 'female' ? 'female' : 'male'
}

function isRecord(value: unknown): value is JsonRecord {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function raceVisual(race: RaceDefinition | undefined): JsonRecord {
  return isRecord(race?.visual) ? race.visual : {}
}

function customPortraitSrc(race: RaceDefinition | undefined, sex: SexKey) {
  const visual = raceVisual(race)
  const portraitKey = typeof visual.portraitKey === 'string' ? visual.portraitKey : race?.name
  return profileIconSrcForCharacter({ race: portraitKey, sex }) ?? '/profile-icons/human_male.png'
}

function raceTraitNames(race: RaceDefinition | undefined) {
  const traits = Array.isArray(race?.traits) ? race.traits : []
  return traits
    .map((trait) => (isRecord(trait) && typeof trait.name === 'string' ? trait.name : ''))
    .filter(Boolean)
}

function raceWarnings(race: RaceDefinition | undefined) {
  const balance: JsonRecord = isRecord(race?.balance) ? race.balance : {}
  const warnings = balance['warnings']
  return Array.isArray(warnings) ? warnings.filter((item): item is string => typeof item === 'string') : []
}

function balanceSummary(race: RaceDefinition | RaceSummary | undefined) {
  const balance: JsonRecord = isRecord(race?.balance) ? race.balance : {}
  const spent = typeof balance['spent'] === 'number' ? balance['spent'] : 0
  const budget = typeof balance['budget'] === 'number' ? balance['budget'] : 5
  const tier = typeof balance['tier'] === 'string' ? balance['tier'] : 'standard'
  return { spent, budget, tier }
}

function BalanceMeter({ race }: { race: RaceDefinition | RaceSummary | undefined }) {
  const { spent, budget, tier } = balanceSummary(race)
  return (
    <div className={`race-balance-meter ${tier}`}>
      <span>Balance</span>
      <strong>
        {spent} / {budget}
      </strong>
      <em>{tier.replace('_', ' ')}</em>
    </div>
  )
}

function RaceChips({ race }: { race: PlayableRace }) {
  return (
    <div className="race-trait-row" aria-label={`${race.name} traits`}>
      {race.traits.slice(0, 3).map((trait) => (
        <span key={trait}>{trait}</span>
      ))}
    </div>
  )
}

function RaceDetailsModal({
  race,
  selected,
  pending,
  onClose,
  onSelect,
}: {
  race: PlayableRace
  selected: boolean
  pending: boolean
  onClose: () => void
  onSelect: () => void
}) {
  return (
    <div
      className="race-details-backdrop"
      role="presentation"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) onClose()
      }}
    >
      <section
        className="race-details-modal"
        role="dialog"
        aria-modal="false"
        aria-labelledby="race-details-title"
        onMouseDown={(event) => event.stopPropagation()}
      >
        <header>
          <div>
            <span>Race Details</span>
            <h3 id="race-details-title">{race.name}</h3>
          </div>
          <button type="button" aria-label={`Close ${race.name} details`} onClick={onClose}>
            <X size={18} />
          </button>
        </header>
        <div className="race-details-body">
          <div className="race-details-hero">
            <img src={profileIconSrcForRace(race.key, 'male')} alt={`${race.name} male portrait`} />
            <img src={profileIconSrcForRace(race.key, 'female')} alt={`${race.name} female portrait`} />
          </div>
          <p>{race.shortDescription}</p>
          <p>{race.longDescription}</p>
          <section className="race-lore-panel" aria-label={`${race.name} story`}>
            <span>Race Story</span>
            <p>{race.originStory}</p>
          </section>
          <dl className="race-detail-grid">
            <div>
              <dt>Traits</dt>
              <dd>{race.traits.join(', ')}</dd>
            </div>
            <div>
              <dt>Build</dt>
              <dd>
                Average height: {race.averageHeight}
                <br />
                Average weight: {race.averageWeight}
              </dd>
            </div>
            <div>
              <dt>Languages</dt>
              <dd>{race.languages.join(', ')}</dd>
            </div>
            <div>
              <dt>Common Proficiencies</dt>
              <dd>{race.commonProficiencies.join(', ')}</dd>
            </div>
            <div>
              <dt>Mechanical</dt>
              <dd>
                <ul>
                  {race.mechanicalEffects.map((effect) => (
                    <li key={effect}>{effect}</li>
                  ))}
                </ul>
              </dd>
            </div>
            <div>
              <dt>AI Flavor</dt>
              <dd>{race.narrativeFlavor}</dd>
            </div>
            <div>
              <dt>Recommended</dt>
              <dd>{race.recommendedClasses.join(', ')}</dd>
            </div>
            <div>
              <dt>Often Friendly With</dt>
              <dd>{race.friendlyWith.join(', ')}</dd>
            </div>
            <div>
              <dt>Often Wary Of</dt>
              <dd>{race.waryOf.join(', ')}</dd>
            </div>
            <div>
              <dt>Difficulty</dt>
              <dd>{race.difficulty}</dd>
            </div>
            <div>
              <dt>Warnings</dt>
              <dd>{race.warnings.length ? race.warnings.join(' ') : 'None'}</dd>
            </div>
          </dl>
        </div>
        <footer>
          <button type="button" className="secondary" onClick={onClose}>
            Close
          </button>
          <button type="button" disabled={pending || selected} onClick={onSelect}>
            {selected ? 'Selected' : `Select ${race.name}`}
          </button>
        </footer>
      </section>
    </div>
  )
}

export function RaceSelector({
  auth,
  baseUrl,
  selectedRace,
  selectedRaceSelection,
  selectedSex,
  pending = false,
  onRaceChange,
  onRaceSelectionChange,
  onSexChange,
}: RaceSelectorProps) {
  const [query, setQuery] = useState('')
  const [selectedFilter, setSelectedFilter] = useState<RaceCategory | typeof ALL_FILTER>(ALL_FILTER)
  const [detailsRace, setDetailsRace] = useState<PlayableRace | null>(null)
  const [viewMode, setViewMode] = useState<RaceViewMode>('recommended')
  const [customPrompt, setCustomPrompt] = useState('')
  const [customDraft, setCustomDraft] = useState<RaceDefinition | null>(null)
  const [customPending, setCustomPending] = useState(false)
  const [customError, setCustomError] = useState('')
  const selectedRaceEntry = playableRaceFromValue(selectedRaceSelection?.raceId ?? selectedRaceSelection?.raceName ?? selectedRace)
  const selectedCustomRace =
    selectedRaceSelection?.source === 'custom' ? selectedRaceSelection.customRaceDefinition : null
  const selectedRaceLabel = selectedRaceSelection?.raceName || selectedRaceEntry?.name || selectedRace
  const selectedSexKey = sexValue(selectedSex)
  const visibleRaces = useMemo(
    () => {
      const filtered = filterPlayableRaces({ query, category: selectedFilter })
      if (viewMode === 'recommended') {
        return filtered.filter(
          (race) => recommendedRaceNames.has(race.name) || race.categories.includes('Beginner Friendly'),
        )
      }
      if (viewMode === 'custom') return []
      return filtered
    },
    [query, selectedFilter, viewMode],
  )

  const chooseRace = (race: PlayableRace) => {
    onRaceSelectionChange?.(raceSelectionFromPlayableRace(race))
    onRaceChange(race.name)
    onSexChange(selectedSexKey)
    setDetailsRace(null)
  }

  const generateCustomRace = async () => {
    const prompt = customPrompt.trim()
    if (!prompt) {
      setCustomError('Describe the custom race first.')
      return
    }
    setCustomPending(true)
    setCustomError('')
    try {
      const response = await apiFetch<CustomRaceGenerateResponse>(
        baseUrl,
        '/api/custom-races/generate',
        auth,
        {
          method: 'POST',
          body: JSON.stringify({ prompt, strictness: 'standard' }),
        },
      )
      setCustomDraft(response.draftRace)
    } catch (error) {
      setCustomError(error instanceof Error ? error.message : String(error))
    } finally {
      setCustomPending(false)
    }
  }

  const acceptCustomRace = async () => {
    if (!customDraft) return
    setCustomPending(true)
    setCustomError('')
    try {
      const response = await apiFetch<CustomRaceSaveResponse>(
        baseUrl,
        '/api/custom-races',
        auth,
        {
          method: 'POST',
          body: JSON.stringify({
            raceDefinition: customDraft,
            approvalStatus:
              customDraft.approvalStatus === 'overpowered_unreviewed' ? 'overpowered_unreviewed' : 'approved_by_user',
          }),
        },
      )
      const savedRace = response.race
      const selection: CharacterRaceSelection = {
        raceId: savedRace.id,
        raceName: savedRace.name,
        source: 'custom',
        customRaceDefinition: savedRace,
        selectedOptions: {},
      }
      onRaceSelectionChange?.(selection)
      onRaceChange(savedRace.name)
      onSexChange(selectedSexKey)
      setCustomDraft(savedRace)
    } catch (error) {
      setCustomError(error instanceof Error ? error.message : String(error))
    } finally {
      setCustomPending(false)
    }
  }

  return (
    <section className="race-selector" aria-labelledby="race-selector-title">
      <div className="race-selector-heading">
        <div>
          <span>Playable Race</span>
          <h3 id="race-selector-title">Race</h3>
        </div>
        {selectedRaceLabel ? (
          <strong>
            <Check size={15} />
            {selectedRaceLabel}
          </strong>
        ) : (
          <strong className="muted">Choose one</strong>
        )}
      </div>

      <div className="race-mode-tabs" aria-label="Race selection mode">
        {[
          ['recommended', 'Recommended'],
          ['all', 'All Races'],
          ['custom', 'Custom'],
        ].map(([mode, label]) => (
          <button
            key={mode}
            type="button"
            className={viewMode === mode ? 'selected' : ''}
            onClick={() => setViewMode(mode as RaceViewMode)}
            disabled={pending}
          >
            {label}
          </button>
        ))}
      </div>

      {viewMode !== 'custom' ? (
        <>
          <label className="race-search-field">
            <span>Search races</span>
            <div>
              <Search size={16} aria-hidden="true" />
              <input
                type="search"
                value={query}
                placeholder="Search name, traits, tags"
                onChange={(event) => setQuery(event.target.value)}
                disabled={pending}
              />
            </div>
          </label>

          <div className="race-filter-row" aria-label="Race categories">
            {[ALL_FILTER, ...RACE_FILTERS].map((filter) => (
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

          <div className="race-card-grid" aria-label="Playable races">
            {visibleRaces.map((race) => {
              const selected = selectedRaceEntry?.key === race.key && selectedRaceSelection?.source !== 'custom'
              return (
                <button
                  key={race.key}
                  type="button"
                  className={`race-card${selected ? ' selected' : ''}`}
                  aria-label={`View ${race.name} details`}
                  onClick={() => setDetailsRace(race)}
                  disabled={pending}
                >
                  <span className="race-card-art">
                    <img src={profileIconSrcForRace(race.key, selected ? selectedSexKey : 'male')} alt="" />
                    <span>{race.name}</span>
                  </span>
                  <span className="race-card-copy">
                    <strong>{race.name}</strong>
                    <em>{race.tagline}</em>
                    <RaceChips race={race} />
                    <small>Good for: {race.recommendedClasses.join(', ')}</small>
                  </span>
                </button>
              )
            })}
          </div>

          {!visibleRaces.length ? (
            <div className="race-empty-state" role="status">
              No playable races match that search.
            </div>
          ) : null}
        </>
      ) : (
        <div className="custom-race-panel">
          <label className="custom-race-prompt">
            <span>Custom race idea</span>
            <textarea
              value={customPrompt}
              placeholder="Example: Emberborn are people descended from fire spirits with glowing veins and a once-per-rest flame burst."
              onChange={(event) => setCustomPrompt(event.target.value)}
              disabled={pending || customPending}
            />
          </label>
          <div className="custom-race-actions">
            <button type="button" onClick={() => void generateCustomRace()} disabled={pending || customPending}>
              <Sparkles size={15} />
              Generate Draft
            </button>
            {customDraft ? (
              <button type="button" className="secondary" onClick={() => setCustomDraft(null)} disabled={customPending}>
                Start Over
              </button>
            ) : null}
          </div>
          {customError ? <div className="race-custom-error">{customError}</div> : null}
          {customDraft ? (
            <div className="custom-race-review">
              <div className="custom-race-review-head">
                <div>
                  <span>Review Custom Race</span>
                  <strong>{customDraft.name}</strong>
                </div>
                <BalanceMeter race={customDraft} />
              </div>
              <div className="custom-race-edit-grid">
                <label>
                  <span>Name</span>
                  <input
                    value={customDraft.name}
                    onChange={(event) =>
                      setCustomDraft((current) => (current ? { ...current, name: event.target.value } : current))
                    }
                    disabled={customPending}
                  />
                </label>
                <label>
                  <span>Short Description</span>
                  <textarea
                    value={customDraft.descriptionShort}
                    onChange={(event) =>
                      setCustomDraft((current) =>
                        current ? { ...current, descriptionShort: event.target.value } : current,
                      )
                    }
                    disabled={customPending}
                  />
                </label>
              </div>
              <p>{customDraft.descriptionShort}</p>
              <div className="race-trait-row">
                {raceTraitNames(customDraft).slice(0, 6).map((trait) => (
                  <span key={trait}>{trait}</span>
                ))}
              </div>
              {raceWarnings(customDraft).length ? (
                <div className="custom-race-warnings">
                  <AlertTriangle size={15} />
                  <span>{raceWarnings(customDraft).join(' ')}</span>
                </div>
              ) : null}
              <div className="custom-race-review-actions">
                <button type="button" onClick={() => void acceptCustomRace()} disabled={pending || customPending}>
                  Accept
                </button>
                <button type="button" className="secondary" onClick={() => void generateCustomRace()} disabled={customPending}>
                  Regenerate
                </button>
              </div>
            </div>
          ) : null}
        </div>
      )}

      {selectedRaceEntry ? (
        <div className="gender-portrait-picker" aria-label="Gender">
          <div>
            <span>Gender Portrait</span>
            <strong>{selectedRaceEntry.name}</strong>
          </div>
          <div className="gender-portrait-options">
            {sexOptions.map((option) => {
              const selected = selectedSexKey === option.key
              return (
                <button
                  key={option.key}
                  type="button"
                  className={selected ? 'selected' : ''}
                  aria-label={`${option.label} ${selectedRaceEntry.name}`}
                  onClick={() => onSexChange(option.key)}
                  disabled={pending}
                >
                  <img
                    src={profileIconSrcForRace(selectedRaceEntry.key, option.key)}
                    alt=""
                    aria-hidden="true"
                  />
                  <span>{option.label}</span>
                </button>
              )
            })}
          </div>
        </div>
      ) : null}

      {selectedCustomRace ? (
        <div className="gender-portrait-picker" aria-label="Custom race portrait">
          <div>
            <span>Gender Portrait</span>
            <strong>{selectedCustomRace.name}</strong>
          </div>
          <div className="gender-portrait-options">
            {sexOptions.map((option) => {
              const selected = selectedSexKey === option.key
              return (
                <button
                  key={option.key}
                  type="button"
                  className={selected ? 'selected' : ''}
                  aria-label={`${option.label} ${selectedCustomRace.name}`}
                  onClick={() => onSexChange(option.key)}
                  disabled={pending}
                >
                  <img src={customPortraitSrc(selectedCustomRace, option.key)} alt="" aria-hidden="true" />
                  <span>{option.label}</span>
                </button>
              )
            })}
          </div>
        </div>
      ) : null}

      {detailsRace ? (
        <RaceDetailsModal
          race={detailsRace}
          selected={selectedRaceEntry?.key === detailsRace.key}
          pending={pending}
          onClose={() => setDetailsRace(null)}
          onSelect={() => chooseRace(detailsRace)}
        />
      ) : null}

      <div className="race-count" aria-live="polite">
        {viewMode === 'custom' ? 'Custom race builder' : `${visibleRaces.length} of ${PLAYABLE_RACES.length} races`}
      </div>
    </section>
  )
}
