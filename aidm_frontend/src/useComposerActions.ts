import { useCallback, useEffect, useMemo, useRef, useState, type Dispatch, type RefObject, type SetStateAction } from 'react'
import type { Socket } from 'socket.io-client'
import {
  INITIATIVE_ROLL_ABILITY_KEY,
  INITIATIVE_ROLL_REASON,
  PLAIN_ROLL_ABILITY_KEY,
  abilityModifierValue,
  buildActionIntent,
  composerTextForMode,
  createClientMessageId,
  diceRollRequestMessage,
  hasReservedAdminPrefix,
  interactionTargetId,
  itemOptionSelectionKey,
  normalizeDie,
  stripComposerCommand,
  type AbilityOption,
  type ActionIntent,
  type ComposerMode,
  type InteractionTarget,
  type InteractionType,
  type InventoryAction,
  type ItemOption,
  type PendingRollGuidance,
  type RollMode,
  type RollRequiredPayload,
  type RollResolvedPayload,
  type RollResult,
} from './gameActions'
import type { PendingRollOption } from './gameSelectors'
import { PRESSED_HOLD_MS, PRESS_MIN_MS, SETTLE_MS, notePressedRoll } from './story/rollPlayback'
import { getStoryPreferences } from './story/sheets/storyPreferences'
import {
  canSubmitWithTurnControl,
  turnControlBlockMessage,
  turnControlStatusLabel,
} from './turnControl'
import type { ActivePlayer, Campaign, Player, SessionState, StreamingTurn, TimelineEntry, TurnControl } from './types'
import { useLatest } from './useLatest'
import type { SessionSocketBinding, TurnRejection } from './useSessionSocket'

export const SEND_PENDING_RECOVERY_MS = 120_000
// A send interrupted by a reconnect is re-emitted automatically when the
// same session is joined again within this window.
export const RECONNECT_RESEND_WINDOW_MS = 20_000
const COMPOSER_DRAFT_PREFIX = 'aidm:composerDraft'
const CONNECTION_INTERRUPTED_REASON = 'Realtime disconnected before confirmation.'

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

/**
 * A roll the player pressed, shown as its roll card where it was pressed: the
 * d20 cycles while the request is out (`requesting`), lands on the server's
 * result (`rolling`, from `landAt`), and has `played` once the card has landed.
 */
export type DiceRollState = {
  die: string
  message: string
  actionIntent: ActionIntent
  clientMessageId: string
  rollKey: number
  /** `Date.now()` of the press: the card lands no sooner than `PRESS_MIN_MS` after it. */
  startedAt: number
  result: RollResolvedPayload | null
  landAt: number | null
  status: 'requesting' | 'rolling' | 'played' | 'failed'
  error?: string
}

export type SharedRollNotice = {
  playerId: number
  turnId: number
  roll: RollResult
}

type OutgoingTurnPayload = {
  session_id: number
  campaign_id: number
  world_id: number
  player_id: number
  message: string
  client_message_id: string
  action_intent: ActionIntent
  admin_passcode?: string
  message_kind?: 'opening'
  /** The player's End my turn for me choice: a fight action that leaves only moving ends their turn. */
  auto_end_turn: boolean
}

type SubmissionKind = 'action' | 'opening'

/** An action sent while the previous turn was still finishing: it is sent by itself when the turn unlocks. */
type HeldAction = {
  message: string
  intent?: ActionIntent
  /** A fight was already on when it was sent. */
  inFight: boolean
  /** Rolls already waiting when it was sent. */
  rollsWaiting: number
}

type StoredSubmission = {
  clientMessageId: string
  message: string
  // Composer text as typed, restored when the server rejects the turn.
  draftText: string
  kind: SubmissionKind
  sessionId: number
  playerId: number
  payload: OutgoingTurnPayload
  autoRetried: boolean
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
  sessionHydrated: boolean
  sendPending: boolean
  dmResponseBlocking: boolean
  streamingTurn: StreamingTurn | null
  setOptimisticEntries: Dispatch<SetStateAction<TimelineEntry[]>>
  setSendPending: Dispatch<SetStateAction<boolean>>
  setStreamingTurn: Dispatch<SetStateAction<StreamingTurn | null>>
  socketRef: RefObject<Socket | null>
  stopTtsAudio: () => void
  turnControl: TurnControl
  pushError: (category: 'validation', message: string) => void
  dismissError: (category: 'validation', message: string) => void
  /** Something was sent or rolled without a hero or a chapter to act in: the app offers the way forward. */
  onActorMissing: () => void
}

const FIGHT_STATUSES = new Set(['starting', 'active'])

