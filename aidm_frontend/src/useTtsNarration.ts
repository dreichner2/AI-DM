import { useCallback, useEffect, useRef, useState } from 'react'
import { addCookieCsrfHeader, addNgrokBrowserWarningBypassHeader, addWorkspaceTokenHeader, normalizeBaseUrl } from './api'
import { metadataTurnId, stripMarkdown } from './gameSelectors'
import type { JsonRecord, StreamingTurn, TimelineEntry, TtsRuntimeConfig } from './types'

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

const TTS_AUDIO_MIME = 'audio/mpeg'
const TTS_MIN_PARTIAL_FLUSH_CHARS = 60
const TTS_FORCE_PARTIAL_FLUSH_CHARS = 320
const TTS_PARTIAL_FLUSH_DELAY_MS = 650
const TTS_RECENT_TEXT_DEDUPE_MS = 120_000
const TTS_AUDIO_CACHE_MAX_ENTRIES = 16
const TTS_AUDIO_CACHE_TTL_MS = 10 * 60_000
const TTS_AUDIO_REQUEST_RETRIES = 1
const TTS_AUDIO_RETRY_DELAY_MS = 250
const TTS_ENDPOINTS = ['/api/tts/stream', '/api/tts/speak']
const TTS_MEDIA_SOURCE_STREAMING_ENABLED = false

type TtsQueueItem = {
  text: string
  loadAudioUrl: () => Promise<string | null>
  controller: AbortController
  failed?: boolean
  cleanup?: () => void
  streamDone?: Promise<void>
  audioUrlPromise?: Promise<string | null>
  cacheKey?: string
  cacheAudioUrlPromise?: Promise<string | null>
  cacheCandidateUrl?: string
  fromCache?: boolean
  failureMessage?: string
  requestedAt: number
}

type TtsLatencySnapshot = {
  source: 'network' | 'cache'
  requestMs: number | null
  playStartMs: number | null
  totalMs: number | null
  textLength: number
}

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

function supportsStreamingTtsAudio() {
  return (
    TTS_MEDIA_SOURCE_STREAMING_ENABLED &&
    typeof MediaSource !== 'undefined' &&
    MediaSource.isTypeSupported(TTS_AUDIO_MIME)
  )
}

async function ttsErrorMessage(response: Response) {
  let message = `TTS request failed with status ${response.status}`
  try {
    const payload = await response.json()
    if (payload && typeof payload === 'object' && 'error' in payload) {
      message = String((payload as JsonRecord).error)
    }
  } catch {
    // Keep the status-based message when the server did not return JSON.
  }
  return message
}

function ttsPlaybackErrorMessage(error: unknown) {
  if (error instanceof Error && error.message) return error.message
  if (error instanceof Event) {
    const target = error.target
    if (target instanceof HTMLMediaElement && target.error) {
      const mediaError = target.error
      const code = mediaError.code
      const detail =
        code === 1
          ? 'aborted'
          : code === 2
            ? 'network error'
            : code === 3
              ? 'decode error'
              : code === 4
                ? 'unsupported audio source'
                : 'unknown media error'
      return `Audio ${detail}`
    }
    return error.type ? `Audio ${error.type}` : 'Audio playback error'
  }
  return String(error || 'Audio playback error')
}

function isTtsUserActivationError(message: string) {
  const normalized = message.toLowerCase()
  return normalized.includes("user didn't interact") || normalized.includes('user activation')
}

function waitForSourceBufferIdle(sourceBuffer: SourceBuffer, signal: AbortSignal) {
  if (!sourceBuffer.updating) return Promise.resolve()

  return new Promise<void>((resolve, reject) => {
    const cleanup = () => {
      sourceBuffer.removeEventListener('updateend', handleUpdateEnd)
      sourceBuffer.removeEventListener('error', handleError)
      signal.removeEventListener('abort', handleAbort)
    }
    const handleUpdateEnd = () => {
      cleanup()
      resolve()
    }
    const handleError = () => {
      cleanup()
      reject(new Error('TTS audio buffer failed to update.'))
    }
    const handleAbort = () => {
      cleanup()
      reject(new Error('TTS request aborted.'))
    }

    sourceBuffer.addEventListener('updateend', handleUpdateEnd)
    sourceBuffer.addEventListener('error', handleError)
    signal.addEventListener('abort', handleAbort, { once: true })
  })
}

