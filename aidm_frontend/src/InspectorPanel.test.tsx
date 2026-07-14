// @vitest-environment jsdom
import '@testing-library/jest-dom/vitest'
import { useState, type ComponentProps } from 'react'
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { InspectorPanel, type InspectorTab } from './InspectorPanel'

vi.mock('./OperatorTools', () => ({
  BestiaryDebugPanel: () => <div>Bestiary tools</div>,
  BetaIncidentPanel: () => <div>Incident tools</div>,
}))

type InspectorPanelProps = ComponentProps<typeof InspectorPanel>

const emptyCombat = {
  active: false,
  status: 'idle',
  round: '—',
  battlefield: '—',
  goal: '—',
  creatureSource: '—',
  resolverMethod: '—',
  tacticalLevel: '—',
  endReason: '',
  combatStartedBy: '',
  enemyGroupSummary: '',
  initiativeRequired: false,
  debugEnabled: false,
  enemies: [],
  allies: [],
  telegraphs: [],
  legalActionBundles: [],
} satisfies InspectorPanelProps['worldStatePanel']['combat']

function baseProps(): Omit<InspectorPanelProps, 'inspectorTab' | 'setInspectorTab'> {
  return {
    setMainTab: vi.fn(),
    baseUrl: '',
    auth: '',
    canUseOperatorTools: false,
    displayCharacter: {
      name: 'Ember',
      ancestryClass: 'Human Fighter',
      level: 2,
      detailId: 'Character 1',
    },
    characterAvatarSrc: '/profile-icons/human_female.png',
    xpProgress: { current: 10, max: 100, percent: 10, label: '10 / 100 XP' },
    playersCount: 1,
    activePlayers: [],
    selectedPlayerId: 1,
    loadPlayer: vi.fn(),
    createDefaultPlayer: vi.fn().mockResolvedValue(undefined),
    editSelectedPlayer: vi.fn(),
    deleteSelectedPlayer: vi.fn(),
    selectedCampaignId: 1,
    selectedSessionId: 1,
    createPlayerPending: false,
    statBlock: {
      hp: '12 / 12',
      ac: '15',
      init: '+2',
      speed: '30 ft',
      abilities: [],
      proficiency: '+2',
      inspiration: true,
    },
    spellbook: { knownSpells: [], preparedSpellNames: [], sources: [] },
    spellResources: { castingMode: 'none', slots: [], pactSlot: null, arcanum: [], concentration: '' },
    characterTraits: [],
    inventoryRows: [],
    inventoryWeightLabel: '0 lb',
    inventoryGoldLabel: '0',
    equipmentPendingItemKey: null,
    toggleInventoryEquipment: vi.fn().mockResolvedValue(undefined),
    memorySnippetCount: 0,
    visibleRecentMemory: [],
    worldStatePanel: {
      sceneName: 'Crossroads',
      sceneDescription: 'A quiet crossroads.',
      sceneType: 'exploration',
      mood: 'calm',
      dangerLevel: 'low',
      activeQuests: [],
      presentNpcs: [],
      sceneItems: [],
      availableExits: [],
      knownLocations: [],
      knownNpcs: [],
      combat: emptyCombat,
    },
    mapPanelTitle: 'Crossroads',
    mapDescription: 'No map recorded',
    mapMeta: { explored: '0%', threat: 'Low', threatTone: 'low', weather: 'Clear' },
    questTitle: 'No quest recorded',
    selectedSegment: null,
    maps: [],
    createDefaultMap: vi.fn().mockResolvedValue(undefined),
    campaign: null,
    createMapPending: false,
    mapManagementForm: { title: '', description: '', visibility: 'player' },
    setMapManagementForm: vi.fn(),
    mapSavePending: false,
    saveMapManagement: vi.fn().mockResolvedValue(undefined),
    segments: [],
    segmentSavePending: false,
    activateSegment: vi.fn().mockResolvedValue(undefined),
    segmentDeletePendingId: null,
    deleteSegment: vi.fn().mockResolvedValue(undefined),
    segmentManagementForm: {
      title: '',
      description: '',
      triggerCondition: '',
      tags: '',
      isTriggered: false,
    },
    setSegmentManagementForm: vi.fn(),
    createSegment: vi.fn().mockResolvedValue(undefined),
    campaignPackSnapshot: null,
    campaignPackControlPending: null,
    controlCampaignPackProgress: vi.fn().mockResolvedValue(undefined),
  }
}

