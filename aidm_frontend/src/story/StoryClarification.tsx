import type { SessionBoardProps } from '../SessionBoard'

type StoryClarificationProps = {
  board: Pick<SessionBoardProps, 'clarificationRequest' | 'resolveClarification'>
}

/**
 * The Dungeon Master's question when an action was ambiguous ("which key?"):
 * the question in prose, each answer a chip. Sits just above the composer.
 */
export function StoryClarification({ board }: StoryClarificationProps) {
  const request = board.clarificationRequest
  if (!request) return null
  return (
    <section className="story-clarification" aria-label="The Dungeon Master asks">
      <p className="story-clarification-prompt">{request.prompt}</p>
      <div className="story-clarification-options" role="group" aria-label="Answers">
        {request.options.map((option) => (
          <button
            key={option.itemId}
            type="button"
            className="story-chip"
            title={option.description || undefined}
            onClick={() => board.resolveClarification(option.itemId)}
          >
            {option.label}
          </button>
        ))}
      </div>
    </section>
  )
}
