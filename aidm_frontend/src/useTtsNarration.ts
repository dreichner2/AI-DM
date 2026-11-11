import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { metadataTurnId, stripMarkdown } from './gameSelectors'
import type { NarrationSetting } from './story/sheets/StorySettingsSheet'
import type {
  TtsCachedAudio,
  TtsLatencySnapshot,
  TtsNarrationHost,
  TtsNarrationRequest,
  TtsQueueItem,
} from './ttsNarrationQueue'
import type { StreamingTurn, TimelineEntry, TtsRuntimeConfig } from './types'

export type TtsPlaybackStatus =
  | 'checking'
  | 'status-error'
  | 'unavailable'
  | 'off'
  | 'ready'
  | 'queued'
  | 'requesting'
  | 'speaking'
  | 'failed'

const TTS_MIN_PARTIAL_FLUSH_CHARS = 60
const TTS_FORCE_PARTIAL_FLUSH_CHARS = 320
const TTS_PARTIAL_FLUSH_DELAY_MS = 650
const TTS_RECENT_TEXT_DEDUPE_MS = 120_000
const TTS_VOICE_STORAGE_KEY = 'aidm:ttsVoice'

/** The voice, accent and quality this browser chose; each is used only while the server offers it. */
type TtsVoiceChoice = { voice?: string; accent?: string; quality?: string }

type UseTtsNarrationOptions = {
  auth: string
  baseUrl: string
  ttsConfig: TtsRuntimeConfig | null
  ttsConfigLoadFailed?: boolean
  selectedSessionId: number | null
  sendPending: boolean
  streamingTurn: StreamingTurn | null
  speakableDmEntry: TimelineEntry | null
  pushError: (category: 'tts', message: string) => void
}

function hashString(value: string) {
  let hash = 0
  for (let index = 0; index < value.length; index += 1) {
    hash = (hash << 5) - hash + value.charCodeAt(index)
    hash |= 0
  }
  return Math.abs(hash)
}

function cleanNarrationText(value: string) {
  const compact = stripMarkdown(value.replace(/<thought>[\s\S]*?(?:<\/thought>|$)/gi, ''))
    .replace(/\s+/g, ' ')
    .trim()
  return compact
    .replace(/^(?:DM|Narrator|Dungeon Master|Game Master|GM|AI[-\s]?DM)(?:\s+Response)?\s*[:-]\s*/i, '')
    .trim()
}

function formatLatency(value: number | null) {
  if (value === null || !Number.isFinite(value)) return null
  const rounded = Math.max(0, Math.round(value))
  if (rounded >= 1000) return `${(rounded / 1000).toFixed(1)}s`
  return `${rounded}ms`
}

function ttsDedupeKeysForText(text: string, turnId: number | null) {
  const cleanText = cleanNarrationText(text)
  const keys: string[] = []
  if (turnId !== null) {
    keys.push(`turn:${turnId}`)
  }
  if (cleanText) {
    keys.push(`text:${hashString(cleanText)}:${cleanText.length}`)
  }
  return keys
}

function readTtsVoiceChoice(): TtsVoiceChoice {
  try {
    const stored: unknown = JSON.parse(localStorage.getItem(TTS_VOICE_STORAGE_KEY) ?? '{}')
    return stored && typeof stored === 'object' ? (stored as TtsVoiceChoice) : {}
  } catch {
    return {}
  }
}

/** The stored choice when the server offers it, else the server's default, else its first option. */
function offeredChoice(stored: string | undefined, options: string[], serverDefault?: string | null) {
  if (stored && options.includes(stored)) return stored
  return serverDefault && options.includes(serverDefault) ? serverDefault : options[0]
}

