import {
  useMemo,
  useState,
  type Dispatch,
  type FormEvent,
  type RefObject,
  type SetStateAction,
} from 'react'
import { ChevronDown, X } from 'lucide-react'
import { ModalShell } from './ModalShell'
import type { World } from './types'
import type { CampaignPackExample, CreateCampaignForm } from './useCampaignActions'

type CreateCampaignDialogProps = {
  defaultWorldId: number | null
  dialogRef: RefObject<HTMLElement | null>
  error: string
  form: CreateCampaignForm
  onClose: () => void
  onSubmit: (event: FormEvent<HTMLFormElement>) => void
  packOptions: CampaignPackExample[]
  packOptionsPending: boolean
  pending: boolean
  setForm: Dispatch<SetStateAction<CreateCampaignForm>>
  worldSelectOptions: World[]
}

function stringValue(value: unknown) {
  return typeof value === 'string' && value.trim() ? value.trim() : ''
}

function formatCampaignPackRange(min?: number, max?: number, unit?: string) {
  if (!min && !max) return ''
  const suffix = unit ? ` ${unit}` : ''
  if (!max || max === min) return `${min ?? max}${suffix}`
  if (!min) return `${max}${suffix}`
  return `${min}-${max}${suffix}`
}

function campaignPackLengthSummary(pack: CampaignPackExample | null) {
  const estimate = pack?.length_estimate
  if (!estimate) return ''
  const label = stringValue(estimate.label)
  const sessions = formatCampaignPackRange(
    estimate.sessions_min,
    estimate.sessions_max,
    'sessions',
  )
  return [label, sessions].filter(Boolean).join(' / ')
}

function campaignPackLengthDetail(pack: CampaignPackExample | null) {
  const estimate = pack?.length_estimate
  if (!estimate) return ''
  const hours = formatCampaignPackRange(estimate.hours_min, estimate.hours_max, 'hours')
  const checkpoints = estimate.checkpoint_count
    ? `${estimate.checkpoint_count} checkpoints`
    : ''
  return [hours, checkpoints].filter(Boolean).join(' / ')
}

