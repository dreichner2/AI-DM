import { X } from 'lucide-react'

export function BetaRuntimeNotesPanel({ onClose }: { onClose: () => void }) {
  return (
    <section
      id="beta-runtime-information"
      className="beta-runtime-notes-panel"
      role="region"
      aria-labelledby="beta-runtime-information-title"
    >
      <div>
        <h2 id="beta-runtime-information-title">Beta information</h2>
        <button
          type="button"
          aria-label="Close beta information"
          onClick={onClose}
        >
          <X size={14} />
        </button>
      </div>
      <p>AIDM is in active beta, so features may change between playtests.</p>
      <ul>
        <li>Live DM availability depends on the provider configured for the table. Current player-impacting problems appear as alerts.</li>
        <li>Voice narration is optional. Its current availability appears in the narration controls.</li>
      </ul>
    </section>
  )
}

export default BetaRuntimeNotesPanel
