import { useEffect, useId, useRef, useState, type ReactNode } from 'react'
import { D20Glyph } from './glyphs'
import { D20_CYCLE_MS, DICE_CYCLE_MS, SETTLE_MS, cardDuration, type RollPlayback } from './rollPlayback'
import {
  diceParts,
  diceWithoutBonus,
  dieSides,
  flatModifier,
  outcomeText,
  partsText,
  signedNumber,
  type RollCardRecord,
} from './storyRolls'

type RollCardProps = {
  roll: RollCardRecord
  /** When the card plays; null shows it landed (history, reduced motion). */
  playback?: RollPlayback | null
  /** A roll the player pressed whose result has not arrived: the d20 keeps cycling. */
  awaiting?: boolean
  /** Called once, when a card that played has landed. */
  onPlayed?: () => void
}

/** Where a card is: held back, its d20 cycling, the die landed, its damage dice cycling, done. */
type Phase = 'waiting' | 'cycle' | 'die' | 'dice' | 'done'

const FIRST_STEP_MS = 40
const LAST_STEP_MS = 150

function hasDiceLine(roll: RollCardRecord) {
  return Boolean(roll.damage || roll.healing)
}

function phaseAt(roll: RollCardRecord, elapsed: number, awaiting: boolean): Phase {
  if (elapsed < 0) return 'waiting'
  if (roll.kind === 'damage' || roll.kind === 'healing') return elapsed < DICE_CYCLE_MS ? 'dice' : 'done'
  if (awaiting || elapsed < D20_CYCLE_MS) return 'cycle'
  if (elapsed < D20_CYCLE_MS + SETTLE_MS) return 'die'
  return hasDiceLine(roll) && elapsed < cardDuration(roll) ? 'dice' : 'done'
}

/** Time to the card's next change: a flicker step that slows as its die nears landing, or a phase boundary. */
function nextStepIn(roll: RollCardRecord, elapsed: number, awaiting: boolean): number | null {
  const phase = phaseAt(roll, elapsed, awaiting)
  if (phase === 'done') return null
  if (phase === 'waiting') return -elapsed
  if (phase === 'die') return D20_CYCLE_MS + SETTLE_MS - elapsed
  if (phase === 'cycle' && awaiting) return FIRST_STEP_MS * 2
  const start = phase === 'dice' && roll.kind !== 'damage' && roll.kind !== 'healing' ? D20_CYCLE_MS + SETTLE_MS : 0
  const window = phase === 'dice' ? DICE_CYCLE_MS : D20_CYCLE_MS
  const progress = Math.min(1, Math.max(0, (elapsed - start) / window))
  const step = FIRST_STEP_MS + (LAST_STEP_MS - FIRST_STEP_MS) * progress * progress
  return Math.max(1, Math.min(step, start + window - elapsed))
}

/**
 * The card's clock: `now` (`Date.now()`) read at mount, then again at each
 * change `nextChangeIn` names, with a fresh `spin` (0–1) for the faces that
 * flicker past.
 */
function useCardClock(nextChangeIn: (now: number) => number | null) {
  const [clock, setClock] = useState(readClock)
  const wait = nextChangeIn(clock.now)
  useEffect(() => {
    if (wait === null) return undefined
    const timer = window.setTimeout(() => setClock(readClock()), wait)
    return () => window.clearTimeout(timer)
  }, [clock, wait])
  return clock
}

function readClock() {
  return { now: Date.now(), spin: Math.random() }
}

/** A face that flickers past: any face of the die, different for each die on the card. */
function cycledFace(spin: number, salt: number, sides: number) {
  return Math.floor(((spin + salt * 0.618034) % 1) * sides) + 1
}

function spaced(nodes: ReactNode[]) {
  return nodes.flatMap((node, index) => (index ? [' ', node] : [node]))
}

function Face({ value, cycling, sides, spin, salt }: { value: number; cycling: boolean; sides: number; spin: number; salt: number }) {
  return <span className="roll-card-face">{cycling ? cycledFace(spin, salt, sides) : value}</span>
}

