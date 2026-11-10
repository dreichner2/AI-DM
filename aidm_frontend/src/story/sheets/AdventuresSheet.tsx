import { type RefObject } from 'react'
import { ModalShell } from '../../ModalShell'
import type { Campaign } from '../../types'

/** What is known about an adventure's chapters (useWorkspaceQueries' campaign session meta). */
export type AdventureChapterMeta = {
  count: number
  updatedAt: string | null
}

type AdventuresSheetProps = {
  campaigns: Campaign[]
  currentCampaignId: number | null
  chapterMeta: Readonly<Record<number, AdventureChapterMeta | undefined>>
  worldNameById: ReadonlyMap<number, string>
  dialogRef: RefObject<HTMLElement | null>
  onClose: () => void
  onContinue: (campaignId: number) => void
  onPlayReadyMade: () => void
  onNewAdventure: () => void
  /** Operators only: absent hides the link. */
  onImportPack?: () => void
  onOpenArchive?: () => void
}

const MINUTE_MS = 60_000

function plural(count: number, word: string) {
  return `${count} ${word}${count === 1 ? '' : 's'}`
}

/** "just now", "5 minutes ago", "yesterday", "3 weeks ago". */
function playedAgo(value: string | null, now = Date.now()) {
  if (!value) return ''
  const time = new Date(value).getTime()
  if (Number.isNaN(time)) return ''
  const minutes = Math.floor(Math.max(0, now - time) / MINUTE_MS)
  if (minutes < 1) return 'just now'
  if (minutes < 60) return `${plural(minutes, 'minute')} ago`
  const hours = Math.floor(minutes / 60)
  if (hours < 24) return `${plural(hours, 'hour')} ago`
  const days = Math.floor(hours / 24)
  if (days === 1) return 'yesterday'
  if (days < 14) return `${days} days ago`
  if (days < 60) return `${Math.floor(days / 7)} weeks ago`
  if (days < 365) return `${Math.floor(days / 30)} months ago`
  return 'over a year ago'
}

/** `{world} · {n} chapters · played {age}`, leaving out what is not known. */
function adventureMeta(
  campaign: Campaign,
  worldNameById: ReadonlyMap<number, string>,
  chapters: AdventureChapterMeta | undefined,
) {
  const world = worldNameById.get(campaign.world_id) ?? campaign.world_name ?? ''
  const count = chapters?.count ?? campaign.session_count
  const age = playedAgo(chapters?.updatedAt ?? campaign.latest_activity_at)
  return [world, count ? plural(count, 'chapter') : '', age ? `played ${age}` : '']
    .filter(Boolean)
    .join(' · ')
}

function lastPlayed(campaign: Campaign, chapters: AdventureChapterMeta | undefined) {
  const time = new Date(chapters?.updatedAt ?? campaign.latest_activity_at ?? campaign.created_at ?? 0).getTime()
  return Number.isNaN(time) ? 0 : time
}

/** Your adventures: continue one, or begin another. */
export function AdventuresSheet({
  campaigns,
  chapterMeta,
  currentCampaignId,
  dialogRef,
  onClose,
  onContinue,
  onImportPack,
  onNewAdventure,
  onOpenArchive,
  onPlayReadyMade,
  worldNameById,
}: AdventuresSheetProps) {
  const adventures = campaigns
    .filter((campaign) => !campaign.is_archived)
    .sort((left, right) => {
      if (left.campaign_id === currentCampaignId) return -1
      if (right.campaign_id === currentCampaignId) return 1
      return lastPlayed(right, chapterMeta[right.campaign_id]) - lastPlayed(left, chapterMeta[left.campaign_id])
    })

  return (
    <ModalShell
      closeLabel="Close your adventures"
      dialogRef={dialogRef}
      labelledBy="adventures-title"
      onClose={onClose}
      title="Your adventures"
    >
      <div className="story-dialog-body">
        {adventures.length ? (
          <ul className="story-dialog-list" aria-label="Adventures">
            {adventures.map((campaign) => {
              const here = campaign.campaign_id === currentCampaignId
              const meta = adventureMeta(campaign, worldNameById, chapterMeta[campaign.campaign_id])
              return (
                <li key={campaign.campaign_id} className="story-dialog-row" data-current={here ? '' : undefined}>
                  <span className="story-dialog-row-copy">
                    <span className="story-dialog-row-name">{campaign.title}</span>
                    {meta ? <span className="story-dialog-row-meta">{meta}</span> : null}
                  </span>
                  <span className="story-dialog-row-actions">
                    {here ? (
                      <span className="story-dialog-here">Here now</span>
                    ) : (
                      <button
                        type="button"
                        className="story-link"
                        aria-label={`Continue ${campaign.title}`}
                        onClick={() => onContinue(campaign.campaign_id)}
                      >
                        Continue
                      </button>
                    )}
                  </span>
                </li>
              )
            })}
          </ul>
        ) : (
          <p className="story-dialog-empty">No adventures yet. Begin one below.</p>
        )}
        <hr className="story-rule" />
        <div className="story-dialog-links">
          <button type="button" className="story-link" onClick={onPlayReadyMade}>
            Play a ready-made adventure
          </button>
          <button type="button" className="story-link" onClick={onNewAdventure}>
            New adventure
          </button>
        </div>
        {onImportPack || onOpenArchive ? (
          <div className="story-dialog-links story-dialog-links-quiet">
            {onImportPack ? (
              <button type="button" className="story-link" onClick={onImportPack}>
                Import an adventure pack
              </button>
            ) : null}
            {onOpenArchive ? (
              <button type="button" className="story-link" onClick={onOpenArchive}>
                Archived adventures
              </button>
            ) : null}
          </div>
        ) : null}
      </div>
    </ModalShell>
  )
}
