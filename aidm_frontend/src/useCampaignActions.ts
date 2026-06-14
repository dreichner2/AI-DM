import { useCallback, useState, type Dispatch, type FormEvent, type SetStateAction } from 'react'
import { ApiClientError, apiFetch } from './api'
import type { InspectorTab } from './InspectorPanel'
import type { MainTab } from './SessionBoard'
import type {
  Campaign,
  SessionLogEntry,
  SessionState,
  StreamingTurn,
  TimelineEntry,
  World,
} from './types'

type ValueUpdater<T> = T | ((current: T) => T)

export type CreateCampaignForm = {
  title: string
  description: string
  worldId: string
  worldName: string
  packId: string
}

export type CampaignLengthEstimate = {
  label?: string
  sessions_min?: number
  sessions_max?: number
  hours_min?: number
  hours_max?: number
  checkpoint_count?: number
  encounter_count?: number
  pacing?: string
}

export type CampaignPackExample = {
  pack_id: string
  title: string
  description: string
  short_description: string
  version?: string
  schema_version?: string
  source_filename?: string
  world_name?: string | null
  length_estimate?: CampaignLengthEstimate | null
}

export type CampaignActionDialogState = {
  mode: 'rename' | 'archive' | 'restore' | 'delete'
  campaign: Campaign
  title: string
  description: string
  error: string
  pending: boolean
} | null

type UseCampaignActionsOptions = {
  auth: string
  baseUrl: string
  campaign: Campaign | null
  selectedCampaignId: number | null
  defaultWorldId: number | null
  rememberDialogTrigger: () => void
  refreshRoot: () => Promise<void>
  refreshCampaignWorkspace: (campaignId: number) => Promise<void>
  campaignUpserted: (campaign: Campaign) => void
  campaignRemoved: (campaignId: number) => void
  setSelectedCampaignId: (value: ValueUpdater<number | null>) => void
  setSelectedSessionId: (value: ValueUpdater<number | null>) => void
  setLogEntries: (value: ValueUpdater<SessionLogEntry[]>) => void
  setSessionState: (value: ValueUpdater<SessionState | null>) => void
  setOptimisticEntries: Dispatch<SetStateAction<TimelineEntry[]>>
  setStreamingTurn: Dispatch<SetStateAction<StreamingTurn | null>>
  setMainTab: Dispatch<SetStateAction<MainTab>>
  setInspectorTab: Dispatch<SetStateAction<InspectorTab>>
  pushError: (category: 'persistence', message: string) => void
}

const emptyCreateCampaignForm: CreateCampaignForm = {
  title: '',
  description: '',
  worldId: '',
  worldName: '',
  packId: '',
}

function parsePositiveInt(value: string | null) {
  if (!value) return null
  const parsed = Number(value)
  return Number.isInteger(parsed) && parsed > 0 ? parsed : null
}