function fightOn(sessionState: SessionState | null) {
  const snapshot = isRecord(sessionState?.state_snapshot) ? sessionState.state_snapshot : {}
  const combat = isRecord(snapshot.combat) ? snapshot.combat : {}
  return FIGHT_STATUSES.has(cleanString(combat.status).toLowerCase())
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
  sessionHydrated,
  sendPending,
  dmResponseBlocking,
  streamingTurn,
  setOptimisticEntries,
  setSendPending,
  setStreamingTurn,
  socketRef,
  stopTtsAudio,
  turnControl,
  pushError,
  dismissError,
  onActorMissing,
}: UseComposerActionsOptions) {
  const [actionText, setActionText] = useState('')
  const [composerMode, setComposerMode] = useState<ComposerMode>('action')
  const [selectedDie, setSelectedDie] = useState('d20')
  const [rollMode, setRollMode] = useState<RollMode>('normal')
  const [rollReason, setRollReason] = useState('')
  const [rawRollTargetPendingTurnId, setRollTargetPendingTurnId] = useState('')
  const [selectedAbilityKey, setSelectedAbilityKey] = useState(PLAIN_ROLL_ABILITY_KEY)
  const [selectedInventoryAction, setSelectedInventoryAction] = useState<InventoryAction>('use')
  const [selectedItemId, setSelectedItemId] = useState('')
  const [itemDraftName, setItemDraftName] = useState('')
  const [itemQuantity, setItemQuantity] = useState('1')
  const [itemCostGold, setItemCostGold] = useState('0')
  const [spellName, setSpellName] = useState('')
  const [selectedInteractionType, setSelectedInteractionType] = useState<InteractionType>('speak_to')
  const [rawSelectedInteractionTargetId, setSelectedInteractionTargetId] = useState('')
  const [adminPasscode, setAdminPasscode] = useState('')
  const [adminToolsUnlocked, setAdminToolsUnlocked] = useState(false)
  const [queuedActionText, setQueuedActionText] = useState('')
  const [diceRoll, setDiceRoll] = useState<DiceRollState | null>(null)
  const diceRollRef = useLatest(diceRoll)
  const [recoverableSubmission, setRecoverableSubmission] = useState<StoredSubmission | null>(null)
  const [reconnectingSubmission, setReconnectingSubmission] = useState<StoredSubmission | null>(null)
  const [turnRejectionMessage, setTurnRejectionMessage] = useState('')
  const [heldAction, setHeldAction] = useState<HeldAction | null>(null)
  const heldActionRef = useLatest(heldAction)
  // A held action that will not be sent gives its words back to the box, after anything already there.
  const returnHeldWords = useCallback((held: HeldAction) => {
    setActionText((current) => (current.trim() ? `${current}\n${held.message}` : held.message))
  }, [])
  const [sharedRollNotice, setSharedRollNotice] = useState<SharedRollNotice | null>(null)
  const diceRollKeyRef = useRef(0)
  const typingStatusRef = useRef(false)
  const typingBindingRef = useRef<{ socket: Socket; sessionId: number; playerId: number } | null>(null)
  const typingIdleTimerRef = useRef<number | null>(null)
  const pendingSubmissionRef = useRef<StoredSubmission | null>(null)
  const reconnectingSubmissionRef = useRef<StoredSubmission | null>(null)
  const reconnectTimerRef = useRef<number | null>(null)
  // Client id of the latest re-emitted payload: the server's turn_duplicate
  // answer releases the lock even if the saved log confirmed it first.
  const resentClientMessageIdRef = useRef<string | null>(null)
  const recoverableErrorRef = useRef<{ clientMessageId: string; message: string } | null>(null)
  const selectionRef = useLatest({ sessionId: selectedSessionId, playerId: selectedPlayerId })
  const abilityOptionsRef = useRef(abilityOptions)
  const actionTextRef = useRef(actionText)

  useEffect(() => {
    abilityOptionsRef.current = abilityOptions
  }, [abilityOptions])

  useEffect(() => {
    actionTextRef.current = actionText
  }, [actionText])

  const composerDraftKey = selectedCampaignId && selectedSessionId && selectedPlayerId
    ? `${COMPOSER_DRAFT_PREFIX}:${selectedCampaignId}:${selectedSessionId}:${selectedPlayerId}`
    : ''

  useEffect(() => {
    if (!composerDraftKey) return undefined
    let storedDraft = ''
    try {
      storedDraft = sessionStorage.getItem(composerDraftKey) ?? ''
    } catch {
      // Session storage may be unavailable in hardened browser contexts. The
      // in-memory composer remains fully functional.
    }
    const hydrationDiceRollKey = diceRollKeyRef.current
    const hydrationTimer = window.setTimeout(() => {
      // Draft hydration is intentionally deferred, but it must never erase a
      // roll the player started before the timer ran. Session selection is
      // locked while a roll is open, so a changed key means this hydration is
      // stale for the current interaction.
      if (diceRollKeyRef.current !== hydrationDiceRollKey) return
      actionTextRef.current = storedDraft
      setActionText(storedDraft)
      setQueuedActionText('')
      setHeldAction(null)
      setRecoverableSubmission(null)
      setTurnRejectionMessage('')
      setDiceRoll(null)
      setSharedRollNotice(null)
      setRollTargetPendingTurnId('')
      setComposerMode('action')
    }, 0)

    const persistDraft = () => {
      try {
        const draft = actionTextRef.current
        if (draft.trim()) sessionStorage.setItem(composerDraftKey, draft)
        else sessionStorage.removeItem(composerDraftKey)
      } catch {
        // Draft persistence is best effort and must never block play.
      }
    }
    const persistWhenHidden = () => {
      if (document.visibilityState === 'hidden') persistDraft()
    }
    window.addEventListener('pagehide', persistDraft)
    document.addEventListener('visibilitychange', persistWhenHidden)
    return () => {
      window.clearTimeout(hydrationTimer)
      persistDraft()
      window.removeEventListener('pagehide', persistDraft)
      document.removeEventListener('visibilitychange', persistWhenHidden)
    }
  }, [composerDraftKey])

  const markSubmissionRecoverable = useCallback((submission: StoredSubmission, reason: string) => {
    setRecoverableSubmission(submission)
    setSendPending(false)
    setStreamingTurn(null)
    setOptimisticEntries((current) =>
      current.map((entry) =>
        entry.metadata.client_message_id === submission.clientMessageId &&
        entry.metadata.persistence_status === 'pending'
          ? { ...entry, metadata: { ...entry.metadata, persistence_status: 'failed' } }
          : entry,
      ),
    )
    setDiceRoll((current) =>
      current?.clientMessageId === submission.clientMessageId
        ? { ...current, status: 'failed', error: reason }
        : current,
    )
    // The opening request is internal: it never becomes a player draft or a
    // "Delivery uncertain" strip, only the delayed-opening notice.
    if (submission.kind === 'opening') return
    setActionText((current) => current || submission.message)
    setQueuedActionText((current) => current || submission.message)
    const errorMessage = `${reason} Retry will reuse the original request, so it cannot roll twice.`
    recoverableErrorRef.current = { clientMessageId: submission.clientMessageId, message: errorMessage }
    pushError('validation', errorMessage)
  }, [pushError, setOptimisticEntries, setSendPending, setStreamingTurn])

  const clearReconnectWait = useCallback(() => {
    if (reconnectTimerRef.current !== null) {
      window.clearTimeout(reconnectTimerRef.current)
      reconnectTimerRef.current = null
    }
    reconnectingSubmissionRef.current = null
    setReconnectingSubmission(null)
  }, [])

  useEffect(() => () => {
    if (reconnectTimerRef.current !== null) window.clearTimeout(reconnectTimerRef.current)
  }, [])

  useEffect(() => {
    const pendingSubmission = pendingSubmissionRef.current
    if (!pendingSubmission || pendingSubmission.sessionId !== selectedSessionId) {
      pendingSubmissionRef.current = null
      return
    }
    if (!sendPending && !dmResponseBlocking) {
      pendingSubmissionRef.current = null
      setRecoverableSubmission((current) =>
        current?.clientMessageId === pendingSubmission.clientMessageId ? null : current,
      )
      return
    }
    const timer = window.setTimeout(() => {
      if (pendingSubmissionRef.current?.clientMessageId === pendingSubmission.clientMessageId) {
        pendingSubmissionRef.current = null
        markSubmissionRecoverable(pendingSubmission, 'Realtime confirmation timed out.')
      }
    }, SEND_PENDING_RECOVERY_MS)
    return () => window.clearTimeout(timer)
  }, [
    dmResponseBlocking,
    markSubmissionRecoverable,
    selectedSessionId,
    sendPending,
    streamingTurn?.text,
  ])

  // Called with the client message ids of a freshly loaded log page or a
  // new_message echo: a submission found there was saved, whatever the socket
  // reported, so it is neither pending nor uncertain any more.
  const handleSavedMessages = useCallback((clientMessageIds: string[]) => {
    const saved = new Set(clientMessageIds.filter(Boolean))
    if (!saved.size) return
    const pendingSubmission = pendingSubmissionRef.current
    if (pendingSubmission && saved.has(pendingSubmission.clientMessageId)) {
      pendingSubmissionRef.current = null
    }
    const waitingSubmission = reconnectingSubmissionRef.current
    if (waitingSubmission && saved.has(waitingSubmission.clientMessageId)) {
      clearReconnectWait()
    }
    const savedSubmission = recoverableSubmission
    if (!savedSubmission || !saved.has(savedSubmission.clientMessageId)) return
    setRecoverableSubmission((current) =>
      current?.clientMessageId === savedSubmission.clientMessageId ? null : current,
    )
    setQueuedActionText((current) => (current === savedSubmission.message ? '' : current))
    setActionText((current) => (current === savedSubmission.message ? '' : current))
    const recordedError = recoverableErrorRef.current
    if (recordedError?.clientMessageId === savedSubmission.clientMessageId) {
      recoverableErrorRef.current = null
      dismissError('validation', recordedError.message)
    }
  }, [clearReconnectWait, dismissError, recoverableSubmission])

  useEffect(() => {
    if (!sharedRollNotice) return
    const timer = window.setTimeout(() => setSharedRollNotice(null), 6_000)
    return () => window.clearTimeout(timer)
  }, [sharedRollNotice])

  useEffect(() => {
    sessionStorage.removeItem('aidm:adminPasscode')
  }, [])

  useEffect(() => {
    return () => {
      if (typingIdleTimerRef.current !== null) {
        window.clearTimeout(typingIdleTimerRef.current)
        typingIdleTimerRef.current = null
      }
      const binding = typingBindingRef.current
      if (!typingStatusRef.current || !binding) return
      if (binding.socket.connected === true) {
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
    if (binding.socket.connected !== true) {
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
    if (!recoverableSubmission) {
      setQueuedActionText((current) => (current && current !== nextText ? '' : current))
    }
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
    itemOptions.find((item, index) => itemOptionSelectionKey(item, index) === selectedItemId) ?? itemOptions[0] ?? null
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
    Number.isInteger(Number(rawRollTargetPendingTurnId)) &&
    Number(rawRollTargetPendingTurnId) > 0
      ? rawRollTargetPendingTurnId
      : ''

  const preparePendingRoll = useCallback((guidance: PendingRollGuidance) => {
    const abilityKey = guidance.rollSpec.ability?.key
    const hasAbility = Boolean(
      abilityKey && abilityOptionsRef.current.some((ability) => ability.key === abilityKey),
    )
    setSelectedDie(normalizeDie(guidance.rollSpec.die))
    setRollMode(guidance.rollSpec.mode)
    setRollReason(guidance.rollSpec.reason || guidance.ruleType.replace(/_/g, ' '))
    setRollTargetPendingTurnId(String(guidance.pendingTurnId))
    setSelectedAbilityKey(
      guidance.ruleType === 'initiative'
        ? INITIATIVE_ROLL_ABILITY_KEY
        : hasAbility && abilityKey
          ? abilityKey
          : PLAIN_ROLL_ABILITY_KEY,
    )
    setComposerMode('roll')
  }, [])

  const handleRollRequired = useCallback((payload: RollRequiredPayload) => {
    if (payload.sessionId !== selectedSessionId) return
    const rejectedSubmission = pendingSubmissionRef.current
    if (rejectedSubmission) {
      pendingSubmissionRef.current = null
      setRecoverableSubmission((current) =>
        current?.clientMessageId === rejectedSubmission.clientMessageId ? null : current,
      )
      setOptimisticEntries((current) =>
        current.filter(
          (entry) => entry.metadata.client_message_id !== rejectedSubmission.clientMessageId,
        ),
      )
      if (rejectedSubmission.kind !== 'opening') {
        setActionText((current) => current || rejectedSubmission.message)
        setQueuedActionText(rejectedSubmission.message)
      }
    }
    // A roll comes first: an action held for the end of the turn goes back to the box.
    const held = heldActionRef.current
    if (held) {
      setHeldAction(null)
      returnHeldWords(held)
    }
    setSendPending(false)
    setStreamingTurn(null)
    preparePendingRoll(payload)
  }, [heldActionRef, preparePendingRoll, returnHeldWords, selectedSessionId, setOptimisticEntries, setSendPending, setStreamingTurn])

  const toggleAdminTools = () => {
    if (adminToolsUnlocked) {
      setAdminToolsUnlocked(false)
      setAdminPasscode('')
      setComposerMode((current) => (current === 'admin' ? 'action' : current))
      setActionText((current) => stripComposerCommand(current))
      return
    }
    setAdminToolsUnlocked(true)
  }

  const retryRecoverableSubmission = () => {
    const retry = recoverableSubmission
    if (!retry) return false
    if (retry.sessionId !== selectedSessionId) {
      pushError('validation', 'Return to the original session before retrying this request.')
      return false
    }
    const socket = socketRef.current
    if (!socket || socket.connected !== true) {
      pushError('validation', 'Realtime is still reconnecting. Try the same request again in a moment.')
      return false
    }
    socket.emit('send_message', retry.payload)
    pendingSubmissionRef.current = { ...retry, autoRetried: false }
    resentClientMessageIdRef.current = retry.clientMessageId
    setRecoverableSubmission(null)
    setSendPending(true)
    setOptimisticEntries((current) =>
      current.map((entry) =>
        entry.metadata.client_message_id === retry.clientMessageId
          ? { ...entry, metadata: { ...entry.metadata, persistence_status: 'pending' } }
          : entry,
      ),
    )
    setDiceRoll((current) =>
      current?.clientMessageId === retry.clientMessageId
        ? { ...current, status: 'requesting', error: undefined }
        : current,
    )
    setActionText((current) => (current === retry.message ? '' : current))
    setQueuedActionText((current) => (current === retry.message ? '' : current))
    stopTtsAudio()
    emitTypingStatus(false)
    return true
  }

  /** `released`: a held action going out; whatever was typed since stays in the box. */
  const sendTurn = (kind: SubmissionKind, overrideMessage?: string, overrideIntent?: ActionIntent, released = false) => {
    if (sendPending || dmResponseBlocking || reconnectingSubmission) {
      // Typed ahead while the turn finishes: the latest action waits and goes the moment it unlocks.
      const message = (overrideMessage ?? actionText).trim()
      if (kind === 'opening' || overrideIntent?.kind === 'roll' || !message) return false
      setHeldAction({ message, intent: overrideIntent, inFight: fightOn(sessionState), rollsWaiting: pendingRollOptions.length })
      if (overrideMessage === undefined) setActionText('')
      emitTypingStatus(false)
      return true
    }
    if (
      kind === 'action' &&
      !overrideMessage &&
      !overrideIntent &&
      recoverableSubmission?.kind === 'action' &&
      actionText.trim() === recoverableSubmission.message
    ) {
      return retryRecoverableSubmission()
    }
    if (recoverableSubmission) {
      pushError(
        'validation',
        recoverableSubmission.kind === 'opening'
          ? 'Try the opening again before sending an action.'
          : 'Delivery of your previous action is uncertain. Retry it safely or discard it after checking the timeline before sending something new.',
      )
      return false
    }
    if (!selectedSessionId || !selectedCampaignId || !campaign || !selectedPlayerId) {
      onActorMissing()
      return false
    }
    // The composer explains this state itself ("Loading this session…").
    if (!sessionHydrated) return false
    const socket = socketRef.current
    if (!socket || socket.connected !== true) {
      pushError('validation', 'Realtime is reconnecting. Try again in a moment.')
      return false
    }
    const message = (overrideMessage ?? actionText).trim()
    if (!message) return false
    const trimmedAdminPasscode = adminPasscode.trim()
    const intentMode: ComposerMode = kind === 'opening' ? 'action' : composerMode
    if (!overrideIntent && intentMode === 'admin' && !trimmedAdminPasscode) {
      pushError('validation', 'Admin passcode is required for Admin mode.')
      return false
    }
    if (!overrideIntent && intentMode === 'interact' && !selectedInteractionTarget) {
      pushError('validation', 'Choose another player before sending an interaction.')
      return false
    }
    if (!overrideIntent && intentMode === 'item') {
      if (selectedInventoryActionRequiresItem && !selectedItem) {
        pushError('validation', 'Choose an item already in your inventory for that action.')
        return false
      }
      if (!itemIntentName.trim()) {
        pushError('validation', 'Name an item before sending an inventory action.')
        return false
      }
    }
    const clientMessageId = overrideIntent?.client_message_id ?? createClientMessageId()
    const actionIntent =
      overrideIntent ??
      buildActionIntent({
        mode: intentMode,
        message,
        clientMessageId,
        ability: intentMode === 'spell' ? selectedSpellAbility : selectedAbility,
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
      return false
    }
    const hasPendingRoll =
      actionIntent.kind === 'roll' &&
      (pendingRollOptions.length > 0 || Boolean(actionIntent.roll?.target_pending_turn_id))
    if (!canSubmitWithTurnControl(turnControl, selectedPlayerId, actionIntent.kind, hasPendingRoll)) {
      if (kind === 'action') setQueuedActionText(message)
      pushError('validation', turnControlBlockMessage(turnControl))
      return false
    }

    const payload: OutgoingTurnPayload = {
      session_id: selectedSessionId,
      campaign_id: selectedCampaignId,
      world_id: campaign.world_id,
      player_id: selectedPlayerId,
      message,
      client_message_id: clientMessageId,
      action_intent: actionIntent,
      ...(actionIntent.kind === 'admin' ? { admin_passcode: trimmedAdminPasscode } : {}),
      ...(kind === 'opening' ? { message_kind: 'opening' as const } : {}),
      auto_end_turn: getStoryPreferences().autoEndTurn,
    }
    socket.emit('send_message', payload)
    resentClientMessageIdRef.current = null
    if (actionIntent.kind === 'admin') setAdminPasscode('')
    pendingSubmissionRef.current = {
      clientMessageId,
      message,
      draftText: overrideMessage === undefined ? actionText : message,
      kind,
      sessionId: selectedSessionId,
      playerId: selectedPlayerId,
      payload,
      autoRetried: false,
    }
    setRecoverableSubmission(null)
    setTurnRejectionMessage('')
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
          ...(kind === 'opening' ? { message_kind: 'opening' } : {}),
          persistence_status: 'pending',
        },
      },
    ])
    if (kind === 'opening') return true
    if (!released) setActionText('')
    setQueuedActionText((current) => (current === message ? '' : current))
    emitTypingStatus(false)
    return true
  }

  const submitAction = (overrideMessage?: string, overrideIntent?: ActionIntent) =>
    sendTurn('action', overrideMessage, overrideIntent)

  const sendTurnRef = useLatest(sendTurn)
  // The held action goes the moment the turn unlocks; a roll prompt or a fight that started meanwhile
  // (or a refusal) gives its words back to the box instead.
  useEffect(() => {
    if (!heldAction || sendPending || dmResponseBlocking || reconnectingSubmission) return undefined
    const release = window.setTimeout(() => {
      setHeldAction(null)
      const sent =
        pendingRollOptions.length <= heldAction.rollsWaiting &&
        (heldAction.inFight || !fightOn(sessionState)) &&
        sendTurnRef.current('action', heldAction.message, heldAction.intent, true)
      if (!sent) returnHeldWords(heldAction)
    }, 0)
    return () => window.clearTimeout(release)
  }, [
    dmResponseBlocking,
    heldAction,
    pendingRollOptions.length,
    reconnectingSubmission,
    returnHeldWords,
    sendPending,
    sendTurnRef,
    sessionState,
  ])

  const cancelHeldAction = () => {
    if (!heldAction) return
    setHeldAction(null)
    returnHeldWords(heldAction)
  }

  // The opening narration request is internal: a delayed opening is retried
  // with its original payload instead of being sent again.
  const startOpening = (message: string) =>
    recoverableSubmission?.kind === 'opening'
      ? retryRecoverableSubmission()
      : sendTurn('opening', message)

  const applyComposerMode = (mode: ComposerMode, die = selectedDie) => {
    if (mode === 'admin' && !adminToolsUnlocked) return
    const abilityForMode = mode === 'spell' ? selectedSpellAbility : selectedAbility
    if (mode === 'spell' && abilityForMode) {
      setSelectedAbilityKey(abilityForMode.key)
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
        null,
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
    const nextModifier = abilityModifierValue(nextAbility)
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
          undefined,
          nextModifier,
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
        composerTextForMode(
          'roll',
          current,
          selectedPlayer?.character_name ?? 'I',
          normalizedDie,
          selectedAbility,
          selectedItem,
          null,
          'speak_to',
          'use',
          '',
          '',
          '',
          null,
        ),
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
    if (!selectedSessionId || !selectedCampaignId || !campaign || !selectedPlayerId) {
      onActorMissing()
      return
    }
    if (!socketRef.current) {
      pushError('validation', 'Realtime is reconnecting. Try again in a moment.')
      return
    }

    const normalizedDie = normalizeDie(die)
    const targetPendingTurnId = rollTargetPendingTurnId ? Number(rollTargetPendingTurnId) : null
    const deferredActionDraft = targetPendingTurnId ? actionText.trim() : ''
    const roll = {
      die: normalizedDie,
      mode: rollMode,
      reason:
        selectedAbilityKey === INITIATIVE_ROLL_ABILITY_KEY
          ? INITIATIVE_ROLL_REASON
          : rollReason || (selectedAbility ? `${selectedAbility.label} check` : ''),
      resultVisibility: 'hidden_until_landed' as const,
      targetPendingTurnId,
    }
    const actionDescription = targetPendingTurnId ? '' : stripComposerCommand(actionText)
    const rollMessage = diceRollRequestMessage(roll)
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
    const rollKey = (diceRollKeyRef.current += 1)
    setDiceRoll({
      die: normalizedDie,
      message,
      actionIntent,
      clientMessageId,
      rollKey,
      startedAt: Date.now(),
      result: null,
      landAt: null,
      status: 'requesting',
    })
    if (!submitAction(message, actionIntent)) {
      setDiceRoll((current) =>
        current?.rollKey === rollKey
          ? { ...current, status: 'failed', error: 'The roll request could not be sent.' }
          : current,
      )
    } else if (deferredActionDraft) {
      setActionText(deferredActionDraft)
      setQueuedActionText(deferredActionDraft)
    }
  }

  /** The pressed roll's card has landed: an action queued behind the roll is the composer's again. */
  const markDiceRollPlayed = () => {
    if (!diceRoll || diceRoll.status !== 'rolling') return
    const { rollKey } = diceRoll
    setDiceRoll((current) => (current?.rollKey === rollKey ? { ...current, status: 'played' } : current))
    if (diceRoll.actionIntent.roll?.target_pending_turn_id && queuedActionText) setComposerMode('action')
  }

  const closeDiceRoll = useCallback(() => {
    setDiceRoll(null)
  }, [])

  const retryDiceRoll = () => {
    if (recoverableSubmission) return retryRecoverableSubmission()
    if (!diceRoll || diceRoll.status !== 'failed') return false
    const { rollKey, message, actionIntent } = diceRoll
    setDiceRoll((current) =>
      current?.rollKey === rollKey ? { ...current, status: 'requesting', error: undefined } : current,
    )
    if (submitAction(message, actionIntent)) return true
    setDiceRoll((current) =>
      current?.rollKey === rollKey
        ? { ...current, status: 'failed', error: 'The roll request could not be sent.' }
        : current,
    )
    return false
  }

  const handleRollResolved = useCallback((payload: RollResolvedPayload) => {
    if (payload.session_id !== selectedSessionId) return
    const roll: RollResult = {
      die: payload.die,
      mode: payload.mode,
      reason: payload.reason,
      resultVisibility: payload.result_visibility,
      targetPendingTurnId: payload.pending_turn_id,
      rolls: payload.rolls,
      kept: payload.kept,
      modifier: payload.modifier,
      total: payload.total,
    }
    if (payload.player_id !== selectedPlayerId) {
      setSharedRollNotice({ playerId: payload.player_id, turnId: payload.turn_id, roll })
      return
    }
    if (!payload.client_message_id) return
    const pressed = diceRollRef.current
    if (pressed?.clientMessageId === payload.client_message_id && pressed.status === 'requesting') {
      const landAt = Math.max(Date.now(), pressed.startedAt + PRESS_MIN_MS)
      notePressedRoll(payload.turn_id, landAt + SETTLE_MS + PRESSED_HOLD_MS)
      setDiceRoll((current) =>
        current?.rollKey === pressed.rollKey
          ? { ...current, die: payload.die, result: payload, landAt, status: 'rolling', error: undefined }
          : current,
      )
    }
    if (pendingSubmissionRef.current?.clientMessageId === payload.client_message_id) {
      pendingSubmissionRef.current = null
    }
    setRecoverableSubmission((current) =>
      current?.clientMessageId === payload.client_message_id ? null : current,
    )
  }, [diceRollRef, selectedPlayerId, selectedSessionId])

  const handleTurnDuplicate = useCallback((payload: {
    session_id: number
    client_message_id: string
    turn_id?: number | null
  }) => {
    if (payload.session_id !== selectedSessionId) return
    const matchesPending = pendingSubmissionRef.current?.clientMessageId === payload.client_message_id
    const answersResend = resentClientMessageIdRef.current === payload.client_message_id
    pendingSubmissionRef.current = matchesPending ? null : pendingSubmissionRef.current
    if (answersResend) resentClientMessageIdRef.current = null
    setRecoverableSubmission((current) =>
      current?.clientMessageId === payload.client_message_id ? null : current,
    )
    if (matchesPending || answersResend) setSendPending(false)
    setOptimisticEntries((current) =>
      current.map((entry) =>
        entry.metadata.client_message_id === payload.client_message_id
          ? {
              ...entry,
              metadata: {
                ...entry.metadata,
                persistence_status: 'received',
                ...(payload.turn_id ? { turn_id: payload.turn_id } : {}),
              },
            }
          : entry,
      ),
    )
    setDiceRoll((current) =>
      current?.clientMessageId === payload.client_message_id ? null : current,
    )
  }, [selectedSessionId, setOptimisticEntries, setSendPending])

  const handleConnectionInterrupted = useCallback(() => {
    const selection = selectionRef.current
    const bindingUnchanged = (submission: StoredSubmission) =>
      submission.sessionId === selection.sessionId && submission.playerId === selection.playerId
    const waitingSubmission = reconnectingSubmissionRef.current
    if (waitingSubmission) {
      if (bindingUnchanged(waitingSubmission)) return
      clearReconnectWait()
      markSubmissionRecoverable(waitingSubmission, CONNECTION_INTERRUPTED_REASON)
      return
    }
    const pendingSubmission = pendingSubmissionRef.current
    if (!pendingSubmission) return
    pendingSubmissionRef.current = null
    if (pendingSubmission.autoRetried || !bindingUnchanged(pendingSubmission)) {
      markSubmissionRecoverable(pendingSubmission, CONNECTION_INTERRUPTED_REASON)
      return
    }
    reconnectingSubmissionRef.current = pendingSubmission
    setReconnectingSubmission(pendingSubmission)
    reconnectTimerRef.current = window.setTimeout(() => {
      reconnectTimerRef.current = null
      if (reconnectingSubmissionRef.current !== pendingSubmission) return
      reconnectingSubmissionRef.current = null
      setReconnectingSubmission(null)
      markSubmissionRecoverable(pendingSubmission, CONNECTION_INTERRUPTED_REASON)
    }, RECONNECT_RESEND_WINDOW_MS)
  }, [clearReconnectWait, markSubmissionRecoverable, selectionRef])

  const handleSessionJoined = useCallback((binding: SessionSocketBinding) => {
    const waitingSubmission = reconnectingSubmissionRef.current
    if (
      !waitingSubmission ||
      waitingSubmission.sessionId !== binding.sessionId ||
      waitingSubmission.playerId !== binding.playerId
    ) {
      return
    }
    clearReconnectWait()
    const socket = socketRef.current
    if (!socket || socket.connected !== true) {
      markSubmissionRecoverable(waitingSubmission, CONNECTION_INTERRUPTED_REASON)
      return
    }
    // Same client_message_id: the server answers turn_duplicate or resumes.
    socket.emit('send_message', waitingSubmission.payload)
    pendingSubmissionRef.current = { ...waitingSubmission, autoRetried: true }
    resentClientMessageIdRef.current = waitingSubmission.clientMessageId
    setSendPending(true)
  }, [clearReconnectWait, markSubmissionRecoverable, setSendPending, socketRef])

  const handleTurnRejected = useCallback((rejection: TurnRejection) => {
    const rejectedSubmission = pendingSubmissionRef.current
    pendingSubmissionRef.current = null
    if (rejectedSubmission) {
      setOptimisticEntries((current) =>
        current.filter(
          (entry) => entry.metadata.client_message_id !== rejectedSubmission.clientMessageId,
        ),
      )
      setDiceRoll((current) =>
        current?.clientMessageId === rejectedSubmission.clientMessageId
          ? { ...current, status: 'failed', error: rejection.message }
          : current,
      )
      if (rejectedSubmission.kind !== 'opening') {
        setActionText((current) => current || rejectedSubmission.draftText)
      }
    }
    setSendPending(false)
    setTurnRejectionMessage(rejection.message)
  }, [setOptimisticEntries, setSendPending])

  const pendingSubmissionCampaignId = useCallback(
    () => (pendingSubmissionRef.current ?? reconnectingSubmissionRef.current)?.payload.campaign_id ?? null,
    [],
  )

  return {
    actionText,
    adminPasscode,
    adminToolsUnlocked,
    applyComposerMode,
    closeDiceRoll,
    composerMode,
    diceRoll,
    handleConnectionInterrupted,
    handleRollRequired,
    handleRollResolved,
    handleSavedMessages,
    handleSessionJoined,
    handleTurnDuplicate,
    handleTurnRejected,
    interactionTargets,
    markDiceRollPlayed,
    openingDelayed: recoverableSubmission?.kind === 'opening',
    pendingSubmissionCampaignId,
    rollMode,
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
    preparePendingRoll,
    setActionText,
    updateActionText,
    setAdminPasscode,
    setSelectedInteractionTargetId,
    setSelectedInteractionType,
    setItemQuantity,
    setRollMode,
    setRollReason,
    setRollTargetPendingTurnId,
    updateRollAbilityKey,
    updateSpellName,
    setSelectedItemId,
    updateSelectedInventoryAction,
    updateItemDraftName,
    updateItemCostGold,
    startDiceRoll,
    startOpening,
    submitAction,
    submissionReconnecting: Boolean(reconnectingSubmission),
    toggleAdminTools,
    turnRejectionMessage,
    heldActionText: heldAction?.message ?? '',
    cancelHeldAction,
    queuedActionText,
    queuedActionRetryable: recoverableSubmission?.kind === 'action',
    retryRecoverableSubmission,
    retryDiceRoll,
    sharedRollNotice,
    clearQueuedAction: () => {
      setQueuedActionText('')
      setRecoverableSubmission(null)
    },
    selectedPlayerHasTurn: canSubmitWithTurnControl(turnControl, selectedPlayerId, 'message', false),
    turnControlStatusLabel: turnControlStatusLabel(turnControl),
    updateSelectedDie,
  }
}
