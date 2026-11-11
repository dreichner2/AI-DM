import type { Dispatch, MutableRefObject, SetStateAction } from 'react'
import { addCookieCsrfHeader, addNgrokBrowserWarningBypassHeader, addWorkspaceTokenHeader, normalizeBaseUrl } from './api'
import { stripMarkdown } from './gameSelectors'
import type { JsonRecord } from './types'
import type { TtsPlaybackStatus } from './useTtsNarration'

// What runs only while narration is on: the queue, its requests with their retry, and audio
// playback. useTtsNarration imports it the first time narration is on or a sample is played.

const TTS_AUDIO_CACHE_MAX_ENTRIES = 16
const TTS_AUDIO_CACHE_TTL_MS = 10 * 60_000
const TTS_AUDIO_REQUEST_RETRIES = 1
const TTS_AUDIO_RETRY_DELAY_MS = 250
const TTS_ENDPOINTS = ['/api/tts/stream', '/api/tts/speak']
const TTS_SAMPLE_TEXT = 'The road remembers every name it was given.'

export type TtsQueueItem = {
  text: string
  loadAudioUrl: () => Promise<string | null>
  /** Set instead of an audio URL when the server streams raw PCM. */
  pcmResponse?: Response
  controller: AbortController
  failed?: boolean
  audioUrlPromise?: Promise<string | null>
  cacheKey?: string
  cacheCandidateUrl?: string
  fromCache?: boolean
  failureMessage?: string
  requestedAt: number
}

export type TtsLatencySnapshot = {
  source: 'network' | 'cache'
  requestMs: number | null
  playStartMs: number | null
  totalMs: number | null
  textLength: number
}

export type TtsCachedAudio = { audioUrl: string; createdAt: number; lastUsedAt: number }

/** The hook's playback state, the same object for the hook's whole life. */
export type TtsNarrationHost = {
  queueRef: MutableRefObject<TtsQueueItem[]>
  currentItemRef: MutableRefObject<TtsQueueItem | null>
  playingRef: MutableRefObject<boolean>
  audioActiveRef: MutableRefObject<boolean>
  loopIdRef: MutableRefObject<number>
  audioRef: MutableRefObject<HTMLAudioElement | null>
  audioUrlRef: MutableRefObject<string | null>
  audioUrlFromCacheRef: MutableRefObject<boolean>
  audioCacheRef: MutableRefObject<Map<string, TtsCachedAudio>>
  enabledRef: MutableRefObject<boolean>
  softFailureReportedRef: MutableRefObject<boolean>
  setQueueCount: (count: number) => void
  setStatus: (status: TtsPlaybackStatus) => void
  setSpeaking: (speaking: boolean) => void
  setLatency: Dispatch<SetStateAction<TtsLatencySnapshot | null>>
  audioContext: () => AudioContext
  suppressQueue: () => void
}

/** The render's settings each request is made with. */
export type TtsNarrationRequest = {
  enabled: boolean
  auth: string
  baseUrl: string
  voice?: string
  accent?: string
  quality?: string
  /** The audio cache key for a request body, scoped to the session. */
  audioCacheKey: (requestBody: string) => string
  pushError: (category: 'tts', message: string) => void
  reportFailure: (message: string) => void
}

