import type { Dispatch, FormEvent, SetStateAction } from 'react'
import { ChevronDown, Coins, ExternalLink } from 'lucide-react'
import { ThinIcon } from './AppChrome'
import {
  truncateText,
  type InventoryRow,
  type MapPanelMeta,
  type StatBlock,
  type XpProgress,
} from './gameSelectors'
import type { ActivePlayer, Campaign, CampaignSegment, MapItem } from './types'
import type { MainTab } from './SessionBoard'

export type InspectorTab = 'party' | 'map' | 'canon' | 'inventory'

type DisplayCharacter = {
  name: string
  ancestryClass: string
  level: number | string
  detailId: string
}

type CanonFact = [fact: string, source: string]

export type MapManagementForm = {
  title: string
  description: string
}

export type SegmentManagementForm = {
  title: string
  description: string
  triggerCondition: string
  tags: string
  isTriggered: boolean
}

type InspectorPanelProps = {
  inspectorTab: InspectorTab
  setInspectorTab: Dispatch<SetStateAction<InspectorTab>>
  setMainTab: Dispatch<SetStateAction<MainTab>>
  displayCharacter: DisplayCharacter
  characterAvatarSrc: string
  xpProgress: XpProgress
  playersCount: number
  activePlayers: ActivePlayer[]
  selectedPlayerId: number | null
  loadPlayer: () => void
  createDefaultPlayer: () => Promise<void>
  editSelectedPlayer: () => void
  deleteSelectedPlayer: () => void
  selectedCampaignId: number | null
  createPlayerPending: boolean
  statBlock: StatBlock
  inventoryRows: InventoryRow[]
  inventoryWeightLabel: string
  inventoryGoldLabel: string
  memorySnippetCount: number
  visibleCanonFacts: CanonFact[]
  mapPanelTitle: string
  mapDescription: string
  mapMeta: MapPanelMeta
  questTitle: string
  selectedSegment: CampaignSegment | null
  maps: MapItem[]
  createDefaultMap: () => Promise<void>
  campaign: Campaign | null
  createMapPending: boolean
  mapManagementForm: MapManagementForm
  setMapManagementForm: Dispatch<SetStateAction<MapManagementForm>>
  mapSavePending: boolean
  saveMapManagement: (event?: FormEvent<HTMLFormElement>) => Promise<void>
  segments: CampaignSegment[]
  segmentSavePending: boolean
  activateSegment: (segment: CampaignSegment) => Promise<void>
  segmentDeletePendingId: number | null
  deleteSegment: (segment: CampaignSegment) => Promise<void>
  segmentManagementForm: SegmentManagementForm
  setSegmentManagementForm: Dispatch<SetStateAction<SegmentManagementForm>>
  createSegment: (event?: FormEvent<HTMLFormElement>) => Promise<void>
}

function displayStatValue(value: string) {
  return value
}

function inventoryIconName(icon: string) {
  if (icon === 'shield') return 'archive'
  if (icon === 'potion') return 'dot'
  if (icon === 'armor') return 'briefcase'
  return 'spark'
}

