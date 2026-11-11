import {
  type ReactNode,
  type RefObject,
} from 'react'
import { X } from 'lucide-react'
import { useStoryPreferences } from './story/sheets/storyPreferences'
import { useSharedStoryNoticeLine } from './story/storyNoticeLine'
import { StoryNotices } from './story/StoryNotices'

/** narrow: confirmations; standard: most sheets; wide: character editing, pack import. */
export type SheetSize = 'narrow' | 'standard' | 'wide'

type ModalShellProps = {
  children: ReactNode
  /** Extra class on the panel for the dialog's own layout. */
  className?: string
  size?: SheetSize
  describedBy?: string
  dialogRef: RefObject<HTMLElement | null>
  labelledBy: string
  onClose?: () => void
  closeDisabled?: boolean
  /**
   * The sheet header. A dialog that still renders its own `<header>` leaves
   * `title` out; the sheet styles that header the same way.
   */
  title?: ReactNode
  description?: ReactNode
  /** Accessible name of the close button; without it the sheet has none. */
  closeLabel?: string
  busy?: boolean
}

/**
 * Every menu and dialog is a sheet: a glass panel over the dimmed game, in
 * the Story design language whichever view opened it (story-sheets.css). In
 * the Story view the app's notice line shows at the sheet's foot.
 */
export function ModalShell({
  busy,
  children,
  className,
  closeDisabled = false,
  closeLabel,
  describedBy,
  description,
  dialogRef,
  labelledBy,
  onClose,
  size = 'standard',
  title,
}: ModalShellProps) {
  const { reduceMotion } = useStoryPreferences()
  const noticeLine = useSharedStoryNoticeLine()
  const descriptionId = describedBy ?? `${labelledBy}-description`

  return (
    <div
      className="story-shell story-dialog"
      role="presentation"
      data-reduce-motion={reduceMotion ? '' : undefined}
      onMouseDown={(event) => {
        if (event.target === event.currentTarget && !closeDisabled) {
          onClose?.()
        }
      }}
    >
      <section
        ref={dialogRef}
        className={className ? `story-dialog-panel ${className}` : 'story-dialog-panel'}
        data-size={size}
        role="dialog"
        aria-modal="true"
        aria-labelledby={labelledBy}
        aria-describedby={describedBy ?? (description ? descriptionId : undefined)}
        aria-busy={busy || undefined}
      >
        {title === undefined ? null : (
          <header className="story-dialog-header">
            <div className="story-dialog-heading">
              <h2 id={labelledBy} className="story-dialog-title">{title}</h2>
              {description ? (
                <p id={descriptionId} className="story-dialog-description">{description}</p>
              ) : null}
            </div>
            {closeLabel ? (
              <button
                type="button"
                className="story-icon-button story-dialog-close"
                aria-label={closeLabel}
                onClick={onClose}
                disabled={closeDisabled}
              >
                <X size={18} strokeWidth={1.6} aria-hidden="true" />
              </button>
            ) : null}
          </header>
        )}
        {children}
        {noticeLine ? <StoryNotices line={noticeLine} className="story-dialog-notice" /> : null}
      </section>
    </div>
  )
}