function ttsRequestHeaders(auth: string, baseUrl: string) {
  const headers = new Headers({ 'Content-Type': 'application/json' })
  if (auth) {
    headers.set('Authorization', `Bearer ${auth}`)
  }
  addWorkspaceTokenHeader(headers)
  addCookieCsrfHeader(headers)
  addNgrokBrowserWarningBypassHeader(headers, baseUrl)
  return headers
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
  requestBody: string,
  signal: AbortSignal,
) {
  const rootUrl = normalizeBaseUrl(baseUrl)

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
  requestBody: string,
  signal: AbortSignal,
) {
  for (let attempt = 0; attempt <= TTS_AUDIO_REQUEST_RETRIES; attempt += 1) {
    try {
      const response = await fetchTtsAudio(baseUrl, headers, requestBody, signal)
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

async function playTtsPcm(host: TtsNarrationHost, response: Response, signal: AbortSignal) {
  const context = host.audioContext()
  const { playPcmResponse } = await import('./ttsPcmPlayer')
  return playPcmResponse(context, response, signal)
}

function rememberCachedTtsAudio(host: TtsNarrationHost, cacheKey: string, audioUrl: string) {
  const cache = host.audioCacheRef.current
  const now = Date.now()
  cache.set(cacheKey, { audioUrl, createdAt: now, lastUsedAt: now })
  const entries = [...cache.entries()]
  for (const [key, entry] of entries) {
    if (now - entry.createdAt > TTS_AUDIO_CACHE_TTL_MS) {
      URL.revokeObjectURL(entry.audioUrl)
      cache.delete(key)
    }
  }
  if (cache.size > TTS_AUDIO_CACHE_MAX_ENTRIES) {
    const sorted = [...cache.entries()].sort(
      (left, right) => left[1].lastUsedAt - right[1].lastUsedAt,
    )
    for (const [key, entry] of sorted.slice(0, cache.size - TTS_AUDIO_CACHE_MAX_ENTRIES)) {
      URL.revokeObjectURL(entry.audioUrl)
      cache.delete(key)
    }
  }
}

function ensureTtsItemAudioUrl(item: TtsQueueItem) {
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
}

function prefetchNextQueuedTtsItem(host: TtsNarrationHost) {
  const nextItem = host.queueRef.current[0]
  if (!nextItem || nextItem.fromCache || nextItem.controller.signal.aborted) return
  void ensureTtsItemAudioUrl(nextItem)
}

async function processTtsQueue(host: TtsNarrationHost, request: TtsNarrationRequest) {
  if (host.playingRef.current || host.queueRef.current.length === 0 || !request.enabled) return
  host.playingRef.current = true

  host.loopIdRef.current += 1
  const currentLoopId = host.loopIdRef.current
  let loopFailed = false
  let playedAnyItem = false

  const clearPendingTtsQueue = () => {
    for (const pendingItem of host.queueRef.current) {
      pendingItem.controller.abort()
    }
    host.queueRef.current = []
    host.setQueueCount(0)
  }

  while (host.queueRef.current.length > 0) {
    if (!request.enabled || host.loopIdRef.current !== currentLoopId) break

    const item = host.queueRef.current.shift()
    if (!item) continue
    host.setQueueCount(host.queueRef.current.length)
    host.currentItemRef.current = item

    host.setSpeaking(false)
    host.setStatus('requesting')
    let audioUrl: string | null = null
    let audioReadyAt: number | null = null

    try {
      audioUrl = await ensureTtsItemAudioUrl(item)
      audioReadyAt = performance.now()

      if (item.failed) {
        if (playedAnyItem) {
          if (!host.softFailureReportedRef.current) {
            host.softFailureReportedRef.current = true
            request.pushError('tts', `TTS skipped a narration chunk: ${item.failureMessage ?? 'audio request failed'}`)
          }
          continue
        } else {
          loopFailed = true
          clearPendingTtsQueue()
          request.reportFailure(`TTS failed: ${item.failureMessage ?? 'audio request failed'}`)
        }
        break
      }

      host.setLatency({
        source: item.fromCache ? 'cache' : 'network',
        requestMs: item.fromCache ? 0 : audioReadyAt - item.requestedAt,
        playStartMs: null,
        totalMs: null,
        textLength: item.text.length,
      })

      if (host.loopIdRef.current !== currentLoopId || item.controller.signal.aborted) {
        if (audioUrl && !item.fromCache) URL.revokeObjectURL(audioUrl)
        break
      }

      if (item.pcmResponse) {
        const playback = await playTtsPcm(host, item.pcmResponse, item.controller.signal)
        host.setSpeaking(true)
        host.setStatus('speaking')
        host.setLatency((current) =>
          current ? { ...current, playStartMs: performance.now() - item.requestedAt } : current,
        )
        host.audioActiveRef.current = true
        prefetchNextQueuedTtsItem(host)
        const wav = await playback.done
        playedAnyItem = true
        if (wav && item.cacheKey) rememberCachedTtsAudio(host, item.cacheKey, URL.createObjectURL(wav))
        host.setLatency((current) =>
          current ? { ...current, totalMs: performance.now() - item.requestedAt } : current,
        )
        continue
      }

      if (!audioUrl) continue

      const audio = new Audio(audioUrl)
      audio.preload = 'auto'
      host.audioRef.current = audio
      host.audioUrlRef.current = audioUrl
      host.audioUrlFromCacheRef.current = Boolean(item.fromCache)
      host.setSpeaking(true)
      host.setStatus('speaking')
      const playRequestedAt = performance.now()
      host.setLatency((current) =>
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
            host.audioActiveRef.current = true
            prefetchNextQueuedTtsItem(host)
          })
          .catch(reject)
      })
      playedAnyItem = true

      if (item.cacheCandidateUrl && item.cacheKey) {
        rememberCachedTtsAudio(host, item.cacheKey, item.cacheCandidateUrl)
        item.cacheCandidateUrl = undefined
      }
      host.setLatency((current) =>
        current
          ? {
              ...current,
              totalMs: performance.now() - item.requestedAt,
            }
          : current,
      )
      if (!item.fromCache && host.audioUrlRef.current === audioUrl) {
        URL.revokeObjectURL(audioUrl)
        host.audioUrlRef.current = null
        host.audioUrlFromCacheRef.current = false
      } else if (item.fromCache && host.audioUrlRef.current === audioUrl) {
        host.audioUrlRef.current = null
        host.audioUrlFromCacheRef.current = false
      }
    } catch (error) {
      if (!item.controller.signal.aborted && host.loopIdRef.current === currentLoopId) {
        item.controller.abort()
        host.setSpeaking(false)
        const message = ttsPlaybackErrorMessage(error)
        if (isTtsUserActivationError(message)) {
          host.suppressQueue()
          clearPendingTtsQueue()
          host.setStatus('ready')
        } else {
          loopFailed = true
          clearPendingTtsQueue()
          request.reportFailure(`TTS playback failed: ${message}`)
        }
      }
      if (audioUrl && !item.fromCache && host.audioUrlRef.current === audioUrl) {
        URL.revokeObjectURL(audioUrl)
        host.audioUrlRef.current = null
        host.audioUrlFromCacheRef.current = false
      } else if (item.fromCache && host.audioUrlRef.current === audioUrl) {
        host.audioUrlRef.current = null
        host.audioUrlFromCacheRef.current = false
      }
    } finally {
      host.audioActiveRef.current = false
      if (item.cacheCandidateUrl) {
        URL.revokeObjectURL(item.cacheCandidateUrl)
        item.cacheCandidateUrl = undefined
      }
      if (host.currentItemRef.current === item) {
        host.currentItemRef.current = null
      }
    }

    if (loopFailed) break
  }

  if (host.loopIdRef.current === currentLoopId) {
    host.setSpeaking(false)
    host.playingRef.current = false
    host.audioActiveRef.current = false
    host.audioRef.current = null
    host.setQueueCount(host.queueRef.current.length)
    if (host.queueRef.current.length === 0 && host.enabledRef.current) {
      host.setStatus(loopFailed ? 'failed' : 'ready')
    }
  }
}