function exactArrayBuffer(bytes: Uint8Array) {
  const copy = new Uint8Array(bytes.byteLength)
  copy.set(bytes)
  return copy.buffer
}

async function appendTtsAudioChunk(
  sourceBuffer: SourceBuffer,
  bytes: Uint8Array,
  signal: AbortSignal,
) {
  if (signal.aborted) throw new Error('TTS request aborted.')
  await waitForSourceBufferIdle(sourceBuffer, signal)
  sourceBuffer.appendBuffer(exactArrayBuffer(bytes))
  await waitForSourceBufferIdle(sourceBuffer, signal)
}

function copyTtsAudioBytes(bytes: Uint8Array) {
  const copy = new Uint8Array(bytes.byteLength)
  copy.set(bytes)
  return copy.buffer
}

async function cancelResponseBody(response: Response) {
  try {
    await response.body?.cancel()
  } catch {
    // Best-effort cleanup before retrying the legacy TTS endpoint.
  }
}

async function fetchTtsAudio(
  baseUrl: string,
  headers: Headers,
  cleanText: string,
  signal: AbortSignal,
) {
  const rootUrl = normalizeBaseUrl(baseUrl)
  const requestBody = JSON.stringify({ text: cleanText })

  for (let index = 0; index < TTS_ENDPOINTS.length; index += 1) {
    const endpoint = TTS_ENDPOINTS[index]
    try {
      const response = await fetch(`${rootUrl}${endpoint}`, {
        method: 'POST',
        headers,
        body: requestBody,
        signal,
      })
      const shouldTryLegacyEndpoint = response.status === 404 || response.status === 405
      if (!shouldTryLegacyEndpoint || index === TTS_ENDPOINTS.length - 1) {
        return response
      }
      await cancelResponseBody(response)
    } catch (error) {
      if (error instanceof DOMException && error.name === 'AbortError') {
        throw error
      }
      throw error
    }
  }

  throw new Error('TTS request failed.')
}

function waitForTtsRetry(delayMs: number, signal: AbortSignal) {
  if (signal.aborted) {
    return Promise.reject(new DOMException('TTS request aborted.', 'AbortError'))
  }

  return new Promise<void>((resolve, reject) => {
    const timeoutId = window.setTimeout(() => {
      signal.removeEventListener('abort', handleAbort)
      resolve()
    }, delayMs)

    const handleAbort = () => {
      window.clearTimeout(timeoutId)
      signal.removeEventListener('abort', handleAbort)
      reject(new DOMException('TTS request aborted.', 'AbortError'))
    }

    signal.addEventListener('abort', handleAbort, { once: true })
  })
}

async function fetchTtsAudioWithRetry(
  baseUrl: string,
  headers: Headers,
  cleanText: string,
  signal: AbortSignal,
) {
  for (let attempt = 0; attempt <= TTS_AUDIO_REQUEST_RETRIES; attempt += 1) {
    try {
      const response = await fetchTtsAudio(baseUrl, headers, cleanText, signal)
      const shouldRetry = !response.ok && response.status >= 500 && attempt < TTS_AUDIO_REQUEST_RETRIES
      if (!shouldRetry) return response
      await cancelResponseBody(response)
    } catch (error) {
      if (error instanceof DOMException && error.name === 'AbortError') {
        throw error
      }
      if (attempt >= TTS_AUDIO_REQUEST_RETRIES) {
        throw error
      }
    }

    await waitForTtsRetry(TTS_AUDIO_RETRY_DELAY_MS, signal)
  }

  throw new Error('TTS request failed.')
}

