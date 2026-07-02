import type { ReactNode } from 'react'

type NarrativeProseProps = {
  text: string
  compactMechanicalLines?: boolean
}

const QUOTED_DIALOGUE_RE = /"([^"]+)"/g
const EMPHASIS_RE = /\*([^*\n]+)\*/g
const MECHANICAL_LINE_RE =
  /\b(?:roll|dc|armor class|takes? \d+|spends? \d+|gains? \d+|loses? \d+|damage|healing|hp|xp|initiative|saving throw|check)\b/i

function splitParagraphs(text: string) {
  return text
    .replace(/\r\n?/g, '\n')
    .split(/\n{2,}/)
    .map((paragraph) => paragraph.trim())
    .filter(Boolean)
}

function pushEmphasisNodes(nodes: ReactNode[], text: string, keyPrefix: string) {
  let lastIndex = 0
  let match: RegExpExecArray | null
  EMPHASIS_RE.lastIndex = 0
  while ((match = EMPHASIS_RE.exec(text)) !== null) {
    if (match.index > lastIndex) {
      nodes.push(text.slice(lastIndex, match.index))
    }
    nodes.push(
      <em key={`${keyPrefix}-em-${match.index}`}>
        {match[1]}
      </em>,
    )
    lastIndex = match.index + match[0].length
  }
  if (lastIndex < text.length) {
    nodes.push(text.slice(lastIndex))
  }
}

function renderInline(text: string, keyPrefix: string) {
  const nodes: ReactNode[] = []
  let lastIndex = 0
  let match: RegExpExecArray | null
  QUOTED_DIALOGUE_RE.lastIndex = 0
  while ((match = QUOTED_DIALOGUE_RE.exec(text)) !== null) {
    if (match.index > lastIndex) {
      pushEmphasisNodes(nodes, text.slice(lastIndex, match.index), `${keyPrefix}-${lastIndex}`)
    }
    nodes.push(
      <span className="narrative-dialogue" key={`${keyPrefix}-quote-${match.index}`}>
        &quot;{match[1]}&quot;
      </span>,
    )
    lastIndex = match.index + match[0].length
  }
  if (lastIndex < text.length) {
    pushEmphasisNodes(nodes, text.slice(lastIndex), `${keyPrefix}-${lastIndex}`)
  }
  return nodes
}

function paragraphLines(paragraph: string) {
  return paragraph
    .split('\n')
    .map((line) => line.trim())
    .filter(Boolean)
}

function isMechanicalLine(paragraph: string) {
  const lines = paragraphLines(paragraph)
  if (lines.length !== 1) return false
  const line = lines[0]
  return line.length <= 180 && MECHANICAL_LINE_RE.test(line)
}

export function NarrativeProse({ text, compactMechanicalLines = true }: NarrativeProseProps) {
  const paragraphs = splitParagraphs(text)
  if (!paragraphs.length) return null

  return (
    <div className="narrative-prose">
      {paragraphs.map((paragraph, index) => {
        if (compactMechanicalLines && isMechanicalLine(paragraph)) {
          return (
            <p className="narrative-mechanical-line" key={`${index}-${paragraph.slice(0, 16)}`}>
              {renderInline(paragraph, `mechanical-${index}`)}
            </p>
          )
        }
        return (
          <p key={`${index}-${paragraph.slice(0, 16)}`}>
            {paragraphLines(paragraph).map((line, lineIndex) => (
              <span key={`${index}-${lineIndex}`}>
                {lineIndex > 0 ? <br /> : null}
                {renderInline(line, `${index}-${lineIndex}`)}
              </span>
            ))}
          </p>
        )
      })}
    </div>
  )
}
