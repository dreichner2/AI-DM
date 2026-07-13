// @vitest-environment jsdom
import '@testing-library/jest-dom/vitest'
import { useRef, useState } from 'react'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { useModalFocusTrap } from './useModalFocusTrap'

function ReplacingDialog() {
  const [resolved, setResolved] = useState(false)
  const dialogRef = useRef<HTMLElement | null>(null)
  const returnFocusRef = useRef<HTMLElement | null>(null)

  useModalFocusTrap({
    activeKey: 'lazy-dialog',
    dialogRef,
    onClose: vi.fn(),
    returnFocusRef,
  })

  return resolved ? (
    <section ref={dialogRef} role="dialog" aria-label="Resolved dialog">
      <button type="button" data-autofocus>Ready</button>
    </section>
  ) : (
    <section ref={dialogRef} role="dialog" aria-label="Loading dialog">
      <button type="button" data-autofocus onClick={() => setResolved(true)}>
        Finish loading
      </button>
    </section>
  )
}

describe('useModalFocusTrap', () => {
  it('moves focus into a resolved dialog when a lazy fallback is replaced', async () => {
    render(<ReplacingDialog />)

    const loadingControl = screen.getByRole('button', { name: 'Finish loading' })
    await waitFor(() => expect(loadingControl).toHaveFocus())

    fireEvent.click(loadingControl)

    await waitFor(() => expect(screen.getByRole('button', { name: 'Ready' })).toHaveFocus())
  })
})
