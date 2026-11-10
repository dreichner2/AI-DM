// @vitest-environment jsdom
import '@testing-library/jest-dom/vitest'
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { CombatHud } from '../CombatHud'
import type { ActionIntent } from '../gameActions'
import { testCombat, VIEWER_ID } from './dockTestFixtures'
import { StoryCombatRibbon } from './StoryCombatRibbon'

type Submit = (message?: string, intent?: ActionIntent) => boolean

function renderRibbon(combat = testCombat(), disabled = false) {
  const submitAction = vi.fn<Submit>(() => true)
  render(
    <StoryCombatRibbon
      combat={combat}
      playerId={VIEWER_ID}
      activePlayers={[{ id: VIEWER_ID, character_name: 'Ysolde Marrow', name: 'Danny', profile_image: '/ysolde.png' }]}
      disabled={disabled}
      submitAction={submitAction}
    />,
  )
  return submitAction
}

afterEach(() => cleanup())

describe('StoryCombatRibbon', () => {
  it('shows the round and every combatant, allies first, with health and conditions', () => {
    renderRibbon()

    expect(screen.getByText('Round 2')).toBeInTheDocument()
    const combatants = within(screen.getByRole('list', { name: 'Combatants' })).getAllByRole('listitem')
    expect(combatants.map((item) => item.getAttribute('aria-label'))).toEqual([
      'Ysolde Marrow: Hurt',
      'Goblin Sentry: Wounded, frightened',
      'Distant Archer: Unhurt',
    ])
    expect(combatants[0]).toHaveAttribute('aria-current', 'true')
    expect(combatants[0]).toHaveAttribute('data-health', 'hurt')
    expect(combatants[0].querySelector('img')).toHaveAttribute('src', '/ysolde.png')
  })

  it('reflects the turn economy in the pips', () => {
    renderRibbon()
    const pips = within(screen.getByRole('list', { name: 'Turn resources' })).getAllByRole('listitem')
    expect(pips.map((pip) => pip.textContent)).toEqual([
      'Action ready',
      'Bonus spent',
      'Move ready',
      'Reaction ready',
    ])
    expect(pips[1]).toHaveAttribute('data-spent')
  })

  it('does not submit an unavailable action and shows the server reason', () => {
    const submitAction = renderRibbon()
    const disengage = screen.getByRole('button', { name: /^Disengage/ })

    expect(disengage).toHaveAttribute('aria-disabled', 'true')
    expect(disengage).toHaveAttribute('title', 'Your movement is already spent.')
    expect(disengage).toHaveAccessibleDescription('Your movement is already spent.')
    fireEvent.click(disengage)

    expect(submitAction).not.toHaveBeenCalled()
    expect(screen.getByRole('status')).toHaveTextContent('Your movement is already spent.')
  })

  it('submits the same message and intent as CombatHud for the same target', () => {
    const ribbonSubmit = renderRibbon()
    fireEvent.click(screen.getByRole('button', { name: 'Attack with Longsword' }))

    const targets = screen.getByRole('group', { name: 'Targets for Attack with Longsword' })
    const archer = within(targets).getByRole('button', { name: /^Distant Archer · far/ })
    expect(archer).toHaveAccessibleDescription('Target is at far range.')
    fireEvent.click(archer)
    expect(ribbonSubmit).not.toHaveBeenCalled()

    fireEvent.click(within(targets).getByRole('button', { name: 'Goblin Sentry · near' }))
    cleanup()

    const hudSubmit = vi.fn<Submit>(() => true)
    render(<CombatHud combat={testCombat()} playerId={VIEWER_ID} disabled={false} submitAction={hudSubmit} />)
    fireEvent.click(screen.getByRole('button', { name: 'Attack with Longsword, target Goblin Sentry' }))

    const [ribbonMessage, ribbonIntent] = ribbonSubmit.mock.calls[0]
    const [hudMessage, hudIntent] = hudSubmit.mock.calls[0]
    expect(ribbonMessage).toBe(hudMessage)
    expect(ribbonIntent).toEqual({ ...hudIntent, client_message_id: expect.stringMatching(/^combat-hud-/) })
    expect(ribbonIntent?.combat).toEqual({ action_id: 'combat.attack.longsword', target_id: 'goblin_1' })
  })

  it('goes back from targets to actions', () => {
    renderRibbon()
    fireEvent.click(screen.getByRole('button', { name: 'Attack with Longsword' }))
    fireEvent.click(screen.getByRole('button', { name: 'Back' }))
    expect(screen.getByRole('group', { name: 'Combat actions' })).toBeInTheDocument()
  })

  it('ends the turn as a quiet choice, or as the primary action once the action is spent', () => {
    const submitAction = renderRibbon()
    const endTurn = screen.getByRole('button', { name: 'End turn' })
    expect(endTurn).toHaveClass('story-chip')

    fireEvent.click(endTurn)
    expect(submitAction).toHaveBeenCalledWith('Ysolde Marrow ends their turn.', expect.objectContaining({
      kind: 'combat',
      source: 'combat_hud',
      combat: { action_id: 'combat.end_turn' },
    }))
    cleanup()

    renderRibbon(testCombat({ bundle: { actionRemaining: 0 } }))
    expect(screen.getByRole('button', { name: 'End turn' })).toHaveClass('story-primary')
  })

  it('blocks every choice while the current turn resolves', () => {
    const submitAction = renderRibbon(testCombat(), true)
    fireEvent.click(screen.getByRole('button', { name: 'End turn' }))
    expect(submitAction).not.toHaveBeenCalled()
    expect(screen.getByRole('status')).toHaveTextContent('Actions are unavailable while the current turn resolves.')
  })

  it('says who is acting when it is not the viewer’s turn, and shows at most two telegraphs', () => {
    renderRibbon(testCombat({ current: false }))

    expect(screen.getByText('Goblin Sentry is acting')).toBeInTheDocument()
    expect(screen.queryByRole('group', { name: 'Combat actions' })).not.toBeInTheDocument()
    expect(screen.getByText(/^Watch for:/)).toHaveTextContent(
      'Watch for: The goblin glances toward the tunnel mouth. · The archer nocks an arrow.',
    )
  })

  it('renders nothing without a bundle for the viewer', () => {
    const { container } = render(
      <StoryCombatRibbon combat={testCombat()} playerId={99} activePlayers={[]} disabled={false} submitAction={vi.fn()} />,
    )
    expect(container).toBeEmptyDOMElement()
  })
})
