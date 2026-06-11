import { useEffect, useMemo, useRef, useState, type Dispatch, type RefObject, type SetStateAction } from 'react'
import type { Socket } from 'socket.io-client'
import {
  INITIATIVE_ROLL_ABILITY_KEY,
  INITIATIVE_ROLL_REASON,
  PLAIN_ROLL_ABILITY_KEY,
  abilityModifierValue,
  buildActionIntent,
  composerTextForMode,
  createClientMessageId,
  diceRollMessage,
  hasReservedAdminPrefix,
  interactionTargetId,
  normalizeDie,
  parseRollModifier,
  resolveRoll,
  stripComposerCommand,
  type AbilityOption,
  type ActionIntent,
  type ComposerMode,
  type InteractionTarget,
  type InteractionType,
  type InventoryAction,
  type ItemOption,
  type RollMode,
  type RollResult,
} from './gameActions'
import type { PendingRollOption } from './gameSelectors'
import {
  canSubmitWithTurnControl,
  turnControlBlockMessage,
  turnControlStatusLabel,
} from './turnControl'
import type { ActivePlayer, Campaign, Player, SessionState, TimelineEntry, TurnControl } from './types'

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function cleanString(value: unknown, fallback = '') {
  if (typeof value === 'string' && value.trim()) return value.trim()
  if (typeof value === 'number' && Number.isFinite(value)) return String(value)
  return fallback
}

function preferredSpellAbilityKey(player: Player | null) {
  const classText = `${player?.char_class ?? ''} ${player?.class_ ?? ''}`.toLowerCase()
  if (/\b(?:sorcerer|bard|warlock|paladin)\b/.test(classText)) return 'charisma'
  if (/\b(?:cleric|druid|ranger|monk)\b/.test(classText)) return 'wisdom'
  if (/\b(?:wizard|artificer|eldritch|arcane)\b/.test(classText)) return 'intelligence'
  return 'charisma'
}

type SceneNpcTargetEntry = {
  target: InteractionTarget
  index: number
  activeIndex: number
  lastSeenTurn: number
}

function sceneNpcTargets(sessionState: SessionState | null): InteractionTarget[] {
  const snapshot = isRecord(sessionState?.state_snapshot) ? sessionState.state_snapshot : {}
  const scene = isRecord(snapshot.currentScene) ? snapshot.currentScene : {}
  const sceneLocationId = cleanString(scene.locationId)
  const activeNpcIds = Array.isArray(scene.activeNpcIds)
    ? scene.activeNpcIds.map((value) => cleanString(value)).filter(Boolean)
    : []
  const records = [
    ...(Array.isArray(snapshot.knownNpcs) ? snapshot.knownNpcs : []),
    ...(Array.isArray(snapshot.partyNpcs) ? snapshot.partyNpcs : []),
  ].filter(isRecord)

  return records
    .map<SceneNpcTargetEntry | null>((npc, index) => {
      const npcId = cleanString(npc.id ?? npc.npcId)
      const npcName = cleanString(npc.name)
      const locationId = cleanString(npc.locationId)
      const activeIndex = activeNpcIds.indexOf(npcId)
      const isActive = activeIndex >= 0
      if (!npcId || !npcName) return null
      if (activeNpcIds.length && !isActive) return null
      if (!activeNpcIds.length && locationId && sceneLocationId && locationId !== sceneLocationId) return null
      return {
        target: {
          kind: 'npc' as const,
          npc_id: npcId,
          character_name: npcName,
          player_name: cleanString(npc.role) || cleanString(npc.disposition, 'Scene NPC'),
          active: true,
        },
        index,
        activeIndex: isActive ? activeIndex : Number.MAX_SAFE_INTEGER,
        lastSeenTurn: Number(cleanString(npc.lastSeenTurn, '0')) || 0,
      }
    })
    .filter((entry): entry is SceneNpcTargetEntry => Boolean(entry))
    .sort((left, right) => left.activeIndex - right.activeIndex || right.lastSeenTurn - left.lastSeenTurn || left.index - right.index)
    .map((entry) => entry.target)
}

type DiceRollState = {
  die: string
  result: number
  message: string
  actionIntent: ActionIntent
  roll: RollResult
  targetLabel: string | null
  rollKey: number
  status: 'rolling' | 'sending'
}

