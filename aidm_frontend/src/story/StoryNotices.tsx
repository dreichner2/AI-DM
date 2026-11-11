import type { StoryNoticeLineState } from './storyNoticeLine'

type StoryNoticesProps = {
  line: StoryNoticeLineState
  className?: string
}

/**
 * One calm line for what a player must see right now that is not part of the
 * story: another player's roll, a chapter change, a refused action, a copied
 * link, a lost connection. Clicking it dismisses it.
 */
export function StoryNotices({ line, className }: StoryNoticesProps) {
  const { notice, dismiss } = line
  const error = notice?.tone === 'fail'
  const content = notice ? (
    <p key={notice.key} className="story-notice" data-tone={notice.tone}>
      <button type="button" className="story-notice-dismiss" title="Dismiss" onClick={dismiss}>
        {notice.text}
      </button>
      {notice.action ? (
        <button
          type="button"
          className="story-link"
          onClick={() => {
            dismiss()
            notice.action?.onSelect()
          }}
        >
          {notice.action.label}
        </button>
      ) : null}
    </p>
  ) : null

  return (
    <div className={className ? `story-notices ${className}` : 'story-notices'} data-active={notice ? '' : undefined}>
      <div role="status">{error ? null : content}</div>
      <div role="alert">{error ? content : null}</div>
    </div>
  )
}