/** Queues one stretch of narration: from the audio cache, else as a request made ahead of its turn. */
export function queueTtsText(host: TtsNarrationHost, request: TtsNarrationRequest, text: string) {
  let cleanText = text.replace(/<thought>[\s\S]*?(?:<\/thought>|$)/gi, '')
  cleanText = stripMarkdown(cleanText).replace(/\s+/g, ' ').trim()
  if (!cleanText) return

  const controller = new AbortController()
  const requestedAt = performance.now()
  const requestBody = JSON.stringify({ text: cleanText, voice: request.voice, accent: request.accent, quality: request.quality })
  const cacheKey = request.audioCacheKey(requestBody)
  const cachedEntry = host.audioCacheRef.current.get(cacheKey)
  if (cachedEntry && Date.now() - cachedEntry.createdAt <= TTS_AUDIO_CACHE_TTL_MS) {
    cachedEntry.lastUsedAt = Date.now()
    host.queueRef.current.push({
      text: cleanText,
      loadAudioUrl: () => Promise.resolve(cachedEntry.audioUrl),
      controller,
      fromCache: true,
      requestedAt,
      cacheKey,
    })
    host.setQueueCount(host.queueRef.current.length)
    host.setStatus('queued')
    void processTtsQueue(host, request)
    return
  }
  if (cachedEntry) {
    URL.revokeObjectURL(cachedEntry.audioUrl)
    host.audioCacheRef.current.delete(cacheKey)
  }

  const item: TtsQueueItem = {
    text: cleanText,
    loadAudioUrl: () => Promise.resolve(null),
    controller,
    cacheKey,
    fromCache: false,
    requestedAt,
  }
  const headers = ttsRequestHeaders(request.auth, request.baseUrl)
  const requestAudio = () => fetchTtsAudioWithRetry(request.baseUrl, headers, requestBody, controller.signal)

  const reportTtsError = (message: string) => {
    if (controller.signal.aborted || item.failed) return
    item.failed = true
    item.failureMessage = message
  }

  item.loadAudioUrl = () =>
    requestAudio()
      .then(async (response) => {
        if (!response.ok) {
          reportTtsError(await ttsErrorMessage(response))
          return null
        }
        if (response.headers.get('Content-Type')?.startsWith('audio/pcm')) {
          item.pcmResponse = response
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

  host.queueRef.current.push(item)
  host.setQueueCount(host.queueRef.current.length)
  host.setStatus('queued')
  if (host.audioActiveRef.current) {
    prefetchNextQueuedTtsItem(host)
  }
  void processTtsQueue(host, request)
}

/** Plays the sample line in the chosen voice; a failure is reported unless the sample was stopped. */
export function playTtsSample(host: TtsNarrationHost, request: TtsNarrationRequest, controller: AbortController) {
  const requestBody = JSON.stringify({ text: TTS_SAMPLE_TEXT, voice: request.voice, accent: request.accent, quality: request.quality })
  return fetchTtsAudio(request.baseUrl, ttsRequestHeaders(request.auth, request.baseUrl), requestBody, controller.signal)
    .then(async (response) => {
      if (!response.ok) throw new Error(await ttsErrorMessage(response))
      await (await playTtsPcm(host, response, controller.signal)).done
    })
    .catch((error: unknown) => {
      if (!controller.signal.aborted) request.pushError('tts', `Voice sample failed: ${ttsPlaybackErrorMessage(error)}`)
    })
}
