import type { ReactNode } from 'react'

type NarrativeProseProps = {
  text: string
  compactMechanicalLines?: boolean
  /**
   * Book typesetting: curly quotes and apostrophes, and dialogue recognised in
   * “curly” as well as "straight" quotes.
   */
  typographicQuotes?: boolean
  /** Rendered inline at the end of the last paragraph (for example a streaming caret). */
  trailing?: ReactNode
}

const QUOTED_DIALOGUE_RE = /"([^"]+)"/g
const TYPOGRAPHIC_DIALOGUE_RE = /"([^"\n]+)"|“([^”\n]+)”/g
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

/** Straight quotes and apostrophes to their typographic forms. */
function typeset(text: string) {
  return text
    .replace(/(^|[\s([—–-])'/g, '$1‘')
    .replace(/'/g, '’')
    .replace(/(^|[\s([—–-])"/g, '$1“')
    .replace(/"/g, '”')
}

function pushEmphasisNodes(nodes: ReactNode[], text: string, keyPrefix: string, typographic: boolean) {
  const source = typographic ? typeset(text) : text
  let lastIndex = 0
  let match: RegExpExecArray | null
  EMPHASIS_RE.lastIndex = 0
  while ((match = EMPHASIS_RE.exec(source)) !== null) {
    if (match.index > lastIndex) {
      nodes.push(source.slice(lastIndex, match.index))
    }
    nodes.push(
      <em key={`${keyPrefix}-em-${match.index}`}>
        {match[1]}
      </em>,
    )
    lastIndex = match.index + match[0].length
  }
  if (lastIndex < source.length) {
    nodes.push(source.slice(lastIndex))
  }
}

function renderInline(text: string, keyPrefix: string, typographic: boolean) {
  const nodes: ReactNode[] = []
  const dialogueRe = typographic ? TYPOGRAPHIC_DIALOGUE_RE : QUOTED_DIALOGUE_RE
  let lastIndex = 0
  let match: RegExpExecArray | null
  dialogueRe.lastIndex = 0
  while ((match = dialogueRe.exec(text)) !== null) {
    if (match.index > lastIndex) {
      pushEmphasisNodes(nodes, text.slice(lastIndex, match.index), `${keyPrefix}-${lastIndex}`, typographic)
    }
    nodes.push(
      typographic ? (
        <span className="narrative-dialogue" key={`${keyPrefix}-quote-${match.index}`}>
          {`“${typeset(match[1] ?? match[2])}”`}
        </span>
      ) : (
        <span className="narrative-dialogue" key={`${keyPrefix}-quote-${match.index}`}>
          &quot;{match[1]}&quot;
        </span>
      ),
    )
    lastIndex = match.index + match[0].length
  }
  if (lastIndex < text.length) {
    pushEmphasisNodes(nodes, text.slice(lastIndex), `${keyPrefix}-${lastIndex}`, typographic)
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

export function NarrativeProse({
  text,
  compactMechanicalLines = true,
  typographicQuotes = false,
  trailing,
}: NarrativeProseProps) {
  const paragraphs = splitParagraphs(text)
  if (!paragraphs.length) {
    return trailing ? (
      <div className="narrative-prose">
        <p>{trailing}</p>
      </div>
    ) : null
  }
  const lastIndex = paragraphs.length - 1

  return (
    <div className="narrative-prose">
      {paragraphs.map((paragraph, index) => {
        const tail = index === lastIndex ? trailing : null
        if (compactMechanicalLines && isMechanicalLine(paragraph)) {
          return (
            <p className="narrative-mechanical-line" key={`${index}-${paragraph.slice(0, 16)}`}>
              {renderInline(paragraph, `mechanical-${index}`, typographicQuotes)}
              {tail}
            </p>
          )
        }
        return (
          <p key={`${index}-${paragraph.slice(0, 16)}`}>
            {paragraphLines(paragraph).map((line, lineIndex) => (
              <span key={`${index}-${lineIndex}`}>
                {lineIndex > 0 ? <br /> : null}
                {renderInline(line, `${index}-${lineIndex}`, typographicQuotes)}
              </span>
            ))}
            {tail}
          </p>
        )
      })}
    </div>
  )
}
