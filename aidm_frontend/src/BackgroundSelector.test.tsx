// @vitest-environment jsdom
import '@testing-library/jest-dom/vitest'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { BackgroundSelector } from './BackgroundSelector'

describe('BackgroundSelector', () => {
  afterEach(cleanup)

  it('shows the exact mechanics and emits the selected identity', () => {
    const onBackgroundChange = vi.fn()
    render(
      <BackgroundSelector
        selectedBackgroundId="criminal"
        pending={false}
        onBackgroundChange={onBackgroundChange}
      />,
    )

    expect(screen.getByText('Deception, Stealth')).toBeInTheDocument()
    expect(screen.getByText('Thieves Tools, Gaming Set')).toBeInTheDocument()
    expect(screen.getByText("Thieves' Cant")).toBeInTheDocument()

    fireEvent.change(screen.getByRole('combobox', { name: 'Background' }), {
      target: { value: 'sage' },
    })
    expect(onBackgroundChange).toHaveBeenCalledWith('sage')
  })
})
