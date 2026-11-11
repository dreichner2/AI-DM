import { useEffect, useLayoutEffect, useMemo, useRef, useState, type KeyboardEvent } from 'react'
import {
  ArrowUp,
  Backpack,
  Coins,
  Dices,
  Drama,
  Footprints,
  Hand,
  KeyRound,
  MessageCircle,
  Plus,
  Quote,
  Sparkles,
  Users,
  type LucideIcon,
} from 'lucide-react'
import type { ComposerMode } from '../gameActions'
import { partyVitalityFromSnapshot } from '../gameSelectors'
import { objectActionSubmission, pickupSubmission, travelSubmission, type ActionSubmission } from './actionIntents'
import { StoryActionTray, type TraySection } from './StoryActionTray'
import { StoryCombatRibbon } from './StoryCombatRibbon'
import { recentSpellNames } from './recentSpells'
import { StoryRollPrompt } from './StoryRollPrompt'
import { MORE_SUGGESTION, storySuggestions, type StorySuggestion } from './storySuggestions'
import type { StoryComposerProps } from './types'
import { useChipsThatFit } from './useChipsThatFit'
import { useStagedComposerAction } from './useStagedComposerAction'

const HINTS_SEEN_KEY = 'aidm:storyHintsSeen'
const HINT_SEND_LIMIT = 3
const MAX_INPUT_LINES = 6

const MODE_ICONS: Partial<Record<ComposerMode, LucideIcon>> = {
  roll: Dices,
  ability: Dices,
  spell: Sparkles,
  item: Backpack,
  interact: Users,
  emote: Drama,
  ooc: Quote,
  admin: KeyRound,
}

const MODE_SECTIONS: Partial<Record<ComposerMode, TraySection>> = {
  roll: 'roll',
  ability: 'roll',
  spell: 'magic',
  item: 'items',
  interact: 'interact',
}

const SUGGESTION_ICONS: Partial<Record<StorySuggestion['kind'], LucideIcon>> = {
  take: Backpack,
  object: Hand,
  talk: MessageCircle,
  trade: Coins,
  travel: Footprints,
}

const DOWN_STATUS = 'You are down. Your companions can still reach you.'
const DOWN_PLACEHOLDER = 'You are down.'

const CONNECTION_LABELS = {
  connecting: 'Connecting…',
  reconnecting: 'Reconnecting…',
  offline: 'Offline',
} as const

function placeholderFor(mode: ComposerMode, hasCharacter: boolean, down: boolean) {
  if (!hasCharacter) return 'Choose a character first'
  if (mode === 'ooc') return 'Say it out of character…'
  if (down) return DOWN_PLACEHOLDER
  if (mode === 'item') return 'Describe what you do with it…'
  if (mode === 'emote') return 'Describe the gesture…'
  return 'What do you do?'
}

function readSentCount() {
  try {
    return Number.parseInt(window.localStorage.getItem(HINTS_SEEN_KEY) ?? '0', 10) || 0
  } catch {
    return HINT_SEND_LIMIT
  }
}

function writeSentCount(count: number) {
  try {
    window.localStorage.setItem(HINTS_SEEN_KEY, String(count))
  } catch {
    // Storage can be unavailable (private mode); the hint simply stays.
  }
}

function Dots() {
  return <span className="story-dots" aria-hidden="true"><i /><i /><i /></span>
}

/** One line of suggestions; chips that would not fit wait behind `More…` instead of being cut off. */
function SuggestionRow({
  suggestions,
  onChoose,
}: {
  suggestions: StorySuggestion[]
  onChoose: (suggestion: StorySuggestion) => void
}) {
  const chips = suggestions.filter((suggestion) => suggestion.kind !== 'more')
  const rowRef = useChipsThatFit<HTMLDivElement>(chips.length < suggestions.length)
  return (
    <div ref={rowRef} className="story-suggestions" role="group" aria-label="Suggestions">
      {[...chips, MORE_SUGGESTION].map((suggestion, index) => {
        const Icon = SUGGESTION_ICONS[suggestion.kind]
        return (
          <button
            key={suggestion.key}
            type="button"
            className="story-chip"
            data-kind={suggestion.kind}
            data-fit={suggestion.kind === 'more' ? 'more' : 'chip'}
            style={{ animationDelay: `${index * 30}ms` }}
            onClick={() => onChoose(suggestion)}
          >
            {Icon ? <Icon aria-hidden="true" /> : null}
            {suggestion.label}
          </button>
        )
      })}
    </div>
  )
}

