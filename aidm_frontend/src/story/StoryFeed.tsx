import {
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  type CSSProperties,
  type KeyboardEvent as ReactKeyboardEvent,
  type RefObject,
} from 'react'
import { Backpack, Coins, Flag, Heart, MapPin, ScrollText, Sparkles, Star, Swords, UserRound } from 'lucide-react'
import type { WorldEnding } from '../gameSelectors'
import { NarrativeProse } from '../NarrativeProse'
import type { SessionBoardProps, TurnQualityScores } from '../SessionBoard'
import type { TimelineEntry } from '../types'
import { D20Glyph, Ornament } from './glyphs'
import {
  buildStoryBeats,
  isEmptyStory,
  type ActionBeat,
  type ChangesBeat,
  type ChapterBeat,
  type NarrationBeat,
  type RollBeat,
  type StoryBeat,
  type StoryChangeGlyph,
} from './storyBeats'

export type StoryTextSize = 'default' | 'large' | 'extra'

type StoryFeedProps = {
  board: SessionBoardProps
  textSize: StoryTextSize
  /** Leaves the adventure for the title screen (the epilogue's "Start another adventure"). */
  onExitToTitle: () => void
}

/** Session storage key under which a dismissed epilogue is remembered for one session. */
const ENDING_DISMISSED_KEY = 'aidm:storyEndingDismissed'

function endingDismissedKey(sessionId: number | null) {
  return `${ENDING_DISMISSED_KEY}:${sessionId ?? 'none'}`
}

function readEndingDismissed(sessionId: number | null) {
  try {
    return window.sessionStorage.getItem(endingDismissedKey(sessionId)) === '1'
  } catch {
    return false
  }
}

function writeEndingDismissed(sessionId: number | null) {
  try {
    window.sessionStorage.setItem(endingDismissedKey(sessionId), '1')
  } catch {
    // Without storage the epilogue stays dismissed until the page reloads.
  }
}

/** Where a new narration's anchor (the action that triggered it) settles, below the feed's top edge. */
const ANCHOR_OFFSET = 96
/** Where the latest narration's first line sits when a session first opens. */
const INITIAL_OFFSET = 56
/** A reader further than this from the latest beat is reading history and is never moved. */
const AWAY_THRESHOLD = 120
const SCROLL_KEYS = new Set(['ArrowUp', 'ArrowDown', 'PageUp', 'PageDown', 'Home', 'End', ' '])
const QUALITY_SCORE_OPTIONS = [1, 2, 3, 4, 5] as const
const DEFAULT_TURN_QUALITY_SCORES: TurnQualityScores = { coherence: 4, fun: 4, rules: 4 }

function findBeat(container: HTMLElement, key: string) {
  for (const element of container.querySelectorAll<HTMLElement>('[data-beat-key]')) {
    if (element.dataset.beatKey === key) return element
  }
  return null
}

function contentTop(container: HTMLElement, element: HTMLElement) {
  return element.getBoundingClientRect().top - container.getBoundingClientRect().top + container.scrollTop
}

function maxScroll(container: HTMLElement) {
  return Math.max(0, container.scrollHeight - container.clientHeight)
}

function anchorKeyFor(beats: StoryBeat[], narrationKey: string) {
  const index = beats.findIndex((beat) => beat.key === narrationKey)
  const before = index > 0 ? beats[index - 1] : null
  return before && (before.kind === 'action' || before.kind === 'roll') ? before.key : narrationKey
}

/** The first beat that starts in view (else the one filling it) and its distance from the feed's top edge. */
type ReadingAnchor = { key: string; offset: number }

function readingAnchor(container: HTMLElement): ReadingAnchor | null {
  const box = container.getBoundingClientRect()
  let covering: ReadingAnchor | null = null
  for (const element of container.querySelectorAll<HTMLElement>('[data-beat-key]')) {
    const rect = element.getBoundingClientRect()
    if (rect.bottom <= box.top + 1) continue
    const anchor = { key: element.dataset.beatKey ?? '', offset: rect.top - box.top }
    if (rect.top >= box.top - 1) return rect.top < box.bottom ? anchor : covering ?? anchor
    covering = anchor
  }
  return covering
}

type ScrollMemory = {
  sessionId: number | null | undefined
  positioned: boolean
  userMoved: boolean
  keys: string[]
  scrollTop: number
  reading: ReadingAnchor | null
  follow: { anchorKey: string } | null
}

