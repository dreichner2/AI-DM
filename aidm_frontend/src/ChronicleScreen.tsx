import type { ChronicleAdventure } from './apiContract.generated'
import './styles/story-return.css'

/** `Your chronicle`: each finished adventure with the endings reached and how much of it was never found. */
export function ChronicleScreen({ adventures, onClose }: { adventures: ChronicleAdventure[]; onClose: () => void }) {
  return (
    <section className="story-title-block story-chronicle" aria-labelledby="story-chronicle-heading">
      <h2 id="story-chronicle-heading" className="story-label">
        Your chronicle
      </h2>
      <ul className="story-chronicle-list">
        {adventures.map((adventure) => (
          <li key={adventure.packId}>
            <p className="story-chronicle-title">{adventure.title}</p>
            <p className="story-chronicle-line">{adventure.endings.join(' · ')}</p>
            <p className="story-chronicle-line">Things you never found: {adventure.neverFound}</p>
          </li>
        ))}
      </ul>
      <div className="story-title-links">
        <button type="button" className="story-link" onClick={onClose}>
          Back
        </button>
      </div>
    </section>
  )
}
