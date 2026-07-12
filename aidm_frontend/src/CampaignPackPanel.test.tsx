// @vitest-environment jsdom
import '@testing-library/jest-dom/vitest'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { CampaignPackPanel } from './CampaignPackPanel'

const snapshot = {
  currentScene: { locationId: 'ash_gate', activeQuestIds: ['open_the_gate'] },
  campaignPack: {
    packId: 'ash_road',
    title: 'The Ash Road',
    version: '1.0.0',
    activeCheckpointId: 'gate',
    checkpoints: [
      {
        id: 'gate',
        title: 'The Broken Gate',
        summary: 'Reach the gate and decide how to enter.',
        nextCheckpointIds: [],
      },
    ],
    checkpointStatuses: { gate: 'active' },
  },
}

afterEach(() => cleanup())

describe('CampaignPackPanel capabilities', () => {
  it('keeps player-visible progress while hiding checkpoint mutation controls', () => {
    render(
      <CampaignPackPanel
        snapshot={snapshot}
        canControl={false}
        pendingAction={null}
        onControl={vi.fn(async () => undefined)}
      />,
    )

    expect(screen.getByRole('heading', { name: 'Campaign Pack' })).toBeInTheDocument()
    expect(screen.getAllByText('The Broken Gate').length).toBeGreaterThan(0)
    expect(screen.queryByLabelText('Campaign pack checkpoint controls')).not.toBeInTheDocument()
    expect(screen.queryByLabelText('Override active checkpoint')).not.toBeInTheDocument()
  })

  it('exposes checkpoint mutation controls to operators', () => {
    const onControl = vi.fn(async () => undefined)
    render(
      <CampaignPackPanel
        snapshot={snapshot}
        canControl
        pendingAction={null}
        onControl={onControl}
      />,
    )

    fireEvent.click(screen.getByRole('button', { name: 'Advance' }))

    expect(onControl).toHaveBeenCalledWith('advance', null, 'Manual advance')
    expect(screen.getByLabelText('Override active checkpoint')).toBeInTheDocument()
  })

  it('does not invent a visible active checkpoint when the server redacts a hidden one', () => {
    render(
      <CampaignPackPanel
        snapshot={{
          campaignPack: {
            packId: 'secret_road',
            title: 'The Secret Road',
            visibility: 'player',
            activeCheckpointId: null,
            checkpoints: [
              { id: 'public_rumor', title: 'A Troubling Rumor', status: 'open' },
            ],
            checkpointStatuses: { public_rumor: 'open' },
          },
        }}
        canControl={false}
        pendingAction={null}
        onControl={vi.fn(async () => undefined)}
      />,
    )

    expect(screen.getByText('Active checkpoint').nextElementSibling).toHaveTextContent('None')
    expect(screen.getByText('A Troubling Rumor').closest('div')).toHaveClass('open')
  })
})
