import { useRef, type Dispatch, type RefObject, type SetStateAction } from 'react'
import { MessagesSquare, Volume2, VolumeX, X } from 'lucide-react'
import { ThinIcon } from './AppChrome'
import {
  DICE_OPTIONS,
  INVENTORY_ACTION_OPTIONS,
  INTERACTION_TYPE_OPTIONS,
  PLAIN_ROLL_ABILITY_KEY,
  composerModeLabel,
  interactionActionText,
  itemActionText,
  type AbilityOption,
  type ComposerMode,
  type InteractionTarget,
  type InteractionType,
  type InventoryAction,
  type ItemOption,
  type RollMode,
} from './gameActions'
import type { PendingRollOption } from './gameSelectors'

export type ActionComposerProps = {
  actionInputRef: RefObject<HTMLTextAreaElement | null>
  actionText: string
  adminPasscode: string
  adminToolsUnlocked: boolean
  setActionText: Dispatch<SetStateAction<string>>
  setAdminPasscode: Dispatch<SetStateAction<string>>
  selectedCharacterName: string | null
  composerMode: ComposerMode
  selectedDie: string
  sendPending: boolean
  ttsEnabled: boolean
  ttsStatusClassName: string
  ttsStatusLabel: string
  ttsLatencyLabel: string
  canStopTts: boolean
  stopTtsAudio: () => void
  submitAction: () => void
  toggleAdminTools: () => void
  startDiceRoll: () => void
  preloadDiceRollDialog: () => void
  applyComposerMode: (mode: ComposerMode) => void
  updateSelectedDie: (die: string) => void
  rollMode: RollMode
  setRollMode: Dispatch<SetStateAction<RollMode>>
  rollModifier: string
  setRollModifier: Dispatch<SetStateAction<string>>
  rollReason: string
  setRollReason: Dispatch<SetStateAction<string>>
  pendingRollOptions: PendingRollOption[]
  rollTargetPendingTurnId: string
  setRollTargetPendingTurnId: Dispatch<SetStateAction<string>>
  selectedAbility: AbilityOption | null
  selectedAbilityKey: string
  abilityOptions: AbilityOption[]
  updateRollAbilityKey: (key: string) => void
  interactionTargets: InteractionTarget[]
  selectedInteractionTarget: InteractionTarget | null
  selectedInteractionTargetId: string
  selectedInteractionType: InteractionType
  setSelectedInteractionTargetId: Dispatch<SetStateAction<string>>
  setSelectedInteractionType: Dispatch<SetStateAction<InteractionType>>
  selectedInventoryAction: InventoryAction
  selectedItem: ItemOption | null
  itemDraftName: string
  itemQuantity: string
  itemCostGold: string
  itemOptions: ItemOption[]
  setSelectedItemName: Dispatch<SetStateAction<string>>
  setItemQuantity: Dispatch<SetStateAction<string>>
  updateSelectedInventoryAction: (action: InventoryAction) => void
  updateItemDraftName: (name: string) => void
  updateItemCostGold: (cost: string) => void
}

