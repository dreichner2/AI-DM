import { useState } from 'react'
import { Check, Circle, Lock, X, type LucideIcon } from 'lucide-react'
import type { QuestObjectiveStatus, WorldQuestSummary, WorldRecordSummary } from '../../gameSelectors'
import type { InspectorPanelProps } from '../../InspectorPanel'
import type { SessionBoardProps } from '../../SessionBoard'

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

/** A quest's objectives, or (for quests recorded without any) its one open objective. */
function QuestObjectives({ quest }: { quest: WorldQuestSummary }) {
  const objectives = quest.objectives?.length
    ? quest.objectives
    : quest.objective
      ? [{ id: 'objective', description: quest.objective, status: 'open' as const, optional: false }]
      : []
  if (!objectives.length) return null
  return (
    <ul className="story-objective-list">
      {objectives.map((objective) => (
        <li key={objective.id} className="story-quest-objective" data-status={objective.status}>
          <ObjectiveGlyph status={objective.status} />
          <span className="story-objective-text">
            {objective.description}
            {objective.optional ? <span className="story-objective-optional"> optional</span> : null}
          </span>
        </li>
      ))}
    </ul>
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

export function JournalSection({ board, inspector }: JournalSectionProps) {
  const [finishedOpen, setFinishedOpen] = useState(false)
  const { activeQuests, knownNpcs, knownLocations } = inspector.worldStatePanel
  const finishedQuests = inspector.worldStatePanel.finishedQuests ?? []
  const clues = inspector.worldStatePanel.clues ?? []
  const papers = [...(inspector.worldStatePanel.handouts ?? []), ...(inspector.worldStatePanel.lore ?? [])]
  const memory = inspector.visibleRecentMemory
  const empty =
    !activeQuests.length && !finishedQuests.length && !clues.length && !papers.length &&
    !knownNpcs.length && !knownLocations.length && !memory.length

  return (
    <div className="story-sheet story-sheet-journal">
      {activeQuests.length || finishedQuests.length ? (
        <section aria-labelledby="story-journal-quests">
          <h3 id="story-journal-quests" className="story-label">Quests</h3>
          {activeQuests.length ? (
            <ul className="story-quest-list">
              {activeQuests.map((quest) => (
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

      {clues.length ? (
        <section aria-labelledby="story-journal-clues">
          <h3 id="story-journal-clues" className="story-label">Clues</h3>
          <ul className="story-clue-list">
            {clues.map((clue) => (
              <li key={clue.id}>
                <span className="story-journal-name">{clue.title}</span>
                {clue.summary ? <span className="story-clue-summary">{clue.summary}</span> : null}
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

      {knownNpcs.length ? (
        <section aria-labelledby="story-journal-people">
          <h3 id="story-journal-people" className="story-label">People</h3>
          <ul className="story-journal-list">
            {knownNpcs.map((npc) => (
              <li key={npc.id || npc.name}>
                <span className="story-journal-name">{npc.name}</span>
                <span className="story-journal-detail"> — {[npc.role, npc.disposition].filter(Boolean).join(' / ')}</span>
              </li>
            ))}
          </ul>
        </section>
      ) : null}

      {knownLocations.length ? (
        <section aria-labelledby="story-journal-places">
          <h3 id="story-journal-places" className="story-label">Places</h3>
          <ul className="story-journal-list">
            {knownLocations.map((location) => (
              <li key={location.id || location.name}>
                <span className="story-journal-name">{location.name}</span>
                {location.status && location.status !== 'unknown' ? (
                  <span className="story-journal-detail"> — {location.status}</span>
                ) : null}
              </li>
            ))}
          </ul>
        </section>
      ) : null}

      {memory.length ? (
        <section aria-labelledby="story-journal-memory">
          <h3 id="story-journal-memory" className="story-label">Recent memory</h3>
          <ul className="story-memory-list">
            {memory.map(([text, source]) => (
              <li key={`${source}-${text}`}>
                {text}
                <span className="story-journal-detail"> · {source}</span>
              </li>
            ))}
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
