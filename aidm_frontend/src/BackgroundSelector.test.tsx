// @vitest-environment jsdom
import '@testing-library/jest-dom/vitest'
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { BackgroundSelector } from './BackgroundSelector'
import { PLAYABLE_BACKGROUNDS } from './backgroundCatalog'

describe('BackgroundSelector', () => {
  afterEach(cleanup)

  it('offers every background as a text row with its exact mechanics and emits the chosen identity', () => {
    const onBackgroundChange = vi.fn()
    render(
      <BackgroundSelector
        selectedBackgroundId="criminal"
        pending={false}
        onBackgroundChange={onBackgroundChange}
      />,
    )

    const group = screen.getByRole('radiogroup', { name: 'Background' })
    expect(within(group).queryByRole('combobox')).toBeNull()
    expect(within(group).getAllByRole('radio')).toHaveLength(PLAYABLE_BACKGROUNDS.length)
    const criminal = within(group).getByRole('radio', { name: /^Criminal/ })
    expect(criminal).toBeChecked()
    expect(criminal.closest('label')).toHaveClass('story-dialog-option')
    expect(criminal.closest('label')).toHaveTextContent("Deception, Stealth · Thieves Tools, Gaming Set · Thieves' Cant")
    expect(within(group).getByRole('radio', { name: /^Folk Hero/ }).closest('label')).toHaveTextContent(
      'Animal Handling, Survival · Artisan Tools, Land Vehicles',
    )

    fireEvent.click(within(group).getByRole('radio', { name: /^Sage/ }))
    expect(onBackgroundChange).toHaveBeenCalledWith('sage')
  })

  it('checks nothing for a character without a background and locks the rows while saving', () => {
    render(<BackgroundSelector selectedBackgroundId="" pending onBackgroundChange={vi.fn()} />)

    const radios = screen.getAllByRole('radio')
    expect(radios.some((radio) => (radio as HTMLInputElement).checked)).toBe(false)
    radios.forEach((radio) => expect(radio).toBeDisabled())
  })
})
