import {
  lazy,
  Suspense,
  useEffect,
  useState,
  type Dispatch,
  type FormEvent,
  type KeyboardEvent,
  type SetStateAction,
} from 'react'
import { ChevronDown, Coins, ExternalLink, ShieldCheck, ShieldOff, Swords, X } from 'lucide-react'
import { ThinIcon } from './AppChrome'
import { CampaignPackPanel, type CampaignPackControlAction } from './CampaignPackPanel'
import {
  truncateText,
  type CharacterTraitSummary,
  type InventoryRow,
  type MapPanelMeta,
  type SpellbookSummary,
  type SpellResourceSummary,
  type StatBlock,
  type WorldStatePanel,
  type XpProgress,
} from './gameSelectors'
import { profileIconSrcForCharacter } from './profileIcons'
import type { ActivePlayer, Campaign, CampaignSegment, JsonRecord, MapItem } from './types'
import type { MainTab } from './SessionBoard'

const BetaIncidentPanel = lazy(() =>
  import('./OperatorTools').then((module) => ({ default: module.BetaIncidentPanel })),
)
const BestiaryDebugPanel = lazy(() =>
  import('./OperatorTools').then((module) => ({ default: module.BestiaryDebugPanel })),
)

export type InspectorTab = 'party' | 'map' | 'magic' | 'canon' | 'inventory' | 'bestiary' | 'ops'

export const INSPECTOR_PANEL_ID = 'character-inspector-drawer'

const INSPECTOR_TABS: ReadonlyArray<{
  id: InspectorTab
  label: string
  operatorOnly?: boolean
}> = [
  { id: 'party', label: 'Party' },
  { id: 'map', label: 'Map' },
  { id: 'magic', label: 'Magic' },
  { id: 'canon', label: 'Memory' },
  { id: 'inventory', label: 'Inventory' },
  { id: 'bestiary', label: 'Bestiary', operatorOnly: true },
  { id: 'ops', label: 'Ops', operatorOnly: true },
]

function inspectorTabId(tab: InspectorTab) {
  return `inspector-tab-${tab}`
}

function inspectorTabPanelId(tab: InspectorTab) {
  return `inspector-tabpanel-${tab}`
}

type DisplayCharacter = {
  name: string
  ancestryClass: string
  level: number | string
  detailId: string
}

type RecentMemoryEntry = [text: string, source: string]

const VISIBLE_WORLD_STATE_ITEMS = 5

export type MapManagementForm = {
  title: string
  description: string
  visibility: MapItem['visibility']
}

export type SegmentManagementForm = {
  title: string
  description: string
  triggerCondition: string
  tags: string
  isTriggered: boolean
}

type InspectorPanelProps = {
  inert?: boolean
  modal?: boolean
  onRequestClose?: () => void
  inspectorTab: InspectorTab
  setInspectorTab: Dispatch<SetStateAction<InspectorTab>>
  setMainTab: Dispatch<SetStateAction<MainTab>>
  baseUrl: string
  auth: string
  canUseOperatorTools: boolean
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
  selectedSessionId: number | null
  createPlayerPending: boolean
  statBlock: StatBlock
  spellbook: SpellbookSummary
  spellResources: SpellResourceSummary
  characterTraits: CharacterTraitSummary[]
  inventoryRows: InventoryRow[]
  inventoryWeightLabel: string
  inventoryGoldLabel: string
  equipmentPendingItemKey: string | null
  toggleInventoryEquipment: (item: InventoryRow) => Promise<void>
  memorySnippetCount: number
  visibleRecentMemory: RecentMemoryEntry[]
  worldStatePanel: WorldStatePanel
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
  campaignPackSnapshot: JsonRecord | null
  campaignPackControlPending: string | null
  controlCampaignPackProgress: (
    action: CampaignPackControlAction,
    checkpointId?: string | null,
    reason?: string,
  ) => Promise<void>
}

function displayStatValue(value: string) {
  return value
}

function mechanicLabel(value: string) {
  return value
    .replace(/^tool:/, '')
    .replace(/_/g, ' ')
    .replace(/\b\w/g, (letter) => letter.toUpperCase())
}

