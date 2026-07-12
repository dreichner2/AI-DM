// @vitest-environment jsdom
import '@testing-library/jest-dom/vitest'
import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import DiceRollDialog from './DiceRollDialog'

const baseProps = {
  die: 'd20',
  result: 16,
  rolls: [16],
  mode: 'normal' as const,
  modifier: 3,
  total: 19,
  rollKey: 1,
  status: 'resolved' as const,
  onCancel: vi.fn(),
  onComplete: vi.fn(),
  onRetry: vi.fn(),
}

describe('DiceRollDialog private provenance', () => {
  afterEach(cleanup)

  it('renders a public authoritative result without requiring private fields', () => {
    render(<DiceRollDialog {...baseProps} />)

    expect(screen.getByText('19')).toBeInTheDocument()
    expect(screen.queryByLabelText('Private roll details')).not.toBeInTheDocument()
  })

  it('renders private provenance when a player-scoped payload includes it', () => {
    render(
      <DiceRollDialog
        {...baseProps}
        provenance={{
          ability: { key: 'strength', label: 'STR', score: 16, modifier: 3 },
          proficiency: { bonus: 2, skills: ['athletics'] },
          modifier_breakdown: {
            ability_modifier: 3,
            proficiency_bonus: 2,
            wound_penalty: 2,
            total: 3,
          },
        }}
      />,
    )

    const details = screen.getByLabelText('Private roll details')
    expect(details).toHaveTextContent('STR score 16 (+3)')
    expect(details).toHaveTextContent('Proficiency +2: athletics')
    expect(details).toHaveTextContent('wounds -2')
  })
})
