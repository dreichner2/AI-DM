// @vitest-environment jsdom
import '@testing-library/jest-dom/vitest'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { TitleScreen } from './TitleScreen'

function renderTitleScreen(overrides: Partial<Parameters<typeof TitleScreen>[0]> = {}) {
  const props: Parameters<typeof TitleScreen>[0] = {
    pending: false,
    accountReady: false,
    canContinue: false,
    campaignCount: 0,
    selectedCampaignTitle: null,
    runtimeConfigured: true,
    onPlayNow: vi.fn(),
    onLogIn: vi.fn(),
    onCreateAccount: vi.fn(),
    onCreateCampaign: vi.fn(),
    onContinue: vi.fn(),
    ...overrides,
  }
  return { props, ...render(<TitleScreen {...props} />) }
}

describe('TitleScreen', () => {
  afterEach(cleanup)

  it('explains quick start and offers hosted account actions before authentication', () => {
    const { props } = renderTitleScreen()

    expect(screen.getByText(/skips account, campaign, and character setup/i)).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Play Now — Ready-Made Adventure' }))
    fireEvent.click(screen.getByRole('button', { name: 'Log In' }))
    fireEvent.click(screen.getByRole('button', { name: 'Create Account' }))

    expect(props.onPlayNow).toHaveBeenCalledOnce()
    expect(props.onLogIn).toHaveBeenCalledOnce()
    expect(props.onCreateAccount).toHaveBeenCalledOnce()
    expect(screen.queryByRole('button', { name: 'New Campaign' })).not.toBeInTheDocument()
  })

  it('restores campaign choices after account access is ready', () => {
    renderTitleScreen({ accountReady: true, canContinue: true, campaignCount: 2 })

    expect(screen.getByRole('button', { name: 'New Campaign' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Continue' })).toBeEnabled()
    expect(screen.queryByRole('button', { name: 'Log In' })).not.toBeInTheDocument()
  })
})