function D20Breakdown({ roll, phase, spin }: { roll: RollCardRecord; phase: Phase; spin: number }) {
  if (!roll.d20) return null
  const cycling = phase === 'cycle'
  const settled = phase === 'dice' || phase === 'done'
  const sides = dieSides(roll.die ?? 'd20')
  const { kept, mode, faces } = roll.d20
  const keptIndex = mode !== 'normal' && faces.length > 1 ? faces.indexOf(kept) : -1
  const modifier = flatModifier(roll)
  return (
    <span className="roll-card-breakdown" aria-hidden={cycling || undefined}>
      {roll.die ?? 'd20'}{' '}
      {keptIndex >= 0
        ? spaced(
            faces.map((face, index) =>
              index === keptIndex ? (
                <Face key={index} value={face} cycling={cycling} sides={sides} spin={spin} salt={0} />
              ) : (
                <s key={index} className="roll-card-dropped" title={`dropped (${mode})`}>
                  {cycling ? cycledFace(spin, index + 1, sides) : face}
                </s>
              ),
            ),
          )
        : <Face value={kept} cycling={cycling} sides={sides} spin={spin} salt={0} />}
      {settled && modifier ? ` ${signedNumber(modifier)}` : ''}
      {settled
        ? diceParts(roll).map((item, index) => (
            <span key={index} className="roll-card-dice-part">
              {` ${item.value < 0 ? '−' : '+'} ${diceWithoutBonus(item.dice ?? '')} ${(item.faces ?? []).join(' ')}`}
            </span>
          ))
        : null}
    </span>
  )
}

function DiceLine({ roll, cycling, spin }: { roll: RollCardRecord; cycling: boolean; spin: number }) {
  const healing = !roll.damage
  const dice = roll.damage ?? roll.healing
  if (!dice) return null
  const sides = dieSides(dice.dice)
  return (
    <div className="roll-card-dice" data-healing={healing || undefined}>
      <span className="roll-card-dice-total" aria-hidden={cycling || undefined}>
        {cycling ? '…' : healing ? `+${dice.total}` : `${dice.total}${roll.damage?.type ? ` ${roll.damage.type}` : ''}`}
      </span>
      <span className="roll-card-breakdown">
        {diceWithoutBonus(dice.dice)}{' '}
        {spaced(dice.faces.map((face, index) => <Face key={index} value={face} cycling={cycling} sides={sides} spin={spin} salt={index + 2} />))}
        {(dice.parts ?? []).map((item, index) => (
          <span key={index} className="roll-card-dice-part">
            {` + ${diceWithoutBonus(item.dice ?? '')} `}
            {spaced((item.faces ?? []).map((face, faceIndex) => (
              <Face key={faceIndex} value={face} cycling={cycling} sides={dieSides(item.dice ?? '')} spin={spin} salt={index * 7 + faceIndex + 3} />
            )))}
          </span>
        ))}
        {dice.bonus ? ` ${signedNumber(dice.bonus)}` : ''}
      </span>
      {roll.damage?.note && !cycling ? <span className="roll-card-note">{roll.damage.note}</span> : null}
    </div>
  )
}

function LabelLine({ roll }: { roll: RollCardRecord }) {
  return (
    <div className="roll-card-label">
      <D20Glyph className="roll-card-glyph" size={16} />
      <span className="roll-card-who">
        {roll.actor?.name ? (
          <>
            <span className="roll-card-actor">{roll.actor.name}</span>
            <span aria-hidden="true"> · </span>
          </>
        ) : null}
        <span className="roll-card-action">{roll.label}</span>
        {roll.source ? <span className="roll-card-source"> ({roll.source})</span> : null}
        {roll.target?.name ? (
          <>
            <span aria-hidden="true"> → </span>
            <span className="roll-card-target">{roll.target.name}</span>
          </>
        ) : null}
      </span>
    </div>
  )
}

/** The labelled parts of the roll, behind a quiet toggle. */
function Details({ roll }: { roll: RollCardRecord }) {
  const id = useId()
  const [open, setOpen] = useState(false)
  const lines = partsText(roll)
  if (!lines.length) return null
  return (
    <div className="roll-card-details">
      <button type="button" className="roll-card-more" aria-expanded={open} aria-controls={id} onClick={() => setOpen((value) => !value)}>
        {open ? 'Hide details' : 'Details'}
      </button>
      {open ? (
        <ul id={id} className="roll-card-parts">
          {lines.map((line, index) => (
            <li key={index}>{line}</li>
          ))}
        </ul>
      ) : null}
    </div>
  )
}

