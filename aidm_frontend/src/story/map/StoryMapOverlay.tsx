import { useRef } from 'react'
import { createPortal } from 'react-dom'
import { X } from 'lucide-react'
import { useModalFocusTrap } from '../../useModalFocusTrap'
import { StoryMap, type StoryMapProps } from './StoryMap'

type StoryMapOverlayProps = StoryMapProps & { onClose: () => void }

/** Large version of the same map, kept inside the Story shell for its tokens. */
export function StoryMapOverlay({ map, travelBlockedReason, onTravel, onClose }: StoryMapOverlayProps) {
  const dialogRef = useRef<HTMLElement | null>(null)
  const returnFocusRef = useRef<HTMLElement | null>(null)
  useModalFocusTrap({ activeKey: 'story-map', dialogRef, onClose, returnFocusRef })
  const shell = document.querySelector('.story-shell')
  if (!shell) return null
  return createPortal(
    <div className="story-map-stage" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose() }}>
      <section
        ref={dialogRef} className="story-map-dialog" role="dialog" aria-modal="true" aria-label="Map"
        onKeyDown={(event) => { if (event.key === 'Escape') { event.preventDefault(); event.stopPropagation(); onClose() } }}
      >
        <button type="button" className="story-icon-button story-map-close" aria-label="Close map" onClick={onClose} data-autofocus>
          <X aria-hidden="true" />
        </button>
        <StoryMap map={map} travelBlockedReason={travelBlockedReason} onTravel={(place) => { onTravel(place); onClose() }} />
      </section>
    </div>,
    shell,
  )
}
