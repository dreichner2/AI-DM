import { useRef, type Dispatch, type RefObject, type SetStateAction } from 'react'
import { MessagesSquare, Minus, Plus, RotateCcw, Sparkles, Volume2, VolumeX, X } from 'lucide-react'
import { ThinIcon } from './AppChrome'
import {
  DICE_OPTIONS,
  INITIATIVE_ROLL_ABILITY_KEY,
  INVENTORY_ACTION_OPTIONS,
  INTERACTION_TYPE_OPTIONS,
  PLAIN_ROLL_ABILITY_KEY,
  composerModeLabel,
  formatModifier,
  interactionActionText,
  interactionTargetId,
  itemActionText,
  parseRollModifier,
  rollActionText,
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

function clampRollModifier(value: number) {
  if (!Number.isFinite(value)) return 0
  return Math.max(-99, Math.min(99, Math.trunc(value)))
}

function abilityChipLabel(ability: AbilityOption) {
  const modifier = ability.modifier && ability.modifier !== '—' ? ability.modifier : ''
  return `${ability.label}${modifier ? ` ${modifier}` : ''}`
}

export type ActionComposerProps = {
  actionInputRef: RefObject<HTMLTextAreaElement | null>
  actionText: string
  adminPasscode: string
  adminToolsUnlocked: boolean
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
  clearQueuedAction: () => void
  updateTurnControl: (mode: TurnControlMode, activePlayerId?: number | null, source?: TurnControlSource) => void
  ttsEnabled: boolean
  ttsStatusClassName: string
  ttsStatusLabel: string
  ttsLatencyLabel: string
  canStopTts: boolean
  stopTtsAudio: () => void
  submitAction: () => void
  toggleAdminTools: () => void
  startDiceRoll: (die?: string) => void
  preloadDiceRollDialog: () => void
  applyComposerMode: (mode: ComposerMode) => void
  updateSelectedDie: (die: string) => void
  rollMode: RollMode
  setRollMode: Dispatch<SetStateAction<RollMode>>
  rollModifier: string
  setRollModifier: Dispatch<SetStateAction<string>>
  rollProficiencyApplied: boolean
  rollProficiencyBonus: number
  setRollProficiencyApplied: Dispatch<SetStateAction<boolean>>
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
  rollModifier,
  setRollModifier,
  rollProficiencyApplied,
  rollProficiencyBonus,
  setRollProficiencyApplied,
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
  setSelectedItemName,
  setItemQuantity,
  updateSelectedInventoryAction,
  updateItemDraftName,
  updateItemCostGold,
}: ActionComposerProps) {
  const characterName = selectedCharacterName ?? 'I'
  const adminUnlockRef = useRef({ count: 0, startedAt: 0 })
  const inventoryActionUsesOwnedItem = ['use', 'equip', 'unequip', 'drop', 'give', 'sell'].includes(selectedInventoryAction)
  const currentItemName = inventoryActionUsesOwnedItem ? selectedItem?.name ?? itemDraftName : itemDraftName
  const dexterityAbility = abilityOptions.find((ability) => ability.key === 'dexterity')
  const initiativeChipModifier =
    dexterityAbility?.modifier && dexterityAbility.modifier !== '—' ? `DEX ${dexterityAbility.modifier}` : 'DEX'
  const rollModifierValue = parseRollModifier(rollModifier)
  const rollModifierPreview = formatModifier(rollModifierValue)
  const rollModeLabel = ROLL_MODE_OPTIONS.find((option) => option.value === rollMode)?.label ?? 'Normal'
  const rollReasonPreview = rollReason.trim() || (selectedAbility ? `${selectedAbility.label} check` : '')
  const rollPreviewParts = [
    `Rolling ${selectedDie.toUpperCase()}${rollModifierPreview ? ` ${rollModifierPreview}` : ''}${
      selectedAbility ? ` ${selectedAbility.label}` : ''
    }`,
    ...(rollMode === 'normal' ? [] : [rollModeLabel]),
    ...(rollReasonPreview ? [rollReasonPreview] : []),
  ]
  const rollPreview = rollPreviewParts.join(' · ')
  const activeTurnPlayerId = turnControl.activePlayerId ?? selectedPlayerId ?? activePlayers[0]?.id ?? null
  const conductorControlled = turnControl.source === 'auto' || turnControl.source === 'ai'
  const manualOverrideActive = turnControl.source === 'manual' || turnControl.source === 'admin'
  const updateRollModifierDraft = (nextModifier: number) => {
    const clamped = clampRollModifier(nextModifier)
    setRollModifier(String(clamped))
    setActionText((current) => rollActionText(selectedDie, selectedAbility, current, clamped))
  }
  const adjustRollModifier = (delta: number) => {
    updateRollModifierDraft(rollModifierValue + delta)
  }
  const clearRollModifier = () => {
    updateRollModifierDraft(0)
    setRollProficiencyApplied(false)
  }
  const toggleRollProficiency = () => {
    if (!rollProficiencyBonus) return
    const nextApplied = !rollProficiencyApplied
    const delta = nextApplied ? rollProficiencyBonus : -rollProficiencyBonus
    updateRollModifierDraft(rollModifierValue + delta)
    setRollProficiencyApplied(nextApplied)
  }
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
      <div className={`turn-control-strip ${selectedPlayerHasTurn ? 'open' : 'locked'}`} aria-live="polite">
        <div className="turn-control-summary">
          <span>Flow</span>
          <strong>{turnControlStatusLabel}</strong>
        </div>
        {adminToolsUnlocked ? (
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
        <div className="queued-action-strip">
          <span>Queued draft</span>
          <strong>{queuedActionText}</strong>
          <button type="button" onClick={clearQueuedAction}>
            Clear
          </button>
        </div>
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

            <div className="roll-modifier-control">
              <span className="roll-tray-label">Mod</span>
              <button
                type="button"
                className="icon-adjust"
                aria-label="Decrease roll modifier"
                title="Decrease modifier"
                onClick={() => adjustRollModifier(-1)}
                disabled={sendPending}
              >
                <Minus size={14} />
              </button>
              <input
                type="number"
                value={rollModifier}
                aria-label="Roll modifier"
                min={-99}
                max={99}
                onChange={(event) => {
                  const nextValue = event.target.value
                  setRollModifier(nextValue)
                  setActionText((current) =>
                    rollActionText(selectedDie, selectedAbility, current, parseRollModifier(nextValue)),
                  )
                  setRollProficiencyApplied(false)
                }}
              />
              <button
                type="button"
                className="icon-adjust"
                aria-label="Increase roll modifier"
                title="Increase modifier"
                onClick={() => adjustRollModifier(1)}
                disabled={sendPending}
              >
                <Plus size={14} />
              </button>
              <div className="roll-modifier-quick" role="group" aria-label="Modifier shortcuts">
                <button type="button" onClick={() => adjustRollModifier(1)} disabled={sendPending}>
                  +1
                </button>
                <button type="button" onClick={() => adjustRollModifier(2)} disabled={sendPending}>
                  +2
                </button>
                <button type="button" onClick={() => adjustRollModifier(-1)} disabled={sendPending}>
                  -1
                </button>
                <button
                  type="button"
                  className={rollProficiencyApplied ? 'selected' : ''}
                  aria-pressed={rollProficiencyApplied}
                  onClick={toggleRollProficiency}
                  disabled={sendPending || !rollProficiencyBonus}
                >
                  +PB <small>{formatModifier(rollProficiencyBonus) || '+0'}</small>
                </button>
                <button
                  type="button"
                  className="icon-adjust"
                  aria-label="Clear roll modifier"
                  title="Clear modifier"
                  onClick={clearRollModifier}
                  disabled={sendPending}
                >
                  <RotateCcw size={14} />
                </button>
              </div>
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
      {composerMode === 'spell' ? (
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
