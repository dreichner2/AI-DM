import { BookOpen, Flame, LogIn, Play, Plus, Sparkles, UserPlus } from 'lucide-react'

type TitleScreenProps = {
  pending: boolean
  accountReady?: boolean
  canContinue: boolean
  campaignCount: number
  selectedCampaignTitle: string | null
  runtimeConfigured: boolean
  onPlayNow: () => void
  onLogIn?: () => void
  onCreateAccount?: () => void
  onCreateCampaign: () => void
  onContinue: () => void
}

function pluralize(value: number, singular: string, plural = `${singular}s`) {
  return `${value} ${value === 1 ? singular : plural}`
}

export function TitleScreen({
  pending,
  accountReady = true,
  canContinue,
  campaignCount,
  selectedCampaignTitle,
  runtimeConfigured,
  onPlayNow,
  onLogIn = () => undefined,
  onCreateAccount = () => undefined,
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
            Play Now jumps straight into The Road of Unremembered Kings with a ready-made hero,
            campaign, and opening scene. It skips account, campaign, and character setup.
          </p>
          <div className="title-screen-actions">
            <button
              type="button"
              className="title-action primary"
              disabled={pending}
              onClick={onPlayNow}
            >
              <Play size={19} fill="currentColor" />
              <span>{pending ? 'Preparing Adventure' : 'Play Now — Ready-Made Adventure'}</span>
            </button>
            {accountReady ? (
              <>
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
              </>
            ) : (
              <>
                <button
                  type="button"
                  className="title-action"
                  disabled={pending}
                  onClick={onLogIn}
                >
                  <LogIn size={19} />
                  <span>Log In</span>
                </button>
                <button
                  type="button"
                  className="title-action"
                  disabled={pending}
                  onClick={onCreateAccount}
                >
                  <UserPlus size={19} />
                  <span>Create Account</span>
                </button>
              </>
            )}
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
