import { useCallback, useState, type Dispatch, type FormEvent, type SetStateAction } from 'react'
import { apiFetch } from './api'
import { backgroundFromValue } from './backgroundCatalog'
import type { PlayableRace } from './raceCatalog'
import type { CreateHeroPayload } from './story/create/createHeroModel'
import type { CharacterRaceSelection, Player, PlayerDetail } from './types'

type ValueUpdater<T> = T | ((current: T) => T)

export type PlayerEditDialogState = {
  player: Player
  characterName: string
  race: string
  raceSelection: CharacterRaceSelection | null
  sex: string
  charClass: string
  backgroundId: string
  level: string
  error: string
  pending: boolean
} | null

export type PlayerDeleteDialogState = {
  player: Player
  error: string
  pending: boolean
} | null

type UsePlayerProfileActionsOptions = {
  auth: string
  baseUrl: string
  selectedPlayer: Player | null
  selectedCampaignId: number | null
  rememberDialogTrigger: () => void
  refreshCampaignWorkspace: (campaignId: number) => Promise<void>
  setProfileSettingsOpen: Dispatch<SetStateAction<boolean>>
  setPlayerDetail: (value: ValueUpdater<PlayerDetail | null>) => void
  setSelectedPlayerId: (value: ValueUpdater<number | null>) => void
  playerUpserted: (player: Player) => void
  pushError: (category: 'persistence', message: string) => void
}

function playerDialogStateFromPlayer(player: Player): NonNullable<PlayerEditDialogState> {
  const background = backgroundFromValue(player.background?.id ?? player.background?.name)
  return {
    player,
    characterName: player.character_name ?? '',
    race: player.race ?? '',
    raceSelection: player.race_selection ?? null,
    sex: player.sex ?? 'male',
    charClass: player.char_class || player.class_ || '',
    backgroundId: background?.id ?? '',
    level: String(player.level ?? 1),
    error: '',
    pending: false,
  }
}