/**
 * Story mode's dock: one status line, then whatever the moment needs — a roll,
 * the combat ribbon or a few suggestions — above a single line to type in.
 */
export function StoryComposer({ board, chrome }: StoryComposerProps) {
  const composer = board.actionComposerProps
  const {
    actionInputRef,
    actionText,
    composerMode,
    applyComposerMode,
    adminToolsUnlocked,
    canUseOperatorTools,
    toggleAdminTools,
  } = composer
  const toggleRef = useRef<HTMLButtonElement | null>(null)
  const [trayOpen, setTrayOpen] = useState(false)
  const [traySection, setTraySection] = useState<TraySection>('scene')
  const [traySpellId, setTraySpellId] = useState<string | undefined>(undefined)
  const [sentCount, setSentCount] = useState(readSentCount)
  const [tradeMerchantId, setTradeMerchantId] = useState('')
  const stage = useStagedComposerAction(composer)
  const snapshot = board.sessionState?.state_snapshot
  const vitality = useMemo(() => partyVitalityFromSnapshot(snapshot), [snapshot])

  const notice = board.pendingRollNotice
  const rollPromptActive = Boolean(notice?.isWaitingOnSelectedPlayer)
  const hasCharacter = Boolean(composer.selectedPlayerId)
  const viewerHealth = board.activePlayers.find((player) => player.id === composer.selectedPlayerId)?.health
  // Dying, stable, unconscious or dead: a hero at 0 HP cannot act.
  const viewerDown =
    composer.selectedPlayerId !== null &&
    (Boolean(vitality[composer.selectedPlayerId]) || (viewerHealth ? viewerHealth.currentHp <= 0 : false))
  const othersConnected = board.activePlayers.some((player) => player.id !== composer.selectedPlayerId)
  const speakingOutOfCharacter = viewerDown && othersConnected && composerMode === 'ooc'
  const dmWorking = board.sendPending || board.streamingTurnActive
  const deliveryUncertain = Boolean(composer.queuedActionRetryable)
  const connected = chrome.connection === 'ready'
  const combatBundle = board.combatState.active
    ? board.combatState.legalActionBundles.find((bundle) => bundle.playerId === board.playerId)
    : undefined
  const ribbonActive = !rollPromptActive && !viewerDown && Boolean(combatBundle)
  const inputDisabled =
    !hasCharacter || Boolean(board.turnRecoveryGate) || (viewerDown && !speakingOutOfCharacter)
  const sendBlocked =
    inputDisabled || dmWorking || deliveryUncertain || !connected || !actionText.trim()
  const quietMoment =
    hasCharacter &&
    !viewerDown &&
    composer.selectedPlayerHasTurn &&
    connected &&
    !notice &&
    !dmWorking &&
    !deliveryUncertain &&
    !board.turnRecoveryGate &&
    !board.clarificationRequest
  const suggestions = quietMoment && !ribbonActive
    ? storySuggestions(board.worldState, board.gameplayControls)
    : []
  const { turnRows, currentResponseEntry } = board
  const heroName = composer.selectedCharacterName ?? ''
  const recentSpells = useMemo(
    () => recentSpellNames(currentResponseEntry ? [...turnRows, currentResponseEntry] : turnRows, heroName),
    [currentResponseEntry, heroName, turnRows],
  )

  // Story mode has no standing roll mode: dice are chosen in the tray or the
  // roll prompt, so a finished roll returns the composer to plain text.
  useEffect(() => {
    if (composerMode === 'roll' && !rollPromptActive) applyComposerMode('action')
  }, [applyComposerMode, composerMode, rollPromptActive])

  useEffect(() => {
    if (!canUseOperatorTools && adminToolsUnlocked) toggleAdminTools()
  }, [adminToolsUnlocked, canUseOperatorTools, toggleAdminTools])

  useLayoutEffect(() => {
    const input = actionInputRef.current
    if (!input) return
    const style = window.getComputedStyle(input)
    const lineHeight = Number.parseFloat(style.lineHeight) || 27
    const padding = (Number.parseFloat(style.paddingTop) || 0) + (Number.parseFloat(style.paddingBottom) || 0)
    const maxHeight = lineHeight * MAX_INPUT_LINES + padding
    input.style.height = 'auto'
    input.style.height = `${Math.min(input.scrollHeight, maxHeight)}px`
    input.style.overflowY = input.scrollHeight > maxHeight ? 'auto' : 'hidden'
  }, [actionInputRef, actionText, rollPromptActive])

  // Reads storage rather than state: staged submits call this from an older render.
  const recordSent = () => {
    const next = readSentCount() + 1
    writeSentCount(next)
    setSentCount(next)
  }

  const focusInput = () => actionInputRef.current?.focus()

  // Outside combat the tray opens on Scene; in a fight, on the Combat list.
  const openTray = (section?: TraySection, spellId?: string) => {
    setTraySection(section ?? MODE_SECTIONS[composerMode] ?? (combatBundle ? 'combat' : 'scene'))
    setTraySpellId(spellId)
    setTrayOpen(true)
  }

  const closeTray = (focusTarget: 'toggle' | 'input') => {
    setTrayOpen(false)
    if (focusTarget === 'toggle') toggleRef.current?.focus()
    else focusInput()
  }

  const send = () => {
    if (sendBlocked) return
    if (composer.submitAction()) recordSent()
  }

  const submitTyped = ({ message, intent }: ActionSubmission) => {
    if (composer.submitAction(message, intent)) recordSent()
  }

  const chooseSuggestion = (suggestion: StorySuggestion) => {
    if (suggestion.kind === 'trade') {
      setTradeMerchantId(suggestion.merchant.id)
      openTray('trade')
      return
    }
    if (suggestion.kind === 'more') {
      openTray('scene')
      return
    }
    if (suggestion.kind === 'take') {
      submitTyped(pickupSubmission(suggestion.item, suggestion.label))
      return
    }
    if (suggestion.kind === 'object') {
      submitTyped(objectActionSubmission(board.gameplayControls.actorName, suggestion.object, suggestion.action))
      return
    }
    if (suggestion.kind === 'travel') {
      submitTyped(travelSubmission(suggestion.exit))
      return
    }
    if (composerMode !== 'action') applyComposerMode('action')
    composer.updateActionText(suggestion.text)
    focusInput()
  }

  const handleKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.nativeEvent.isComposing || event.keyCode === 229) return
    if (event.key === 'Enter' && (!event.shiftKey || event.metaKey || event.ctrlKey)) {
      event.preventDefault()
      send()
      return
    }
    if (event.key === '/' && !actionText && !viewerDown && !event.metaKey && !event.ctrlKey && !event.altKey) {
      event.preventDefault()
      openTray()
      return
    }
    if (event.key === 'Escape' && trayOpen) {
      event.preventDefault()
      closeTray('input')
    }
  }

  if (chrome.endedChapter) {
    return (
      <div className="story-dock" data-state="ended">
        <div className="story-dock-ended">
          <p className="story-dock-status">This chapter has closed.</p>
          <button type="button" className="story-primary" onClick={chrome.endedChapter.onContinue}>
            Continue the adventure
          </button>
        </div>
      </div>
    )
  }

  const renderStatus = () => {
    if (board.turnRecoveryGate) {
      return (
        <p className="story-dock-status" role="status">
          <span>The Dungeon Master is reviewing the last turn.</span>
          {chrome.canUseOperatorTools && chrome.onOpenConsole ? (
            <button type="button" className="story-link" onClick={chrome.onOpenConsole}>
              Open table tools
            </button>
          ) : null}
        </p>
      )
    }
    if (deliveryUncertain) {
      return (
        <p className="story-dock-status" data-tone="alert" role="alert">
          <span>We could not confirm that reached the table.</span>
          <button
            type="button"
            className="story-link"
            disabled={composer.sendPending}
            onClick={() => composer.retryRecoverableSubmission()}
          >
            Send again
          </button>
          <button
            type="button"
            className="story-link"
            disabled={composer.sendPending}
            onClick={composer.clearQueuedAction}
          >
            Discard
          </button>
        </p>
      )
    }
    if (chrome.connection !== 'ready') {
      return (
        <p className="story-dock-status" data-tone={chrome.connection === 'offline' ? 'fail' : 'busy'} role="status">
          <span>{CONNECTION_LABELS[chrome.connection]}</span>
          {chrome.connection === 'offline' ? null : <Dots />}
        </p>
      )
    }
    if (board.operationalError) {
      return (
        <p className="story-dock-status" data-tone="fail" role="status">
          <span>{board.operationalError}</span>
          {board.onRecoverOperationalError ? (
            <button type="button" className="story-link" onClick={() => void board.onRecoverOperationalError?.()}>
              Refresh and reconnect
            </button>
          ) : null}
        </p>
      )
    }
    if (viewerDown) {
      return (
        <p className="story-dock-status" data-tone="down" role="status">
          <span>{DOWN_STATUS}</span>
          {othersConnected && composerMode !== 'ooc' ? (
            <button
              type="button"
              className="story-link"
              onClick={() => {
                applyComposerMode('ooc')
                focusInput()
              }}
            >
              Speak out of character
            </button>
          ) : null}
        </p>
      )
    }
    if (notice && !notice.isWaitingOnSelectedPlayer) {
      return (
        <p className="story-dock-status" role="status">
          <span>Waiting for {notice.waitingOnLabel} to roll</span>
          <Dots />
        </p>
      )
    }
    if (dmWorking) {
      return (
        <p className="story-dock-status" role="status">
          <span>The Dungeon Master is narrating</span>
          <Dots />
        </p>
      )
    }
    if (hasCharacter && !composer.selectedPlayerHasTurn) {
      return <p className="story-dock-status" data-tone="muted" role="status">{composer.turnControlStatusLabel}</p>
    }
    return null
  }

  const ModeIcon = MODE_ICONS[composerMode] ?? Plus
  const showHint = sentCount < HINT_SEND_LIMIT

  return (
    <div className="story-dock" data-tray-open={trayOpen || undefined}>
      {renderStatus()}

      {rollPromptActive && notice ? (
        <StoryRollPrompt notice={notice} composer={composer} onPrepare={board.onPreparePendingRoll} />
      ) : (
        <>
          {ribbonActive ? (
            <StoryCombatRibbon
              combat={board.combatState}
              playerId={board.playerId}
              activePlayers={board.activePlayers}
              controls={board.gameplayControls}
              recentSpellNames={recentSpells}
              onOpenTray={openTray}
              disabled={
                dmWorking ||
                Boolean(board.turnRecoveryGate) ||
                Boolean(notice) ||
                Boolean(board.clarificationRequest)
              }
              submitAction={(message, intent) => {
                const sent = composer.submitAction(message, intent)
                if (sent) recordSent()
                return sent
              }}
            />
          ) : suggestions.length ? (
            <SuggestionRow
              key={suggestions.map((suggestion) => suggestion.key).join('|')}
              suggestions={suggestions}
              onChoose={chooseSuggestion}
            />
          ) : null}

          <div className="story-input-row">
            <button
              ref={toggleRef}
              type="button"
              className="story-icon-button story-actions-toggle"
              aria-label="Actions"
              aria-expanded={trayOpen}
              aria-haspopup="dialog"
              data-mode={composerMode === 'action' ? undefined : composerMode}
              disabled={inputDisabled || viewerDown}
              onClick={() => (trayOpen ? closeTray('toggle') : openTray())}
            >
              <ModeIcon size={20} strokeWidth={1.6} aria-hidden="true" />
            </button>
            <label htmlFor="action-input" className="story-sr-only">Your Action</label>
            <textarea
              id="action-input"
              ref={actionInputRef}
              className="story-input"
              rows={1}
              value={actionText}
              placeholder={placeholderFor(composerMode, hasCharacter, viewerDown)}
              disabled={inputDisabled}
              onChange={(event) => composer.updateActionText(event.target.value)}
              onKeyDown={handleKeyDown}
            />
            <button
              type="button"
              className="story-send"
              aria-label="Send"
              disabled={sendBlocked}
              onClick={send}
            >
              <ArrowUp size={20} strokeWidth={2.2} aria-hidden="true" />
            </button>
            {trayOpen ? (
              <StoryActionTray
                key={traySpellId ?? ''}
                board={board}
                othersConnected={othersConnected}
                section={traySection}
                initialSpellId={traySpellId}
                onSectionChange={setTraySection}
                tradeMerchantId={tradeMerchantId}
                onTradeMerchantChange={setTradeMerchantId}
                stage={stage}
                onDone={() => closeTray('input')}
                onDismiss={() => closeTray('toggle')}
                onSent={recordSent}
                toggleRef={toggleRef}
              />
            ) : null}
          </div>

          {showHint ? (
            <p className="story-dock-hint">Enter to send · / actions · C character · J journal · M map</p>
          ) : null}
        </>
      )}
    </div>
  )
}
