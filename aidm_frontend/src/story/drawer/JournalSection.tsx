import { useState } from 'react'
import { Check, Circle, Lock, X, type LucideIcon } from 'lucide-react'
import {
  UNKNOWN_NPC_ROLE,
  type QuestObjectiveStatus,
  type WorldQuestSummary,
  type WorldRecordSummary,
} from '../../gameSelectors'
import type { InspectorPanelProps } from '../../InspectorPanel'
import type { SessionBoardProps } from '../../SessionBoard'
import { humanizedRole, storyJournalFromSnapshot, type StoryJournal } from './storyJournal'

type JournalSectionProps = {
  board: SessionBoardProps
  inspector: InspectorPanelProps
}

const OBJECTIVE_GLYPHS: Record<QuestObjectiveStatus, { icon: LucideIcon; label: string }> = {
  open: { icon: Circle, label: 'Open objective' },
  completed: { icon: Check, label: 'Completed objective' },
  failed: { icon: X, label: 'Failed objective' },
  blocked: { icon: Lock, label: 'Blocked objective' },
}

function ObjectiveGlyph({ status }: { status: QuestObjectiveStatus }) {
  const { icon: Icon, label } = OBJECTIVE_GLYPHS[status]
  return (
    <Icon className="story-objective-glyph" data-status={status} size={12} strokeWidth={2} role="img" aria-label={label} />
  )
}

type ObjectiveLine = { id: string; title: string; status: QuestObjectiveStatus; optional: boolean }

function ObjectiveList({ objectives }: { objectives: ObjectiveLine[] }) {
  if (!objectives.length) return null
  return (
    <ul className="story-objective-list">
      {objectives.map((objective) => (
        <li key={objective.id} className="story-quest-objective" data-status={objective.status}>
          <ObjectiveGlyph status={objective.status} />
          <span className="story-objective-text">
            {objective.title}
            {objective.optional ? <span className="story-objective-optional"> optional</span> : null}
          </span>
        </li>
      ))}
    </ul>
  )
}

/** A quest's objectives by title, or (for quests recorded without any) its one open objective. */
function QuestObjectives({ quest }: { quest: WorldQuestSummary }) {
  const objectives: ObjectiveLine[] = quest.objectives?.length
    ? quest.objectives.map((objective) => ({ ...objective, title: objective.title || objective.description }))
    : quest.objective
      ? [{ id: 'objective', title: quest.objective, status: 'open', optional: false }]
      : []
  return <ObjectiveList objectives={objectives} />
}

/** The quest from the server's journal: its title, the current stage, and the objectives so far. */
function JournalQuest({ journal }: { journal: StoryJournal }) {
  const { quest } = journal
  if (!quest) return null
  return (
    <li>
      <span className="story-quest-title">{quest.title}</span>
      {quest.stageTitle ? <span className="story-label story-quest-stage">{quest.stageTitle}</span> : null}
      {quest.stageSummary ? <span className="story-quest-summary">{quest.stageSummary}</span> : null}
      <ObjectiveList
        objectives={journal.objectives.map((objective) => ({
          ...objective,
          status: objective.status === 'completed' ? 'completed' : 'open',
        }))}
      />
    </li>
  )
}

function Paper({ paper }: { paper: WorldRecordSummary }) {
  const [open, setOpen] = useState(false)
  const text = paper.text || paper.summary
  return (
    <li>
      <button
        type="button"
        className="story-link story-paper-title"
        aria-expanded={open}
        disabled={!text}
        onClick={() => setOpen((value) => !value)}
      >
        {paper.title}
      </button>
      {open ? <p className="story-paper-text">{text}</p> : null}
    </li>
  )
}

function npcRole(role: string) {
  return role === UNKNOWN_NPC_ROLE ? '' : humanizedRole(role)
}

/**
 * The Journal tab: from the server's player-safe journal when it sends one,
 * otherwise derived from the world state (objective titles, plain roles, no
 * dispositions). Papers, finished quests and recent memory come from the world
 * state either way.
 */
