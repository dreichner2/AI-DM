import { type RefObject } from 'react'
import { X } from 'lucide-react'
import { ModalShell } from './ModalShell'
import type { Campaign } from './types'

type CampaignChooserDialogProps = {
  campaigns: Campaign[]
  canCreateCampaign: boolean
  dialogRef: RefObject<HTMLElement | null>
  onChoose: (campaignId: number) => void
  onClose: () => void
  onCreate: () => void
  worldNameById: ReadonlyMap<number, string>
}

export function CampaignChooserDialog({
  campaigns,
  canCreateCampaign,
  dialogRef,
  onChoose,
  onClose,
  onCreate,
  worldNameById,
}: CampaignChooserDialogProps) {
  return (
    <ModalShell
      className="campaign-dialog campaign-chooser-dialog"
      dialogRef={dialogRef}
      labelledBy="campaign-chooser-title"
      describedBy="campaign-chooser-description"
      onClose={onClose}
    >
        <header>
          <div>
            <span>Campaign</span>
            <h2 id="campaign-chooser-title">Choose Campaign</h2>
          </div>
          <button type="button" aria-label="Close campaign chooser" onClick={onClose}>
            <X size={18} />
          </button>
        </header>
        <div className="character-join-body">
          <p id="campaign-chooser-description">Choose the campaign before selecting or creating a character.</p>
          {campaigns.length ? (
            <div className="character-choice-list" aria-label="Available campaigns">
              {campaigns.map((item) => {
                const worldLabel = worldNameById.get(item.world_id) ?? `World ${item.world_id}`
                return (
                  <button
                    key={item.campaign_id}
                    type="button"
                    className="character-choice-card"
                    aria-label={`Choose ${item.title}`}
                    onClick={() => onChoose(item.campaign_id)}
                  >
                    <span>
                      <strong>{item.title}</strong>
                      <small>
                        {item.is_archived ? 'Archived' : 'Active'} / {worldLabel}
                      </small>
                    </span>
                    <em>Select</em>
                  </button>
                )
              })}
            </div>
          ) : (
            <div className="dialog-warning">
              <strong>No campaigns yet.</strong>
              <span>
                {canCreateCampaign
                  ? 'Create a campaign before choosing a character.'
                  : 'Ask the table Dungeon Master to create a campaign.'}
              </span>
            </div>
          )}
          <footer>
            <button type="button" className="secondary" onClick={onClose}>
              Cancel
            </button>
            {canCreateCampaign ? (
              <button type="button" data-autofocus onClick={onCreate}>
                Create Campaign
              </button>
            ) : null}
          </footer>
        </div>
    </ModalShell>
  )
}