function createStreamingTtsSource(
  requestAudio: () => Promise<Response>,
  controller: AbortController,
  onError: (message: string) => void,
) {
  if (!supportsStreamingTtsAudio()) return null

  const mediaSource = new MediaSource()
  const audioUrl = URL.createObjectURL(mediaSource)
  let sourceBuffer: SourceBuffer | null = null
  let cleaned = false
  const audioChunks: ArrayBuffer[] = []
  let streamFailed = false
  let resolveCachedAudio: (value: string | null) => void = () => undefined
  const cachedAudioUrlPromise = new Promise<string | null>((resolve) => {
    resolveCachedAudio = resolve
  })

  const cleanup = () => {
    if (cleaned) return
    cleaned = true
    try {
      if (sourceBuffer?.updating) {
        sourceBuffer.abort()
      }
      if (mediaSource.readyState === 'open') {
        mediaSource.endOfStream()
      }
    } catch {
      // The audio element may already have torn the MediaSource down.
    }
    URL.revokeObjectURL(audioUrl)
  }

  const streamDone = new Promise<void>((resolve) => {
    mediaSource.addEventListener(
      'sourceopen',
      () => {
        void (async () => {
          try {
            sourceBuffer = mediaSource.addSourceBuffer(TTS_AUDIO_MIME)
            sourceBuffer.mode = 'sequence'

            const response = await requestAudio()
            if (!response.ok) {
              throw new Error(await ttsErrorMessage(response))
            }

            const reader = response.body?.getReader()
            if (!reader) {
              const bytes = new Uint8Array(await response.arrayBuffer())
              audioChunks.push(copyTtsAudioBytes(bytes))
              await appendTtsAudioChunk(sourceBuffer, bytes, controller.signal)
            } else {
              while (true) {
                const { done, value } = await reader.read()
                if (done) break
                if (value) {
                  audioChunks.push(copyTtsAudioBytes(value))
                  await appendTtsAudioChunk(sourceBuffer, value, controller.signal)
                }
              }
            }

            await waitForSourceBufferIdle(sourceBuffer, controller.signal)
            if (mediaSource.readyState === 'open') {
              mediaSource.endOfStream()
            }
          } catch (error) {
            streamFailed = true
            if (!controller.signal.aborted) {
              onError(error instanceof Error ? error.message : String(error))
              try {
                if (mediaSource.readyState === 'open') {
                  mediaSource.endOfStream('network')
                }
              } catch {
                // Ignore cleanup errors after a failed stream.
              }
            }
          } finally {
            if (!streamFailed && !controller.signal.aborted && audioChunks.length) {
              resolveCachedAudio(URL.createObjectURL(new Blob(audioChunks, { type: TTS_AUDIO_MIME })))
            } else {
              resolveCachedAudio(null)
            }
            resolve()
          }
        })()
      },
      { once: true },
    )
  })

  return {
    audioUrl,
    cleanup,
    streamDone,
    cachedAudioUrlPromise,
  }
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
  const ttsAudioRef = useRef<HTMLAudioElement | null>(null)
  const ttsAudioUrlRef = useRef<string | null>(null)
  const ttsAudioUrlFromCacheRef = useRef(false)
  const ttsAudioCacheRef = useRef<Map<string, { audioUrl: string; createdAt: number; lastUsedAt: number }>>(new Map())
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
    ttsCurrentItemRef.current?.cleanup?.()
    ttsCurrentItemRef.current = null

    for (const item of ttsQueueRef.current) {
      item.controller.abort()
      item.cleanup?.()
    }
    ttsQueueRef.current = []
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
      pushError('tts', 'Deepgram TTS is not configured on the backend.')
      return
    }
    resetTtsFailureForNextResponse()
    setTtsEnabled(true)
    setTtsStatus('ready')
  }

  const rememberCachedTtsAudio = useCallback((cacheKey: string, audioUrl: string) => {
    const now = Date.now()
    ttsAudioCacheRef.current.set(cacheKey, { audioUrl, createdAt: now, lastUsedAt: now })
    const entries = [...ttsAudioCacheRef.current.entries()]
    for (const [key, entry] of entries) {
      if (now - entry.createdAt > TTS_AUDIO_CACHE_TTL_MS) {
        URL.revokeObjectURL(entry.audioUrl)
        ttsAudioCacheRef.current.delete(key)
      }
    }
    if (ttsAudioCacheRef.current.size > TTS_AUDIO_CACHE_MAX_ENTRIES) {
      const sorted = [...ttsAudioCacheRef.current.entries()].sort(
        (left, right) => left[1].lastUsedAt - right[1].lastUsedAt,
      )
      for (const [key, entry] of sorted.slice(0, ttsAudioCacheRef.current.size - TTS_AUDIO_CACHE_MAX_ENTRIES)) {
        URL.revokeObjectURL(entry.audioUrl)
        ttsAudioCacheRef.current.delete(key)
      }
    }
  }, [])

  const ensureTtsItemAudioUrl = useCallback((item: TtsQueueItem) => {
    if (!item.audioUrlPromise) {
      item.audioUrlPromise = Promise.resolve()
        .then(() => item.loadAudioUrl())
        .catch((error: unknown) => {
          if (!item.controller.signal.aborted) {
            item.failed = true
            item.failureMessage = error instanceof Error ? error.message : String(error)
          }
          return null
        })
    }
    return item.audioUrlPromise
  }, [])

  const prefetchNextQueuedTtsItem = useCallback(() => {
    const nextItem = ttsQueueRef.current[0]
    if (!nextItem || nextItem.fromCache || nextItem.controller.signal.aborted) return
    void ensureTtsItemAudioUrl(nextItem)
  }, [ensureTtsItemAudioUrl])

  const processTtsQueue = useCallback(async () => {
    if (ttsPlayingRef.current || ttsQueueRef.current.length === 0 || !ttsEnabled) return
    ttsPlayingRef.current = true

    ttsLoopIdRef.current += 1
    const currentLoopId = ttsLoopIdRef.current
    let loopFailed = false
    let playedAnyItem = false

    const clearPendingTtsQueue = () => {
      for (const pendingItem of ttsQueueRef.current) {
        pendingItem.controller.abort()
        pendingItem.cleanup?.()
      }
      ttsQueueRef.current = []
      setTtsQueueCount(0)
    }

    while (ttsQueueRef.current.length > 0) {
      if (!ttsEnabled || ttsLoopIdRef.current !== currentLoopId) break

      const item = ttsQueueRef.current.shift()
      if (!item) continue
      setTtsQueueCount(ttsQueueRef.current.length)
      ttsCurrentItemRef.current = item

      setTtsSpeaking(false)
      setTtsStatus('requesting')
      let audioUrl: string | null = null
      let audioReadyAt: number | null = null

      try {
        audioUrl = await ensureTtsItemAudioUrl(item)
        audioReadyAt = performance.now()

        if (item.failed) {
          if (playedAnyItem) {
            if (!ttsSoftFailureReportedRef.current) {
              ttsSoftFailureReportedRef.current = true
              pushError('tts', `TTS skipped a narration chunk: ${item.failureMessage ?? 'audio request failed'}`)
            }
            continue
          } else {
            loopFailed = true
            clearPendingTtsQueue()
            reportTtsFailureForCurrentResponse(`TTS failed: ${item.failureMessage ?? 'audio request failed'}`)
          }
          break
        }

        setTtsLatency({
          source: item.fromCache ? 'cache' : 'network',
          requestMs: item.fromCache ? 0 : audioReadyAt - item.requestedAt,
          playStartMs: null,
          totalMs: null,
          textLength: item.text.length,
        })

        if (ttsLoopIdRef.current !== currentLoopId || item.controller.signal.aborted) {
          if (audioUrl && !item.fromCache) URL.revokeObjectURL(audioUrl)
          break
        }

        if (!audioUrl) continue

        const audio = new Audio(audioUrl)
        audio.preload = 'auto'
        ttsAudioRef.current = audio
        ttsAudioUrlRef.current = audioUrl
        ttsAudioUrlFromCacheRef.current = Boolean(item.fromCache)
        setTtsSpeaking(true)
        setTtsStatus('speaking')
        const playRequestedAt = performance.now()
        setTtsLatency((current) =>
          current
            ? {
                ...current,
                playStartMs: audioReadyAt === null ? null : playRequestedAt - item.requestedAt,
              }
            : current,
        )

        await new Promise<void>((resolve, reject) => {
          audio.onended = () => resolve()
          audio.onerror = (e) => reject(e)
          audio.onpause = () => resolve()
          audio
            .play()
            .then(() => {
              ttsAudioActiveRef.current = true
              prefetchNextQueuedTtsItem()
            })
            .catch(reject)
        })
        playedAnyItem = true

        await item.streamDone?.catch(() => undefined)
        const cachedAudioUrl = item.cacheCandidateUrl ?? (await item.cacheAudioUrlPromise?.catch(() => null))
        if (cachedAudioUrl && item.cacheKey) {
          rememberCachedTtsAudio(item.cacheKey, cachedAudioUrl)
          item.cacheCandidateUrl = undefined
        }
        setTtsLatency((current) =>
          current
            ? {
                ...current,
                totalMs: performance.now() - item.requestedAt,
              }
            : current,
        )
        if (!item.fromCache && ttsAudioUrlRef.current === audioUrl) {
          URL.revokeObjectURL(audioUrl)
          ttsAudioUrlRef.current = null
          ttsAudioUrlFromCacheRef.current = false
        } else if (item.fromCache && ttsAudioUrlRef.current === audioUrl) {
          ttsAudioUrlRef.current = null
          ttsAudioUrlFromCacheRef.current = false
        }
      } catch (error) {
        if (!item.controller.signal.aborted && ttsLoopIdRef.current === currentLoopId) {
          item.controller.abort()
          setTtsSpeaking(false)
          const message = ttsPlaybackErrorMessage(error)
          if (isTtsUserActivationError(message)) {
            suppressTtsQueueForCurrentResponse()
            clearPendingTtsQueue()
            setTtsStatus('ready')
          } else {
            loopFailed = true
            clearPendingTtsQueue()
            reportTtsFailureForCurrentResponse(`TTS playback failed: ${message}`)
          }
        }
        if (audioUrl && !item.fromCache && ttsAudioUrlRef.current === audioUrl) {
          URL.revokeObjectURL(audioUrl)
          ttsAudioUrlRef.current = null
          ttsAudioUrlFromCacheRef.current = false
        } else if (item.fromCache && ttsAudioUrlRef.current === audioUrl) {
          ttsAudioUrlRef.current = null
          ttsAudioUrlFromCacheRef.current = false
        }
      } finally {
        ttsAudioActiveRef.current = false
        item.cleanup?.()
        if (item.cacheCandidateUrl) {
          URL.revokeObjectURL(item.cacheCandidateUrl)
          item.cacheCandidateUrl = undefined
        }
        if (ttsCurrentItemRef.current === item) {
          ttsCurrentItemRef.current = null
        }
      }

      if (loopFailed) break
    }

    if (ttsLoopIdRef.current === currentLoopId) {
      setTtsSpeaking(false)
      ttsPlayingRef.current = false
      ttsAudioActiveRef.current = false
      ttsAudioRef.current = null
      setTtsQueueCount(ttsQueueRef.current.length)
      if (ttsQueueRef.current.length === 0 && ttsEnabledRef.current) {
        setTtsStatus(loopFailed ? 'failed' : 'ready')
      }
    }
  }, [
    ensureTtsItemAudioUrl,
    prefetchNextQueuedTtsItem,
    pushError,
    rememberCachedTtsAudio,
    reportTtsFailureForCurrentResponse,
    suppressTtsQueueForCurrentResponse,
    ttsEnabled,
  ])

  const queueTtsText = useCallback(
    (text: string) => {
      if (!ttsEnabled || ttsQueueSuppressedRef.current || ttsFailureReportedRef.current) return
      let cleanText = text.replace(/<thought>[\s\S]*?(?:<\/thought>|$)/gi, '')
      cleanText = stripMarkdown(cleanText).replace(/\s+/g, ' ').trim()
      if (!cleanText) return

      const controller = new AbortController()
      const requestedAt = performance.now()
      const cacheKey = scopedTtsKey(`audio:${hashString(cleanText)}:${cleanText.length}`)
      const cachedEntry = ttsAudioCacheRef.current.get(cacheKey)
      if (cachedEntry && Date.now() - cachedEntry.createdAt <= TTS_AUDIO_CACHE_TTL_MS) {
        cachedEntry.lastUsedAt = Date.now()
        ttsQueueRef.current.push({
          text: cleanText,
          loadAudioUrl: () => Promise.resolve(cachedEntry.audioUrl),
          controller,
          fromCache: true,
          requestedAt,
          cacheKey,
        })
        setTtsQueueCount(ttsQueueRef.current.length)
        setTtsStatus('queued')
        void processTtsQueue()
        return
      }
      if (cachedEntry) {
        URL.revokeObjectURL(cachedEntry.audioUrl)
        ttsAudioCacheRef.current.delete(cacheKey)
      }

      const item: TtsQueueItem = {
        text: cleanText,
        loadAudioUrl: () => Promise.resolve(null),
        controller,
        cacheKey,
        fromCache: false,
        requestedAt,
      }
      const headers = new Headers({ 'Content-Type': 'application/json' })
      if (auth) {
        headers.set('Authorization', `Bearer ${auth}`)
      }
      addWorkspaceTokenHeader(headers)
      addCookieCsrfHeader(headers)
      addNgrokBrowserWarningBypassHeader(headers, baseUrl)

      const requestAudio = () => fetchTtsAudioWithRetry(baseUrl, headers, cleanText, controller.signal)

      const reportTtsError = (message: string) => {
        if (controller.signal.aborted || item.failed) return
        item.failed = true
        item.failureMessage = message
      }

      item.loadAudioUrl = () => {
        const streamedSource = createStreamingTtsSource(requestAudio, controller, reportTtsError)
        item.cleanup = streamedSource?.cleanup
        item.streamDone = streamedSource?.streamDone
        item.cacheAudioUrlPromise = streamedSource?.cachedAudioUrlPromise
        if (streamedSource?.audioUrl) {
          return Promise.resolve(streamedSource.audioUrl)
        }
        return requestAudio()
          .then(async (response) => {
            if (!response.ok) {
              reportTtsError(await ttsErrorMessage(response))
              return null
            }
            const blob = await response.blob()
            if (controller.signal.aborted) return null
            if (blob.size === 0) {
              reportTtsError('Deepgram returned an empty audio response.')
              return null
            }
            const playbackUrl = URL.createObjectURL(blob)
            item.cacheCandidateUrl = URL.createObjectURL(blob)
            return playbackUrl
          })
          .catch((error) => {
            if (error.name !== 'AbortError') {
              reportTtsError(error instanceof Error ? error.message : String(error))
            }
            return null
          })
      }

      ttsQueueRef.current.push(item)
      setTtsQueueCount(ttsQueueRef.current.length)
      setTtsStatus('queued')
      if (ttsAudioActiveRef.current) {
        prefetchNextQueuedTtsItem()
      }
      void processTtsQueue()
    },
    [auth, baseUrl, prefetchNextQueuedTtsItem, processTtsQueue, scopedTtsKey, ttsEnabled],
  )

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

  return {
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
