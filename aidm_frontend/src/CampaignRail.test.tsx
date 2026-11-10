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
    playability: { label: 'Ready to play', tone: 'good' },
    lastSavedLabel: 'just now',
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

  it('shows the playability line and last saved time instead of a blanket system status', () => {
    const { rerender } = render(<CampaignRail {...railProps({ lastSavedLabel: '3m ago' })} />)

    expect(screen.getByText('Ready to play')).toBeInTheDocument()
    expect(screen.getByText(/Last saved: 3m ago/)).toBeInTheDocument()
    expect(screen.queryByText('All Systems Operational')).not.toBeInTheDocument()
    expect(screen.queryByText(/Last sync/)).not.toBeInTheDocument()

    rerender(<CampaignRail {...railProps({ playability: { label: 'Reconnecting…', tone: 'neutral' } })} />)
    expect(screen.getByText('Reconnecting…')).toBeInTheDocument()
  })

  it('labels sessions as chapters with their state and marks the selection only visually', () => {
    render(
      <CampaignRail
        {...railProps({
          selectedSessionId: 22,
          sessionCards: [
            { id: 22, label: 'Chapter 3', state: 'Current', name: '' },
            { id: 21, label: 'Chapter 2', state: 'Ended', name: 'The Drowned Lantern' },
            { id: 20, label: 'Chapter 1', state: 'Archived', name: '' },
            { id: 19, label: 'Chapter 4', state: 'Archive', name: 'Archive — Old save' },
          ],
        })}
      />,
    )

    const current = screen.getByRole('button', { name: /^Chapter 3\s*Current$/ })
    expect(current).toHaveClass('active')
    expect(current).toHaveAttribute('aria-current', 'true')
    expect(screen.getByRole('button', { name: /^Chapter 2\s*Ended · The Drowned Lantern$/ })).not.toHaveClass('active')
    expect(screen.getByRole('button', { name: /^Chapter 1\s*Archived$/ })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /^Chapter 4\s*Archive · Archive — Old save$/ })).toBeInTheDocument()
    expect(screen.queryByText(/Active/)).not.toBeInTheDocument()
  })

  it('starts the next chapter from the operator controls and the empty list', () => {
    const onStartSession = vi.fn()
    const { rerender } = render(
      <CampaignRail {...railProps({ canUseOperatorTools: true, selectedCampaignId: 10, onStartSession })} />,
    )

    const startButtons = screen.getAllByRole('button', { name: 'Start next chapter' })
    expect(startButtons).toHaveLength(2)
    startButtons.forEach((button) => fireEvent.click(button))
    expect(onStartSession).toHaveBeenCalledTimes(2)

    rerender(<CampaignRail {...railProps({ selectedCampaignId: 10, onStartSession })} />)
    expect(screen.queryByRole('button', { name: 'Start next chapter' })).not.toBeInTheDocument()
  })
})

