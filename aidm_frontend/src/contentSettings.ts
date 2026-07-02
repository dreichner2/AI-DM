export type ContentRating = 'standard' | 'mature' | 'unrestricted'

export type ContentSettings = {
  contentRating: ContentRating
  toneTags: string[]
  updatedAt: string | null
}

export const CONTENT_RATING_OPTIONS: Array<{
  value: ContentRating
  label: string
}> = [
  { value: 'standard', label: 'Standard' },
  { value: 'mature', label: 'Mature' },
  { value: 'unrestricted', label: 'Unrestricted' },
]

export const CONTENT_TONE_TAG_OPTIONS = [
  'heroic',
  'hopeful',
  'grim',
  'horror',
  'whimsical',
  'comedic',
  'noir',
  'mystery',
  'political',
  'pulpy',
  'tragic',
  'romantic',
] as const

export const DEFAULT_CONTENT_SETTINGS: ContentSettings = {
  contentRating: 'standard',
  toneTags: [],
  updatedAt: null,
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

export function normalizeContentRating(value: unknown): ContentRating {
  const rating = typeof value === 'string' ? value.trim().toLowerCase() : ''
  if (rating === 'mature' || rating === 'unrestricted' || rating === 'standard') return rating
  return 'standard'
}

function normalizeToneTags(value: unknown) {
  if (!Array.isArray(value)) return []
  const tags: string[] = []
  value.forEach((item) => {
    const tag = typeof item === 'string' ? item.trim().toLowerCase() : ''
    if (tag && !tags.includes(tag)) tags.push(tag)
  })
  return tags.slice(0, 4)
}

export function contentSettingsFromSnapshot(snapshot: unknown): ContentSettings {
  const record = isRecord(snapshot) ? snapshot : {}
  const rawSettings = isRecord(record.contentSettings)
    ? record.contentSettings
    : isRecord(record.content_settings)
      ? record.content_settings
      : {}
  const contentRating = normalizeContentRating(
    rawSettings.contentRating ??
      rawSettings.content_rating ??
      rawSettings.rating ??
      record.contentRating ??
      record.content_rating,
  )
  const toneTags = normalizeToneTags(rawSettings.toneTags ?? rawSettings.tone_tags)
  const updatedAt =
    typeof rawSettings.updatedAt === 'string'
      ? rawSettings.updatedAt
      : typeof rawSettings.updated_at === 'string'
        ? rawSettings.updated_at
        : null

  return {
    contentRating,
    toneTags,
    updatedAt,
  }
}