export function InspectorPanel({
  inspectorTab,
  setInspectorTab,
  setMainTab,
  displayCharacter,
  characterAvatarSrc,
  xpProgress,
  playersCount,
  activePlayers,
  selectedPlayerId,
  loadPlayer,
  createDefaultPlayer,
  editSelectedPlayer,
  deleteSelectedPlayer,
  selectedCampaignId,
  createPlayerPending,
  statBlock,
  inventoryRows,
  inventoryWeightLabel,
  inventoryGoldLabel,
  memorySnippetCount,
  visibleCanonFacts,
  mapPanelTitle,
  mapDescription,
  mapMeta,
  questTitle,
  selectedSegment,
  maps,
  createDefaultMap,
  campaign,
  createMapPending,
  mapManagementForm,
  setMapManagementForm,
  mapSavePending,
  saveMapManagement,
  segments,
  segmentSavePending,
  activateSegment,
  segmentDeletePendingId,
  deleteSegment,
  segmentManagementForm,
  setSegmentManagementForm,
  createSegment,
}: InspectorPanelProps) {
  return (
    <aside className="right-inspector">
      <div className="inspector-tabs" role="tablist" aria-label="Inspector panels">
        <button
          type="button"
          role="tab"
          aria-selected={inspectorTab === 'party'}
          className={inspectorTab === 'party' ? 'active' : ''}
          onClick={() => setInspectorTab('party')}
        >
          Party
        </button>
        <button
          type="button"
          role="tab"
          aria-selected={inspectorTab === 'map'}
          className={inspectorTab === 'map' ? 'active' : ''}
          onClick={() => setInspectorTab('map')}
        >
          Map
        </button>
        <button
          type="button"
          role="tab"
          aria-selected={inspectorTab === 'canon'}
          className={inspectorTab === 'canon' ? 'active' : ''}
          onClick={() => setInspectorTab('canon')}
        >
          Canon
        </button>
        <button
          type="button"
          role="tab"
          aria-selected={inspectorTab === 'inventory'}
          className={inspectorTab === 'inventory' ? 'active' : ''}
          onClick={() => setInspectorTab('inventory')}
        >
          Inventory
        </button>
      </div>

      {inspectorTab === 'party' || inspectorTab === 'inventory' ? (
        <section className="character-panel">
          <div className="character-card">
            <div className="portrait">
              <img src={characterAvatarSrc} alt="" aria-hidden="true" />
            </div>
            <div className="character-main">
              <div>
                <h2>{displayCharacter.name}</h2>
                <p>{displayCharacter.ancestryClass}</p>
              </div>
              <div className="level-stack">
                <span>Level</span>
                <strong>{displayCharacter.level}</strong>
              </div>
              <div className="xp-track">
                <span style={{ width: `${xpProgress.percent}%` }} />
              </div>
              <div className="xp-label">
                <span>{displayCharacter.detailId}</span>
                <small>{xpProgress.label}</small>
              </div>
            </div>
          </div>
          <div className="character-actions" aria-label="Character actions">
            <button type="button" onClick={loadPlayer} disabled={!selectedCampaignId || !playersCount}>
              Load
            </button>
            <button
              type="button"
              onClick={() => void createDefaultPlayer()}
              disabled={!selectedCampaignId || createPlayerPending}
            >
              {createPlayerPending ? 'Creating...' : 'New'}
            </button>
            <button type="button" onClick={editSelectedPlayer} disabled={!selectedPlayerId}>
              Edit
            </button>
            <button type="button" onClick={deleteSelectedPlayer} disabled={!selectedPlayerId}>
              Delete
            </button>
          </div>
          {!playersCount ? (
            <div className="empty-inline-action">
              <span>No characters in this campaign yet.</span>
            </div>
          ) : null}

          <div className="vital-grid">
            <div>
              <span>HP</span>
              <strong className="hp">{displayStatValue(statBlock.hp)}</strong>
            </div>
            <div>
              <span>AC</span>
              <strong>{displayStatValue(statBlock.ac)}</strong>
            </div>
            <div>
              <span>INIT</span>
              <strong>{displayStatValue(statBlock.init)}</strong>
            </div>
            <div>
              <span>SPEED</span>
              <strong>{displayStatValue(statBlock.speed)}</strong>
            </div>
          </div>

          <div className="ability-grid">
            {statBlock.abilities.map(([label, score, mod]) => (
              <div key={label}>
                <span>{label}</span>
                <strong>{displayStatValue(score)}</strong>
                <small>{displayStatValue(mod)}</small>
              </div>
            ))}
          </div>

          <div className="inspiration-row">
            <span>Inspiration</span>
            <button
              type="button"
              className={`inspiration-toggle ${statBlock.inspiration ? 'filled' : ''}`}
              aria-label="Inspiration"
            />
            <span>Proficiency</span>
            <strong>{displayStatValue(statBlock.proficiency)}</strong>
          </div>
        </section>
      ) : null}

      {inspectorTab === 'party' ? (
        <section className="inspector-box active-player-box">
          <div className="box-title">
            <h3>Active Players ({activePlayers.length})</h3>
            <span>Live</span>
          </div>
          {activePlayers.length ? (
            <ul className="active-player-list" aria-label="Active players in this session">
              {activePlayers.map((player) => {
                const isSelectedPlayer = player.id === selectedPlayerId
                return (
                  <li key={player.id} className={isSelectedPlayer ? 'selected' : ''}>
                    <span className="presence-dot" aria-hidden="true" />
                    <div>
                      <strong>{player.character_name}</strong>
                      <small>{player.name}</small>
                    </div>
                    {isSelectedPlayer ? <span className="presence-badge">You</span> : null}
                  </li>
                )
              })}
            </ul>
          ) : (
            <div className="empty-row">No active players connected.</div>
          )}
        </section>
      ) : null}

      {inspectorTab === 'party' || inspectorTab === 'inventory' ? (
        <section className="inspector-box">
          <div className="box-title">
            <h3>Inventory ({inventoryRows.length})</h3>
            <div className="inventory-metrics">
              <span className="gold-count" aria-label={`Gold ${inventoryGoldLabel}`}>
                <Coins size={13} />
                {inventoryGoldLabel}
              </span>
              <span>{inventoryWeightLabel}</span>
            </div>
          </div>
          <div className="inventory-table">
            {inventoryRows.length ? (
              inventoryRows.slice(0, inspectorTab === 'inventory' ? 8 : 4).map((item, index) => (
                <div key={`${item.item}-${index}`}>
                  <span className={`item-icon ${item.icon}`}>
                    <ThinIcon name={inventoryIconName(item.icon)} size={15} />
                  </span>
                  <strong>{item.item}</strong>
                  <span>{item.count}</span>
                  <span>{item.weight}</span>
                </div>
              ))
            ) : (
              <div className="empty-row">No inventory recorded.</div>
            )}
          </div>
          <button type="button" className="view-link" onClick={() => setInspectorTab('inventory')}>
            View All Inventory <ExternalLink size={12} />
          </button>
        </section>
      ) : null}

      {inspectorTab === 'party' || inspectorTab === 'canon' ? (
        <section className="inspector-box">
          <div className="box-title">
            <h3>Canon Facts ({memorySnippetCount})</h3>
            <span>{inspectorTab === 'canon' ? 'All' : 'Recent'} <ChevronDown size={14} /></span>
          </div>
          <div className="canon-list">
            {visibleCanonFacts.length ? (
              visibleCanonFacts.map(([fact, source]) => (
                <div key={`${fact}-${source}`}>
                  <ThinIcon name="dot" size={12} />
                  <span>{fact}</span>
                  <small>{source}</small>
                </div>
              ))
            ) : (
              <div className="empty-row">No memory snippets recorded.</div>
            )}
          </div>
          <button
            type="button"
            className="view-link"
            onClick={() => {
              setInspectorTab('canon')
              setMainTab('notes')
            }}
          >
            View All Canon <ExternalLink size={12} />
          </button>
        </section>
      ) : null}

      {inspectorTab === 'party' || inspectorTab === 'map' ? (
        <section className="inspector-box">
          <div className="box-title">
            <h3>Current Map / Segment</h3>
            <button
              type="button"
              onClick={() => {
                setInspectorTab('map')
              }}
            >
              Change
            </button>
          </div>
          <div className="map-segment">
            <div className="mini-map">
              <span />
            </div>
            <div className="map-meta-column">
              <h4>{mapPanelTitle}</h4>
              <p>{mapDescription}</p>
              <dl>
                <dt>Explored</dt>
                <dd>{mapMeta.explored}</dd>
                <dt>Threat</dt>
                <dd className={`threat-${mapMeta.threatTone}`}>{mapMeta.threat}</dd>
                <dt>Weather</dt>
                <dd>{mapMeta.weather}</dd>
              </dl>
              <small>{truncateText(questTitle, 30)} / {selectedSegment?.title ? truncateText(selectedSegment.title, 30) : 'None'}</small>
            </div>
          </div>
          {!maps.length ? (
            <div className="empty-inline-action">
              <span>No campaign map has been recorded.</span>
              <button
                type="button"
                onClick={() => void createDefaultMap()}
                disabled={!selectedCampaignId || !campaign || createMapPending}
              >
                {createMapPending ? 'Creating...' : 'Create map'}
              </button>
            </div>
          ) : null}
        </section>
      ) : null}

      {inspectorTab === 'map' ? (
        <section className="inspector-box map-management-box">
          <div className="box-title">
            <h3>Map Details</h3>
            <span>{maps[0] ? 'Saved map' : 'New map'}</span>
          </div>
          <form className="management-form" onSubmit={(event) => void saveMapManagement(event)}>
            <label>
              Map title
              <input
                value={mapManagementForm.title}
                onChange={(event) =>
                  setMapManagementForm((current) => ({
                    ...current,
                    title: event.target.value,
                  }))
                }
                disabled={mapSavePending}
              />
            </label>
            <label>
              Map description
              <textarea
                value={mapManagementForm.description}
                onChange={(event) =>
                  setMapManagementForm((current) => ({
                    ...current,
                    description: event.target.value,
                  }))
                }
                rows={3}
                disabled={mapSavePending}
              />
            </label>
            <button
              type="submit"
              disabled={!selectedCampaignId || !campaign || mapSavePending}
            >
              {mapSavePending ? 'Saving...' : maps[0] ? 'Save map details' : 'Create map details'}
            </button>
          </form>
        </section>
      ) : null}

      {inspectorTab === 'map' ? (
        <section className="inspector-box segment-management-box">
          <div className="box-title">
            <h3>Segments</h3>
            <span>{segments.length} total</span>
          </div>
          <div className="segment-list">
            {segments.length ? (
              segments.map((segment) => (
                <article
                  key={segment.segment_id}
                  className={segment.is_triggered ? 'active' : ''}
                >
                  <div>
                    <strong>{segment.title}</strong>
                    <span>{segment.is_triggered ? 'Active' : 'Inactive'}</span>
                  </div>
                  <p>{segment.description || segment.trigger_condition || 'No segment notes recorded.'}</p>
                  {segment.tags ? <small>{segment.tags}</small> : null}
                  <div className="segment-actions">
                    <button
                      type="button"
                      onClick={() => void activateSegment(segment)}
                      disabled={segmentSavePending || segment.is_triggered}
                    >
                      Set active
                    </button>
                    <button
                      type="button"
                      className="danger"
                      onClick={() => void deleteSegment(segment)}
                      disabled={segmentDeletePendingId === segment.segment_id}
                    >
                      {segmentDeletePendingId === segment.segment_id ? 'Deleting...' : 'Delete'}
                    </button>
                  </div>
                </article>
              ))
            ) : (
              <div className="empty-row">No campaign segments recorded.</div>
            )}
          </div>
          <form className="management-form" onSubmit={(event) => void createSegment(event)}>
            <label>
              Segment title
              <input
                value={segmentManagementForm.title}
                onChange={(event) =>
                  setSegmentManagementForm((current) => ({
                    ...current,
                    title: event.target.value,
                  }))
                }
                disabled={segmentSavePending}
              />
            </label>
            <label>
              Segment description
              <textarea
                value={segmentManagementForm.description}
                onChange={(event) =>
                  setSegmentManagementForm((current) => ({
                    ...current,
                    description: event.target.value,
                  }))
                }
                rows={2}
                disabled={segmentSavePending}
              />
            </label>
            <label>
              Trigger condition
              <input
                value={segmentManagementForm.triggerCondition}
                onChange={(event) =>
                  setSegmentManagementForm((current) => ({
                    ...current,
                    triggerCondition: event.target.value,
                  }))
                }
                disabled={segmentSavePending}
              />
            </label>
            <label>
              Tags
              <input
                value={segmentManagementForm.tags}
                onChange={(event) =>
                  setSegmentManagementForm((current) => ({
                    ...current,
                    tags: event.target.value,
                  }))
                }
                disabled={segmentSavePending}
              />
            </label>
            <label className="management-checkbox">
              <input
                type="checkbox"
                checked={segmentManagementForm.isTriggered}
                onChange={(event) =>
                  setSegmentManagementForm((current) => ({
                    ...current,
                    isTriggered: event.target.checked,
                  }))
                }
                disabled={segmentSavePending}
              />
              Start as active segment
            </label>
            <button type="submit" disabled={!selectedCampaignId || segmentSavePending}>
              {segmentSavePending ? 'Adding...' : 'Add segment'}
            </button>
          </form>
        </section>
      ) : null}
    </aside>
  )
}