export function JournalSection({ board, inspector }: JournalSectionProps) {
  const [finishedOpen, setFinishedOpen] = useState(false)
  const journal = storyJournalFromSnapshot(board.sessionState?.state_snapshot)
  const world = inspector.worldStatePanel
  const activeQuests = journal ? [] : world.activeQuests
  const finishedQuests = world.finishedQuests ?? []
  const clues = journal
    ? journal.clues.map((clue) => ({ id: clue.id, title: clue.title, text: clue.text }))
    : (world.clues ?? []).map((clue) => ({ id: clue.id, title: clue.title, text: clue.summary }))
  const papers = [...(world.handouts ?? []), ...(world.lore ?? [])]
  const people = journal
    ? journal.people
    : world.knownNpcs.map((npc) => ({ id: npc.id, name: npc.name, role: npcRole(npc.role), summary: '' }))
  const places = journal
    ? journal.places.map((place) => ({ id: place.id, name: place.name, detail: place.state }))
    : world.knownLocations.map((location) => ({
        id: location.id,
        name: location.name,
        detail: location.status && location.status !== 'unknown' ? location.status : '',
      }))
  const memory = inspector.visibleRecentMemory.map(([text]) => text)
  const threads = journal?.threads ?? []
  const hasQuest = Boolean(journal?.quest) || activeQuests.length > 0
  const empty =
    !hasQuest && !finishedQuests.length && !threads.length && !clues.length && !papers.length &&
    !people.length && !places.length && !memory.length

  return (
    <div className="story-sheet story-sheet-journal">
      {hasQuest || finishedQuests.length ? (
        <section aria-labelledby="story-journal-quests">
          <h3 id="story-journal-quests" className="story-label">Quests</h3>
          {hasQuest ? (
            <ul className="story-quest-list">
              {journal ? <JournalQuest journal={journal} /> : activeQuests.map((quest) => (
                <li key={quest.id || quest.title}>
                  <span className="story-quest-title">{quest.title}</span>
                  {quest.stage ? <span className="story-quest-summary">{quest.stage}</span> : null}
                  <QuestObjectives quest={quest} />
                </li>
              ))}
            </ul>
          ) : null}
          {finishedQuests.length ? (
            <div className="story-finished">
              <button
                type="button"
                className="story-link story-finished-toggle"
                aria-expanded={finishedOpen}
                onClick={() => setFinishedOpen((value) => !value)}
              >
                Finished · {finishedQuests.length}
              </button>
              {finishedOpen ? (
                <ul className="story-finished-list">
                  {finishedQuests.map((quest) => {
                    const status = quest.status.toLowerCase() === 'failed' ? 'failed' : 'completed'
                    return (
                      <li key={quest.id || quest.title} data-status={status}>
                        <ObjectiveGlyph status={status} />
                        <span className="story-finished-title">{quest.title}</span>
                      </li>
                    )
                  })}
                </ul>
              ) : null}
            </div>
          ) : null}
        </section>
      ) : null}

      {threads.length ? (
        <section aria-labelledby="story-journal-threads">
          <h3 id="story-journal-threads" className="story-label">Your threads</h3>
          <ul className="story-clue-list">
            {threads.map((thread) => (
              <li key={thread.id}>
                <span className="story-journal-name">{thread.title}</span>
                <span className="story-clue-summary">{thread.text}</span>
              </li>
            ))}
          </ul>
        </section>
      ) : null}

      {clues.length ? (
        <section aria-labelledby="story-journal-clues">
          <h3 id="story-journal-clues" className="story-label">Clues</h3>
          <ul className="story-clue-list">
            {clues.map((clue) => (
              <li key={clue.id}>
                <span className="story-journal-name">{clue.title}</span>
                {clue.text ? <span className="story-clue-summary">{clue.text}</span> : null}
              </li>
            ))}
          </ul>
        </section>
      ) : null}

      {papers.length ? (
        <section aria-labelledby="story-journal-papers">
          <h3 id="story-journal-papers" className="story-label">Papers</h3>
          <ul className="story-paper-list">
            {papers.map((paper) => <Paper key={paper.id} paper={paper} />)}
          </ul>
        </section>
      ) : null}

      {people.length ? (
        <section aria-labelledby="story-journal-people">
          <h3 id="story-journal-people" className="story-label">People</h3>
          <ul className="story-journal-list">
            {people.map((person) => (
              <li key={person.id || person.name}>
                <span className="story-journal-name">{person.name}</span>
                {person.role ? <span className="story-journal-detail"> — {person.role}</span> : null}
                {person.summary ? <span className="story-journal-summary">{person.summary}</span> : null}
              </li>
            ))}
          </ul>
        </section>
      ) : null}

      {places.length ? (
        <section aria-labelledby="story-journal-places">
          <h3 id="story-journal-places" className="story-label">Places</h3>
          <ul className="story-journal-list">
            {places.map((place) => (
              <li key={place.id || place.name}>
                <span className="story-journal-name">{place.name}</span>
                {place.detail ? <span className="story-journal-detail"> — {place.detail}</span> : null}
              </li>
            ))}
          </ul>
        </section>
      ) : null}

      {memory.length ? (
        <section aria-labelledby="story-journal-memory">
          <h3 id="story-journal-memory" className="story-label">Recent memory</h3>
          <ul className="story-memory-list">
            {memory.map((text, index) => <li key={`${index}-${text}`}>{text}</li>)}
          </ul>
        </section>
      ) : null}

      {empty ? <p className="story-sheet-empty">Nothing recorded yet.</p> : null}

      <div className="story-sheet-actions">
        <button type="button" className="story-link" onClick={() => void board.downloadSessionChronicle()}>
          Download Chronicle
        </button>
      </div>
    </div>
  )
}
