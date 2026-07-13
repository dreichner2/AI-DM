// @vitest-environment jsdom
import '@testing-library/jest-dom/vitest'
import { type ComponentProps } from 'react'
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { CampaignRail } from './CampaignRail'

afterEach(cleanup)

function railProps(
  overrides: Partial<ComponentProps<typeof CampaignRail>> = {},
): ComponentProps<typeof CampaignRail> {
  return {
    backendStatus: 'ok',
    campaignTitle: 'Ember Road',
    campaignCards: [],
    sessionCards: [],
    campaignFilter: '',
    setCampaignFilter: vi.fn(),
    selectedCampaignId: null,
    selectedSessionId: null,
    loadingCampaignId: null,
    sessionLoading: false,
    workspaceLoading: false,
    mainTab: 'turns',
    setMainTab: vi.fn(),
    inspectorTab: 'party',
    setInspectorTab: vi.fn(),
    canUseOperatorTools: false,
    canManageCampaign: false,
    canManageSession: false,
    canOpenCampaignArchive: false,
    canOpenSessionArchive: false,
    selectionLocked: false,
    onRenameCampaign: vi.fn(),
    onArchiveCampaign: vi.fn(),
    onDeleteCampaign: vi.fn(),
    onCreateCampaign: vi.fn(),
    onImportCampaignPack: vi.fn(),
    onManageWorlds: vi.fn(),
    onRenameSession: vi.fn(),
    onArchiveSession: vi.fn(),
    onDeleteSession: vi.fn(),
    onStartSession: vi.fn(),
    onSelectCampaign: vi.fn(),
    onSelectSession: vi.fn(),
    lastSyncLabel: 'just now',
    onRefreshWorkspace: vi.fn(),
    errors: [],
    ...overrides,
  }
}

describe('CampaignRail', () => {
  it.each([
    ['party', 'Adventure'],
    ['map', 'Map'],
    ['canon', 'Memory'],
    ['inventory', 'Inventory'],
  ] as const)('exposes one current destination for the %s inspector state', (inspectorTab, label) => {
    render(<CampaignRail {...railProps({ inspectorTab })} />)

    const navigation = screen.getByRole('navigation')
    const currentItems = within(navigation).getAllByRole('button').filter(
      (item) => item.getAttribute('aria-current') === 'page',
    )

    expect(currentItems).toHaveLength(1)
    expect(currentItems[0]).toHaveAccessibleName(label)
    expect(within(navigation).queryByRole('button', { name: 'Turns' })).not.toBeInTheDocument()
    expect(within(navigation).queryByRole('button', { name: 'Campaigns' })).not.toBeInTheDocument()
  })

  it('returns to the primary adventure view using the existing tab callbacks', () => {
    const setMainTab = vi.fn()
    const setInspectorTab = vi.fn()
    render(
      <CampaignRail
        {...railProps({ inspectorTab: 'map', setMainTab, setInspectorTab })}
      />,
    )

    fireEvent.click(screen.getByRole('button', { name: 'Adventure' }))

    expect(setMainTab).toHaveBeenCalledWith('turns')
    expect(setInspectorTab).toHaveBeenCalledWith('party')
  })

  it('distinguishes an empty campaign list from an empty search result', () => {
    const { rerender } = render(<CampaignRail {...railProps()} />)

    expect(screen.getByText('No campaigns yet.')).toBeInTheDocument()
    expect(screen.queryByText('No campaigns match your search.')).not.toBeInTheDocument()

    rerender(<CampaignRail {...railProps({ campaignFilter: 'ember' })} />)

    expect(screen.getByText('No campaigns match your search.')).toBeInTheDocument()
    expect(screen.queryByText('No campaigns yet.')).not.toBeInTheDocument()
  })
})