export function usePlayerProfileActions({
  auth,
  baseUrl,
  selectedPlayer,
  selectedCampaignId,
  rememberDialogTrigger,
  refreshCampaignWorkspace,
  setProfileSettingsOpen,
  setPlayerDetail,
  setSelectedPlayerId,
  playerUpserted,
  pushError,
}: UsePlayerProfileActionsOptions) {
  const [playerEditDialog, setPlayerEditDialog] = useState<PlayerEditDialogState>(null)
  const [playerDeleteDialog, setPlayerDeleteDialog] = useState<PlayerDeleteDialogState>(null)

  const openPlayerEditDialog = useCallback(() => {
    if (!selectedPlayer) return
    rememberDialogTrigger()
    setProfileSettingsOpen(false)
    setPlayerEditDialog(playerDialogStateFromPlayer(selectedPlayer))
  }, [rememberDialogTrigger, selectedPlayer, setProfileSettingsOpen])

  const closePlayerEditDialog = useCallback(() => {
    if (playerEditDialog?.pending) return
    setPlayerEditDialog(null)
  }, [playerEditDialog?.pending])

  const openPlayerDeleteDialog = useCallback(() => {
    if (!selectedPlayer) return
    rememberDialogTrigger()
    setProfileSettingsOpen(false)
    setPlayerDeleteDialog({
      player: selectedPlayer,
      error: '',
      pending: false,
    })
  }, [rememberDialogTrigger, selectedPlayer, setProfileSettingsOpen])

  const closePlayerDeleteDialog = useCallback(() => {
    if (playerDeleteDialog?.pending) return
    setPlayerDeleteDialog(null)
  }, [playerDeleteDialog?.pending])

  const submitPlayerEditDialog = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    if (!playerEditDialog) return
    const characterName = playerEditDialog.characterName.trim()
    const level = Number(playerEditDialog.level)
    if (!characterName) {
      setPlayerEditDialog((current) =>
        current ? { ...current, error: 'Character name is required.' } : current,
      )
      return
    }
    if (!playerEditDialog.charClass.trim()) {
      setPlayerEditDialog((current) =>
        current ? { ...current, error: 'Choose a class.' } : current,
      )
      return
    }
    if (!Number.isInteger(level) || level < 1 || level > 20) {
      setPlayerEditDialog((current) =>
        current ? { ...current, error: 'Level must be 1 through 20.' } : current,
      )
      return
    }
    let playableRace: PlayableRace | null
    try {
      const { playableRaceFromValue } = await import('./raceCatalog')
      playableRace = playableRaceFromValue(playerEditDialog.race)
    } catch {
      setPlayerEditDialog((current) =>
        current ? { ...current, error: 'Race options could not be loaded. Try again.' } : current,
      )
      return
    }
    const raceSelection =
      playerEditDialog.raceSelection ??
      (playableRace
        ? {
            raceId: playableRace.key,
            raceName: playableRace.name,
            source: 'curated' as const,
            selectedOptions: {},
          }
        : null)
    if (!raceSelection) {
      setPlayerEditDialog((current) =>
        current ? { ...current, error: 'Choose a playable race.' } : current,
      )
      return
    }

    const playerId = playerEditDialog.player.player_id
    setPlayerEditDialog((current) => (current ? { ...current, pending: true, error: '' } : current))
    try {
      const payload: Record<string, unknown> = {
        character_name: characterName,
        race: raceSelection.raceName,
        race_selection: raceSelection,
        sex: playerEditDialog.sex === 'female' ? 'female' : 'male',
        char_class: playerEditDialog.charClass.trim(),
        level,
      }
      if (playerEditDialog.backgroundId) {
        payload.background = playerEditDialog.backgroundId
      }
      const updated = await apiFetch<PlayerDetail>(baseUrl, `/api/players/${playerId}`, auth, {
        method: 'PATCH',
        body: JSON.stringify(payload),
      })
      playerUpserted(updated)
      setPlayerDetail(updated)
      setSelectedPlayerId(updated.player_id)
      setPlayerEditDialog(null)
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error)
      setPlayerEditDialog((current) => (current ? { ...current, pending: false, error: message } : current))
      pushError('persistence', `Could not update player: ${message}`)
    }
  }

  /** Creates a hero from the creation flow and selects it, which joins the campaign. Errors reach the caller. */
  const createHeroCharacter = useCallback(
    async (campaignId: number, payload: CreateHeroPayload) => {
      const created = await apiFetch<{ player_id: number }>(
        baseUrl,
        `/api/players/campaigns/${campaignId}/players`,
        auth,
        {
          method: 'POST',
          body: JSON.stringify(payload),
        },
      )
      const detail = await apiFetch<PlayerDetail>(baseUrl, `/api/players/${created.player_id}`, auth)
      playerUpserted(detail)
      setPlayerDetail(detail)
      setSelectedPlayerId(detail.player_id)
    },
    [auth, baseUrl, playerUpserted, setPlayerDetail, setSelectedPlayerId],
  )

  const submitPlayerDeleteDialog = async () => {
    if (!playerDeleteDialog) return
    const player = playerDeleteDialog.player
    setPlayerDeleteDialog((current) => (current ? { ...current, pending: true, error: '' } : current))
    try {
      await apiFetch<{ deleted: boolean }>(baseUrl, `/api/players/${player.player_id}`, auth, {
        method: 'DELETE',
      })
      setPlayerDetail(null)
      setSelectedPlayerId((current) => (current === player.player_id ? null : current))
      setPlayerDeleteDialog(null)
      const campaignId = selectedCampaignId ?? player.campaign_id
      if (campaignId) {
        await refreshCampaignWorkspace(campaignId)
      }
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error)
      setPlayerDeleteDialog((current) => (current ? { ...current, pending: false, error: message } : current))
      pushError('persistence', `Could not delete player: ${message}`)
    }
  }

  return {
    closePlayerDeleteDialog,
    closePlayerEditDialog,
    createHeroCharacter,
    openPlayerDeleteDialog,
    openPlayerEditDialog,
    playerDeleteDialog,
    playerEditDialog,
    setPlayerEditDialog,
    submitPlayerDeleteDialog,
    submitPlayerEditDialog,
  }
}
