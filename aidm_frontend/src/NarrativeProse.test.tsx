// @vitest-environment jsdom
import '@testing-library/jest-dom/vitest'
import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { NarrativeProse } from './NarrativeProse'

describe('NarrativeProse', () => {
  it('renders paragraphs, dialogue, and emphasis without injecting HTML', () => {
    const { container } = render(
      <NarrativeProse text={'The door opens.\n\n"Stay close," Mira says. The sigil *flares*. <img src=x onerror=alert(1)>'} />,
    )

    expect(container.querySelectorAll('p')).toHaveLength(2)
    expect(container.querySelector('.narrative-dialogue')).toHaveTextContent('"Stay close,"')
    expect(container.querySelector('em')).toHaveTextContent('flares')
    expect(container.querySelector('img')).toBeNull()
    expect(screen.getByText(/<img src=x onerror=alert\(1\)>/)).toBeInTheDocument()
  })

  it('marks concise mechanical lines for compact styling', () => {
    const { container } = render(<NarrativeProse text={'Aric takes 5 slashing damage.'} />)

    expect(container.querySelector('.narrative-mechanical-line')).toHaveTextContent(
      'Aric takes 5 slashing damage.',
    )
  })
})
