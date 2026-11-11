import { useCallback, useEffect, useState } from 'react'
import { apiFetch } from './api'
import type { JoinableHero, JoinableHeroesResponse, PlayerCreatedResponse, PlayerDetail } from './types'

type UseJoinableHeroesOptions = {
  auth: string
  baseUrl: string
  campaignId: number | null
  /** The hero choice is open: its list of the player's other heroes is fetched for this adventure. */
  choosing: boolean
  /** A brought hero's detail, to select as a created one is. */
  onBrought: (detail: PlayerDetail) => void
}

function errorText(error: unknown) {
  return error instanceof Error ? error.message : String(error)
}

/** The player's heroes from their other adventures that could join this one; none when the list cannot load. */
function fetchJoinableHeroes(baseUrl: string, auth: string, campaignId: number) {
  return apiFetch<JoinableHeroesResponse>(baseUrl, `/api/players/campaigns/${campaignId}/joinable-heroes`, auth).then(
    (response) => response.heroes,
    () => [] as JoinableHero[],
  )
}

/**
 * The heroes a player already has in their other adventures, which the hero choice offers under `Your heroes`,
 * and bringing one in: the adventure gets its own copy, as if it had been created here.
 */
export function useJoinableHeroes({ auth, baseUrl, campaignId, choosing, onBrought }: UseJoinableHeroesOptions) {
  const [heroes, setHeroes] = useState<{ campaignId: number; list: JoinableHero[] } | null>(null)
  const [note, setNote] = useState('')
  const [pendingId, setPendingId] = useState<number | null>(null)

  useEffect(() => {
    if (!choosing || campaignId === null) return
    let cancelled = false
    void fetchJoinableHeroes(baseUrl, auth, campaignId).then((list) => {
      if (cancelled) return
      setHeroes({ campaignId, list })
      setNote('')
    })
    return () => {
      cancelled = true
    }
  }, [auth, baseUrl, campaignId, choosing])

  const bringHero = useCallback(
    async (hero: JoinableHero) => {
      if (campaignId === null || pendingId !== null) return
      setPendingId(hero.player_id)
      setNote('')
      try {
        const created = await apiFetch<PlayerCreatedResponse>(baseUrl, `/api/players/campaigns/${campaignId}/players/bring`, auth, {
          method: 'POST',
          body: JSON.stringify({ player_id: hero.player_id }),
        })
        onBrought(await apiFetch<PlayerDetail>(baseUrl, `/api/players/${created.player_id}`, auth))
      } catch (error) {
        setNote(errorText(error))
      } finally {
        setPendingId(null)
      }
    },
    [auth, baseUrl, campaignId, onBrought, pendingId],
  )

  return {
    joinableHeroes: heroes && heroes.campaignId === campaignId ? heroes.list : [],
    bringNote: note,
    bringingId: pendingId,
    bringHero,
  }
}
