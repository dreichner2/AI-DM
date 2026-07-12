import { useState, type Dispatch, type FormEvent, type SetStateAction } from 'react'
import { apiFetch } from './api'
import type { InspectorTab, MapManagementForm, SegmentManagementForm } from './InspectorPanel'
import type { Campaign, CampaignSegment, MapItem } from './types'

type ValueUpdater<T> = T | ((current: T) => T)

type UseWorldMapSegmentActionsOptions = {
  auth: string
  baseUrl: string
  campaign: Campaign | null
  maps: MapItem[]
  selectedCampaignId: number | null
  refreshCampaignWorkspace: (campaignId: number) => Promise<void>
  setSelectedPlayerId: (value: ValueUpdater<number | null>) => void
  setInspectorTab: Dispatch<SetStateAction<InspectorTab>>
  pushError: (
    category: 'persistence' | 'validation',
    message: string,
  ) => void
}

const emptyMapManagementForm: MapManagementForm = {
  title: '',
  description: '',
  visibility: 'player',
}

const emptySegmentManagementForm: SegmentManagementForm = {
  title: '',
  description: '',
  triggerCondition: '',
  tags: '',
  isTriggered: true,
}

export function useWorldMapSegmentActions({
  auth,
  baseUrl,
  campaign,
  maps,
  selectedCampaignId,
  refreshCampaignWorkspace,
  setSelectedPlayerId,
  setInspectorTab,
  pushError,
}: UseWorldMapSegmentActionsOptions) {
  const [createPlayerPending, setCreatePlayerPending] = useState(false)
  const [createMapPending, setCreateMapPending] = useState(false)
  const [mapSavePending, setMapSavePending] = useState(false)
  const [segmentSavePending, setSegmentSavePending] = useState(false)
  const [segmentDeletePendingId, setSegmentDeletePendingId] = useState<number | null>(null)
  const [mapManagementForm, setMapManagementForm] = useState<MapManagementForm>(
    emptyMapManagementForm,
  )
  const [segmentManagementForm, setSegmentManagementForm] =
    useState<SegmentManagementForm>(emptySegmentManagementForm)

  const createDefaultPlayer = async () => {
    if (!selectedCampaignId) return
    setCreatePlayerPending(true)
    try {
      const result = await apiFetch<{ player_id: number }>(
        baseUrl,
        `/api/players/campaigns/${selectedCampaignId}/players`,
        auth,
        {
          method: 'POST',
          body: JSON.stringify({
            name: 'Local Player',
            character_name: 'New Adventurer',
            race: '',
            char_class: '',
            level: 1,
          }),
        },
      )
      await refreshCampaignWorkspace(selectedCampaignId)
      setSelectedPlayerId(result.player_id)
      setInspectorTab('party')
    } catch (error) {
      pushError('persistence', `Could not create player: ${error instanceof Error ? error.message : String(error)}`)
    } finally {
      setCreatePlayerPending(false)
    }
  }

  const createDefaultMap = async () => {
    if (!selectedCampaignId || !campaign) return
    setCreateMapPending(true)
    try {
      await apiFetch<{ map_id: number }>(baseUrl, '/api/maps', auth, {
        method: 'POST',
        body: JSON.stringify({
          campaign_id: selectedCampaignId,
          world_id: campaign.world_id,
          title: `${campaign.title} Map`,
          description: campaign.location || 'Campaign map notes.',
          map_data: {},
          visibility: 'player',
        }),
      })
      await refreshCampaignWorkspace(selectedCampaignId)
      setInspectorTab('map')
    } catch (error) {
      pushError('persistence', `Could not create map: ${error instanceof Error ? error.message : String(error)}`)
    } finally {
      setCreateMapPending(false)
    }
  }

  const saveMapManagement = async (event?: FormEvent<HTMLFormElement>) => {
    event?.preventDefault()
    if (!selectedCampaignId || !campaign) return
    const title = mapManagementForm.title.trim() || `${campaign.title} Map`
    const description = mapManagementForm.description.trim()
    const currentMap = maps[0]
    setMapSavePending(true)
    try {
      if (currentMap) {
        await apiFetch<{ message: string }>(baseUrl, `/api/maps/${currentMap.map_id}`, auth, {
          method: 'PATCH',
          body: JSON.stringify({
            title,
            description,
            visibility: mapManagementForm.visibility,
          }),
        })
      } else {
        await apiFetch<{ map_id: number }>(baseUrl, '/api/maps', auth, {
          method: 'POST',
          body: JSON.stringify({
            campaign_id: selectedCampaignId,
            world_id: campaign.world_id,
            title,
            description,
            map_data: {},
            visibility: mapManagementForm.visibility,
          }),
        })
      }
      await refreshCampaignWorkspace(selectedCampaignId)
      setInspectorTab('map')
    } catch (error) {
      pushError('persistence', `Could not save map: ${error instanceof Error ? error.message : String(error)}`)
    } finally {
      setMapSavePending(false)
    }
  }

  const createSegment = async (event?: FormEvent<HTMLFormElement>) => {
    event?.preventDefault()
    if (!selectedCampaignId) return
    const title = segmentManagementForm.title.trim()
    if (!title) {
      pushError('validation', 'Segment title is required.')
      return
    }
    setSegmentSavePending(true)
    try {
      await apiFetch<{ segment_id: number }>(baseUrl, '/api/segments', auth, {
        method: 'POST',
        body: JSON.stringify({
          campaign_id: selectedCampaignId,
          title,
          description: segmentManagementForm.description.trim(),
          trigger_condition: segmentManagementForm.triggerCondition.trim(),
          tags: segmentManagementForm.tags.trim(),
          is_triggered: segmentManagementForm.isTriggered,
        }),
      })
      setSegmentManagementForm(emptySegmentManagementForm)
      await refreshCampaignWorkspace(selectedCampaignId)
      setInspectorTab('map')
    } catch (error) {
      pushError('persistence', `Could not create segment: ${error instanceof Error ? error.message : String(error)}`)
    } finally {
      setSegmentSavePending(false)
    }
  }

  const activateSegment = async (segment: CampaignSegment) => {
    if (!selectedCampaignId) return
    setSegmentSavePending(true)
    try {
      await apiFetch<{ segments: CampaignSegment[] }>(baseUrl, '/api/segments/activate', auth, {
        method: 'POST',
        body: JSON.stringify({
          campaign_id: selectedCampaignId,
          segment_id: segment.segment_id,
          exclusive: true,
        }),
      })
      await refreshCampaignWorkspace(selectedCampaignId)
      setInspectorTab('map')
    } catch (error) {
      pushError('persistence', `Could not activate segment: ${error instanceof Error ? error.message : String(error)}`)
    } finally {
      setSegmentSavePending(false)
    }
  }

  const deleteSegment = async (segment: CampaignSegment) => {
    if (!selectedCampaignId) return
    setSegmentDeletePendingId(segment.segment_id)
    try {
      await apiFetch<{ message: string }>(baseUrl, `/api/segments/${segment.segment_id}`, auth, {
        method: 'DELETE',
      })
      await refreshCampaignWorkspace(selectedCampaignId)
      setInspectorTab('map')
    } catch (error) {
      pushError('persistence', `Could not delete segment: ${error instanceof Error ? error.message : String(error)}`)
    } finally {
      setSegmentDeletePendingId(null)
    }
  }

  return {
    activateSegment,
    createDefaultMap,
    createDefaultPlayer,
    createMapPending,
    createPlayerPending,
    createSegment,
    deleteSegment,
    mapManagementForm,
    mapSavePending,
    saveMapManagement,
    segmentDeletePendingId,
    segmentManagementForm,
    segmentSavePending,
    setMapManagementForm,
    setSegmentManagementForm,
  }
}