function inventoryIconName(icon: string) {
  if (icon === 'shield') return 'archive'
  if (icon === 'potion') return 'dot'
  if (icon === 'armor') return 'briefcase'
  return 'spark'
}

function activePlayerAvatarSrc(player: ActivePlayer) {
  return (
    player.profile_image ||
    profileIconSrcForCharacter({ race: player.race, sex: player.sex }) ||
    '/profile-icons/human_male.png'
  )
}

function activePlayerAncestryClass(player: ActivePlayer) {
  const className = player.char_class || player.class_
  return [player.race, className].filter(Boolean).join(' ') || 'Adventurer'
}

export function InspectorPanel({
  inert,
  modal = false,
  onRequestClose,
  inspectorTab,
  setInspectorTab,
  setMainTab,
  baseUrl,
  auth,
  canUseOperatorTools,
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
  selectedSessionId,
  createPlayerPending,
  statBlock,
  spellbook,
  spellResources,
  characterTraits,
  inventoryRows,
  inventoryWeightLabel,
  inventoryGoldLabel,
  equipmentPendingItemKey,
  toggleInventoryEquipment,
  memorySnippetCount,
  visibleRecentMemory,
  worldStatePanel,
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
  campaignPackSnapshot,
  campaignPackControlPending,
  controlCampaignPackProgress,
}: InspectorPanelProps) {
  const [showAllKnownNpcs, setShowAllKnownNpcs] = useState(false)
  const [showAllKnownLocations, setShowAllKnownLocations] = useState(false)
  const skillProficiencies = statBlock.skillProficiencies ?? []
  const skillExpertise = statBlock.skillExpertise ?? []
  const toolProficiencies = statBlock.toolProficiencies ?? []
  const languages = statBlock.languages ?? []
  const availableTabs = INSPECTOR_TABS.filter((tab) => canUseOperatorTools || !tab.operatorOnly)
  const activeInspectorTab = availableTabs.some((tab) => tab.id === inspectorTab)
    ? inspectorTab
    : 'party'
  useEffect(() => {
    if (!canUseOperatorTools && (inspectorTab === 'bestiary' || inspectorTab === 'ops')) {
      setInspectorTab('party')
    }
  }, [canUseOperatorTools, inspectorTab, setInspectorTab])

  const visibleKnownNpcs = showAllKnownNpcs
    ? worldStatePanel.knownNpcs
    : worldStatePanel.knownNpcs.slice(0, VISIBLE_WORLD_STATE_ITEMS)
  const visibleKnownLocations = showAllKnownLocations
    ? worldStatePanel.knownLocations
    : worldStatePanel.knownLocations.slice(0, VISIBLE_WORLD_STATE_ITEMS)
  const olderNpcCount = Math.max(0, worldStatePanel.knownNpcs.length - VISIBLE_WORLD_STATE_ITEMS)
  const olderLocationCount = Math.max(0, worldStatePanel.knownLocations.length - VISIBLE_WORLD_STATE_ITEMS)
  const visibleSpells = activeInspectorTab === 'magic' ? spellbook.knownSpells : spellbook.knownSpells.slice(0, 5)
  const visibleCharacterTraits = activeInspectorTab === 'magic' ? characterTraits : characterTraits.slice(0, 4)
  const spellbookSourceLabel = spellbook.sources.some((source) => source === 'aidm-original')
    ? 'AIDM'
    : spellbook.sources.find((source) => source.toLowerCase().includes('class')) ||
      spellbook.sources[0] ||
      'Known'

  const handleTabKeyDown = (event: KeyboardEvent<HTMLButtonElement>) => {
    const currentIndex = availableTabs.findIndex((tab) => tab.id === activeInspectorTab)
    if (currentIndex < 0) return

    let nextIndex: number | null = null
    if (event.key === 'ArrowRight') {
      nextIndex = (currentIndex + 1) % availableTabs.length
    } else if (event.key === 'ArrowLeft') {
      nextIndex = (currentIndex - 1 + availableTabs.length) % availableTabs.length
    } else if (event.key === 'Home') {
      nextIndex = 0
    } else if (event.key === 'End') {
      nextIndex = availableTabs.length - 1
    }
    if (nextIndex === null) return

    event.preventDefault()
    const nextTab = availableTabs[nextIndex]
    setInspectorTab(nextTab.id)
    document.getElementById(inspectorTabId(nextTab.id))?.focus()
  }

  return (
    <aside
      id={INSPECTOR_PANEL_ID}
      className="right-inspector"
      role={modal ? 'dialog' : undefined}
      aria-label="Character and campaign inspector"
      aria-modal={modal ? true : undefined}
      inert={inert ? true : undefined}
    >
      {modal && onRequestClose ? (
        <button
          type="button"
          className="drawer-close-button"
          aria-label="Close character panel"
          onClick={onRequestClose}
        >
          <X size={18} />
        </button>
      ) : null}
      <div className="inspector-tabs" role="tablist" aria-label="Inspector panels">
        {availableTabs.map((tab) => {
          const selected = activeInspectorTab === tab.id
          return (
            <button
              key={tab.id}
              id={inspectorTabId(tab.id)}
              type="button"
              role="tab"
              aria-controls={inspectorTabPanelId(tab.id)}
              aria-selected={selected}
              className={selected ? 'active' : ''}
              tabIndex={selected ? 0 : -1}
              onClick={() => setInspectorTab(tab.id)}
              onKeyDown={handleTabKeyDown}
            >
              {tab.label}
            </button>
          )
        })}
      </div>

      <div
        id={inspectorTabPanelId(activeInspectorTab)}
        className="inspector-tabpanel"
        role="tabpanel"
        aria-labelledby={inspectorTabId(activeInspectorTab)}
        tabIndex={0}
      >
      {activeInspectorTab === 'party' || activeInspectorTab === 'inventory' ? (
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
            <span
              className={`inspiration-toggle ${statBlock.inspiration ? 'filled' : ''}`}
              role="status"
              aria-label={`Inspiration: ${statBlock.inspiration ? 'available' : 'not available'}`}
            />
            <span>Proficiency</span>
            <strong>{displayStatValue(statBlock.proficiency)}</strong>
          </div>
          {statBlock.background || skillProficiencies.length || toolProficiencies.length ? (
            <dl className="character-mechanics-grid" aria-label="Character proficiencies">
              {statBlock.background ? (
                <div>
                  <dt>Background</dt>
                  <dd>{statBlock.background}</dd>
                </div>
              ) : null}
              {statBlock.hitDie ? (
                <div>
                  <dt>Hit Die</dt>
                  <dd>{statBlock.hitDie}</dd>
                </div>
              ) : null}
              {skillProficiencies.length ? (
                <div>
                  <dt>Skills</dt>
                  <dd>
                    {skillProficiencies.map((skill) => {
                      const expertise = skillExpertise.includes(skill)
                      return `${mechanicLabel(skill)}${expertise ? ' (expertise)' : ''}`
                    }).join(', ')}
                  </dd>
                </div>
              ) : null}
              {toolProficiencies.length ? (
                <div>
                  <dt>Tools</dt>
                  <dd>{toolProficiencies.map(mechanicLabel).join(', ')}</dd>
                </div>
              ) : null}
              {languages.length ? (
                <div>
                  <dt>Languages</dt>
                  <dd>{languages.join(', ')}</dd>
                </div>
              ) : null}
            </dl>
          ) : null}
        </section>
      ) : null}

      {activeInspectorTab === 'party' ? (
        <section className="inspector-box active-player-box">
          <div className="box-title">
            <h3>Active Players ({activePlayers.length})</h3>
            <span>Live</span>
          </div>
          {activePlayers.length ? (
            <ul className="active-player-list" aria-label="Active players in this session">
              {activePlayers.map((player) => {
                const isSelectedPlayer = player.id === selectedPlayerId
                const ancestryClass = activePlayerAncestryClass(player)
                const isOtherPlayerTyping = !isSelectedPlayer && player.is_typing
                const health = player.health
                const healthClassName = health ? `active-player-health-${health.tone}` : ''
                return (
                  <li
                    key={player.id}
                    className={[isSelectedPlayer ? 'selected' : '', healthClassName].filter(Boolean).join(' ')}
                  >
                    <div className="active-player-avatar-wrap">
                      <img
                        className="active-player-avatar"
                        src={activePlayerAvatarSrc(player)}
                        alt={`${player.character_name} character icon`}
                      />
                      <span className="presence-dot" aria-hidden="true" />
                    </div>
                    <div className="active-player-copy">
                      <strong>{player.character_name}</strong>
                      {health ? (
                        <small
                          className="active-player-health-text"
                          aria-label={`${player.character_name} health: ${health.label}`}
                          title={`${health.label}: ${health.currentHp}/${health.maxHp} HP`}
                        >
                          {health.label}
                        </small>
                      ) : null}
                      <small className="active-player-detail">{player.name} - {ancestryClass}</small>
                    </div>
                    <div className="presence-badges">
                      {isOtherPlayerTyping ? (
                        <span className="typing-badge" aria-label={`${player.character_name} is typing`}>
                          Typing...
                        </span>
                      ) : null}
                      {isSelectedPlayer ? <span className="presence-badge">You</span> : null}
                    </div>
                  </li>
                )
              })}
            </ul>
          ) : (
            <div className="empty-row">No active players connected.</div>
          )}
        </section>
      ) : null}

      {activeInspectorTab === 'party' || activeInspectorTab === 'magic' ? (
        <section className="inspector-box spellbook-box">
          <div className="box-title">
            <h3>Spellbook ({spellbook.knownSpells.length})</h3>
            <span>{spellbookSourceLabel}</span>
          </div>
          {spellResources.slots.length || spellResources.pactSlot || spellResources.arcanum.length || spellResources.concentration ? (
            <div className="spell-resource-summary" aria-label="Spell resources">
              {spellResources.slots.map((slot) => (
                <span key={`slot-${slot.level}`}>
                  Level {slot.level}: <strong>{slot.current}/{slot.max}</strong>
                </span>
              ))}
              {spellResources.pactSlot ? (
                <span>
                  Pact level {spellResources.pactSlot.level}: <strong>{spellResources.pactSlot.current}/{spellResources.pactSlot.max}</strong>
                </span>
              ) : null}
              {spellResources.arcanum.map((use) => (
                <span key={`arcanum-${use.level}`}>
                  Arcanum {use.level}: <strong>{use.current}/{use.max}</strong>
                </span>
              ))}
              {spellResources.concentration ? (
                <span>Concentrating: <strong>{spellResources.concentration}</strong></span>
              ) : null}
            </div>
          ) : null}
          <div className="spellbook-list" aria-label="Known spells">
            {visibleSpells.length ? (
              visibleSpells.map((spell) => (
                <div key={spell.id || spell.name} className={spell.prepared ? 'prepared' : ''}>
                  <span className="spell-level">{spell.levelLabel}</span>
                  <div>
                    <strong>{spell.name}</strong>
                    <small>
                      {[spell.source, spell.catalog === 'aidm-original' ? 'AIDM' : '']
                        .filter(Boolean)
                        .join(' / ') || 'Known spell'}
                    </small>
                    {spell.description ? (
                      <p>{truncateText(spell.description, activeInspectorTab === 'magic' ? 110 : 82)}</p>
                    ) : null}
                  </div>
                </div>
              ))
            ) : (
              <div className="empty-row">No spells recorded.</div>
            )}
          </div>
          {activeInspectorTab !== 'magic' && spellbook.knownSpells.length > 5 ? (
            <button type="button" className="view-link" onClick={() => setInspectorTab('magic')}>
              View All Magic <ExternalLink size={12} />
            </button>
          ) : null}
        </section>
      ) : null}

      {(activeInspectorTab === 'party' || activeInspectorTab === 'magic') && characterTraits.length ? (
        <section className="inspector-box trait-box">
          <div className="box-title">
            <h3>Abilities &amp; Traits ({characterTraits.length})</h3>
            <span>{characterTraits.some((trait) => trait.active) ? 'Active' : 'Traits'}</span>
          </div>
          <div className="trait-list" aria-label="Character abilities and traits">
            {visibleCharacterTraits.map((trait) => (
              <div key={trait.id || trait.name} className={trait.active ? 'active' : ''}>
                <span className="trait-type">{trait.typeLabel}</span>
                <div>
                  <strong>{trait.name}</strong>
                  <small>
                    {[trait.source, trait.actionType, trait.cooldown].filter(Boolean).join(' / ') ||
                      'Character trait'}
                  </small>
                  {trait.description ? (
                    <p>{truncateText(trait.description, activeInspectorTab === 'magic' ? 120 : 86)}</p>
                  ) : null}
                </div>
              </div>
            ))}
          </div>
          {activeInspectorTab !== 'magic' && characterTraits.length > 4 ? (
            <button type="button" className="view-link" onClick={() => setInspectorTab('magic')}>
              View All Magic <ExternalLink size={12} />
            </button>
          ) : null}
        </section>
      ) : null}

      {activeInspectorTab === 'party' || activeInspectorTab === 'inventory' ? (
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
              (activeInspectorTab === 'inventory' ? inventoryRows : inventoryRows.slice(0, 4)).map((item, index) => (
                <div key={`${item.id || item.item}-${index}`} className={item.equipped ? 'equipped' : ''}>
                  <span className={`item-icon ${item.icon}`}>
                    <ThinIcon name={inventoryIconName(item.icon)} size={15} />
                  </span>
                  <strong>
                    {item.item}
                    {item.equipped ? <small>Equipped{item.slot ? ` - ${item.slot.replace(/_/g, ' ')}` : ''}</small> : null}
                  </strong>
                  <span>{item.count}</span>
                  <span>{item.weight}</span>
                  {item.equippable ? (
                    <button
                      type="button"
                      className="equipment-toggle"
                      aria-label={`${item.equipped ? 'Unequip' : 'Equip'} ${item.item}`}
                      title={`${item.equipped ? 'Unequip' : 'Equip'} ${item.item}`}
                      disabled={equipmentPendingItemKey === (item.id || item.item)}
                      onClick={() => void toggleInventoryEquipment(item)}
                    >
                      {item.equipped ? <ShieldOff size={13} /> : <ShieldCheck size={13} />}
                      {item.equipped ? 'Unequip' : 'Equip'}
                    </button>
                  ) : null}
                </div>
              ))
            ) : (
              <div className="empty-row">No inventory recorded.</div>
            )}
          </div>
          {activeInspectorTab !== 'inventory' && inventoryRows.length > 4 ? (
            <button type="button" className="view-link" onClick={() => setInspectorTab('inventory')}>
              View All Inventory <ExternalLink size={12} />
            </button>
          ) : null}
        </section>
      ) : null}

      {activeInspectorTab === 'party' || activeInspectorTab === 'canon' ? (
        <section className="inspector-box">
          <div className="box-title">
            <h3>Recent Memory ({memorySnippetCount})</h3>
            <span>{activeInspectorTab === 'canon' ? 'All' : 'Recent'} <ChevronDown size={14} /></span>
          </div>
          <div className="canon-list">
            {visibleRecentMemory.length ? (
              visibleRecentMemory.map(([text, source]) => (
                <div key={`${text}-${source}`}>
                  <ThinIcon name="dot" size={12} />
                  <span>{text}</span>
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
              setMainTab('turns')
            }}
          >
            View All Memory <ExternalLink size={12} />
          </button>
        </section>
      ) : null}

      {activeInspectorTab === 'party' || activeInspectorTab === 'map' ? (
        <section className="inspector-box world-state-box">
          <div className="box-title">
            <h3>Scene State</h3>
            <span>{worldStatePanel.sceneType}</span>
          </div>
          <div className="scene-state-grid">
            <div>
              <span>Scene</span>
              <strong>{worldStatePanel.sceneName}</strong>
            </div>
            <div>
              <span>Mood</span>
              <strong>{worldStatePanel.mood}</strong>
            </div>
            <div>
              <span>Danger</span>
              <strong>{worldStatePanel.dangerLevel}</strong>
            </div>
          </div>
          {worldStatePanel.combat.active ? (
            <div className="combat-state-panel">
              <div className="combat-state-header">
                <span>
                  <Swords size={13} aria-hidden="true" />
                  Round {worldStatePanel.combat.round}
                </span>
                <small>{worldStatePanel.combat.battlefield}</small>
              </div>
              <p>{worldStatePanel.combat.goal}</p>
              <div className="combatant-list">
                {worldStatePanel.combat.enemies.slice(0, 5).map((enemy) => (
                  <div key={enemy.id || enemy.name} className={`combatant-row health-${enemy.healthTone}`}>
                    <div>
                      <strong>{enemy.name}</strong>
                      <small>
                        {enemy.health}
                        {enemy.conditions.length ? ` / ${enemy.conditions.join(', ')}` : ''}
                        {worldStatePanel.combat.debugEnabled && enemy.morale !== '—' ? ` / morale ${enemy.morale}` : ''}
                        {worldStatePanel.combat.debugEnabled && enemy.position ? ` / ${enemy.position}` : ''}
                        {worldStatePanel.combat.debugEnabled && enemy.selectionScore ? ` / score ${enemy.selectionScore}` : ''}
                        {worldStatePanel.combat.debugEnabled && enemy.brainSource ? ` / ${enemy.brainSource}` : ''}
                      </small>
                    </div>
                    <span title={worldStatePanel.combat.debugEnabled && enemy.selectionMethod ? enemy.selectionMethod : undefined}>
                      {enemy.intent || 'watching'}
                    </span>
                  </div>
                ))}
              </div>
              {worldStatePanel.combat.debugEnabled ? (
                <div className="combat-debug-strip">
                  <span>{worldStatePanel.combat.resolverMethod || 'manual'}</span>
                  <span>{worldStatePanel.combat.creatureSource || 'unknown source'}</span>
                  {worldStatePanel.combat.enemyGroupSummary ? <span>{worldStatePanel.combat.enemyGroupSummary}</span> : null}
                  <span>{worldStatePanel.combat.tacticalLevel}</span>
                  {worldStatePanel.combat.combatStartedBy ? <span>{worldStatePanel.combat.combatStartedBy}</span> : null}
                  {worldStatePanel.combat.initiativeRequired ? <span>initiative</span> : null}
                  {worldStatePanel.combat.endReason ? <span>{worldStatePanel.combat.endReason}</span> : null}
                  {worldStatePanel.combat.enemies.flatMap((enemy) => enemy.moraleEvents).slice(0, 3).map((event) => (
                    <span key={event}>{event}</span>
                  ))}
                </div>
              ) : null}
              {worldStatePanel.combat.telegraphs.length ? (
                <div className="combat-telegraphs">
                  {worldStatePanel.combat.telegraphs.map((telegraph) => (
                    <span key={telegraph}>{telegraph}</span>
                  ))}
                </div>
              ) : null}
            </div>
          ) : null}
          <div className="world-state-list">
            <div>
              <strong>Active Quests</strong>
              {worldStatePanel.activeQuests.length ? (
                worldStatePanel.activeQuests.map((quest) => (
                  <span key={quest.id || quest.title}>
                    {quest.title}
                    <small>{quest.stage}</small>
                  </span>
                ))
              ) : (
                <span className="empty-row">No active quests.</span>
              )}
            </div>
            <div>
              <strong>Known NPCs</strong>
              {worldStatePanel.knownNpcs.length ? (
                <>
                  {visibleKnownNpcs.map((npc) => (
                    <span key={npc.id || npc.name}>
                      {npc.name}{npc.race ? ` (${npc.race})` : ''}
                      <small>{npc.role} / {npc.disposition}</small>
                    </span>
                  ))}
                  {olderNpcCount ? (
                    <button
                      type="button"
                      className={`world-state-toggle ${showAllKnownNpcs ? 'expanded' : ''}`}
                      aria-expanded={showAllKnownNpcs}
                      onClick={() => setShowAllKnownNpcs((value) => !value)}
                    >
                      <ChevronDown size={12} aria-hidden="true" />
                      {showAllKnownNpcs
                        ? 'Show recent NPCs'
                        : `Show ${olderNpcCount} older NPC${olderNpcCount === 1 ? '' : 's'}`}
                    </button>
                  ) : null}
                </>
              ) : (
                <span className="empty-row">No known NPCs.</span>
              )}
            </div>
            <div>
              <strong>Known Places</strong>
              {worldStatePanel.knownLocations.length ? (
                <>
                  {visibleKnownLocations.map((location) => (
                    <span key={location.id || location.name}>
                      {location.name}
                      <small>{location.status} / {location.type}</small>
                    </span>
                  ))}
                  {olderLocationCount ? (
                    <button
                      type="button"
                      className={`world-state-toggle ${showAllKnownLocations ? 'expanded' : ''}`}
                      aria-expanded={showAllKnownLocations}
                      onClick={() => setShowAllKnownLocations((value) => !value)}
                    >
                      <ChevronDown size={12} aria-hidden="true" />
                      {showAllKnownLocations
                        ? 'Show recent places'
                        : `Show ${olderLocationCount} older place${olderLocationCount === 1 ? '' : 's'}`}
                    </button>
                  ) : null}
                </>
              ) : (
                <span className="empty-row">No known locations.</span>
              )}
            </div>
          </div>
        </section>
      ) : null}

      {activeInspectorTab === 'party' || activeInspectorTab === 'map' || activeInspectorTab === 'canon' ? (
        <CampaignPackPanel
          snapshot={campaignPackSnapshot}
          canControl={canUseOperatorTools}
          pendingAction={campaignPackControlPending}
          onControl={controlCampaignPackProgress}
        />
      ) : null}

      {activeInspectorTab === 'bestiary' && canUseOperatorTools ? (
        <Suspense
          fallback={
            <section className="inspector-box bestiary-debug-panel" aria-label="Bestiary tools">
              <div className="empty-row">Loading bestiary tools...</div>
            </section>
          }
        >
          <BestiaryDebugPanel
            baseUrl={baseUrl}
            auth={auth}
            selectedCampaignId={selectedCampaignId}
            selectedSessionId={selectedSessionId}
            canUseOperatorTools={canUseOperatorTools}
          />
        </Suspense>
      ) : null}

      {activeInspectorTab === 'ops' && canUseOperatorTools ? (
        <Suspense
          fallback={
            <section className="inspector-box beta-incident-panel" aria-label="Beta incidents">
              <div className="empty-row">Loading incidents...</div>
            </section>
          }
        >
          <BetaIncidentPanel baseUrl={baseUrl} auth={auth} selectedSessionId={selectedSessionId} />
        </Suspense>
      ) : null}

      {activeInspectorTab === 'party' || activeInspectorTab === 'map' ? (
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
              {canUseOperatorTools ? (
                <button
                  type="button"
                  onClick={() => void createDefaultMap()}
                  disabled={!selectedCampaignId || !campaign || createMapPending}
                >
                  {createMapPending ? 'Creating...' : 'Create map'}
                </button>
              ) : null}
            </div>
          ) : null}
        </section>
      ) : null}

      {activeInspectorTab === 'map' && canUseOperatorTools ? (
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
            <label>
              Player visibility
              <select
                value={mapManagementForm.visibility}
                onChange={(event) =>
                  setMapManagementForm((current) => ({
                    ...current,
                    visibility: event.target.value as MapItem['visibility'],
                  }))
                }
                disabled={mapSavePending}
              >
                <option value="player">Players (revealed)</option>
                <option value="dm">DM only</option>
              </select>
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

      {activeInspectorTab === 'map' ? (
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
                  {canUseOperatorTools ? (
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
                  ) : null}
                </article>
              ))
            ) : (
              <div className="empty-row">No campaign segments recorded.</div>
            )}
          </div>
          {canUseOperatorTools ? (
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
          ) : null}
        </section>
      ) : null}
      </div>
      {availableTabs
        .filter((tab) => tab.id !== activeInspectorTab)
        .map((tab) => (
          <div
            key={tab.id}
            id={inspectorTabPanelId(tab.id)}
            role="tabpanel"
            aria-labelledby={inspectorTabId(tab.id)}
            hidden
          />
        ))}
    </aside>
  )
}
