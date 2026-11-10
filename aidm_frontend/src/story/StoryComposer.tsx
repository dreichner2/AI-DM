import { useEffect, useLayoutEffect, useRef, useState, type KeyboardEvent } from 'react'
import {
  ArrowUp,
  Backpack,
  Dices,
  Drama,
  Eye,
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
import { objectActionSubmission, travelSubmission, type ActionSubmission } from './actionIntents'
import { StoryActionTray, type TraySection } from './StoryActionTray'
import { StoryCombatRibbon } from './StoryCombatRibbon'
import { StoryRollPrompt } from './StoryRollPrompt'
import { storySuggestions, type StorySuggestion } from './storySuggestions'
import type { StoryComposerProps } from './types'
import { useEdgeFades } from './useEdgeFades'
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

const SUGGESTION_ICONS: Record<StorySuggestion['kind'], LucideIcon> = {
  talk: MessageCircle,
  object: Hand,
  examine: Eye,
  travel: Footprints,
}

const CONNECTION_LABELS = {
  connecting: 'Connecting…',
  reconnecting: 'Reconnecting…',
  offline: 'Offline',
} as const

function placeholderFor(mode: ComposerMode, hasCharacter: boolean) {
  if (!hasCharacter) return 'Choose a character first'
  if (mode === 'ooc') return 'Say it out of character…'
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
  const [traySection, setTraySection] = useState<TraySection>(
    () => (board.gameplayControls.spells.length ? 'magic' : 'roll'),
  )
  const [sentCount, setSentCount] = useState(readSentCount)
  const stage = useStagedComposerAction(composer)
  const suggestionsRef = useEdgeFades<HTMLDivElement>()

  const notice = board.pendingRollNotice
  const rollPromptActive = Boolean(notice?.isWaitingOnSelectedPlayer)
  const hasCharacter = Boolean(composer.selectedPlayerId)
  const dmWorking = board.sendPending || board.streamingTurnActive
  const deliveryUncertain = Boolean(composer.queuedActionRetryable)
  const connected = chrome.connection === 'ready'
  const combatBundle = board.combatState.active
    ? board.combatState.legalActionBundles.find((bundle) => bundle.playerId === board.playerId)
    : undefined
  const ribbonActive = !rollPromptActive && Boolean(combatBundle)
  const inputDisabled = !hasCharacter || Boolean(board.turnRecoveryGate)
  const sendBlocked =
    inputDisabled || dmWorking || deliveryUncertain || !connected || !actionText.trim()
  const quietMoment =
    hasCharacter &&
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

  const openTray = () => {
    setTraySection((current) => MODE_SECTIONS[composerMode] ?? current)
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
    if (event.key === '/' && !actionText && !event.metaKey && !event.ctrlKey && !event.altKey) {
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
      return <p className="story-dock-status" role="status">The Dungeon Master is reviewing the last turn.</p>
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
            <div
              key={suggestions.map((suggestion) => suggestion.key).join('|')}
              ref={suggestionsRef}
              className="story-suggestions story-scroll-row"
              role="group"
              aria-label="Suggestions"
            >
              {suggestions.map((suggestion, index) => {
                const Icon = SUGGESTION_ICONS[suggestion.kind]
                return (
                  <button
                    key={suggestion.key}
                    type="button"
                    className="story-chip"
                    style={{ animationDelay: `${index * 30}ms` }}
                    onClick={() => chooseSuggestion(suggestion)}
                  >
                    <Icon aria-hidden="true" />
                    {suggestion.label}
                  </button>
                )
              })}
            </div>
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
              disabled={inputDisabled}
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
              placeholder={placeholderFor(composerMode, hasCharacter)}
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
                board={board}
                canUseOperatorTools={chrome.canUseOperatorTools}
                section={traySection}
                onSectionChange={setTraySection}
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
