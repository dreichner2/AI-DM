type MediaQueryListListeners = {
  addEventListener?: (type: 'change', listener: () => void) => void
  removeEventListener?: (type: 'change', listener: () => void) => void
  addListener?: (listener: () => void) => void
  removeListener?: (listener: () => void) => void
}

export function subscribeToMediaQueryChange(mediaQuery: MediaQueryList, listener: () => void) {
  const listeners = mediaQuery as unknown as MediaQueryListListeners

  if (typeof listeners.addEventListener === 'function' && typeof listeners.removeEventListener === 'function') {
    listeners.addEventListener('change', listener)
    return () => listeners.removeEventListener?.('change', listener)
  }

  if (typeof listeners.addListener === 'function') {
    listeners.addListener(listener)
    return () => listeners.removeListener?.(listener)
  }

  return () => undefined
}