/**
 * The feed's reading behaviour: open on the latest narration's first line, keep
 * the line being read still when anything above it changes (older pages
 * prepended, the feedback glyphs moving on), settle a new narration under the
 * action that caused it and then stop, and never move a reader who has
 * scrolled away.
 */
function useStoryScroll(scrollRef: RefObject<HTMLDivElement | null>, beats: StoryBeat[], sessionId: number | null, loading: boolean) {
  const memory = useRef<ScrollMemory>({
    sessionId: undefined,
    positioned: false,
    userMoved: false,
    keys: [],
    scrollTop: 0,
    reading: null,
    follow: null,
  })
  const [jumpVisible, setJumpVisible] = useState(false)

  useLayoutEffect(() => {
    const container = scrollRef.current
    if (!container) return
    const memo = memory.current
    const record = () => {
      memo.scrollTop = container.scrollTop
      memo.reading = readingAnchor(container)
    }
    if (memo.sessionId !== sessionId) {
      memo.sessionId = sessionId
      memo.positioned = false
      memo.userMoved = false
      memo.keys = []
      memo.follow = null
      setJumpVisible(false)
    }
    const keys = beats.map((beat) => beat.key)
    const previousKeys = memo.keys
    memo.keys = keys

    if (!memo.positioned) {
      if (loading || !beats.length) {
        record()
        return
      }
      const latest = [...beats].reverse().find((beat): beat is NarrationBeat => beat.kind === 'narration')
      const positionAtLatest = () => {
        const element = latest ? findBeat(container, latest.key) : null
        // A session still on its opening narration starts at its chapter heading.
        container.scrollTop = latest?.opening
          ? 0
          : element
            ? Math.min(maxScroll(container), contentTop(container, element) - INITIAL_OFFSET)
            : maxScroll(container)
        record()
      }
      positionAtLatest()
      memo.positioned = true
      if (latest?.streaming) memo.follow = { anchorKey: anchorKeyFor(beats, latest.key) }
      // Web fonts change line lengths after first paint; settle again once they land.
      document.fonts?.ready.then(() => {
        if (!memo.userMoved && memo.sessionId === sessionId) positionAtLatest()
      })
      return
    }

    const reading = memo.reading ? findBeat(container, memo.reading.key) : null
    if (reading && memo.reading) {
      const shift = reading.getBoundingClientRect().top - container.getBoundingClientRect().top - memo.reading.offset
      if (Math.abs(shift) > 0.5) container.scrollTop += shift
    }

    const previousLast = previousKeys[previousKeys.length - 1]
    const previousLastIndex = previousLast ? keys.indexOf(previousLast) : -1
    const appended = previousLastIndex >= 0 ? beats.slice(previousLastIndex + 1) : previousKeys.length ? [] : beats
    if (appended.length) {
      const previousElement = previousLast ? findBeat(container, previousLast) : null
      const away =
        previousElement !== null &&
        previousElement.getBoundingClientRect().bottom - container.getBoundingClientRect().bottom > AWAY_THRESHOLD
      if (away) {
        memo.follow = null
        setJumpVisible(true)
      } else {
        const narration = appended.find((beat) => beat.kind === 'narration')
        if (narration) {
          memo.follow = { anchorKey: anchorKeyFor(beats, narration.key) }
          memo.userMoved = false
        } else {
          memo.follow = null
          container.scrollTo({ top: maxScroll(container), behavior: 'smooth' })
        }
      }
    }

    const last = beats[beats.length - 1]
    if (!appended.length && memo.userMoved && last?.kind === 'narration' && last.streaming) {
      // New words are arriving below a reader who has scrolled away from them.
      const element = findBeat(container, last.key)
      if (element && element.getBoundingClientRect().top - container.getBoundingClientRect().bottom > AWAY_THRESHOLD) {
        setJumpVisible(true)
      }
    }

    if (memo.follow && !memo.userMoved) {
      // Follow the text down until the anchor reaches its line, then stop.
      const anchor = findBeat(container, memo.follow.anchorKey)
      if (anchor) {
        const target = Math.min(contentTop(container, anchor) - ANCHOR_OFFSET, maxScroll(container))
        if (target > container.scrollTop + 1) container.scrollTo({ top: target, behavior: 'smooth' })
      }
    }
    record()
  }, [beats, loading, scrollRef, sessionId])

  const markUserMoved = () => {
    memory.current.userMoved = true
    memory.current.follow = null
  }

  const onScroll = () => {
    const container = scrollRef.current
    if (!container) return
    const memo = memory.current
    if (container.scrollTop < memo.scrollTop - 2 && memo.follow) markUserMoved()
    memo.scrollTop = container.scrollTop
    memo.reading = readingAnchor(container)
    if (!jumpVisible) return
    const lastKey = memo.keys[memo.keys.length - 1]
    const last = lastKey ? findBeat(container, lastKey) : null
    if (!last || last.getBoundingClientRect().top < container.getBoundingClientRect().bottom - 48) {
      setJumpVisible(false)
    }
  }

  const onKeyDown = (event: ReactKeyboardEvent<HTMLDivElement>) => {
    if (SCROLL_KEYS.has(event.key)) markUserMoved()
  }

  const jumpToLatest = () => {
    const container = scrollRef.current
    if (!container) return
    const memo = memory.current
    const last = beats[beats.length - 1]
    let top = maxScroll(container)
    memo.userMoved = false
    memo.follow = null
    if (last?.kind === 'narration') {
      const anchorKey = anchorKeyFor(beats, last.key)
      const anchor = findBeat(container, anchorKey)
      if (anchor) top = Math.min(top, contentTop(container, anchor) - ANCHOR_OFFSET)
      // Back with the latest narration: keep reading along with it as it streams.
      if (last.streaming) memo.follow = { anchorKey }
    }
    container.scrollTo({ top, behavior: 'smooth' })
    setJumpVisible(false)
  }

  return { jumpVisible, jumpToLatest, onScroll, onKeyDown, onUserScroll: markUserMoved }
}

