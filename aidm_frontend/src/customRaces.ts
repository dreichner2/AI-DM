import { useEffect, useState } from 'react'
import { apiFetch } from './api'
import type {
  CharacterRaceSelection,
  CustomRaceGenerateResponse,
  CustomRaceSaveResponse,
  JsonRecord,
  RaceDefinition,
  RaceListResponse,
  RaceSummary,
} from './types'

/*
 * Ancestries a player invents with the Dungeon Master: the server calls, the
 * draft being reviewed and the saved list, shared by the classic race selector
 * and the Story ancestry catalog.
 */

export type CustomRaceGenerationMode = 'canon' | 'balanced'

export function isRecord(value: unknown): value is JsonRecord {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

export function raceVisual(race: RaceDefinition | RaceSummary | undefined): JsonRecord {
  return isRecord(race?.visual) ? race.visual : {}
}

/** The portrait art a custom ancestry borrows: its visual portrait key, else its name. */
export function customRacePortraitKey(race: RaceDefinition | RaceSummary | undefined) {
  const visual = raceVisual(race)
  return typeof visual.portraitKey === 'string' ? visual.portraitKey : race?.name
}

/** A custom ancestry's traits that have a name, with their description when the draft gives one. */
export function customRaceTraits(race: RaceDefinition | undefined) {
  const traits = Array.isArray(race?.traits) ? race.traits : []
  return traits
    .map((trait) => ({
      name: isRecord(trait) && typeof trait.name === 'string' ? trait.name : '',
      text: isRecord(trait) && typeof trait.description === 'string' ? trait.description : '',
    }))
    .filter((trait) => trait.name)
}

export function customRaceWarnings(race: RaceDefinition | undefined) {
  const balance: JsonRecord = isRecord(race?.balance) ? race.balance : {}
  const warnings = balance['warnings']
  return Array.isArray(warnings) ? warnings.filter((item): item is string => typeof item === 'string') : []
}

export function customRaceBalance(race: RaceDefinition | RaceSummary | undefined) {
  const balance: JsonRecord = isRecord(race?.balance) ? race.balance : {}
  const spent = typeof balance['spent'] === 'number' ? balance['spent'] : 0
  const budget = typeof balance['budget'] === 'number' ? balance['budget'] : 5
  const tier = typeof balance['tier'] === 'string' ? balance['tier'] : 'standard'
  return { spent, budget, tier }
}

function customSelection(race: RaceDefinition): CharacterRaceSelection {
  return {
    raceId: race.id,
    raceName: race.name,
    source: 'custom',
    customRaceDefinition: race,
    selectedOptions: {},
  }
}

function errorText(error: unknown) {
  return error instanceof Error ? error.message : String(error)
}

type UseCustomRacesOptions = {
  auth: string
  baseUrl: string
  /** While true the saved custom ancestries are fetched (again each time it turns true). */
  listing: boolean
  /** Called with the selection when a draft is saved or a saved ancestry is picked. */
  onChosen: (selection: CharacterRaceSelection) => void
}

export function useCustomRaces({ auth, baseUrl, listing, onChosen }: UseCustomRacesOptions) {
  const [prompt, setPrompt] = useState('')
  const [draft, setDraft] = useState<RaceDefinition | null>(null)
  const [pending, setPending] = useState(false)
  const [error, setError] = useState('')
  const [saved, setSaved] = useState<RaceSummary[]>([])
  const [savedLoading, setSavedLoading] = useState(false)
  const [savedError, setSavedError] = useState('')

  useEffect(() => {
    if (!listing) return
    let cancelled = false
    void Promise.resolve().then(() => {
      if (cancelled) return
      setSavedLoading(true)
      setSavedError('')
    })
    apiFetch<RaceListResponse>(baseUrl, '/api/races?source=custom', auth)
      .then((response) => {
        if (!cancelled) setSaved(response.races)
      })
      .catch((failure) => {
        if (!cancelled) setSavedError(errorText(failure))
      })
      .finally(() => {
        if (!cancelled) setSavedLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [auth, baseUrl, listing])

  const generate = async (generationMode: CustomRaceGenerationMode = 'canon') => {
    const idea = prompt.trim()
    if (!idea) {
      setError('Describe the custom race first.')
      return
    }
    setPending(true)
    setError('')
    try {
      const response = await apiFetch<CustomRaceGenerateResponse>(baseUrl, '/api/custom-races/generate', auth, {
        method: 'POST',
        body: JSON.stringify({
          prompt: idea,
          strictness: 'standard',
          generationMode,
          ...(generationMode === 'balanced' && draft ? { currentDraft: draft } : {}),
        }),
      })
      setDraft(response.draftRace)
    } catch (failure) {
      setError(errorText(failure))
    } finally {
      setPending(false)
    }
  }

  const accept = async () => {
    if (!draft) return
    setPending(true)
    setError('')
    try {
      const response = await apiFetch<CustomRaceSaveResponse>(baseUrl, '/api/custom-races', auth, {
        method: 'POST',
        body: JSON.stringify({
          raceDefinition: draft,
          approvalStatus: draft.approvalStatus === 'overpowered_unreviewed' ? 'overpowered_unreviewed' : 'approved_by_user',
        }),
      })
      const savedRace = response.race
      onChosen(customSelection(savedRace))
      setDraft(savedRace)
      setSaved((current) => [response.summary, ...current.filter((race) => race.id !== response.summary.id)])
    } catch (failure) {
      setError(errorText(failure))
    } finally {
      setPending(false)
    }
  }

  /** Fetches a saved ancestry's full definition and chooses it. */
  const chooseSaved = async (race: RaceSummary) => {
    setPending(true)
    setError('')
    try {
      const workspaceQuery = race.workspaceId ? `?workspaceId=${encodeURIComponent(race.workspaceId)}` : ''
      const fullRace = await apiFetch<RaceDefinition>(
        baseUrl,
        `/api/races/${encodeURIComponent(race.id)}${workspaceQuery}`,
        auth,
      )
      onChosen(customSelection(fullRace))
      setDraft(fullRace)
    } catch (failure) {
      setError(errorText(failure))
    } finally {
      setPending(false)
    }
  }

  return {
    prompt,
    setPrompt,
    draft,
    setDraft,
    pending,
    error,
    saved,
    savedLoading,
    savedError,
    generate,
    accept,
    chooseSaved,
  }
}
