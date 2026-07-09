import { BookOpen, Flame, Play, Plus, Sparkles } from 'lucide-react'

type TitleScreenProps = {
  pending: boolean
  canContinue: boolean
  campaignCount: number
  selectedCampaignTitle: string | null
  runtimeConfigured: boolean
  onPlayNow: () => void
  onCreateCampaign: () => void
  onContinue: () => void
}

function pluralize(value: number, singular: string, plural = `${singular}s`) {
  return `${value} ${value === 1 ? singular : plural}`
}

export function TitleScreen({
  pending,
  canContinue,
  campaignCount,
  selectedCampaignTitle,
  runtimeConfigured,
  onPlayNow,
  onCreateCampaign,
  onContinue,
}: TitleScreenProps) {
  const continueLabel = selectedCampaignTitle || pluralize(campaignCount, 'campaign')
  return (
    <section className="title-screen" aria-labelledby="title-screen-heading">
      <div className="title-screen-stage">
        <div className="title-screen-copy">
          <div className="title-mark" aria-hidden="true">
            <Flame size={34} fill="currentColor" />
            <span>AIDM</span>
          </div>
          <h1 id="title-screen-heading">AI-DM</h1>
          <p>
            The Road of Unremembered Kings is ready with a starter hero, a live table, and an opening scene.
          </p>
          <div className="title-screen-actions">
            <button
              type="button"
              className="title-action primary"
              disabled={pending}
              onClick={onPlayNow}
            >
              <Play size={19} fill="currentColor" />
              <span>{pending ? 'Preparing' : 'Play Now'}</span>
            </button>
            <button
              type="button"
              className="title-action"
              disabled={pending}
              onClick={onCreateCampaign}
            >
              <Plus size={19} />
              <span>New Campaign</span>
            </button>
            <button
              type="button"
              className="title-action"
              disabled={pending || !canContinue}
              onClick={onContinue}
            >
              <BookOpen size={19} />
              <span>Continue</span>
            </button>
          </div>
        </div>
        <aside className="title-screen-table" aria-label="Starting table">
          <div>
            <Sparkles size={18} />
            <span>Featured Table</span>
          </div>
          <strong>Road of Unremembered Kings</strong>
          <p>
            Arden Vale stands at a rain-dark mile marker where old crowns, missing names, and roadside trouble are waiting.
          </p>
          <dl>
            <div>
              <dt>Runtime</dt>
              <dd>{runtimeConfigured ? 'Live DM' : 'Safe Mode'}</dd>
            </div>
            <div>
              <dt>Continue</dt>
              <dd>{canContinue ? continueLabel : 'None yet'}</dd>
            </div>
          </dl>
        </aside>
      </div>
    </section>
  )
}