export function CreateCampaignDialog({
  defaultWorldId,
  dialogRef,
  error,
  form,
  onClose,
  onSubmit,
  packOptions,
  packOptionsPending,
  pending,
  setForm,
  worldSelectOptions,
}: CreateCampaignDialogProps) {
  const [packPickerOpen, setPackPickerOpen] = useState(false)
  const selectedPack = useMemo(
    () => packOptions.find((pack) => pack.pack_id === form.packId) ?? null,
    [form.packId, packOptions],
  )
  const selectedPackLength = campaignPackLengthSummary(selectedPack)
  const selectedPackLengthDetail = campaignPackLengthDetail(selectedPack)

  const selectCampaignPack = (packId: string) => {
    const pack = packOptions.find((item) => item.pack_id === packId)
    setForm((current) => ({
      ...current,
      packId,
      title: pack ? pack.title : current.title,
      description: pack ? pack.description : current.description,
      worldId: pack ? '' : current.worldId || (defaultWorldId ? String(defaultWorldId) : ''),
      worldName: pack ? '' : current.worldName,
    }))
    setPackPickerOpen(false)
  }

  return (
    <ModalShell
      dialogRef={dialogRef}
      labelledBy="create-campaign-title"
      describedBy="create-campaign-description"
      onClose={onClose}
    >
        <header>
          <div>
            <span>Campaign</span>
            <h2 id="create-campaign-title">Create New Campaign</h2>
          </div>
          <button type="button" aria-label="Close create campaign" onClick={onClose}>
            <X size={18} />
          </button>
        </header>
        <form onSubmit={onSubmit}>
          <div className="campaign-pack-picker-field">
            <span>Campaign Pack</span>
            <div className="campaign-pack-picker">
              <button
                type="button"
                className="campaign-pack-picker-button"
                aria-label="Campaign Pack"
                aria-expanded={packPickerOpen}
                aria-controls="create-campaign-pack-options"
                onClick={() => setPackPickerOpen((current) => !current)}
                disabled={pending || packOptionsPending}
              >
                <span className="campaign-pack-picker-button-copy">
                  <strong>
                    {selectedPack
                      ? selectedPack.title
                      : packOptionsPending
                        ? 'Loading packs...'
                        : 'Start from scratch'}
                  </strong>
                  <small>
                    {selectedPack
                      ? selectedPackLength || 'Authored campaign pack'
                      : 'Create a custom campaign without a bundled story spine'}
                  </small>
                </span>
                <span className="campaign-pack-picker-button-meta">
                  {selectedPackLengthDetail || 'Custom'}
                </span>
                <ChevronDown size={16} aria-hidden="true" />
              </button>
              {packPickerOpen ? (
                <div
                  id="create-campaign-pack-options"
                  className="campaign-pack-picker-menu"
                  role="group"
                  aria-label="Campaign packs"
                >
                  <button
                    type="button"
                    aria-pressed={!form.packId}
                    className="campaign-pack-picker-option"
                    onClick={() => selectCampaignPack('')}
                  >
                    <span>
                      <strong>Start from scratch</strong>
                      <small>Create your own campaign name, description, and world.</small>
                    </span>
                    <em>Custom</em>
                  </button>
                  {packOptions.map((pack) => {
                    const lengthSummary = campaignPackLengthSummary(pack)
                    const lengthDetail = campaignPackLengthDetail(pack)
                    return (
                      <button
                        key={pack.pack_id}
                        type="button"
                        aria-pressed={form.packId === pack.pack_id}
                        className="campaign-pack-picker-option"
                        onClick={() => selectCampaignPack(pack.pack_id)}
                      >
                        <span>
                          <strong>{pack.title}</strong>
                          <small>
                            {pack.world_name ? `${pack.world_name} / ` : ''}
                            {lengthSummary || 'Authored campaign pack'}
                          </small>
                        </span>
                        <em>{lengthDetail || 'Story pack'}</em>
                      </button>
                    )
                  })}
                </div>
              ) : null}
            </div>
          </div>
          {selectedPack ? (
            <div className="campaign-pack-starter-summary">
              <strong>{selectedPack.title}</strong>
              <span>{selectedPack.short_description || selectedPack.description}</span>
              {selectedPackLength ? <small>{selectedPackLength}</small> : null}
              {selectedPackLengthDetail ? <small>{selectedPackLengthDetail}</small> : null}
              {selectedPack.world_name ? <small>{selectedPack.world_name}</small> : null}
            </div>
          ) : null}
          <label>
            Campaign Name
            <input
              autoFocus
              data-autofocus
              value={form.title}
              onChange={(event) =>
                setForm((current) => ({
                  ...current,
                  title: event.target.value,
                }))
              }
              placeholder="Ashes Beyond the Gate"
              disabled={pending || Boolean(selectedPack)}
            />
          </label>
          <label>
            Description
            <textarea
              value={form.description}
              onChange={(event) =>
                setForm((current) => ({
                  ...current,
                  description: event.target.value,
                }))
              }
              rows={3}
              placeholder="Opening premise, party goal, or tone..."
              disabled={pending || Boolean(selectedPack)}
            />
          </label>
          <label>
            World
            <select
              value={selectedPack ? form.worldId : form.worldName.trim() ? '' : form.worldId}
              onChange={(event) =>
                setForm((current) => ({
                  ...current,
                  worldId: event.target.value,
                  worldName: '',
                }))
              }
              disabled={pending}
            >
              <option value="">{selectedPack ? 'Use pack world' : 'Create a new world'}</option>
              {worldSelectOptions.map((world) => (
                <option key={world.world_id} value={world.world_id}>
                  {world.name}
                </option>
              ))}
            </select>
          </label>
          {selectedPack ? null : (
            <label>
              New World Name
              <input
                value={form.worldName}
                onChange={(event) =>
                  setForm((current) => ({
                    ...current,
                    worldId: '',
                    worldName: event.target.value,
                  }))
                }
                placeholder="Crystal Reach"
                disabled={pending}
              />
            </label>
          )}
          <p id="create-campaign-description">
            {selectedPack
              ? 'Use the pack world, or attach this campaign to an existing world.'
              : 'Select an existing world, or enter a new world name to create one for this campaign.'}
          </p>
          {error ? <div className="dialog-error" role="alert">{error}</div> : null}
          <footer>
            <button
              type="button"
              className="secondary"
              onClick={onClose}
              disabled={pending}
            >
              Cancel
            </button>
            <button type="submit" disabled={pending}>
              {pending ? 'Creating...' : 'Create Campaign'}
            </button>
          </footer>
        </form>
    </ModalShell>
  )
}