export function ActionComposer({
  actionInputRef,
  actionText,
  adminPasscode,
  adminToolsUnlocked,
  setActionText,
  setAdminPasscode,
  selectedCharacterName,
  composerMode,
  selectedDie,
  sendPending,
  ttsEnabled,
  ttsStatusClassName,
  ttsStatusLabel,
  ttsLatencyLabel,
  canStopTts,
  stopTtsAudio,
  submitAction,
  toggleAdminTools,
  startDiceRoll,
  preloadDiceRollDialog,
  applyComposerMode,
  updateSelectedDie,
  rollMode,
  setRollMode,
  rollModifier,
  setRollModifier,
  rollReason,
  setRollReason,
  pendingRollOptions,
  rollTargetPendingTurnId,
  setRollTargetPendingTurnId,
  selectedAbility,
  selectedAbilityKey,
  abilityOptions,
  updateRollAbilityKey,
  interactionTargets,
  selectedInteractionTarget,
  selectedInteractionTargetId,
  selectedInteractionType,
  setSelectedInteractionTargetId,
  setSelectedInteractionType,
  selectedInventoryAction,
  selectedItem,
  itemDraftName,
  itemQuantity,
  itemCostGold,
  itemOptions,
  setSelectedItemName,
  setItemQuantity,
  updateSelectedInventoryAction,
  updateItemDraftName,
  updateItemCostGold,
}: ActionComposerProps) {
  const characterName = selectedCharacterName ?? 'I'
  const adminUnlockRef = useRef({ count: 0, startedAt: 0 })
  const inventoryActionUsesOwnedItem = ['use', 'drop', 'give', 'sell'].includes(selectedInventoryAction)
  const currentItemName = inventoryActionUsesOwnedItem ? selectedItem?.name ?? itemDraftName : itemDraftName

  const handleActionLabelClick = () => {
    const now = Date.now()
    const unlockState = adminUnlockRef.current
    if (now - unlockState.startedAt > 15000) {
      unlockState.count = 0
      unlockState.startedAt = now
    }
    unlockState.count += 1
    if (unlockState.count >= 5) {
      unlockState.count = 0
      unlockState.startedAt = now
      toggleAdminTools()
    }
  }

  return (
    <section className="action-composer">
      <label htmlFor="action-input" onClick={handleActionLabelClick}>
        Your Action <span>({composerModeLabel(composerMode, selectedDie)})</span>
      </label>
      <div className={`tts-status-strip ${ttsStatusClassName}`} role="status" aria-live="polite">
        <span>
          {ttsEnabled ? <Volume2 size={14} /> : <VolumeX size={14} />}
          Narration <strong>{ttsStatusLabel}</strong>
        </span>
        {ttsLatencyLabel ? <small>{ttsLatencyLabel}</small> : null}
        {canStopTts ? (
          <button type="button" onClick={stopTtsAudio}>
            <X size={14} />
            Stop
          </button>
        ) : null}
      </div>
      <div className="composer-frame">
        <textarea
          id="action-input"
          ref={actionInputRef}
          value={actionText}
          onChange={(event) => setActionText(event.target.value)}
          placeholder={selectedCharacterName ? 'Write your action...' : 'Choose a player before sending.'}
          rows={4}
        />
        <div className="input-action-row">
          <div className="mode-buttons">
            <button
              type="button"
              aria-label="Dice mode"
              aria-pressed={composerMode === 'roll'}
              className={composerMode === 'roll' ? 'selected' : ''}
              onClick={() => applyComposerMode('roll')}
              onFocus={preloadDiceRollDialog}
              onMouseEnter={preloadDiceRollDialog}
              disabled={sendPending}
            >
              <ThinIcon name="dice" size={18} />
            </button>
            <button
              type="button"
              aria-label="Action mode"
              aria-pressed={composerMode === 'action'}
              className={composerMode === 'action' ? 'selected' : ''}
              onClick={() => applyComposerMode('action')}
            >
              <ThinIcon name="bolt" size={18} />
            </button>
            <button
              type="button"
              aria-label="Interact mode"
              aria-pressed={composerMode === 'interact'}
              className={composerMode === 'interact' ? 'selected' : ''}
              onClick={() => applyComposerMode('interact')}
            >
              <MessagesSquare size={18} strokeWidth={1.45} />
            </button>
            <button
              type="button"
              aria-label="OOC mode"
              aria-pressed={composerMode === 'ooc'}
              className={composerMode === 'ooc' ? 'selected' : ''}
              onClick={() => applyComposerMode('ooc')}
            >
              <ThinIcon name="chevron" size={17} />
            </button>
            {adminToolsUnlocked ? (
              <button
                type="button"
                aria-label="Admin mode"
                aria-pressed={composerMode === 'admin'}
                className={composerMode === 'admin' ? 'selected' : ''}
                onClick={() => applyComposerMode('admin')}
              >
                <ThinIcon name="spark" size={17} />
              </button>
            ) : null}
          </div>
          <button
            type="button"
            className="send-button"
            onClick={() => submitAction()}
            disabled={sendPending || !actionText.trim()}
          >
            <ThinIcon name="send" size={18} />
            Send
          </button>
        </div>
      </div>
      {composerMode === 'roll' ? (
        <div className="action-intent-panel" aria-label="Roll options">
          <select
            value={selectedAbilityKey}
            aria-label="Roll ability"
            onChange={(event) => updateRollAbilityKey(event.target.value)}
          >
            <option value={PLAIN_ROLL_ABILITY_KEY}>Plain roll</option>
            {abilityOptions.map((ability) => (
              <option key={ability.key} value={ability.key}>
                {ability.label} {ability.modifier}
              </option>
            ))}
          </select>
          <select
            value={rollMode}
            aria-label="Roll mode"
            onChange={(event) => setRollMode(event.target.value as RollMode)}
          >
            <option value="normal">Normal</option>
            <option value="advantage">Advantage</option>
            <option value="disadvantage">Disadvantage</option>
          </select>
          <input
            type="number"
            value={rollModifier}
            aria-label="Roll modifier"
            min={-99}
            max={99}
            onChange={(event) => setRollModifier(event.target.value)}
          />
          <input
            type="text"
            value={rollReason}
            aria-label="Roll reason"
            maxLength={120}
            placeholder="Reason"
            onChange={(event) => setRollReason(event.target.value)}
          />
          {pendingRollOptions.length ? (
            <select
              value={rollTargetPendingTurnId}
              aria-label="Target pending check"
              title="Target pending check"
              onChange={(event) => setRollTargetPendingTurnId(event.target.value)}
            >
              <option value="">Latest pending check</option>
              {pendingRollOptions.map((option) => (
                <option key={option.turnId} value={option.turnId}>
                  {option.label}
                </option>
              ))}
            </select>
          ) : null}
          <span>{selectedAbility ? `${selectedAbility.score} score` : 'No ability check'}</span>
          <button type="button" aria-label="Roll dice" onClick={() => startDiceRoll()} disabled={sendPending}>
            <ThinIcon name="dice" size={15} /> Roll
          </button>
        </div>
      ) : null}
      {composerMode === 'item' ? (
        <div className="action-intent-panel item-intent-panel" aria-label="Item options">
          <select
            value={selectedInventoryAction}
            aria-label="Inventory action"
            onChange={(event) => updateSelectedInventoryAction(event.target.value as InventoryAction)}
          >
            {INVENTORY_ACTION_OPTIONS.map((option) => (
              <option key={option.value} value={option.value}>
                {option.label}
              </option>
            ))}
          </select>
          <select
            value={selectedItem?.name ?? ''}
            aria-label="Inventory item"
            onChange={(event) => {
              const nextItem = itemOptions.find((item) => item.name === event.target.value) ?? null
              setSelectedItemName(event.target.value)
              setActionText((current) =>
                itemActionText(characterName, selectedInventoryAction, nextItem?.name ?? itemDraftName, current, itemCostGold),
              )
            }}
            disabled={!inventoryActionUsesOwnedItem || !itemOptions.length}
          >
            {itemOptions.length ? (
              itemOptions.map((item) => (
                <option key={item.name} value={item.name}>
                  {item.name} x{item.quantity}
                </option>
              ))
            ) : (
              <option value="">No inventory</option>
            )}
          </select>
          <input
            type="text"
            value={inventoryActionUsesOwnedItem ? currentItemName : itemDraftName}
            aria-label="Item name"
            maxLength={80}
            placeholder={inventoryActionUsesOwnedItem ? 'Inventory item' : 'Item name'}
            onChange={(event) => updateItemDraftName(event.target.value)}
            disabled={inventoryActionUsesOwnedItem}
          />
          <input
            type="number"
            value={itemQuantity}
            aria-label="Item quantity"
            min={1}
            max={999}
            onChange={(event) => setItemQuantity(event.target.value)}
          />
          {selectedInventoryAction === 'buy' || selectedInventoryAction === 'sell' ? (
            <input
              type="number"
              value={itemCostGold}
              aria-label="Gold cost"
              min={0}
              max={99999}
              onChange={(event) => updateItemCostGold(event.target.value)}
            />
          ) : null}
          <span>{inventoryActionUsesOwnedItem ? 'Held item' : 'Attempt'}</span>
        </div>
      ) : null}
      {composerMode === 'interact' ? (
        <div className="action-intent-panel interaction-intent-panel" aria-label="Interaction options">
          <select
            value={selectedInteractionType}
            aria-label="Interaction type"
            onChange={(event) => {
              const nextType = event.target.value as InteractionType
              setSelectedInteractionType(nextType)
              setActionText((current) =>
                interactionActionText(characterName, selectedInteractionTarget, nextType, current),
              )
            }}
          >
            {INTERACTION_TYPE_OPTIONS.map((option) => (
              <option key={option.value} value={option.value}>
                {option.label}
              </option>
            ))}
          </select>
          <select
            value={selectedInteractionTargetId}
            aria-label="Interaction target"
            disabled={!interactionTargets.length}
            onChange={(event) => {
              const nextTarget =
                interactionTargets.find((target) => String(target.player_id) === event.target.value) ?? null
              setSelectedInteractionTargetId(event.target.value)
              setActionText((current) =>
                interactionActionText(characterName, nextTarget, selectedInteractionType, current),
              )
            }}
          >
            {interactionTargets.length ? (
              interactionTargets.map((target) => (
                <option key={target.player_id} value={String(target.player_id)}>
                  {target.character_name} ({target.player_name})
                </option>
              ))
            ) : (
              <option value="">No other players</option>
            )}
          </select>
          <span>{selectedInteractionTarget?.active ? 'Active now' : selectedInteractionTarget ? 'Campaign player' : 'No target'}</span>
        </div>
      ) : null}
      {adminToolsUnlocked && composerMode === 'admin' ? (
        <div className="action-intent-panel admin-intent-panel" aria-label="Admin options">
          <input
            type="password"
            value={adminPasscode}
            aria-label="Admin passcode"
            placeholder="Admin passcode"
            autoComplete="off"
            onChange={(event) => setAdminPasscode(event.target.value)}
          />
          <span>Authenticated override</span>
        </div>
      ) : null}
      <div className="composer-tools">
        <button
          type="button"
          className={composerMode === 'roll' ? 'selected' : ''}
          aria-pressed={composerMode === 'roll'}
          onClick={() => applyComposerMode('roll')}
          onFocus={preloadDiceRollDialog}
          onMouseEnter={preloadDiceRollDialog}
          disabled={sendPending}
        >
          <ThinIcon name="dice" size={16} /> Roll <ThinIcon name="chevron" size={13} />
        </button>
        <select
          className="dice-select"
          value={selectedDie}
          aria-label="Select die"
          onChange={(event) => updateSelectedDie(event.target.value)}
        >
          {DICE_OPTIONS.map((die) => (
            <option key={die} value={die}>
              {die.toUpperCase()}
            </option>
          ))}
        </select>
        <button
          type="button"
          className={composerMode === 'item' ? 'selected' : ''}
          aria-pressed={composerMode === 'item'}
          onClick={() => applyComposerMode('item')}
        >
          <ThinIcon name="briefcase" size={16} /> Item
        </button>
        <button
          type="button"
          className={composerMode === 'interact' ? 'selected' : ''}
          aria-pressed={composerMode === 'interact'}
          onClick={() => applyComposerMode('interact')}
        >
          <MessagesSquare size={16} strokeWidth={1.45} /> Interact
        </button>
        <button
          type="button"
          className={composerMode === 'emote' ? 'selected' : ''}
          aria-pressed={composerMode === 'emote'}
          onClick={() => applyComposerMode('emote')}
        >
          <ThinIcon name="smile" size={16} /> Emote
        </button>
        <button
          type="button"
          className={composerMode === 'ooc' ? 'selected' : ''}
          aria-pressed={composerMode === 'ooc'}
          onClick={() => applyComposerMode('ooc')}
        >
          <ThinIcon name="dot" size={16} /> OOC
        </button>
        {adminToolsUnlocked ? (
          <button
            type="button"
            className={composerMode === 'admin' ? 'selected' : ''}
            aria-pressed={composerMode === 'admin'}
            onClick={() => applyComposerMode('admin')}
          >
            <ThinIcon name="spark" size={16} /> Admin
          </button>
        ) : null}
      </div>
    </section>
  )
}