function ChangeGlyph({ glyph }: { glyph: StoryChangeGlyph }) {
  const props = { size: 14, strokeWidth: 1.6, 'aria-hidden': true } as const
  switch (glyph) {
    case 'heart':
      return <Heart {...props} />
    case 'coin':
      return <Coins {...props} />
    case 'star':
      return <Star {...props} />
    case 'pack':
      return <Backpack {...props} />
    case 'spark':
      return <Sparkles {...props} />
    case 'scroll':
      return <ScrollText {...props} />
    case 'place':
      return <MapPin {...props} />
    case 'person':
      return <UserRound {...props} />
    case 'swords':
      return <Swords {...props} />
    case 'mark':
      return <span className="story-change-mark" aria-hidden="true" />
  }
}

function PreviouslyRecap({ recap }: { recap: string }) {
  const textRef = useRef<HTMLParagraphElement | null>(null)
  const [expanded, setExpanded] = useState(false)
  const [overflowing, setOverflowing] = useState(false)

  useLayoutEffect(() => {
    const element = textRef.current
    if (!element || expanded) return
    const measure = () => setOverflowing(element.scrollHeight > element.clientHeight + 1)
    const frame = requestAnimationFrame(measure)
    return () => cancelAnimationFrame(frame)
  }, [recap, expanded])

  return (
    <div className="story-previously">
      <p ref={textRef} className="story-previously-text" data-expanded={expanded || undefined}>
        <span className="story-previously-lead">Previously</span> {recap}
      </p>
      {overflowing || expanded ? (
        <button type="button" className="story-link story-previously-more" onClick={() => setExpanded((value) => !value)}>
          {expanded ? 'Read less' : 'Read more'}
        </button>
      ) : null}
    </div>
  )
}

function ChapterBeatView({ beat, board }: { beat: ChapterBeat; board: SessionBoardProps }) {
  const chapterNumber = board.activeSession?.chapter_number
  const label = beat.chapterLabel ?? (chapterNumber ? `Chapter ${chapterNumber}` : board.activeSessionTitle)
  const recap = board.sessionRecap.trim()
  return (
    <div className="story-chapter-heading">
      <Ornament className="story-chapter-ornament" />
      <p className="story-label story-chapter-label">{label}</p>
      <hr className="story-rule story-chapter-rule" />
      {recap ? <PreviouslyRecap recap={recap} /> : null}
    </div>
  )
}

