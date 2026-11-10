// @vitest-environment jsdom
import '@testing-library/jest-dom/vitest'
import { createRef, type ComponentProps } from 'react'
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { Campaign } from '../../types'
import { AdventuresSheet } from './AdventuresSheet'

const NOW = new Date('2026-11-10T12:00:00Z').getTime()
const HOUR_MS = 60 * 60 * 1000

function campaign(id: number, title: string, overrides: Partial<Campaign> = {}): Campaign {
  return {
    campaign_id: id,
    title,
    description: null,
    world_id: 5,
    world_name: null,
    created_at: null,
    updated_at: null,
    status: 'active',
    is_archived: false,
    current_quest: null,
    location: null,
    session_count: 0,
    latest_session_id: null,
    latest_activity_at: null,
    ...overrides,
  }
}

function sheetProps(overrides: Partial<ComponentProps<typeof AdventuresSheet>> = {}) {
  return {
    campaigns: [
      campaign(1, 'Ashes Beyond the Gate', { latest_activity_at: new Date(NOW - 30 * 24 * HOUR_MS).toISOString() }),
      campaign(2, 'The Lantern Road'),
      campaign(3, 'Lanterns under Blackwater', { world_id: 8, world_name: 'Blackwater Reach', session_count: 1 }),
      campaign(4, 'The Old Archive', { is_archived: true }),
    ],
    chapterMeta: {
      1: { count: 12, updatedAt: new Date(NOW - 30 * 24 * HOUR_MS).toISOString() },
      2: { count: 3, updatedAt: new Date(NOW - 2 * HOUR_MS).toISOString() },
      3: { count: 1, updatedAt: new Date(NOW - 26 * HOUR_MS).toISOString() },
    },
    currentCampaignId: 2,
    dialogRef: createRef<HTMLElement>(),
    onClose: vi.fn(),
    onContinue: vi.fn(),
    onNewAdventure: vi.fn(),
    onPlayReadyMade: vi.fn(),
    worldNameById: new Map([[5, 'The Western Roadlands']]),
    ...overrides,
  } satisfies ComponentProps<typeof AdventuresSheet>
}

beforeEach(() => {
  vi.useFakeTimers({ toFake: ['Date'] })
  vi.setSystemTime(NOW)
})
afterEach(() => {
  cleanup()
  vi.useRealTimers()
})

describe('AdventuresSheet', () => {
  it('lists each adventure as a row, the one you are in first and marked Here now', () => {
    render(<AdventuresSheet {...sheetProps()} />)

    const dialog = screen.getByRole('dialog', { name: 'Your adventures' })
    const rows = within(within(dialog).getByRole('list', { name: 'Adventures' })).getAllByRole('listitem')
    expect(rows.map((row) => row.querySelector('.story-dialog-row-name')?.textContent)).toEqual([
      'The Lantern Road',
      'Lanterns under Blackwater',
      'Ashes Beyond the Gate',
    ])
    expect(rows[0]).toHaveTextContent('The Western Roadlands · 3 chapters · played 2 hours ago')
    expect(within(rows[0]).getByText('Here now')).toBeInTheDocument()
    expect(within(rows[0]).queryByRole('button')).not.toBeInTheDocument()
    expect(rows[1]).toHaveTextContent('Blackwater Reach · 1 chapter · played yesterday')
    expect(rows[2]).toHaveTextContent('The Western Roadlands · 12 chapters · played 4 weeks ago')
    expect(dialog).not.toHaveTextContent('The Old Archive')
  })

  it('leaves unknown parts out of the meta line', () => {
    render(
      <AdventuresSheet
        {...sheetProps({ campaigns: [campaign(9, 'Untold', { world_id: 99 })], chapterMeta: {}, currentCampaignId: null })}
      />,
    )

    const row = screen.getByRole('listitem')
    expect(row.querySelector('.story-dialog-row-meta')).toBeNull()
  })

  it('continues an adventure and offers the other ways to begin', () => {
    const props = sheetProps()
    render(<AdventuresSheet {...props} />)

    fireEvent.click(screen.getByRole('button', { name: 'Continue Lanterns under Blackwater' }))
    expect(props.onContinue).toHaveBeenCalledWith(3)
    fireEvent.click(screen.getByRole('button', { name: 'Play a ready-made adventure' }))
    expect(props.onPlayReadyMade).toHaveBeenCalledOnce()
    fireEvent.click(screen.getByRole('button', { name: 'New adventure' }))
    expect(props.onNewAdventure).toHaveBeenCalledOnce()
    expect(screen.queryByRole('button', { name: 'Import an adventure pack' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Archived adventures' })).not.toBeInTheDocument()
  })

  it('gives operators the pack import and the archive', () => {
    const onImportPack = vi.fn()
    const onOpenArchive = vi.fn()
    render(<AdventuresSheet {...sheetProps({ onImportPack, onOpenArchive })} />)

    fireEvent.click(screen.getByRole('button', { name: 'Import an adventure pack' }))
    fireEvent.click(screen.getByRole('button', { name: 'Archived adventures' }))
    expect(onImportPack).toHaveBeenCalledOnce()
    expect(onOpenArchive).toHaveBeenCalledOnce()
  })

  it('says so when there are no adventures yet', () => {
    render(<AdventuresSheet {...sheetProps({ campaigns: [] })} />)

    expect(screen.getByText('No adventures yet. Begin one below.')).toBeInTheDocument()
  })
})