export function useCampaignActions({
  auth,
  baseUrl,
  campaign,
  selectedCampaignId,
  defaultWorldId,
  rememberDialogTrigger,
  refreshRoot,
  refreshCampaignWorkspace,
  campaignUpserted,
  campaignRemoved,
  setSelectedCampaignId,
  setSelectedSessionId,
  setLogEntries,
  setSessionState,
  setOptimisticEntries,
  setStreamingTurn,
  setMainTab,
  setInspectorTab,
  pushError,
}: UseCampaignActionsOptions) {
  const [createCampaignOpen, setCreateCampaignOpen] = useState(false)
  const [createCampaignPending, setCreateCampaignPending] = useState(false)
  const [createCampaignError, setCreateCampaignError] = useState('')
  const [createCampaignPackOptions, setCreateCampaignPackOptions] = useState<CampaignPackExample[]>([])
  const [createCampaignPackOptionsPending, setCreateCampaignPackOptionsPending] = useState(false)
  const [createCampaignForm, setCreateCampaignForm] = useState<CreateCampaignForm>(
    emptyCreateCampaignForm,
  )
  const [campaignActionDialog, setCampaignActionDialog] =
    useState<CampaignActionDialogState>(null)

  const loadCreateCampaignPackOptions = useCallback(async () => {
    setCreateCampaignPackOptionsPending(true)
    try {
      const response = await apiFetch<{ packs: CampaignPackExample[] }>(
        baseUrl,
        '/api/campaigns/example-packs',
        auth,
      )
      setCreateCampaignPackOptions(Array.isArray(response.packs) ? response.packs : [])
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error)
      setCreateCampaignPackOptions([])
      pushError('persistence', `Could not load campaign packs: ${message}`)
    } finally {
      setCreateCampaignPackOptionsPending(false)
    }
  }, [auth, baseUrl, pushError])

  const openCreateCampaignDialog = useCallback(() => {
    rememberDialogTrigger()
    setCreateCampaignForm({
      ...emptyCreateCampaignForm,
      worldId: defaultWorldId ? String(defaultWorldId) : '',
    })
    setCreateCampaignError('')
    setCreateCampaignOpen(true)
    void loadCreateCampaignPackOptions()
  }, [defaultWorldId, loadCreateCampaignPackOptions, rememberDialogTrigger])

  const closeCreateCampaignDialog = useCallback(() => {
    if (createCampaignPending) return
    setCreateCampaignOpen(false)
  }, [createCampaignPending])

  const createWorldForCampaign = useCallback(
    async (title: string, description: string) => {
      const worldName = createCampaignForm.worldName.trim() || `${title} World`
      const world = await apiFetch<Pick<World, 'world_id'>>(baseUrl, '/api/worlds', auth, {
        method: 'POST',
        body: JSON.stringify({
          name: worldName,
          description: description || `World for ${title}`,
        }),
      })
      return world.world_id
    },
    [auth, baseUrl, createCampaignForm.worldName],
  )

  const submitCreateCampaign = useCallback(
    async (event?: FormEvent<HTMLFormElement>) => {
      event?.preventDefault()
      const title = createCampaignForm.title.trim()
      const description = createCampaignForm.description.trim()
      const selectedPackId = createCampaignForm.packId.trim()
      if (!selectedPackId && !title) {
        setCreateCampaignError('Campaign name is required.')
        return
      }

      setCreateCampaignPending(true)
      setCreateCampaignError('')

      try {
        if (selectedPackId) {
          const selectedWorldId = parsePositiveInt(createCampaignForm.worldId)
          const importBody: { world_id?: number } = {}
          if (selectedWorldId) {
            importBody.world_id = selectedWorldId
          }
          const result = await apiFetch<{ campaign_id: number; session_id?: number }>(
            baseUrl,
            `/api/campaigns/example-packs/${encodeURIComponent(selectedPackId)}/import`,
            auth,
            {
              method: 'POST',
              body: JSON.stringify(importBody),
            },
          )
          setCreateCampaignOpen(false)
          setCreateCampaignForm(emptyCreateCampaignForm)
          setSelectedSessionId(null)
          setLogEntries([])
          setSessionState(null)
          setOptimisticEntries([])
          setStreamingTurn(null)
          setMainTab('turns')
          setInspectorTab('map')
          await refreshRoot()
          setSelectedCampaignId(result.campaign_id)
          await refreshCampaignWorkspace(result.campaign_id)
          if (result.session_id) {
            setSelectedSessionId(result.session_id)
          }
          return
        }

        let createdWorld = false
        let worldId: number | null = null
        const selectedWorldId = parsePositiveInt(createCampaignForm.worldId)
        if (createCampaignForm.worldName.trim() || !selectedWorldId) {
          worldId = await createWorldForCampaign(title, description)
          createdWorld = true
        } else {
          worldId = selectedWorldId
        }

        const createCampaign = (nextWorldId: number) =>
          apiFetch<{ campaign_id: number }>(baseUrl, '/api/campaigns', auth, {
            method: 'POST',
            body: JSON.stringify({
              title,
              world_id: nextWorldId,
              description,
            }),
          })

        let result: { campaign_id: number }
        if (!worldId) {
          worldId = await createWorldForCampaign(title, description)
          createdWorld = true
        }

        try {
          result = await createCampaign(worldId)
        } catch (error) {
          if (error instanceof ApiClientError && error.status === 404 && !createdWorld) {
            worldId = await createWorldForCampaign(title, description)
            result = await createCampaign(worldId)
          } else {
            throw error
          }
        }

        setCreateCampaignOpen(false)
        setCreateCampaignForm(emptyCreateCampaignForm)
        setSelectedSessionId(null)
        setLogEntries([])
        setSessionState(null)
        setOptimisticEntries([])
        setStreamingTurn(null)
        setMainTab('turns')
        setInspectorTab('party')
        await refreshRoot()
        setSelectedCampaignId(result.campaign_id)
      } catch (error) {
        const message = error instanceof Error ? error.message : String(error)
        setCreateCampaignError(message)
        pushError('persistence', `Could not create campaign: ${message}`)
      } finally {
        setCreateCampaignPending(false)
      }
    },
    [
      auth,
      baseUrl,
      createCampaignForm.description,
      createCampaignForm.packId,
      createCampaignForm.title,
      createCampaignForm.worldId,
      createCampaignForm.worldName,
      createWorldForCampaign,
      pushError,
      refreshCampaignWorkspace,
      refreshRoot,
      setInspectorTab,
      setLogEntries,
      setMainTab,
      setOptimisticEntries,
      setSelectedCampaignId,
      setSelectedSessionId,
      setSessionState,
      setStreamingTurn,
    ],
  )

  const openRenameCampaignDialog = useCallback(() => {
    if (!campaign) return
    rememberDialogTrigger()
    setCampaignActionDialog({
      mode: 'rename',
      campaign,
      title: campaign.title,
      description: campaign.description ?? '',
      error: '',
      pending: false,
    })
  }, [campaign, rememberDialogTrigger])

  const openArchiveCampaignDialog = useCallback(() => {
    if (!campaign) return
    rememberDialogTrigger()
    setCampaignActionDialog({
      mode: campaign.is_archived ? 'restore' : 'archive',
      campaign,
      title: campaign.title,
      description: campaign.description ?? '',
      error: '',
      pending: false,
    })
  }, [campaign, rememberDialogTrigger])

  const openDeleteCampaignDialog = useCallback(() => {
    if (!campaign) return
    rememberDialogTrigger()
    setCampaignActionDialog({
      mode: 'delete',
      campaign,
      title: campaign.title,
      description: campaign.description ?? '',
      error: '',
      pending: false,
    })
  }, [campaign, rememberDialogTrigger])

  const closeCampaignActionDialog = useCallback(() => {
    if (campaignActionDialog?.pending) return
    setCampaignActionDialog(null)
  }, [campaignActionDialog?.pending])

  const submitCampaignActionDialog = useCallback(
    async (event: FormEvent<HTMLFormElement>) => {
      event.preventDefault()
      if (!campaignActionDialog) return
      const { mode, campaign: dialogCampaign } = campaignActionDialog
      const title = campaignActionDialog.title.trim()
      const description = campaignActionDialog.description.trim()

      if (mode === 'rename' && !title) {
        setCampaignActionDialog((current) =>
          current ? { ...current, error: 'Campaign name is required.' } : current,
        )
        return
      }

      setCampaignActionDialog((current) =>
        current ? { ...current, pending: true, error: '' } : current,
      )

      try {
        if (mode === 'rename') {
          const updated = await apiFetch<Campaign>(
            baseUrl,
            `/api/campaigns/${dialogCampaign.campaign_id}`,
            auth,
            {
              method: 'PATCH',
              body: JSON.stringify({
                title,
                description,
                expected_updated_at: dialogCampaign.updated_at ?? null,
              }),
            },
          )
          campaignUpserted(updated)
          setCampaignActionDialog(null)
          await refreshCampaignWorkspace(updated.campaign_id)
        } else if (mode === 'archive') {
          await apiFetch<{ deleted: boolean; archived?: boolean }>(
            baseUrl,
            `/api/campaigns/${dialogCampaign.campaign_id}`,
            auth,
            { method: 'DELETE' },
          )
          setCampaignActionDialog(null)
          if (selectedCampaignId === dialogCampaign.campaign_id) {
            setSelectedCampaignId(null)
            setSelectedSessionId(null)
            campaignRemoved(dialogCampaign.campaign_id)
            setLogEntries([])
            setSessionState(null)
          }
          await refreshRoot()
        } else if (mode === 'restore') {
          const response = await apiFetch<{ restored: boolean; campaign: Campaign }>(
            baseUrl,
            `/api/campaigns/${dialogCampaign.campaign_id}/restore`,
            auth,
            { method: 'POST' },
          )
          campaignUpserted(response.campaign)
          setCampaignActionDialog(null)
          await refreshRoot()
          if (selectedCampaignId === dialogCampaign.campaign_id) {
            await refreshCampaignWorkspace(dialogCampaign.campaign_id)
          }
        } else {
          await apiFetch<{ deleted: boolean; hard_deleted?: boolean }>(
            baseUrl,
            `/api/campaigns/${dialogCampaign.campaign_id}?hard=true&force=true`,
            auth,
            { method: 'DELETE' },
          )
          setCampaignActionDialog(null)
          if (selectedCampaignId === dialogCampaign.campaign_id) {
            setSelectedCampaignId(null)
            setSelectedSessionId(null)
            setLogEntries([])
            setSessionState(null)
          }
          campaignRemoved(dialogCampaign.campaign_id)
          await refreshRoot()
        }
      } catch (error) {
        const message = error instanceof Error ? error.message : String(error)
        setCampaignActionDialog((current) =>
          current ? { ...current, pending: false, error: message } : current,
        )
        pushError(
          'persistence',
          `Could not ${
            mode === 'rename'
              ? 'rename'
              : mode === 'archive'
                ? 'archive'
                : mode === 'restore'
                  ? 'restore'
                  : 'delete'
          } campaign: ${message}`,
        )
      }
    },
    [
      auth,
      baseUrl,
      campaignActionDialog,
      campaignRemoved,
      campaignUpserted,
      pushError,
      refreshCampaignWorkspace,
      refreshRoot,
      selectedCampaignId,
      setLogEntries,
      setSelectedCampaignId,
      setSelectedSessionId,
      setSessionState,
    ],
  )

  return {
    campaignActionDialog,
    closeCampaignActionDialog,
    closeCreateCampaignDialog,
    createCampaignError,
    createCampaignForm,
    createCampaignPackOptions,
    createCampaignPackOptionsPending,
    createCampaignOpen,
    createCampaignPending,
    openArchiveCampaignDialog,
    openCreateCampaignDialog,
    openDeleteCampaignDialog,
    openRenameCampaignDialog,
    setCampaignActionDialog,
    setCreateCampaignForm,
    submitCampaignActionDialog,
    submitCreateCampaign,
  }
}
