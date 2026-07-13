import { useEffect, useRef, type Dispatch, type RefObject, type SetStateAction } from 'react'
import { MessagesSquare, Sparkles, Volume2, VolumeX, X } from 'lucide-react'
import { ThinIcon } from './AppChrome'
import {
  DICE_OPTIONS,
  INITIATIVE_ROLL_ABILITY_KEY,
  INVENTORY_ACTION_OPTIONS,
  INTERACTION_TYPE_OPTIONS,
  PLAIN_ROLL_ABILITY_KEY,
  composerModeLabel,
  interactionActionText,
  interactionTargetId,
  itemActionText,
  itemOptionSelectionKey,
  type ActionIntent,
  type AbilityOption,
  type ComposerMode,
  type InteractionTarget,
  type InteractionType,
  type InventoryAction,
  type ItemOption,
  type RollMode,
} from './gameActions'
import type { PendingRollOption } from './gameSelectors'
import type { ActivePlayer, TurnControl, TurnControlMode, TurnControlSource } from './types'

const ROLL_TRAY_DICE_OPTIONS = ['d20', ...DICE_OPTIONS.filter((die) => die !== 'd20')]
const ROLL_MODE_OPTIONS: Array<{ value: RollMode; label: string; shortLabel: string }> = [
  { value: 'normal', label: 'Normal', shortLabel: 'Normal' },
  { value: 'advantage', label: 'Advantage', shortLabel: 'Adv' },
  { value: 'disadvantage', label: 'Disadvantage', shortLabel: 'Dis' },
]

function abilityChipLabel(ability: AbilityOption) {
  const modifier = ability.modifier && ability.modifier !== '—' ? ability.modifier : ''
  return `${ability.label}${modifier ? ` ${modifier}` : ''}`
}