function TurnFeedback({ entry, turnId, board }: { entry: TimelineEntry; turnId: number; board: SessionBoardProps }) {
  const [open, setOpen] = useState(false)
  const [draft, setDraft] = useState<TurnQualityScores>(DEFAULT_TURN_QUALITY_SCORES)
  const rateButtonRef = useRef<HTMLButtonElement | null>(null)
  const popoverRef = useRef<HTMLDivElement | null>(null)
  const reported = board.reportedBadTurnIds.has(turnId)
  const reporting = board.reportingBadTurnIds.has(turnId)
  const submitted = board.ratedTurnQualityIds.has(turnId)
  const submitting = board.ratingTurnQualityIds.has(turnId)

  useEffect(() => {
    if (!open) return
    popoverRef.current?.querySelector<HTMLElement>('[aria-pressed="true"], button')?.focus()
    const close = (event: PointerEvent) => {
      const target = event.target as Node | null
      if (target && (popoverRef.current?.contains(target) || rateButtonRef.current?.contains(target))) return
      setOpen(false)
    }
    document.addEventListener('pointerdown', close)
    return () => document.removeEventListener('pointerdown', close)
  }, [open])

  const closeAndReturnFocus = () => {
    setOpen(false)
    rateButtonRef.current?.focus()
  }

  const scoreGroup = (field: keyof TurnQualityScores, label: string) => (
    <div className="story-rate-group" role="group" aria-label={label}>
      <span className="story-label">{label}</span>
      <div className="story-rate-scores">
        {QUALITY_SCORE_OPTIONS.map((score) => (
          <button
            key={score}
            type="button"
            className="story-rate-score"
            aria-label={`${label} ${score}`}
            aria-pressed={draft[field] === score}
            onClick={() => setDraft((current) => ({ ...current, [field]: score }))}
          >
            {score}
          </button>
        ))}
      </div>
    </div>
  )

  return (
    <div
      className="story-turn-tools"
      data-open={open || undefined}
      onKeyDown={(event) => {
        if (open && event.key === 'Escape') {
          event.stopPropagation()
          closeAndReturnFocus()
        }
      }}
    >
      <button
        ref={rateButtonRef}
        type="button"
        className="story-turn-tool"
        aria-label="Rate this turn"
        title="Rate this turn"
        aria-expanded={open}
        onClick={() => setOpen((value) => !value)}
      >
        <Star size={15} strokeWidth={1.6} aria-hidden="true" />
      </button>
      <button
        type="button"
        className="story-turn-tool"
        aria-label={reported ? 'Bad turn reported' : 'Report bad turn'}
        title={reported ? 'Bad turn reported' : 'Report bad turn'}
        data-reported={reported || undefined}
        disabled={reported || reporting}
        onClick={() => board.reportBadTurn(entry)}
      >
        <Flag size={15} strokeWidth={1.6} aria-hidden="true" />
      </button>
      {open ? (
        <div ref={popoverRef} className="story-popover story-rate-popover">
          {submitted ? (
            <p className="story-rate-sent" role="status">
              Feedback sent.
            </p>
          ) : (
            <form
              aria-label="Beta turn feedback"
              onSubmit={(event) => {
                event.preventDefault()
                if (!submitting) board.submitTurnQuality(entry, draft)
              }}
            >
              {scoreGroup('coherence', 'Coherence')}
              {scoreGroup('fun', 'Fun')}
              {scoreGroup('rules', 'Rules')}
              <button type="submit" className="story-link story-rate-submit" disabled={submitting}>
                {submitting ? 'Recording' : 'Record'}
              </button>
            </form>
          )}
        </div>
      ) : null}
    </div>
  )
}

function NarrationBeatView({
  beat,
  board,
  withFeedback,
}: {
  beat: NarrationBeat
  board: SessionBoardProps
  withFeedback: boolean
}) {
  const caret = beat.streaming ? (
    <span className="story-caret" aria-hidden="true">
      ▍
    </span>
  ) : null
  return (
    <>
      <NarrativeProse text={beat.text} compactMechanicalLines={false} typographicQuotes trailing={caret} />
      {beat.notSaved ? <p className="story-label story-not-saved">not saved</p> : null}
      {withFeedback && beat.turnId !== null ? (
        <TurnFeedback entry={beat.entry} turnId={beat.turnId} board={board} />
      ) : null}
    </>
  )
}

function ActionBeatView({ beat, board }: { beat: ActionBeat; board: SessionBoardProps }) {
  return (
    <>
      <p className="story-label story-action-speaker">{beat.speaker}</p>
      <p className="story-action-text">{beat.text}</p>
      {beat.delivery === 'pending' ? <p className="story-label story-action-status">sending…</p> : null}
      {beat.delivery === 'failed' ? (
        <p className="story-action-status" data-failed="">
          <span className="story-label">not sent</span>
          <button type="button" className="story-link" onClick={() => board.dismissTimelineEntry(beat.entry.id)}>
            Remove
          </button>
        </p>
      ) : null}
    </>
  )
}

function formatModifier(modifier: number) {
  return modifier < 0 ? `− ${Math.abs(modifier)}` : `+ ${modifier}`
}