function InitiativeList({ roll, phase, spin }: { roll: RollCardRecord; phase: Phase; spin: number }) {
  const settled = phase === 'dice' || phase === 'done'
  return (
    <ol className="roll-card-initiative">
      {[...(roll.entries ?? [])].sort((left, right) => right.total - left.total).map((entry, index) => (
        <li key={`${entry.name}-${index}`} data-side={entry.side}>
          <span className="roll-card-initiative-name">{entry.name}</span>
          <span className="roll-card-initiative-total">{settled ? entry.total : ''}</span>
          <span className="roll-card-breakdown">
            d20 <Face value={entry.faces[0] ?? 0} cycling={phase === 'cycle'} sides={20} spin={spin} salt={index + 1} />
            {settled && entry.bonus ? ` ${signedNumber(entry.bonus)}` : ''}
          </span>
        </li>
      ))}
    </ol>
  )
}

/**
 * One roll on the table: who rolled what against whom, the total large with its
 * dice beside it, the outcome, and the damage or healing. A card that arrived
 * live plays first: its d20 cycles and lands on the record's face, the modifier
 * and total settle, the outcome appears, then the damage dice land. The numbers
 * it lands on are always the record's.
 */
export function RollCard({ roll, playback = null, awaiting = false, onPlayed }: RollCardProps) {
  const clock = useCardClock((at) => {
    if (playback?.instant) return at < playback.startAt ? playback.startAt - at : null
    if (!playback && !awaiting) return null
    return nextStepIn(roll, at - (playback?.startAt ?? at), awaiting)
  })
  const { now, spin } = clock
  const phase: Phase = !playback && !awaiting
    ? 'done'
    : playback?.instant
      ? now < playback.startAt ? 'waiting' : 'done'
      : phaseAt(roll, now - (playback?.startAt ?? now), awaiting)
  const live = (Boolean(playback) && !playback?.instant) || awaiting
  const played = useRef(false)

  useEffect(() => {
    if (phase !== 'done' || !live || played.current) return
    played.current = true
    onPlayed?.()
  }, [live, onPlayed, phase])

  const settled = phase === 'dice' || phase === 'done'
  const card = {
    className: 'roll-card',
    'data-kind': roll.kind,
    'data-side': roll.actor?.side ?? (roll.kind === 'initiative' ? 'hero' : 'enemy'),
    'data-phase': phase,
    'data-live': live || undefined,
  }

  if (roll.kind === 'initiative') {
    return (
      <div {...card}>
        <LabelLine roll={roll} />
        <InitiativeList roll={roll} phase={phase} spin={spin} />
      </div>
    )
  }

  if (roll.kind === 'damage' || roll.kind === 'healing') {
    return (
      <div {...card}>
        <LabelLine roll={roll} />
        <DiceLine roll={roll} cycling={phase === 'dice'} spin={spin} />
        {phase === 'done' ? <Details roll={roll} /> : null}
      </div>
    )
  }

  const big = phase === 'cycle' ? cycledFace(spin, 0, dieSides(roll.die ?? 'd20')) : phase === 'die' ? roll.d20?.kept : roll.total
  return (
    <div {...card} data-natural={settled ? roll.natural ?? undefined : undefined}>
      <LabelLine roll={roll} />
      <div className="roll-card-roll">
        <span className="roll-card-total" aria-hidden={phase === 'cycle' || undefined}>
          {big ?? ''}
        </span>
        <D20Breakdown roll={roll} phase={phase} spin={spin} />
        {phase === 'cycle' ? <span className="roll-card-sr">Rolling</span> : null}
      </div>
      {settled && (roll.outcome || roll.natural) ? (
        <div className="roll-card-outcome" data-outcome={roll.outcome ?? undefined}>
          {outcomeText(roll)}
          {roll.natural ? <span className="roll-card-natural">{`${roll.outcome ? ' · ' : ''}natural ${roll.natural === 'twenty' ? 20 : 1}`}</span> : null}
        </div>
      ) : null}
      {settled && hasDiceLine(roll) ? <DiceLine roll={roll} cycling={phase === 'dice'} spin={spin} /> : null}
      {phase === 'done' ? <Details roll={roll} /> : null}
    </div>
  )
}