export type ActionComposerProps = {
  actionInputRef: RefObject<HTMLTextAreaElement | null>
  actionText: string
  adminPasscode: string
  adminToolsUnlocked: boolean
  canUseOperatorTools: boolean
  setActionText: Dispatch<SetStateAction<string>>
  updateActionText: (nextText: string) => void
  setAdminPasscode: Dispatch<SetStateAction<string>>
  selectedCharacterName: string | null
  selectedPlayerId: number | null
  activePlayers: ActivePlayer[]
  composerMode: ComposerMode
  selectedDie: string
  sendPending: boolean
  turnControl: TurnControl
  turnControlStatusLabel: string
  selectedPlayerHasTurn: boolean
  queuedActionText: string
  queuedActionRetryable?: boolean
  retryRecoverableSubmission: () => boolean
  clearQueuedAction: () => void
  updateTurnControl: (mode: TurnControlMode, activePlayerId?: number | null, source?: TurnControlSource) => void
  ttsEnabled: boolean
  ttsStatusClassName: string
  ttsStatusLabel: string
  ttsLatencyLabel: string
  canStopTts: boolean
  stopTtsAudio: () => void
  submitAction: (overrideMessage?: string, overrideIntent?: ActionIntent) => boolean
  toggleAdminTools: () => void
  startDiceRoll: (die?: string) => void
  preloadDiceRollDialog: () => void
  applyComposerMode: (mode: ComposerMode) => void
  updateSelectedDie: (die: string) => void
  rollMode: RollMode
  setRollMode: Dispatch<SetStateAction<RollMode>>
  rollReason: string
  setRollReason: Dispatch<SetStateAction<string>>
  pendingRollOptions: PendingRollOption[]
  rollTargetPendingTurnId: string
  setRollTargetPendingTurnId: Dispatch<SetStateAction<string>>
  spellName: string
  selectedAbility: AbilityOption | null
  selectedAbilityKey: string
  abilityOptions: AbilityOption[]
  updateRollAbilityKey: (key: string) => void
  updateSpellName: (name: string) => void
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
  setSelectedItemId: Dispatch<SetStateAction<string>>
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
  canUseOperatorTools,
  setActionText,
  updateActionText,
  setAdminPasscode,
  selectedCharacterName,
  selectedPlayerId,
  activePlayers,
  composerMode,
  selectedDie,
  sendPending,
  turnControl,
  turnControlStatusLabel,
  selectedPlayerHasTurn,
  queuedActionText,
  queuedActionRetryable,
  retryRecoverableSubmission,
  clearQueuedAction,
  updateTurnControl,
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
  rollReason,
  setRollReason,
  pendingRollOptions,
  rollTargetPendingTurnId,
  setRollTargetPendingTurnId,
  spellName,
  selectedAbility,
  selectedAbilityKey,
  abilityOptions,
  updateRollAbilityKey,
  updateSpellName,
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
  setSelectedItemId,
  setItemQuantity,
  updateSelectedInventoryAction,
  updateItemDraftName,
  updateItemCostGold,
}: ActionComposerProps) {
  const selectedItemIndex = selectedItem
    ? itemOptions.findIndex((item) => item === selectedItem || (item.id && item.id === selectedItem.id))
    : -1
  useEffect(() => {
    if (composerMode === 'roll') {
      preloadDiceRollDialog()
    }
  }, [composerMode, preloadDiceRollDialog])

  useEffect(() => {
    if (!canUseOperatorTools && adminToolsUnlocked) toggleAdminTools()
  }, [adminToolsUnlocked, canUseOperatorTools, toggleAdminTools])

  const characterName = selectedCharacterName ?? 'I'
  const adminUnlockRef = useRef({ count: 0, startedAt: 0 })
  const inventoryActionUsesOwnedItem = ['use', 'equip', 'unequip', 'drop', 'give', 'sell'].includes(selectedInventoryAction)
  const currentItemName = inventoryActionUsesOwnedItem ? selectedItem?.name ?? itemDraftName : itemDraftName
  const dexterityAbility = abilityOptions.find((ability) => ability.key === 'dexterity')
  const initiativeChipModifier =
    dexterityAbility?.modifier && dexterityAbility.modifier !== '—' ? `DEX ${dexterityAbility.modifier}` : 'DEX'
  const rollModeLabel = ROLL_MODE_OPTIONS.find((option) => option.value === rollMode)?.label ?? 'Normal'
  const rollReasonPreview = rollReason.trim() || (selectedAbility ? `${selectedAbility.label} check` : '')
  const rollPreviewParts = [
    `Requesting ${selectedDie.toUpperCase()}${
      selectedAbility ? ` ${selectedAbility.label}` : ''
    }`,
    ...(rollMode === 'normal' ? [] : [rollModeLabel]),
    ...(rollReasonPreview ? [rollReasonPreview] : []),
  ]
  const rollPreview = rollPreviewParts.join(' · ')
  const activeTurnPlayerId = turnControl.activePlayerId ?? selectedPlayerId ?? activePlayers[0]?.id ?? null
  const conductorControlled = turnControl.source === 'auto' || turnControl.source === 'ai'
  const manualOverrideActive = turnControl.source === 'manual' || turnControl.source === 'admin'
  const adminControlsVisible = canUseOperatorTools && adminToolsUnlocked
  const isRollMode = composerMode === 'roll'
  const toggleRollMode = () => applyComposerMode(isRollMode ? 'action' : 'roll')
  const turnModeButton = (mode: TurnControlMode, label: string) => (
    <button
      key={mode}
      type="button"
      aria-pressed={manualOverrideActive && turnControl.mode === mode}
      className={manualOverrideActive && turnControl.mode === mode ? 'selected' : ''}
      onClick={() => updateTurnControl(mode, mode === 'free' ? null : activeTurnPlayerId, 'manual')}
      disabled={!selectedPlayerId}
    >
      {label}
    </button>
  )

  const handleActionLabelClick = () => {
    if (!canUseOperatorTools) return
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
    <section className={`action-composer ${isRollMode ? 'roll-focused' : ''}`}>
      {!isRollMode ? (
        <>
          <label htmlFor="action-input" onClick={handleActionLabelClick}>
            Your Action <span>({composerModeLabel(composerMode, selectedDie)})</span>
          </label>
          <div className={`turn-control-strip ${selectedPlayerHasTurn ? 'open' : 'locked'}`} aria-live="polite">
            <div className="turn-control-summary">
              <span>Flow</span>
              <strong>{turnControlStatusLabel}</strong>
            </div>
            {adminControlsVisible ? (
              <div className="turn-control-actions" role="group" aria-label="Turn mode override">
                <button
                  type="button"
                  aria-pressed={conductorControlled}
                  className={conductorControlled ? 'selected' : ''}
                  onClick={() => updateTurnControl('free', null, 'auto')}
                  disabled={!selectedPlayerId}
                >
                  Auto
                </button>
                {turnModeButton('free', 'Free')}
                {turnModeButton('spotlight', 'Spotlight')}
                {turnModeButton('structured', 'Structured')}
                {turnControl.mode !== 'free' ? (
                  <select
                    aria-label="Active turn player"
                    value={activeTurnPlayerId ?? ''}
                    onChange={(event) => updateTurnControl(turnControl.mode, Number(event.target.value) || selectedPlayerId)}
                    disabled={!activePlayers.length || !selectedPlayerId}
                  >
                    {activePlayers.length ? (
                      activePlayers.map((player) => (
                        <option key={player.id} value={player.id}>
                          {player.character_name || player.name}
                        </option>
                      ))
                    ) : (
                      <option value={selectedPlayerId ?? ''}>{selectedCharacterName ?? 'Current player'}</option>
                    )}
                  </select>
                ) : null}
              </div>
            ) : null}
          </div>
          {queuedActionText ? (
            queuedActionRetryable ? (
              <div className="queued-action-strip delivery-uncertain" role="alert" aria-live="polite">
                <div className="queued-action-copy">
                  <span>Delivery uncertain</span>
                  <strong>{queuedActionText}</strong>
                  <small>
                    Retry safely reuses the original request. Check the timeline before discarding it.
                  </small>
                </div>
                <div className="queued-action-actions">
                  <button
                    type="button"
                    onClick={() => retryRecoverableSubmission()}
                    disabled={sendPending}
                  >
                    Retry safely
                  </button>
                  <button type="button" onClick={clearQueuedAction} disabled={sendPending}>
                    Discard after checking
                  </button>
                </div>
              </div>
            ) : (
              <div className="queued-action-strip" role="status" aria-live="polite">
                <span>Queued draft</span>
                <strong>{queuedActionText}</strong>
                <button type="button" onClick={clearQueuedAction}>
                  Clear
                </button>
              </div>
            )
          ) : null}
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
              onChange={(event) => updateActionText(event.target.value)}
              placeholder={selectedCharacterName ? 'Write your action...' : 'Choose a player before sending.'}
              rows={3}
            />
            <div className="input-action-row">
              <div className="mode-buttons">
                <button
                  type="button"
                  aria-label="Dice mode"
                  aria-pressed={false}
                  onClick={toggleRollMode}
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
                  aria-label="Spell mode"
                  aria-pressed={composerMode === 'spell'}
                  className={composerMode === 'spell' ? 'selected' : ''}
                  onClick={() => applyComposerMode('spell')}
                >
                  <Sparkles size={18} strokeWidth={1.45} />
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
                {adminControlsVisible ? (
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
                onClick={() => queuedActionRetryable ? retryRecoverableSubmission() : submitAction()}
                disabled={sendPending || (!queuedActionRetryable && !actionText.trim())}
              >
                <ThinIcon name="send" size={18} />
                {queuedActionRetryable ? 'Retry safely' : 'Send'}
              </button>
            </div>
          </div>
        </>
      ) : null}
      {isRollMode ? (
        <section className="roll-tray" aria-label="Roll options">
          <div className="roll-tray-section dice-section">
            <span className="roll-tray-label">Dice</span>
            <div className="dice-chip-grid" role="group" aria-label="Dice">
              {ROLL_TRAY_DICE_OPTIONS.map((dieOption) => (
                <button
                  key={dieOption}
                  type="button"
                  className={`dice-chip ${selectedDie === dieOption ? 'selected' : ''} ${
                    dieOption === 'd20' ? 'primary' : ''
                  }`}
                  aria-pressed={selectedDie === dieOption}
                  title={`Double-click to roll ${dieOption.toUpperCase()}`}
                  onClick={() => updateSelectedDie(dieOption)}
                  onDoubleClick={() => startDiceRoll(dieOption)}
                  disabled={sendPending}
                >
                  {dieOption.toUpperCase()}
                </button>
              ))}
            </div>
          </div>

          <div className="roll-tray-section check-section">
            <span className="roll-tray-label">Check</span>
            <div className="ability-chip-grid" role="group" aria-label="Roll ability">
              <button
                type="button"
                className={selectedAbilityKey === PLAIN_ROLL_ABILITY_KEY ? 'selected' : ''}
                aria-pressed={selectedAbilityKey === PLAIN_ROLL_ABILITY_KEY}
                onClick={() => updateRollAbilityKey(PLAIN_ROLL_ABILITY_KEY)}
                disabled={sendPending}
              >
                Plain
              </button>
              <button
                type="button"
                className={selectedAbilityKey === INITIATIVE_ROLL_ABILITY_KEY ? 'selected' : ''}
                aria-pressed={selectedAbilityKey === INITIATIVE_ROLL_ABILITY_KEY}
                aria-label={`Initiative ${initiativeChipModifier}`}
                onClick={() => updateRollAbilityKey(INITIATIVE_ROLL_ABILITY_KEY)}
                disabled={sendPending}
              >
                <span>Initiative</span>
                <small>{initiativeChipModifier}</small>
              </button>
              {abilityOptions.map((ability) => (
                <button
                  key={ability.key}
                  type="button"
                  className={selectedAbilityKey === ability.key ? 'selected' : ''}
                  aria-pressed={selectedAbilityKey === ability.key}
                  onClick={() => updateRollAbilityKey(ability.key)}
                  disabled={sendPending}
                >
                  {abilityChipLabel(ability)}
                </button>
              ))}
            </div>
            <span className="roll-ability-note">
              {selectedAbility ? `${selectedAbility.score} score` : 'No ability check'}
            </span>
          </div>

          <div className="roll-tray-controls">
            <div className="roll-mode-toggle" role="group" aria-label="Roll mode">
              {ROLL_MODE_OPTIONS.map((option) => (
                <button
                  key={option.value}
                  type="button"
                  className={rollMode === option.value ? 'selected' : ''}
                  aria-pressed={rollMode === option.value}
                  aria-label={option.label}
                  onClick={() => setRollMode(option.value)}
                  disabled={sendPending}
                >
                  {option.shortLabel}
                </button>
              ))}
            </div>

            <div className="roll-authority-note" role="note">
              <strong>Server roll</strong>
              <span>Modifiers, proficiency, wounds, and the final total use the current character sheet.</span>
            </div>

            <label className="roll-reason-field">
              <span>Reason</span>
              <input
                type="text"
                value={rollReason}
                aria-label="Roll reason"
                maxLength={120}
                placeholder="Stealth check"
                onChange={(event) => setRollReason(event.target.value)}
              />
            </label>

            {pendingRollOptions.length ? (
              <label className="roll-pending-field">
                <span>For</span>
                <select
                  value={rollTargetPendingTurnId}
                  aria-label="Target pending check"
                  title="Target pending check"
                  onChange={(event) => setRollTargetPendingTurnId(event.target.value)}
                >
                  <option value="">Latest pending check</option>
                  {pendingRollOptions.map((option) => (
                    <option key={option.turnId} value={option.turnId}>
                      {option.label} - {option.detail}
                    </option>
                  ))}
                </select>
              </label>
            ) : null}
          </div>

          <div className="roll-tray-footer">
            <span className="roll-preview" aria-live="polite">
              {rollPreview}
            </span>
            <button
              type="button"
              className="roll-primary-button"
              aria-label="Roll dice"
              onClick={() => startDiceRoll()}
              disabled={sendPending}
            >
              <ThinIcon name="dice" size={16} />
              Roll {selectedDie.toUpperCase()}
            </button>
          </div>
        </section>
      ) : null}
      {!isRollMode && composerMode === 'spell' ? (
        <div className="action-intent-panel spell-intent-panel" aria-label="Spell options">
          <select
            value={selectedAbilityKey}
            aria-label="Spellcasting ability"
            onChange={(event) => updateRollAbilityKey(event.target.value)}
            disabled={!abilityOptions.length}
          >
            {abilityOptions.length ? (
              abilityOptions.map((ability) => (
                <option key={ability.key} value={ability.key}>
                  {ability.label} {ability.modifier}
                </option>
              ))
            ) : (
              <option value={selectedAbilityKey}>No abilities</option>
            )}
          </select>
          <input
            type="text"
            value={spellName}
            aria-label="Spell name"
            maxLength={80}
            placeholder="Spell name"
            onChange={(event) => updateSpellName(event.target.value)}
          />
          <span>{selectedAbility ? `${selectedAbility.label} ${selectedAbility.modifier}` : 'Spell check'}</span>
        </div>
      ) : null}
      {!isRollMode && composerMode === 'item' ? (
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
            value={selectedItem && selectedItemIndex >= 0 ? itemOptionSelectionKey(selectedItem, selectedItemIndex) : ''}
            aria-label="Inventory item"
            onChange={(event) => {
              const nextItem = itemOptions.find(
                (item, index) => itemOptionSelectionKey(item, index) === event.target.value,
              ) ?? null
              setSelectedItemId(event.target.value)
              setActionText((current) =>
                itemActionText(characterName, selectedInventoryAction, nextItem?.name ?? itemDraftName, current, itemCostGold),
              )
            }}
            disabled={!inventoryActionUsesOwnedItem || !itemOptions.length}
          >
            {itemOptions.length ? (
              itemOptions.map((item, index) => (
                <option key={itemOptionSelectionKey(item, index)} value={itemOptionSelectionKey(item, index)}>
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
      {!isRollMode && composerMode === 'interact' ? (
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
                interactionTargets.find((target) => interactionTargetId(target) === event.target.value) ?? null
              setSelectedInteractionTargetId(event.target.value)
              setActionText((current) =>
                interactionActionText(characterName, nextTarget, selectedInteractionType, current),
              )
            }}
          >
            {interactionTargets.length ? (
              interactionTargets.map((target) => (
                <option key={interactionTargetId(target)} value={interactionTargetId(target)}>
                  {target.character_name} ({target.player_name})
                </option>
              ))
            ) : (
              <option value="">No current targets</option>
            )}
          </select>
          <span>
            {selectedInteractionTarget?.kind === 'npc'
              ? 'Scene NPC'
              : selectedInteractionTarget?.active
                ? 'Active now'
                : 'No target'}
          </span>
        </div>
      ) : null}
      {!isRollMode && adminControlsVisible && composerMode === 'admin' ? (
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
      <div className={`composer-tools ${isRollMode ? 'roll-focused-tools' : ''}`}>
        <button
          type="button"
          className={isRollMode ? 'selected' : ''}
          aria-pressed={isRollMode}
          onClick={toggleRollMode}
          onFocus={preloadDiceRollDialog}
          onMouseEnter={preloadDiceRollDialog}
          disabled={sendPending}
        >
          <ThinIcon name="dice" size={16} /> Roll <ThinIcon name="chevron" size={13} />
        </button>
        {!isRollMode ? (
          <>
            <button
              type="button"
              className={composerMode === 'spell' ? 'selected' : ''}
              aria-pressed={composerMode === 'spell'}
              onClick={() => applyComposerMode('spell')}
            >
              <Sparkles size={16} strokeWidth={1.45} /> Spell
            </button>
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
            {adminControlsVisible ? (
              <button
                type="button"
                className={composerMode === 'admin' ? 'selected' : ''}
                aria-pressed={composerMode === 'admin'}
                onClick={() => applyComposerMode('admin')}
              >
                <ThinIcon name="spark" size={16} /> Admin
              </button>
            ) : null}
          </>
        ) : null}
      </div>
    </section>
  )
}