type UseComposerActionsOptions = {
  activePlayers: ActivePlayer[]
  abilityOptions: AbilityOption[]
  campaign: Campaign | null
  itemOptions: ItemOption[]
  pendingRollOptions: PendingRollOption[]
  sessionState: SessionState | null
  selectedCampaignId: number | null
  selectedPlayer: Player | null
  selectedPlayerId: number | null
  selectedSessionId: number | null
  sendPending: boolean
  dmResponseBlocking: boolean
  setOptimisticEntries: Dispatch<SetStateAction<TimelineEntry[]>>
  setSendPending: Dispatch<SetStateAction<boolean>>
  socketRef: RefObject<Socket | null>
  stopTtsAudio: () => void
  turnControl: TurnControl
  pushError: (category: 'validation', message: string) => void
}

export function useComposerActions({
  activePlayers,
  abilityOptions,
  campaign,
  itemOptions,
  pendingRollOptions,
  sessionState,
  selectedCampaignId,
  selectedPlayer,
  selectedPlayerId,
  selectedSessionId,
  sendPending,
  dmResponseBlocking,
  setOptimisticEntries,
  setSendPending,
  socketRef,
  stopTtsAudio,
  turnControl,
  pushError,
}: UseComposerActionsOptions) {
  const [actionText, setActionText] = useState('')
  const [composerMode, setComposerMode] = useState<ComposerMode>('action')
  const [selectedDie, setSelectedDie] = useState('d20')
  const [rollModifier, setRollModifier] = useState('0')
  const [rollMode, setRollMode] = useState<RollMode>('normal')
  const [rollReason, setRollReason] = useState('')
  const [rawRollTargetPendingTurnId, setRollTargetPendingTurnId] = useState('')
  const [selectedAbilityKey, setSelectedAbilityKey] = useState(PLAIN_ROLL_ABILITY_KEY)
  const [selectedInventoryAction, setSelectedInventoryAction] = useState<InventoryAction>('use')
  const [selectedItemName, setSelectedItemName] = useState('')
  const [itemDraftName, setItemDraftName] = useState('')
  const [itemQuantity, setItemQuantity] = useState('1')
  const [itemCostGold, setItemCostGold] = useState('0')
  const [spellName, setSpellName] = useState('')
  const [selectedInteractionType, setSelectedInteractionType] = useState<InteractionType>('speak_to')
  const [rawSelectedInteractionTargetId, setSelectedInteractionTargetId] = useState('')
  const [adminPasscode, setAdminPasscode] = useState(() => sessionStorage.getItem('aidm:adminPasscode') ?? '')
  const [adminToolsUnlocked, setAdminToolsUnlocked] = useState(false)
  const [queuedActionText, setQueuedActionText] = useState('')
  const [diceRoll, setDiceRoll] = useState<DiceRollState | null>(null)
  const diceRollKeyRef = useRef(0)
  const typingStatusRef = useRef(false)
  const typingBindingRef = useRef<{ socket: Socket; sessionId: number; playerId: number } | null>(null)
  const typingIdleTimerRef = useRef<number | null>(null)

  useEffect(() => {
    const trimmed = adminPasscode.trim()
    if (trimmed) {
      sessionStorage.setItem('aidm:adminPasscode', trimmed)
    } else {
      sessionStorage.removeItem('aidm:adminPasscode')
    }
  }, [adminPasscode])

  useEffect(() => {
    return () => {
      if (typingIdleTimerRef.current !== null) {
        window.clearTimeout(typingIdleTimerRef.current)
        typingIdleTimerRef.current = null
      }
      const binding = typingBindingRef.current
      if (!typingStatusRef.current || !binding) return
      if (binding.socket.connected !== false) {
        binding.socket.emit('typing_status', {
          session_id: binding.sessionId,
          player_id: binding.playerId,
          is_typing: false,
        })
      }
      typingStatusRef.current = false
      typingBindingRef.current = null
    }
  }, [selectedPlayerId, selectedSessionId])

  const clearTypingIdleTimer = () => {
    if (typingIdleTimerRef.current !== null) {
      window.clearTimeout(typingIdleTimerRef.current)
      typingIdleTimerRef.current = null
    }
  }

  const emitTypingStatus = (isTyping: boolean) => {
    if (!isTyping) clearTypingIdleTimer()
    if (typingStatusRef.current === isTyping) return
    const socket = socketRef.current
    const binding = isTyping
      ? socket && selectedSessionId && selectedPlayerId
        ? { socket, sessionId: selectedSessionId, playerId: selectedPlayerId }
        : null
      : typingBindingRef.current
    if (!binding) return
    if (binding.socket.connected === false) {
      if (!isTyping) {
        typingStatusRef.current = false
        typingBindingRef.current = null
      }
      return
    }
    typingStatusRef.current = isTyping
    typingBindingRef.current = isTyping ? binding : null
    binding.socket.emit('typing_status', {
      session_id: binding.sessionId,
      player_id: binding.playerId,
      is_typing: isTyping,
    })
  }

  const scheduleTypingIdle = () => {
    clearTypingIdleTimer()
    typingIdleTimerRef.current = window.setTimeout(() => emitTypingStatus(false), 1800)
  }

  const updateActionText = (nextText: string) => {
    setActionText(nextText)
    setQueuedActionText((current) => (current && current !== nextText ? '' : current))
    if (nextText.trim()) {
      emitTypingStatus(true)
      scheduleTypingIdle()
    } else {
      emitTypingStatus(false)
    }
  }

  const dexterityAbility =
    abilityOptions.find((ability) => ability.key === 'dexterity') ?? {
      key: 'dexterity',
      label: 'DEX',
      score: '—',
      modifier: '—',
    }
  const initiativeAbility: AbilityOption = { ...dexterityAbility, label: 'Initiative' }
  const selectedAbility =
    selectedAbilityKey === PLAIN_ROLL_ABILITY_KEY
      ? null
      : selectedAbilityKey === INITIATIVE_ROLL_ABILITY_KEY
        ? initiativeAbility
        : abilityOptions.find((ability) => ability.key === selectedAbilityKey) ?? null
  const defaultSpellAbility =
    abilityOptions.find((ability) => ability.key === preferredSpellAbilityKey(selectedPlayer)) ??
    abilityOptions.find((ability) => ability.key === 'charisma') ??
    abilityOptions.find((ability) => ability.key === 'intelligence') ??
    abilityOptions[0] ??
    null
  const selectedSpellAbility =
    selectedAbilityKey === INITIATIVE_ROLL_ABILITY_KEY ? defaultSpellAbility : selectedAbility ?? defaultSpellAbility
  const selectedItem =
    itemOptions.find((item) => item.name === selectedItemName) ?? itemOptions[0] ?? null
  const selectedInventoryActionRequiresItem = ['use', 'equip', 'unequip', 'drop', 'give', 'sell'].includes(selectedInventoryAction)
  const itemIntentName = selectedInventoryActionRequiresItem
    ? selectedItem?.name ?? itemDraftName
    : itemDraftName
  const interactionTargets = useMemo<InteractionTarget[]>(() => {
    const playerTargets = activePlayers
      .filter((player) => player.id !== selectedPlayerId)
      .map((player) => ({
        kind: 'player' as const,
        player_id: player.id,
        character_name: player.character_name || player.name || `Player ${player.id}`,
        player_name: player.name || 'Active player',
        active: true,
      }))
    return [...playerTargets, ...sceneNpcTargets(sessionState)]
  }, [activePlayers, selectedPlayerId, sessionState])
  const selectedInteractionTargetId =
    rawSelectedInteractionTargetId &&
    interactionTargets.some((target) => interactionTargetId(target) === rawSelectedInteractionTargetId)
      ? rawSelectedInteractionTargetId
      : interactionTargets[0]
        ? interactionTargetId(interactionTargets[0])
        : ''
  const selectedInteractionTarget =
    interactionTargets.find((target) => interactionTargetId(target) === selectedInteractionTargetId) ?? null
  const rollTargetPendingTurnId =
    rawRollTargetPendingTurnId &&
    pendingRollOptions.some((option) => String(option.turnId) === rawRollTargetPendingTurnId)
      ? rawRollTargetPendingTurnId
      : ''

  const toggleAdminTools = () => {
    if (adminToolsUnlocked) {
      setAdminToolsUnlocked(false)
      setComposerMode((current) => (current === 'admin' ? 'action' : current))
      setActionText((current) => stripComposerCommand(current))
      return
    }
    setAdminToolsUnlocked(true)
  }

  const submitAction = (overrideMessage?: string, overrideIntent?: ActionIntent) => {
    if (sendPending || dmResponseBlocking) {
      pushError('validation', 'Wait for the current DM response to save before sending again.')
      return
    }
    if (
      !socketRef.current ||
      socketRef.current.connected === false ||
      !selectedSessionId ||
      !selectedCampaignId ||
      !campaign ||
      !selectedPlayerId
    ) {
      if (socketRef.current?.connected === false) {
        pushError('validation', 'Realtime is reconnecting. Try again in a moment.')
      } else {
        pushError('validation', 'Choose a campaign, session, and player before sending.')
      }
      return
    }
    const message = (overrideMessage ?? actionText).trim()
    if (!message) return
    const trimmedAdminPasscode = adminPasscode.trim()
    if (!overrideIntent && composerMode === 'admin' && !trimmedAdminPasscode) {
      pushError('validation', 'Admin passcode is required for Admin mode.')
      return
    }
    if (!overrideIntent && composerMode === 'interact' && !selectedInteractionTarget) {
      pushError('validation', 'Choose another player before sending an interaction.')
      return
    }
    if (!overrideIntent && composerMode === 'item') {
      if (selectedInventoryActionRequiresItem && !selectedItem) {
        pushError('validation', 'Choose an item already in your inventory for that action.')
        return
      }
      if (!itemIntentName.trim()) {
        pushError('validation', 'Name an item before sending an inventory action.')
        return
      }
    }
    const clientMessageId = overrideIntent?.client_message_id ?? createClientMessageId()
    const actionIntent =
      overrideIntent ??
      buildActionIntent({
        mode: composerMode,
        message,
        clientMessageId,
        ability: composerMode === 'spell' ? selectedSpellAbility : selectedAbility,
        item: selectedItem,
        inventoryAction: selectedInventoryAction,
        itemName: itemIntentName,
        itemQuantity,
        costGold: itemCostGold,
        spellName,
        interactionType: selectedInteractionType,
        interactionTarget: selectedInteractionTarget,
      })
    if (actionIntent.kind !== 'admin' && hasReservedAdminPrefix(message)) {
      pushError('validation', 'Admin-prefixed messages require authenticated Admin mode.')
      return
    }
    const hasPendingRoll =
      actionIntent.kind === 'roll' &&
      (pendingRollOptions.length > 0 || Boolean(actionIntent.roll?.target_pending_turn_id))
    if (!canSubmitWithTurnControl(turnControl, selectedPlayerId, actionIntent.kind, hasPendingRoll)) {
      setQueuedActionText(message)
      pushError('validation', turnControlBlockMessage(turnControl))
      return
    }

    stopTtsAudio()
    setSendPending(true)
    setOptimisticEntries((current) => [
      ...current,
      {
        id: `local-${Date.now()}`,
        role: 'player',
        speaker: selectedPlayer?.character_name ?? 'Player',
        text: message,
        timestamp: new Date().toISOString(),
        metadata: {
          client_message_id: clientMessageId,
          action_intent: actionIntent,
          persistence_status: 'pending',
        },
      },
    ])
    socketRef.current.emit('send_message', {
      session_id: selectedSessionId,
      campaign_id: selectedCampaignId,
      world_id: campaign.world_id,
      player_id: selectedPlayerId,
      message,
      client_message_id: clientMessageId,
      action_intent: actionIntent,
      ...(actionIntent.kind === 'admin' ? { admin_passcode: trimmedAdminPasscode } : {}),
    })
    setActionText('')
    setQueuedActionText((current) => (current === message ? '' : current))
    emitTypingStatus(false)
  }

  const applyComposerMode = (mode: ComposerMode, die = selectedDie) => {
    if (mode === 'admin' && !adminToolsUnlocked) return
    const abilityForMode = mode === 'spell' ? selectedSpellAbility : selectedAbility
    if (mode === 'spell' && abilityForMode) {
      setSelectedAbilityKey(abilityForMode.key)
      setRollModifier(String(abilityModifierValue(abilityForMode)))
      setRollReason(`${abilityForMode.label} spell`)
    }
    setComposerMode(mode)
    setActionText((current) =>
      composerTextForMode(
        mode,
        current,
        selectedPlayer?.character_name ?? 'I',
        die,
        abilityForMode,
        selectedItem,
        selectedInteractionTarget,
        selectedInteractionType,
        selectedInventoryAction,
        itemIntentName,
        itemCostGold,
        spellName,
      ),
    )
  }

  const updateRollAbilityKey = (nextKey: string) => {
    const nextAbility =
      nextKey === PLAIN_ROLL_ABILITY_KEY
        ? null
        : nextKey === INITIATIVE_ROLL_ABILITY_KEY
          ? initiativeAbility
          : abilityOptions.find((ability) => ability.key === nextKey) ?? null
    setSelectedAbilityKey(
      nextKey === INITIATIVE_ROLL_ABILITY_KEY
        ? INITIATIVE_ROLL_ABILITY_KEY
        : nextAbility?.key ?? PLAIN_ROLL_ABILITY_KEY,
    )
    setRollModifier(String(abilityModifierValue(nextAbility)))
    setRollReason(
      nextKey === INITIATIVE_ROLL_ABILITY_KEY
        ? INITIATIVE_ROLL_REASON
        : nextAbility
          ? `${nextAbility.label} ${composerMode === 'spell' ? 'spell' : 'check'}`
          : '',
    )
    if (composerMode === 'roll') {
      setActionText((current) =>
        composerTextForMode(
          'roll',
          current,
          selectedPlayer?.character_name ?? 'I',
          selectedDie,
          nextAbility,
          selectedItem,
          selectedInteractionTarget,
          selectedInteractionType,
          selectedInventoryAction,
          itemIntentName,
          itemCostGold,
        ),
      )
    }
  }

  const updateSpellName = (nextName: string) => {
    setSpellName(nextName)
    if (composerMode === 'spell') {
      setActionText((current) =>
        composerTextForMode(
          'spell',
          current,
          selectedPlayer?.character_name ?? 'I',
          selectedDie,
          selectedSpellAbility,
          selectedItem,
          selectedInteractionTarget,
          selectedInteractionType,
          selectedInventoryAction,
          itemIntentName,
          itemCostGold,
          nextName,
        ),
      )
    }
  }

  const updateSelectedDie = (die: string) => {
    const normalizedDie = normalizeDie(die)
    setSelectedDie(normalizedDie)
    if (composerMode === 'roll') {
      setActionText((current) =>
        composerTextForMode('roll', current, selectedPlayer?.character_name ?? 'I', normalizedDie, selectedAbility, selectedItem),
      )
    }
  }

  const updateSelectedInventoryAction = (nextAction: InventoryAction) => {
    setSelectedInventoryAction(nextAction)
    setActionText((current) =>
      composerTextForMode(
        'item',
        current,
        selectedPlayer?.character_name ?? 'I',
        selectedDie,
        selectedAbility,
        selectedItem,
        selectedInteractionTarget,
        selectedInteractionType,
        nextAction,
        nextAction === 'pick_up' || nextAction === 'buy' ? itemDraftName : selectedItem?.name ?? itemDraftName,
        itemCostGold,
      ),
    )
  }

  const updateItemDraftName = (nextName: string) => {
    setItemDraftName(nextName)
    if (composerMode === 'item' && (selectedInventoryAction === 'pick_up' || selectedInventoryAction === 'buy')) {
      setActionText((current) =>
        composerTextForMode(
          'item',
          current,
          selectedPlayer?.character_name ?? 'I',
          selectedDie,
          selectedAbility,
          selectedItem,
          selectedInteractionTarget,
          selectedInteractionType,
          selectedInventoryAction,
          nextName,
          itemCostGold,
        ),
      )
    }
  }

  const updateItemCostGold = (nextCost: string) => {
    setItemCostGold(nextCost)
    if (composerMode === 'item' && (selectedInventoryAction === 'buy' || selectedInventoryAction === 'sell')) {
      setActionText((current) =>
        composerTextForMode(
          'item',
          current,
          selectedPlayer?.character_name ?? 'I',
          selectedDie,
          selectedAbility,
          selectedItem,
          selectedInteractionTarget,
          selectedInteractionType,
          selectedInventoryAction,
          itemIntentName,
          nextCost,
        ),
      )
    }
  }

  const startDiceRoll = (die = selectedDie) => {
    if (sendPending) {
      pushError('validation', 'Wait for the current DM response before rolling again.')
      return
    }
    if (
      !socketRef.current ||
      !selectedSessionId ||
      !selectedCampaignId ||
      !campaign ||
      !selectedPlayerId
    ) {
      pushError('validation', 'Choose a campaign, session, and player before rolling.')
      return
    }

    const normalizedDie = normalizeDie(die)
    const targetPendingTurnId = rollTargetPendingTurnId ? Number(rollTargetPendingTurnId) : null
    const targetOption = pendingRollOptions.find((option) => option.turnId === targetPendingTurnId) ?? null
    const roll = resolveRoll({
      die: normalizedDie,
      modifier: parseRollModifier(rollModifier),
      mode: rollMode,
      reason:
        selectedAbilityKey === INITIATIVE_ROLL_ABILITY_KEY
          ? INITIATIVE_ROLL_REASON
          : rollReason || (selectedAbility ? `${selectedAbility.label} check` : ''),
      resultVisibility: 'hidden_until_landed',
      targetPendingTurnId,
    })
    const actionDescription = stripComposerCommand(actionText)
    const rollMessage = diceRollMessage(roll)
    const message = actionDescription ? `${actionDescription}\n${rollMessage}` : rollMessage
    const clientMessageId = createClientMessageId()
    const actionIntent = buildActionIntent({
      mode: 'roll',
      message,
      clientMessageId,
      source: 'dice_roller',
      roll,
      ability: selectedAbility,
    })
    setSelectedDie(normalizedDie)
    setComposerMode('roll')
    setActionText(message)
    setDiceRoll({
      die: normalizedDie,
      result: roll.kept,
      message,
      actionIntent,
      roll,
      targetLabel: targetOption ? `${targetOption.label} - ${targetOption.detail}` : null,
      rollKey: (diceRollKeyRef.current += 1),
      status: 'rolling',
    })
  }

  const completeDiceRoll = () => {
    if (!diceRoll || diceRoll.status !== 'rolling') return
    const { rollKey, message, actionIntent } = diceRoll
    setDiceRoll((current) =>
      current?.rollKey === rollKey ? { ...current, status: 'sending' } : current,
    )
    submitAction(message, actionIntent)
    window.setTimeout(() => {
      setDiceRoll((current) => (current?.rollKey === rollKey ? null : current))
    }, 450)
  }

  const closeDiceRoll = () => {
    setDiceRoll(null)
  }

  return {
    actionText,
    adminPasscode,
    adminToolsUnlocked,
    applyComposerMode,
    closeDiceRoll,
    completeDiceRoll,
    composerMode,
    diceRoll,
    interactionTargets,
    rollMode,
    rollModifier,
    rollReason,
    rollTargetPendingTurnId,
    spellName,
    selectedAbility,
    selectedAbilityKey,
    selectedDie,
    selectedInteractionTarget,
    selectedInteractionTargetId,
    selectedInteractionType,
    selectedInventoryAction,
    selectedItem,
    itemDraftName,
    itemQuantity,
    itemCostGold,
    setActionText,
    updateActionText,
    setAdminPasscode,
    setSelectedInteractionTargetId,
    setSelectedInteractionType,
    setItemQuantity,
    setRollMode,
    setRollModifier,
    setRollReason,
    setRollTargetPendingTurnId,
    updateRollAbilityKey,
    updateSpellName,
    setSelectedItemName,
    updateSelectedInventoryAction,
    updateItemDraftName,
    updateItemCostGold,
    startDiceRoll,
    submitAction,
    toggleAdminTools,
    queuedActionText,
    clearQueuedAction: () => setQueuedActionText(''),
    selectedPlayerHasTurn: canSubmitWithTurnControl(turnControl, selectedPlayerId, 'message', false),
    turnControlStatusLabel: turnControlStatusLabel(turnControl),
    updateSelectedDie,
  }
}