function RollBeatView({ beat }: { beat: RollBeat }) {
  const { roll } = beat
  const label = roll.name ? `${roll.name} · ${roll.checkLabel}` : roll.checkLabel
  const breakdown =
    roll.kept !== null
      ? [roll.die, roll.mode !== 'normal' && roll.faces.length > 1 ? roll.faces.join(' / ') : String(roll.kept)]
          .concat(roll.modifier ? [formatModifier(roll.modifier)] : [])
          .join(' ')
      : ''
  return (
    <p className="story-roll-line">
      <span className="story-roll-who">
        <D20Glyph className="story-roll-glyph" size={18} />
        <span className="story-label story-roll-label">{label}</span>
      </span>
      <span className="story-roll-result">
        {roll.total !== null ? (
          <span className="story-roll-total" data-natural={roll.natural ?? undefined}>
            {roll.total}
          </span>
        ) : null}
        {breakdown ? (
          <span className="story-roll-breakdown">
            {breakdown}
            {roll.mode !== 'normal' ? ` · ${roll.mode}` : ''}
          </span>
        ) : null}
        {roll.outcome ? (
          <span className="story-roll-outcome" data-outcome={roll.outcome}>
            {roll.attack ? (roll.outcome === 'success' ? 'hit' : 'miss') : roll.outcome}
            {roll.dc !== null ? (
              <span className="story-roll-dc">{roll.attack ? ` vs AC ${roll.dc}` : ` vs DC ${roll.dc}`}</span>
            ) : null}
          </span>
        ) : null}
      </span>
    </p>
  )
}

function ChangesBeatView({ beat }: { beat: ChangesBeat }) {
  return (
    <ul className="story-change-list" aria-label="What changed">
      {beat.changes.map((change, index) => (
        <li key={`${index}-${change.text}`} style={{ '--story-chip-index': index } as CSSProperties}>
          <span className="story-chip" data-tone={change.tone === 'default' ? undefined : change.tone}>
            <ChangeGlyph glyph={change.glyph} />
            {change.text}
          </span>
        </li>
      ))}
    </ul>
  )
}

function BeatView({
  beat,
  board,
  feedbackKey,
}: {
  beat: StoryBeat
  board: SessionBoardProps
  feedbackKey: string | null
}) {
  switch (beat.kind) {
    case 'chapter':
      return <ChapterBeatView beat={beat} board={board} />
    case 'narration':
      return <NarrationBeatView beat={beat} board={board} withFeedback={beat.key === feedbackKey} />
    case 'action':
      return <ActionBeatView beat={beat} board={board} />
    case 'roll':
      return <RollBeatView beat={beat} />
    case 'changes':
      return <ChangesBeatView beat={beat} />
    case 'notice':
      return <p className="story-notice-text">{beat.text}</p>
  }
}

function EmptyStory({ board }: { board: SessionBoardProps }) {
  const title = board.campaign?.title || board.campaignTitle || board.activeSessionTitle
  const description = board.campaign?.description?.trim() ?? ''
  return (
    <div className="story-empty">
      <p className="story-empty-title">{title}</p>
      {description ? <p className="story-empty-description">{description}</p> : null}
      {board.sendPending ? (
        <p className="story-empty-beginning" role="status">
          The story is beginning
          <span className="story-dots" aria-hidden="true">
            <i />
            <i />
            <i />
          </span>
        </p>
      ) : (
        <button type="button" className="story-primary" onClick={board.onStartAdventure}>
          Begin the adventure
        </button>
      )}
    </div>
  )
}

/** The adventure's last beat: its ending, and what to do next. */
function EpilogueBeat({
  ending,
  onDownloadChronicle,
  onExitToTitle,
  onKeepPlaying,
}: {
  ending: WorldEnding
  onDownloadChronicle: () => void
  onExitToTitle: () => void
  onKeepPlaying: () => void
}) {
  return (
    <section className="story-epilogue" aria-labelledby="story-epilogue-title">
      <Ornament className="story-chapter-ornament" />
      <p className="story-label story-epilogue-label">The end</p>
      <h2 id="story-epilogue-title" className="story-epilogue-title">{ending.title}</h2>
      {ending.summary ? <p className="story-epilogue-summary">{ending.summary}</p> : null}
      <div className="story-epilogue-links">
        <button type="button" className="story-link" onClick={onDownloadChronicle}>Download the Chronicle</button>
        <button type="button" className="story-link" onClick={onExitToTitle}>Start another adventure</button>
        <button type="button" className="story-link" onClick={onKeepPlaying}>Keep playing</button>
      </div>
    </section>
  )
}

