import { Circle } from 'lucide-react'
import type { InspectorPanelProps } from '../../InspectorPanel'
import type { SessionBoardProps } from '../../SessionBoard'

type JournalSectionProps = {
  board: SessionBoardProps
  inspector: InspectorPanelProps
}

export function JournalSection({ board, inspector }: JournalSectionProps) {
  const { activeQuests, knownNpcs, knownLocations } = inspector.worldStatePanel
  const memory = inspector.visibleRecentMemory
  const empty = !activeQuests.length && !knownNpcs.length && !knownLocations.length && !memory.length

  return (
    <div className="story-sheet story-sheet-journal">
      {activeQuests.length ? (
        <section aria-labelledby="story-journal-quests">
          <h3 id="story-journal-quests" className="story-label">Quests</h3>
          <ul className="story-quest-list">
            {activeQuests.map((quest) => (
              <li key={quest.id || quest.title}>
                <span className="story-quest-title">{quest.title}</span>
                {quest.stage ? <span className="story-quest-summary">{quest.stage}</span> : null}
                {quest.objective ? (
                  <span className="story-quest-objective">
                    <Circle className="story-objective-glyph" size={11} aria-label="Open objective" />
                    {quest.objective}
                  </span>
                ) : null}
              </li>
            ))}
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
