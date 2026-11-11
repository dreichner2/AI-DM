import { useStoryPreferences } from './sheets/storyPreferences'

const REDUCED_MOTION_QUERY = '(prefers-reduced-motion: reduce)'

/** True when the system or Settings → Motion asks for no motion. */
export function useReducedMotion() {
  const { reduceMotion } = useStoryPreferences()
  return reduceMotion || (typeof window.matchMedia === 'function' && window.matchMedia(REDUCED_MOTION_QUERY).matches)
}