function ttsFlushLength(text: string, forcePartial: boolean) {
  if (!text.trim()) return 0

  const limit = Math.min(text.length, TTS_FORCE_PARTIAL_FLUSH_CHARS)
  const windowText = text.slice(0, limit)
  const sentenceBreaks = windowText.matchAll(/[.!?]+["'*\])_]*(?=\s|$)|[\n]+/g)
  for (const match of sentenceBreaks) {
    const breakLength = (match.index ?? 0) + match[0].length
    if (breakLength >= TTS_MIN_PARTIAL_FLUSH_CHARS) return breakLength
  }

  if (text.length >= TTS_FORCE_PARTIAL_FLUSH_CHARS || forcePartial) {
    const naturalBreak = Math.max(
      windowText.lastIndexOf(','),
      windowText.lastIndexOf(';'),
      windowText.lastIndexOf(':'),
      windowText.lastIndexOf(' - '),
    )
    if (naturalBreak >= TTS_MIN_PARTIAL_FLUSH_CHARS) return naturalBreak + 1

    const wordBreak = windowText.lastIndexOf(' ')
    if (wordBreak >= TTS_MIN_PARTIAL_FLUSH_CHARS) return wordBreak + 1
    if (forcePartial && windowText.trim().length >= TTS_MIN_PARTIAL_FLUSH_CHARS) return limit
  }

  return 0
}

export function useTtsNarration({
  auth,
  baseUrl,
  ttsConfig,
  ttsConfigLoadFailed = false,
  selectedSessionId,
  sendPending,
  streamingTurn,
  speakableDmEntry,
  pushError,
}: UseTtsNarrationOptions) {
  const [ttsEnabledPreference, setTtsEnabled] = useState(
    () => localStorage.getItem('aidm:ttsEnabled') === 'true',
  )
  const ttsEnabled = ttsConfig?.configured === true && ttsEnabledPreference
  const [ttsSpeaking, setTtsSpeaking] = useState(false)
  const [ttsQueueCount, setTtsQueueCount] = useState(0)
  const [ttsLatency, setTtsLatency] = useState<TtsLatencySnapshot | null>(null)
  const [ttsStatus, setTtsStatus] = useState<TtsPlaybackStatus>(() =>
    localStorage.getItem('aidm:ttsEnabled') === 'true' ? 'ready' : 'off',
  )
  const [ttsVoiceChoice, setTtsVoiceChoice] = useState(readTtsVoiceChoice)
  const ttsVoices = ttsConfig?.voices ?? []
  const ttsAccents = ttsConfig?.accents ?? []
  const ttsQualities = ttsConfig?.qualities ?? []
  // Sent with every request, and only to a server that offers voices.
  const ttsVoice = ttsVoices.length ? offeredChoice(ttsVoiceChoice.voice, ttsVoices, ttsConfig?.voice) : undefined
  const ttsAccent = ttsVoices.length
    ? offeredChoice(ttsVoiceChoice.accent, ttsAccents.map((accent) => accent.id), ttsConfig?.accent)
    : undefined
  const ttsQuality = ttsVoices.length
    ? offeredChoice(ttsVoiceChoice.quality, ttsQualities.map((quality) => quality.id))
    : undefined
  const ttsAudioContextRef = useRef<AudioContext | null>(null)
  const ttsSampleControllerRef = useRef<AbortController | null>(null)
  const ttsAudioRef = useRef<HTMLAudioElement | null>(null)
  const ttsAudioUrlRef = useRef<string | null>(null)
  const ttsAudioUrlFromCacheRef = useRef(false)
  const ttsAudioCacheRef = useRef<Map<string, TtsCachedAudio>>(new Map())
  const lastSpokenDmEntryRef = useRef<string | null>(null)
  const queueTtsNarrationRef = useRef<((text: string) => void) | null>(null)
  const ttsEnabledRef = useRef(ttsEnabled)
  const lastSpokenTurnIdRef = useRef<number | null>(null)
  const ttsQueueRef = useRef<TtsQueueItem[]>([])
  const ttsCurrentItemRef = useRef<TtsQueueItem | null>(null)
  const ttsPlayingRef = useRef(false)
  const ttsAudioActiveRef = useRef(false)
  const spokenTextLengthRef = useRef(0)
  const speakableStreamingTextRef = useRef('')
  const ttsQueueSuppressedRef = useRef(false)
  const ttsFailureReportedRef = useRef(false)
  const ttsSoftFailureReportedRef = useRef(false)
  const ttsPartialFlushTimerRef = useRef<number | null>(null)
  const ttsLoopIdRef = useRef(0)
  const lastSpokenTextRef = useRef<string | null>(null)
  const spokenTtsKeysRef = useRef<Map<string, number>>(new Map())
  const recentlyStreamedTtsTurnsRef = useRef<Map<string, number>>(new Map())
  const narrationQueueRef = useRef<typeof import('./ttsNarrationQueue') | null>(null)
  // Narration said before the queue's code has arrived, in order.
  const waitingTextsRef = useRef<string[]>([])
  const queueTtsTextRef = useRef<(text: string) => void>(() => undefined)

  const ttsDedupeScope = selectedSessionId ? `session:${selectedSessionId}` : 'session:none'
  const scopedTtsKey = useCallback(
    (key: string) => `${ttsDedupeScope}:${key}`,
    [ttsDedupeScope],
  )
  const rememberSpokenTts = useCallback(
    (text: string, turnId: number | null) => {
      const now = Date.now()
      const keys = ttsDedupeKeysForText(text, turnId)
      keys.forEach((key) => spokenTtsKeysRef.current.set(scopedTtsKey(key), now))
      for (const [storedKey, spokenAt] of spokenTtsKeysRef.current) {
        if (storedKey.includes(':text:') && now - spokenAt > TTS_RECENT_TEXT_DEDUPE_MS) {
          spokenTtsKeysRef.current.delete(storedKey)
        }
      }
      if (spokenTtsKeysRef.current.size > 160) {
        spokenTtsKeysRef.current = new Map([...spokenTtsKeysRef.current].slice(-80))
      }
    },
    [scopedTtsKey],
  )
  const rememberStreamedTtsTurn = useCallback(
    (turnId: number, text: string) => {
      const now = Date.now()
      recentlyStreamedTtsTurnsRef.current.set(scopedTtsKey(`streamed-turn:${turnId}`), now)
      rememberSpokenTts(text, turnId)
      for (const [storedTurnKey, spokenAt] of recentlyStreamedTtsTurnsRef.current) {
        if (now - spokenAt > TTS_RECENT_TEXT_DEDUPE_MS) {
          recentlyStreamedTtsTurnsRef.current.delete(storedTurnKey)
        }
      }
    },
    [rememberSpokenTts, scopedTtsKey],
  )

  const effectiveTtsStatus: TtsPlaybackStatus = ttsConfigLoadFailed
    ? 'status-error'
    : ttsConfig === null
      ? 'checking'
      : !ttsConfig.configured
        ? 'unavailable'
        : ttsEnabled ? ttsStatus : 'off'
  const ttsStatusLabel =
    effectiveTtsStatus === 'status-error'
      ? 'Status unavailable'
      : effectiveTtsStatus === 'checking'
        ? 'Checking'
        : effectiveTtsStatus === 'unavailable'
          ? 'Unavailable'
          : effectiveTtsStatus === 'off'
            ? 'Off'
            : effectiveTtsStatus === 'ready'
              ? 'Ready'
              : effectiveTtsStatus === 'queued'
                ? `${ttsQueueCount} queued`
                : effectiveTtsStatus === 'requesting'
                  ? 'Requesting'
                  : effectiveTtsStatus === 'speaking'
                    ? 'Speaking'
                    : 'Failed'
  const ttsLatencyLabel = ttsLatency
    ? [
        ttsLatency.source === 'cache' ? 'cache' : 'network',
        formatLatency(ttsLatency.playStartMs ?? ttsLatency.requestMs),
        ttsLatency.totalMs !== null ? `total ${formatLatency(ttsLatency.totalMs)}` : null,
      ]
        .filter(Boolean)
        .join(' · ')
    : ''
  const canStopTts = ['queued', 'requesting', 'speaking'].includes(effectiveTtsStatus)

  const suppressTtsQueueForCurrentResponse = useCallback(() => {
    ttsQueueSuppressedRef.current = true
    if (ttsPartialFlushTimerRef.current !== null) {
      window.clearTimeout(ttsPartialFlushTimerRef.current)
      ttsPartialFlushTimerRef.current = null
    }
  }, [])

  const resetTtsFailureForNextResponse = useCallback(() => {
    ttsFailureReportedRef.current = false
    ttsSoftFailureReportedRef.current = false
    ttsQueueSuppressedRef.current = false
  }, [])

  const reportTtsFailureForCurrentResponse = useCallback(
    (message: string) => {
      suppressTtsQueueForCurrentResponse()
      setTtsStatus('failed')
      ttsEnabledRef.current = false
      queueTtsNarrationRef.current = null
      setTtsEnabled(false)
      if (ttsFailureReportedRef.current) return
      ttsFailureReportedRef.current = true
      pushError('tts', message)
    },
    [pushError, suppressTtsQueueForCurrentResponse],
  )

  const stopTtsAudio = useCallback((options: { suppressQueue?: boolean } = {}) => {
    if (options.suppressQueue ?? true) {
      suppressTtsQueueForCurrentResponse()
    }
    ttsLoopIdRef.current += 1

    if (ttsPartialFlushTimerRef.current !== null) {
      window.clearTimeout(ttsPartialFlushTimerRef.current)
      ttsPartialFlushTimerRef.current = null
    }

    ttsCurrentItemRef.current?.controller.abort()
    ttsCurrentItemRef.current = null
    ttsSampleControllerRef.current?.abort()

    for (const item of ttsQueueRef.current) {
      item.controller.abort()
    }
    ttsQueueRef.current = []
    waitingTextsRef.current = []
    setTtsQueueCount(0)

    ttsAudioRef.current?.pause()
    ttsAudioRef.current = null
    ttsPlayingRef.current = false
    ttsAudioActiveRef.current = false
    spokenTextLengthRef.current = 0
    speakableStreamingTextRef.current = ''
    if (ttsAudioUrlRef.current && !ttsAudioUrlFromCacheRef.current) {
      URL.revokeObjectURL(ttsAudioUrlRef.current)
    }
    ttsAudioUrlRef.current = null
    ttsAudioUrlFromCacheRef.current = false
    setTtsSpeaking(false)
    setTtsStatus(ttsEnabledRef.current ? 'ready' : 'off')
  }, [suppressTtsQueueForCurrentResponse])

  useEffect(() => {
    if (ttsConfig?.configured === true) {
      if (!ttsEnabledPreference) return
      const timeoutId = window.setTimeout(() => {
        ttsEnabledRef.current = true
        setTtsStatus((current) => current === 'off' ? 'ready' : current)
      }, 0)
      return () => window.clearTimeout(timeoutId)
    }
    if (!ttsEnabledPreference) return
    const timeoutId = window.setTimeout(() => {
      stopTtsAudio()
      ttsEnabledRef.current = false
      setTtsStatus('off')
      if (ttsConfig?.configured === false) {
        setTtsEnabled(false)
      }
    }, 0)
    return () => window.clearTimeout(timeoutId)
  }, [stopTtsAudio, ttsConfig?.configured, ttsEnabledPreference])

  /** One AudioContext for every PCM clip; created or resumed on the player's own action where possible. */
  const ttsAudioContext = useCallback(() => {
    ttsAudioContextRef.current ??= new AudioContext()
    void ttsAudioContextRef.current.resume()
    return ttsAudioContextRef.current
  }, [])

  const narrationHost = useMemo<TtsNarrationHost>(
    () => ({
      queueRef: ttsQueueRef,
      currentItemRef: ttsCurrentItemRef,
      playingRef: ttsPlayingRef,
      audioActiveRef: ttsAudioActiveRef,
      loopIdRef: ttsLoopIdRef,
      audioRef: ttsAudioRef,
      audioUrlRef: ttsAudioUrlRef,
      audioUrlFromCacheRef: ttsAudioUrlFromCacheRef,
      audioCacheRef: ttsAudioCacheRef,
      enabledRef: ttsEnabledRef,
      softFailureReportedRef: ttsSoftFailureReportedRef,
      setQueueCount: setTtsQueueCount,
      setStatus: setTtsStatus,
      setSpeaking: setTtsSpeaking,
      setLatency: setTtsLatency,
      audioContext: ttsAudioContext,
      suppressQueue: suppressTtsQueueForCurrentResponse,
    }),
    [suppressTtsQueueForCurrentResponse, ttsAudioContext],
  )

  /** The queue, requests and playback: fetched the first time narration is on or a sample is played. */
  const loadNarrationQueue = useCallback(
    () => import('./ttsNarrationQueue').then((narrationQueue) => (narrationQueueRef.current = narrationQueue)),
    [],
  )

  /** What every request is made with, as this render has it. */
  const narrationRequest = useCallback(
    (): TtsNarrationRequest => ({
      enabled: ttsEnabled,
      auth,
      baseUrl,
      voice: ttsVoice,
      accent: ttsAccent,
      quality: ttsQuality,
      // Keyed by the whole request, so a clip is replayed only in the voice it was spoken in.
      audioCacheKey: (requestBody) => scopedTtsKey(`audio:${hashString(requestBody)}:${requestBody.length}`),
      pushError,
      reportFailure: reportTtsFailureForCurrentResponse,
    }),
    [auth, baseUrl, pushError, reportTtsFailureForCurrentResponse, scopedTtsKey, ttsAccent, ttsEnabled, ttsQuality, ttsVoice],
  )

  const toggleTts = () => {
    if (ttsEnabled) {
      stopTtsAudio()
      setTtsEnabled(false)
      setTtsStatus('off')
      return
    }
    if (ttsConfigLoadFailed) {
      pushError('tts', 'Narration configuration could not be checked. Refresh to retry.')
      return
    }
    if (ttsConfig === null) {
      pushError('tts', 'Narration availability is still being checked.')
      return
    }
    if (!ttsConfig.configured) {
      pushError('tts', 'No narrator voice is set up on this server.')
      return
    }
    if (ttsConfig.audio_format?.startsWith('audio/pcm') && typeof AudioContext !== 'undefined') {
      ttsAudioContext()
      void import('./ttsPcmPlayer')
    }
    resetTtsFailureForNextResponse()
    setTtsEnabled(true)
    setTtsStatus('ready')
  }

  const queueTtsText = useCallback(
    (text: string) => {
      if (!ttsEnabled || ttsQueueSuppressedRef.current || ttsFailureReportedRef.current) return
      const narrationQueue = narrationQueueRef.current
      if (narrationQueue) {
        narrationQueue.queueTtsText(narrationHost, narrationRequest(), text)
        return
      }
      waitingTextsRef.current.push(text)
      if (waitingTextsRef.current.length > 1) return
      loadNarrationQueue().then(
        () => {
          const waiting = waitingTextsRef.current
          waitingTextsRef.current = []
          waiting.forEach((waitingText) => queueTtsTextRef.current(waitingText))
        },
        (error: unknown) => {
          waitingTextsRef.current = []
          reportTtsFailureForCurrentResponse(`TTS failed: ${error instanceof Error ? error.message : String(error)}`)
        },
      )
    },
    [loadNarrationQueue, narrationHost, narrationRequest, reportTtsFailureForCurrentResponse, ttsEnabled],
  )

  useEffect(() => {
    queueTtsTextRef.current = queueTtsText
  }, [queueTtsText])

  useEffect(() => {
    if (ttsEnabled) loadNarrationQueue().catch(() => undefined)
  }, [loadNarrationQueue, ttsEnabled])

  const queueTtsNarration = useCallback(
    (text: string) => {
      let remaining = text.replace(/<thought>[\s\S]*?(?:<\/thought>|$)/gi, '').trim()
      while (remaining.trim()) {
        const flushLength = ttsFlushLength(remaining, true) || remaining.length
        queueTtsText(remaining.slice(0, flushLength))
        remaining = remaining.slice(flushLength)
      }
    },
    [queueTtsText],
  )

  const flushStreamingTtsChunks = useCallback(
    (forcePartial = false) => {
      let pending = speakableStreamingTextRef.current.slice(spokenTextLengthRef.current)
      while (pending.trim()) {
        const flushLength = ttsFlushLength(pending, forcePartial)
        if (flushLength <= 0) break

        const chunk = pending.slice(0, flushLength)
        spokenTextLengthRef.current += flushLength
        queueTtsText(chunk)
        pending = speakableStreamingTextRef.current.slice(spokenTextLengthRef.current)
        forcePartial = false
      }
    },
    [queueTtsText],
  )

  useEffect(() => {
    localStorage.setItem('aidm:ttsEnabled', String(ttsEnabledPreference))
  }, [ttsEnabledPreference])

  useEffect(() => {
    localStorage.setItem(TTS_VOICE_STORAGE_KEY, JSON.stringify(ttsVoiceChoice))
  }, [ttsVoiceChoice])

  /** A new choice applies from the next request: a session cannot change voice once audio has started. */
  const chooseTtsVoice = useCallback((choice: TtsVoiceChoice) => {
    ttsSampleControllerRef.current?.abort()
    setTtsVoiceChoice((current) => ({ ...current, ...choice }))
  }, [])

  const playTtsSample = useCallback(() => {
    stopTtsAudio()
    const controller = new AbortController()
    ttsSampleControllerRef.current = controller
    // Still inside the player's click, so the browser lets the audio start.
    ttsAudioContext()
    const request = narrationRequest()
    loadNarrationQueue().then(
      (narrationQueue) => narrationQueue.playTtsSample(narrationHost, request, controller),
      (error: unknown) => {
        if (!controller.signal.aborted) {
          pushError('tts', `Voice sample failed: ${error instanceof Error ? error.message : String(error)}`)
        }
      },
    )
  }, [loadNarrationQueue, narrationHost, narrationRequest, pushError, stopTtsAudio, ttsAudioContext])

  useEffect(() => {
    const cache = ttsAudioCacheRef.current
    return () => {
      stopTtsAudio()
      for (const entry of cache.values()) {
        URL.revokeObjectURL(entry.audioUrl)
      }
      cache.clear()
    }
  }, [stopTtsAudio])

  useEffect(() => {
    ttsEnabledRef.current = ttsEnabled
    queueTtsNarrationRef.current = ttsEnabled ? queueTtsNarration : null
  }, [queueTtsNarration, ttsEnabled])

  useEffect(() => {
    if (!speakableDmEntry || sendPending || streamingTurn) return
    if (lastSpokenDmEntryRef.current === speakableDmEntry.id) return
    const entryTurnId = metadataTurnId(speakableDmEntry.metadata)
    lastSpokenDmEntryRef.current = speakableDmEntry.id
    lastSpokenTextRef.current = speakableDmEntry.text
    if (entryTurnId !== null) {
      lastSpokenTurnIdRef.current = entryTurnId
    }
    rememberSpokenTts(speakableDmEntry.text, entryTurnId)
  }, [
    rememberSpokenTts,
    sendPending,
    speakableDmEntry,
    streamingTurn,
  ])

  const streamingText = streamingTurn?.text ?? ''
  const speakableStreamingText = streamingText
    ? streamingText.replace(/<thought>[\s\S]*?(?:<\/thought>|$)/gi, '')
    : ''

  useEffect(() => {
    speakableStreamingTextRef.current = speakableStreamingText
    if (ttsPartialFlushTimerRef.current !== null) {
      window.clearTimeout(ttsPartialFlushTimerRef.current)
      ttsPartialFlushTimerRef.current = null
    }
    if (!speakableStreamingText) return

    flushStreamingTtsChunks(false)

    const remaining = speakableStreamingTextRef.current.slice(spokenTextLengthRef.current).trim()
    if (remaining.length >= TTS_MIN_PARTIAL_FLUSH_CHARS) {
      ttsPartialFlushTimerRef.current = window.setTimeout(() => {
        ttsPartialFlushTimerRef.current = null
        flushStreamingTtsChunks(true)
      }, TTS_PARTIAL_FLUSH_DELAY_MS)
    }
  }, [flushStreamingTtsChunks, speakableStreamingText])

  const narrationSetting: NarrationSetting = ttsConfig?.configured
    ? {
        enabled: ttsEnabled,
        problem: effectiveTtsStatus === 'failed' ? 'The narration voice could not be played just now.' : '',
        voice:
          ttsVoice && ttsAccent && ttsQuality
            ? {
                voices: ttsVoices,
                voice: ttsVoice,
                accents: ttsAccents,
                accent: ttsAccent,
                qualities: ttsQualities,
                quality: ttsQuality,
                onChange: chooseTtsVoice,
                onPlaySample: playTtsSample,
              }
            : undefined,
      }
    : ttsConfigLoadFailed
      ? { enabled: false, problem: 'Could not check whether narration is available. Refresh to try again.' }
      : ttsConfig
        ? 'none'
        : null

  return {
    narrationSetting,
    ttsEnabled,
    ttsSpeaking,
    effectiveTtsStatus,
    ttsStatusLabel,
    ttsLatencyLabel,
    canStopTts,
    stopTtsAudio,
    toggleTts,
    resetTtsFailureForNextResponse,
    rememberStreamedTtsTurn,
    spokenTextLengthRef,
    speakableStreamingTextRef,
    queueTtsNarrationRef,
    ttsEnabledRef,
    ttsQueueSuppressedRef,
    ttsFailureReportedRef,
    ttsPartialFlushTimerRef,
    lastSpokenDmEntryRef,
    lastSpokenTurnIdRef,
    lastSpokenTextRef,
  }
}
