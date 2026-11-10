import { useState } from 'react'

export type StoryNotice = {
  /** Identifies one occurrence: a new roll, error or redirect gets a new key, so it counts as newest. */
  key: string
  text: string
  tone?: 'fail'
  action?: { label: string; onSelect: () => void }
}

type StoryNoticesProps = {
  /** Every notice that currently applies, in any order. */
  notices: StoryNotice[]
}

/**
 * One calm line above the composer for what a player must see right now that
 * is not part of the story: another player's roll, a chapter change, a lost
 * connection. Only the most recently arrived notice is shown.
 */
export function StoryNotices({ notices }: StoryNoticesProps) {
  const [arrivalOrder, setArrivalOrder] = useState<string[]>([])
  const activeKeys = notices.map((notice) => notice.key)
  const stillActive = arrivalOrder.filter((key) => activeKeys.includes(key))
  const arrived = activeKeys.filter((key) => !arrivalOrder.includes(key))
  if (arrived.length || stillActive.length !== arrivalOrder.length) {
    setArrivalOrder([...stillActive, ...arrived])
  }
  const newestKey = [...stillActive, ...arrived].at(-1)
  const notice = notices.find((candidate) => candidate.key === newestKey) ?? null

  return (
    <div className="story-notices" role="status">
      {notice ? (
        <p key={notice.key} className="story-notice" data-tone={notice.tone}>
          <span>{notice.text}</span>
          {notice.action ? (
            <button type="button" className="story-link" onClick={notice.action.onSelect}>
              {notice.action.label}
            </button>
          ) : null}
        </p>
      ) : null}
    </div>
  )
}
