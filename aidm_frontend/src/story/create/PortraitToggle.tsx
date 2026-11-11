import { Fragment, useId } from 'react'
import type { SexKey } from '../../profileIcons'
import '../../styles/story-catalog.css'

const PORTRAITS: { sex: SexKey; label: string }[] = [
  { sex: 'male', label: 'Male' },
  { sex: 'female', label: 'Female' },
]

type PortraitToggleProps = {
  sex: SexKey
  disabled?: boolean
  /** Shows the `Portrait` label above the toggle; leave it out where a row already names it. */
  labelled?: boolean
  onChange: (sex: SexKey) => void
}

/** `Male · Female` as two text buttons, the current one underlined in ember. */
export function PortraitToggle({ sex, disabled = false, labelled = true, onChange }: PortraitToggleProps) {
  const labelId = useId()
  return (
    <div className="story-portrait-toggle">
      {labelled ? (
        <p id={labelId} className="story-label">
          Portrait
        </p>
      ) : null}
      <div role="group" aria-label={labelled ? undefined : 'Portrait'} aria-labelledby={labelled ? labelId : undefined}>
        {PORTRAITS.map(({ sex: option, label }, index) => (
          <Fragment key={option}>
            {index ? <span aria-hidden="true">·</span> : null}
            <button
              type="button"
              className="story-link"
              aria-pressed={sex === option}
              disabled={disabled}
              onClick={() => onChange(option)}
            >
              {label}
            </button>
          </Fragment>
        ))}
      </div>
    </div>
  )
}
