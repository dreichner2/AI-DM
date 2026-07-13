import { useEffect, useRef, type RefObject } from 'react'

type ModalFocusTrapOptions = {
  activeKey: string | null
  dialogRef: RefObject<HTMLElement | null>
  onClose: () => void
  returnFocusRef: RefObject<HTMLElement | null>
}

function focusableDialogElements(container: HTMLElement) {
  const selector = [
    'button:not([disabled])',
    'input:not([disabled])',
    'textarea:not([disabled])',
    'select:not([disabled])',
    'a[href]',
    '[tabindex]:not([tabindex="-1"])',
  ].join(',')
  return Array.from(container.querySelectorAll<HTMLElement>(selector)).filter((element) => {
    if (element.getAttribute('aria-hidden') === 'true') return false
    const style = window.getComputedStyle(element)
    return style.display !== 'none' && style.visibility !== 'hidden'
  })
}

export function useModalFocusTrap({
  activeKey,
  dialogRef,
  onClose,
  returnFocusRef,
}: ModalFocusTrapOptions) {
  const onCloseRef = useRef(onClose)

  useEffect(() => {
    onCloseRef.current = onClose
  }, [onClose])

  useEffect(() => {
    if (!activeKey) return undefined
    const previouslyFocused =
      returnFocusRef.current ??
      (document.activeElement instanceof HTMLElement ? document.activeElement : null)
    const focusDialog = (dialog: HTMLElement | null) => {
      const focusTarget = dialog
        ?.querySelector<HTMLElement>('[data-autofocus]')
        ?? dialog?.querySelector<HTMLElement>(
          'input:not([disabled]), textarea:not([disabled]), button:not([disabled])',
        )
      focusTarget?.focus()
    }
    const focusTimer = window.setTimeout(() => {
      focusDialog(dialogRef.current)
    }, 0)
    let currentDialog = dialogRef.current
    const dialogObserver = new MutationObserver(() => {
      const nextDialog = dialogRef.current
      if (!nextDialog || nextDialog === currentDialog) return
      currentDialog = nextDialog
      if (!nextDialog.contains(document.activeElement)) {
        focusDialog(nextDialog)
      }
    })
    dialogObserver.observe(document.body, { childList: true, subtree: true })

    const handleKeyDown = (event: KeyboardEvent) => {
      const dialog = dialogRef.current
      if (!dialog) return
      if (event.key === 'Escape') {
        event.preventDefault()
        event.stopPropagation()
        onCloseRef.current()
        return
      }
      if (event.key !== 'Tab') return
      const focusable = focusableDialogElements(dialog)
      if (!focusable.length) {
        event.preventDefault()
        return
      }
      const first = focusable[0]
      const last = focusable[focusable.length - 1]
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault()
        last.focus()
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault()
        first.focus()
      }
    }

    document.addEventListener('keydown', handleKeyDown)
    return () => {
      window.clearTimeout(focusTimer)
      dialogObserver.disconnect()
      document.removeEventListener('keydown', handleKeyDown)
      if (previouslyFocused?.isConnected) {
        const canRestoreDirectly = !previouslyFocused.matches(':disabled, [aria-disabled="true"]')
        const nearbyContainer = previouslyFocused.closest<HTMLElement>('section, form, main')
        const returnTarget = canRestoreDirectly
          ? previouslyFocused
          : nearbyContainer
            ? focusableDialogElements(nearbyContainer)[0]
            : null
        returnTarget?.focus()
      }
      returnFocusRef.current = null
    }
  }, [activeKey, dialogRef, returnFocusRef])
}
