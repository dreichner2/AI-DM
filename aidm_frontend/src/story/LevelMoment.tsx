import type { CSSProperties, ReactNode } from 'react'
import type { StoryBeatLevel } from '../apiContract.generated'
import { isRecord, stringValue } from '../gameSelectors'
import type { TimelineEntry } from '../types'
import '../styles/story-level.css'

const ORDINAL_SUFFIXES: Record<number, string> = { 1: 'st', 2: 'nd', 3: 'rd' }

/** A spell slot level (1 to 9) as "1st", "2nd", "3rd", "4th". */
function ordinal(level: number) {
  return `${level}${ORDINAL_SUFFIXES[level] ?? 'th'}`
}

/** The `level` object of the entry's level beat that reads `text`; null for a beat saved without one. */
function levelOf(entry: TimelineEntry, text: string): StoryBeatLevel | null {
  const beats: unknown[] = Array.isArray(entry.metadata.storyBeats) ? entry.metadata.storyBeats : []
  const beat = beats.find((value) => isRecord(value) && value.kind === 'level' && stringValue(value.text).trim() === text)
  const level = isRecord(beat) ? beat.level : null
  return isRecord(level) && typeof level.level === 'number' && isRecord(level.maxHp) ? (level as StoryBeatLevel) : null
}

/** What the hero gained, one quiet line each, in the moment's order. */
function gainedLines(level: StoryBeatLevel) {
  const lines = [`Max HP ${level.maxHp.from} → ${level.maxHp.to}`]
  if (level.proficiencyBonus) {
    lines.push(`Proficiency bonus +${level.proficiencyBonus.from} → +${level.proficiencyBonus.to}`)
  }
  for (const feature of level.features ?? []) lines.push(feature.text ? `${feature.name} — ${feature.text}` : feature.name)
  if (level.spellSlots?.length) {
    lines.push(`Spell slots — ${level.spellSlots.map((slot) => `${ordinal(slot.level)} level ${slot.from} → ${slot.to}`).join(', ')}`)
  }
  if (level.spells?.length) lines.push(`New spells — ${level.spells.join(', ')}${level.more ? ` and ${level.more} more` : ''}`)
  return lines
}

/**
 * A level reached, as a moment among the turn's beats: a hairline, the level in the accent, the hero's name,
 * what they gained in quiet lines, a hairline. A level beat saved without what was gained shows `fallback`.
 */
export function LevelMoment({
  entry,
  text,
  style,
  fallback,
}: {
  entry: TimelineEntry
  text: string
  style: CSSProperties
  fallback: ReactNode
}) {
  const level = levelOf(entry, text)
  if (!level) return fallback
  return (
    <li className="story-level-moment" style={style}>
      <hr className="story-rule" />
      <p className="story-label story-level-label">Level {level.level}</p>
      <p className="story-level-hero">{level.hero}</p>
      {gainedLines(level).map((line, index) => (
        <p key={`${index}-${line}`} className="story-level-line">
          {line}
        </p>
      ))}
      <hr className="story-rule" />
    </li>
  )
}