function InspectorHarness({
  canUseOperatorTools = false,
  initialTab = 'party',
}: {
  canUseOperatorTools?: boolean
  initialTab?: InspectorTab
}) {
  const [inspectorTab, setInspectorTab] = useState<InspectorTab>(initialTab)
  return (
    <InspectorPanel
      {...baseProps()}
      canUseOperatorTools={canUseOperatorTools}
      inspectorTab={inspectorTab}
      setInspectorTab={setInspectorTab}
    />
  )
}

afterEach(() => cleanup())

describe('InspectorPanel tabs', () => {
  it('associates every available tab with a stable panel and keeps one tab in the Tab order', () => {
    render(<InspectorHarness />)

    const tablist = screen.getByRole('tablist', { name: 'Inspector panels' })
    const tabs = within(tablist).getAllByRole('tab')
    expect(tabs.map((tab) => tab.textContent)).toEqual(['Party', 'Map', 'Magic', 'Memory', 'Inventory'])

    for (const tab of tabs) {
      const panelId = tab.getAttribute('aria-controls')
      expect(panelId).toBeTruthy()
      expect(document.getElementById(panelId as string)).toHaveAttribute('role', 'tabpanel')
    }

    expect(within(tablist).getByRole('tab', { name: 'Party' })).toHaveAttribute('tabindex', '0')
    expect(tabs.filter((tab) => tab.getAttribute('tabindex') === '-1')).toHaveLength(4)
    expect(screen.getByRole('tabpanel')).toHaveAttribute('aria-labelledby', 'inspector-tab-party')
  })

  it('supports ArrowLeft, ArrowRight, Home, and End with wrapping for player tabs', () => {
    render(<InspectorHarness />)

    const partyTab = screen.getByRole('tab', { name: 'Party' })
    partyTab.focus()
    fireEvent.keyDown(partyTab, { key: 'ArrowRight' })

    const mapTab = screen.getByRole('tab', { name: 'Map' })
    expect(mapTab).toHaveFocus()
    expect(mapTab).toHaveAttribute('aria-selected', 'true')
    expect(mapTab).toHaveAttribute('tabindex', '0')

    fireEvent.keyDown(mapTab, { key: 'ArrowLeft' })
    expect(screen.getByRole('tab', { name: 'Party' })).toHaveFocus()

    fireEvent.keyDown(screen.getByRole('tab', { name: 'Party' }), { key: 'ArrowLeft' })
    expect(screen.getByRole('tab', { name: 'Inventory' })).toHaveFocus()

    fireEvent.keyDown(screen.getByRole('tab', { name: 'Inventory' }), { key: 'Home' })
    expect(screen.getByRole('tab', { name: 'Party' })).toHaveFocus()

    fireEvent.keyDown(screen.getByRole('tab', { name: 'Party' }), { key: 'End' })
    expect(screen.getByRole('tab', { name: 'Inventory' })).toHaveFocus()
  })

  it('includes capability-gated tabs in keyboard navigation only for operators', () => {
    const { rerender } = render(<InspectorHarness />)
    expect(screen.queryByRole('tab', { name: 'Bestiary' })).not.toBeInTheDocument()
    expect(screen.queryByRole('tab', { name: 'Ops' })).not.toBeInTheDocument()

    rerender(<InspectorHarness canUseOperatorTools />)
    const partyTab = screen.getByRole('tab', { name: 'Party' })
    partyTab.focus()
    fireEvent.keyDown(partyTab, { key: 'End' })

    const opsTab = screen.getByRole('tab', { name: 'Ops' })
    expect(opsTab).toHaveFocus()
    expect(opsTab).toHaveAttribute('aria-selected', 'true')

    fireEvent.keyDown(opsTab, { key: 'ArrowRight' })
    expect(screen.getByRole('tab', { name: 'Party' })).toHaveFocus()
  })

  it('exposes Inspiration as a noninteractive status', () => {
    render(<InspectorHarness />)

    expect(screen.getByRole('status', { name: 'Inspiration: available' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Inspiration' })).not.toBeInTheDocument()
  })
})