function LoadingStory() {
  return (
    <div className="story-loading" aria-label="Loading the story">
      <span />
      <span />
      <span />
    </div>
  )
}

export function StoryFeed({ board, textSize, onExitToTitle }: StoryFeedProps) {
  const scrollRef = useRef<HTMLDivElement | null>(null)
  const [dismissedEndings, setDismissedEndings] = useState<ReadonlySet<number | null>>(() => new Set())
  const { turnRows, currentResponseEntry, sessionLogHasMore } = board
  const selectedPlayerName = board.selectedPlayer?.character_name ?? null
  const entries = useMemo(
    () =>
      currentResponseEntry && !turnRows.some((row) => row.id === currentResponseEntry.id)
        ? [...turnRows, currentResponseEntry]
        : turnRows,
    [turnRows, currentResponseEntry],
  )
  const beats = useMemo(
    () => buildStoryBeats(entries, { selectedPlayerName, historyComplete: !sessionLogHasMore }),
    [entries, selectedPlayerName, sessionLogHasMore],
  )
  const loading = board.sessionLoading && entries.length === 0
  const empty = !loading && isEmptyStory(beats)
  const scroll = useStoryScroll(scrollRef, beats, board.sessionId, loading)
  const feedbackKey =
    [...beats].reverse().find(
      (beat): beat is NarrationBeat => beat.kind === 'narration' && !beat.streaming && beat.turnId !== null,
    )?.key ?? null
  const ending = board.worldState.ending ?? null
  const showEnding =
    Boolean(ending) && !loading && !empty &&
    !dismissedEndings.has(board.sessionId) && !readEndingDismissed(board.sessionId)
  const keepPlaying = () => {
    writeEndingDismissed(board.sessionId)
    setDismissedEndings((current) => new Set(current).add(board.sessionId))
  }
  const announcement =
    board.sendPending || board.streamingTurnActive
      ? 'Dungeon Master response in progress.'
      : board.currentResponseEntry
        ? 'Dungeon Master response complete.'
        : 'Dungeon Master ready.'

  return (
    <div className="story-feed-region">
      <div
        ref={scrollRef}
        className="story-feed"
        role="log"
        aria-label="Session timeline"
        aria-live="off"
        data-text-size={textSize}
        onScroll={scroll.onScroll}
        onWheel={scroll.onUserScroll}
        onTouchMove={scroll.onUserScroll}
        onKeyDown={scroll.onKeyDown}
      >
        <div className="story-feed-column" data-state={loading ? 'loading' : empty ? 'empty' : 'story'}>
          {board.sessionLogHasMore && !empty ? (
            <div className="story-earlier">
              <button
                type="button"
                className="story-link"
                disabled={board.olderLogLoading}
                onClick={() => void board.loadOlderSessionLog()}
              >
                Earlier in the story
              </button>
              {board.olderLogLoading ? (
                <span className="story-dots" aria-hidden="true">
                  <i />
                  <i />
                  <i />
                </span>
              ) : null}
            </div>
          ) : null}
          {loading ? <LoadingStory /> : null}
          {empty ? <EmptyStory board={board} /> : null}
          {!loading && !empty
            ? beats.map((beat) => (
                <div
                  key={beat.key}
                  className={`story-beat story-${beat.kind}`}
                  data-beat-key={beat.key}
                  data-gap={beat.gap}
                  data-opening={beat.kind === 'narration' && beat.opening ? '' : undefined}
                  data-streaming={beat.kind === 'narration' && beat.streaming ? '' : undefined}
                  data-delivery={beat.kind === 'action' ? beat.delivery : undefined}
                >
                  <BeatView beat={beat} board={board} feedbackKey={feedbackKey} />
                </div>
              ))
            : null}
          {showEnding && ending ? (
            <EpilogueBeat
              ending={ending}
              onDownloadChronicle={() => void board.downloadSessionChronicle()}
              onExitToTitle={onExitToTitle}
              onKeepPlaying={keepPlaying}
            />
          ) : null}
        </div>
      </div>
      <span className="story-sr-only" role="status" aria-live="polite">
        {announcement}
      </span>
      {scroll.jumpVisible ? (
        <button type="button" className="story-link story-jump" onClick={scroll.jumpToLatest}>
          New — jump to latest
        </button>
      ) : null}
    </div>
  )
}
