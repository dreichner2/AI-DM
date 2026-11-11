import { useEffect, useState, type FormEvent } from 'react'
import type { ComingBackBlock } from '../apiContract.generated'
import { isRecord, stringValue } from '../gameSelectors'
import type { JsonRecord } from '../types'
import { Ornament } from './glyphs'
import '../styles/story-return.css'

/*
 * What brings a party back, kept out of the start-up bundle: the end of a chapter in the feed, the block that greets
 * a party returning after a while, the ending's last sections and the question asked of a player leaving.
 * No boxes: label lines and quiet text in the Story styles.
 */

function lines(value: unknown): string[] {
  return Array.isArray(value) ? value.map((line) => stringValue(line).trim()).filter(Boolean) : []
}

function rows(value: unknown): JsonRecord[] {
  return Array.isArray(value) ? value.filter(isRecord) : []
}

function Lines({ label, items }: { label: string; items: string[] }) {
  if (!items.length) return null
  return (
    <>
      <p className="story-label story-return-label">{label}</p>
      <ul className="story-return-lines">
        {items.map((item, index) => (
          <li key={`${index}-${item}`}>{item}</li>
        ))}
      </ul>
    </>
  )
}

/** `END OF CHAPTER 1 · THE EMPTY CARAVAN`, what the players decided, what is still open, and what lies ahead. */
export function ChapterEnd({ text, chapterEnd }: { text: string; chapterEnd: JsonRecord }) {
  const teaser = stringValue(chapterEnd.teaser).trim()
  return (
    <section className="story-chapter-end" aria-label={text}>
      <Ornament className="story-chapter-ornament" />
      <p className="story-label story-chapter-end-title">{text}</p>
      <Lines label="What you decided" items={lines(chapterEnd.decided)} />
      <Lines label="Still open" items={lines(chapterEnd.open)} />
      {teaser ? <p className="story-return-teaser">{teaser}</p> : null}
    </section>
  )
}

/** Above the action box after half an hour away: where the viewer stands, who has changed toward them, their plans. */
export function ComingBack({ load }: { load: () => Promise<ComingBackBlock | null> }) {
  const [block, setBlock] = useState<ComingBackBlock | null>(null)
  useEffect(() => {
    let live = true
    load().then(
      (loaded) => {
        if (live) setBlock(loaded)
      },
      () => undefined,
    )
    return () => {
      live = false
    }
  }, [load])
  if (!block) return null
  return (
    <section className="story-coming-back" aria-label="Where you stand">
      <Lines label="Where you stand" items={[block.where, ...block.people].filter(Boolean)} />
      <Lines label="You meant to" items={block.plans} />
    </section>
  )
}

/** The ending card's `WHAT BECAME OF THEM` and `YOUR WORD`, from the player view's `campaignPack.afterword`. */
export function EndingSections({ ending }: { ending: JsonRecord }) {
  const became = rows(ending.becameOfThem).map((row) => stringValue(row.text).trim())
  const word = rows(ending.yourWord).map((row) => stringValue(row.line).trim())
  return (
    <div className="story-ending-sections">
      <Lines label="What became of them" items={became.filter(Boolean)} />
      <Lines label="Your word" items={word.filter(Boolean)} />
    </div>
  )
}

/** Asked once of a player leaving with no plan noted this sitting. */
export function LeavingQuestion({ onNote, onSkip }: { onNote: (text: string) => void; onSkip: () => void }) {
  const [text, setText] = useState('')
  const submit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    if (text.trim()) onNote(text.trim())
    else onSkip()
  }
  return (
    <form className="story-leaving" onSubmit={submit}>
      <label className="story-label story-return-label" htmlFor="story-leaving-plan">
        Before you go — what do you mean to do next?
      </label>
      <input
        id="story-leaving-plan"
        className="story-leaving-input"
        value={text}
        maxLength={160}
        autoFocus
        onChange={(event) => setText(event.target.value)}
      />
      <div className="story-leaving-links">
        <button type="submit" className="story-link">
          Note it and leave
        </button>
        <button type="button" className="story-link" onClick={onSkip}>
          Skip
        </button>
      </div>
    </form>
  )
}
