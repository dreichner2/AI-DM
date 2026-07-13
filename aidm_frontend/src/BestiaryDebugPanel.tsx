import { Bug, RefreshCcw, Search, Sparkles } from 'lucide-react'
import { useCallback, useEffect, useMemo, useState } from 'react'
import { ApiClientError, apiFetch } from './api'
import type {
  BestiaryEntryPayload,
  BestiaryListResponse,
  CampaignPackGenerateResponse,
  CombatDebugEventsResponse,
  CreatureDefinition,
  JsonRecord,
} from './types'

type BestiaryDebugPanelProps = {
  baseUrl: string
  auth: string
  selectedCampaignId: number | null
  selectedSessionId: number | null
  canUseOperatorTools: boolean
}

type CreatureRow = {
  key: string
  name: string
  source: string
  scope: string
  tier: string
  role: string
  type: string
  tags: string[]
  creature: CreatureDefinition
  entry: BestiaryEntryPayload | null
}

function isBestiaryEntryPayload(entry: BestiaryEntryPayload | CreatureDefinition): entry is BestiaryEntryPayload {
  return (
    'bestiary_entry_id' in entry &&
    'creature' in entry &&
    typeof (entry as { creature?: unknown }).creature === 'object' &&
    (entry as { creature?: unknown }).creature !== null
  )
}

function entryCreature(entry: BestiaryEntryPayload | CreatureDefinition): CreatureDefinition {
  return isBestiaryEntryPayload(entry) ? entry.creature : entry
}

function entrySource(entry: BestiaryEntryPayload | CreatureDefinition, creature: CreatureDefinition) {
  return 'source' in entry && typeof entry.source === 'string' ? entry.source : creature.source
}

function entryScope(entry: BestiaryEntryPayload | CreatureDefinition) {
  return 'scope' in entry && typeof entry.scope === 'string' ? entry.scope : 'core'
}

function entryKey(entry: BestiaryEntryPayload | CreatureDefinition, creature: CreatureDefinition, index: number) {
  if (isBestiaryEntryPayload(entry) && entry.bestiary_entry_id) return `entry-${entry.bestiary_entry_id}`
  return `${entryScope(entry)}-${creature.id}-${index}`
}

function normalizeRows(response: BestiaryListResponse | null, sourceLabel: string): CreatureRow[] {
  return (response?.entries ?? []).map((entry, index) => {
    const creature = entryCreature(entry)
    const behavior = creature.behavior ?? {}
    const tags = Array.isArray(creature.visualTags)
      ? creature.visualTags.map((tag) => String(tag)).filter(Boolean)
      : []
    return {
      key: entryKey(entry, creature, index),
      name: creature.name,
      source: entrySource(entry, creature) || sourceLabel,
      scope: entryScope(entry),
      tier: String(creature.challengeTier || 'standard'),
      role: String(behavior.combatRole || 'unknown'),
      type: String(creature.creatureType || 'custom'),
      tags,
      creature,
      entry: isBestiaryEntryPayload(entry) ? entry : null,
    }
  })
}

function debugSummary(event: JsonRecord) {
  const payload = event.payload && typeof event.payload === 'object' && !Array.isArray(event.payload)
    ? event.payload as JsonRecord
    : {}
  const combatDebug = payload.combatDebug && typeof payload.combatDebug === 'object' && !Array.isArray(payload.combatDebug)
    ? payload.combatDebug as JsonRecord
    : {}
  const resolver = payload.resolver && typeof payload.resolver === 'object' && !Array.isArray(payload.resolver)
    ? payload.resolver as JsonRecord
    : combatDebug.resolver && typeof combatDebug.resolver === 'object' && !Array.isArray(combatDebug.resolver)
      ? combatDebug.resolver as JsonRecord
    : {}
  const intentPlan = payload.intentPlan && typeof payload.intentPlan === 'object' && !Array.isArray(payload.intentPlan)
    ? payload.intentPlan as JsonRecord
    : combatDebug.intentPlan && typeof combatDebug.intentPlan === 'object' && !Array.isArray(combatDebug.intentPlan)
      ? combatDebug.intentPlan as JsonRecord
    : {}
  if (event.event_type === 'post_dm_combat_outcome') {
    const counts = payload.validationCounts && typeof payload.validationCounts === 'object' && !Array.isArray(payload.validationCounts)
      ? payload.validationCounts as JsonRecord
      : {}
    const applied = Array.isArray(payload.appliedCombatChanges) ? payload.appliedCombatChanges.length : 0
    const rejected = Array.isArray(payload.rejectedCombatChanges) ? payload.rejectedCombatChanges.length : 0
    return [
      'Outcome',
      `${applied} applied`,
      rejected ? `${rejected} rejected` : '',
      counts.accepted !== undefined ? `${String(counts.accepted)} accepted total` : '',
      String(intentPlan.summaryForDm || ''),
    ].filter(Boolean).join(' / ')
  }
  return [
    event.event_type === 'pre_dm_combat_plan' ? 'Plan' : String(event.event_type || 'combat debug'),
    String(resolver.resolutionMethod || ''),
    String(intentPlan.summaryForDm || ''),
  ].filter(Boolean).join(' / ')
}

function errorMessage(error: unknown) {
  if (error instanceof ApiClientError) return error.message
  if (error instanceof Error) return error.message
  return 'Bestiary request failed.'
}

export function BestiaryDebugPanel({
  baseUrl,
  auth,
  selectedCampaignId,
  selectedSessionId,
  canUseOperatorTools,
}: BestiaryDebugPanelProps) {
  const [query, setQuery] = useState('')
  const [sourceFilter, setSourceFilter] = useState('all')
  const [packTheme, setPackTheme] = useState('')
  const [rows, setRows] = useState<CreatureRow[]>([])
  const [debugEvents, setDebugEvents] = useState<JsonRecord[]>([])
  const [selectedKey, setSelectedKey] = useState('')
  const [loading, setLoading] = useState(false)
  const [seeding, setSeeding] = useState(false)
  const [error, setError] = useState('')
  const [status, setStatus] = useState('')

  const loadBestiary = useCallback(async () => {
    await Promise.resolve()
    setLoading(true)
    setError('')
    try {
      const debugRequest = selectedSessionId && canUseOperatorTools
        ? apiFetch<CombatDebugEventsResponse>(baseUrl, `/api/sessions/${selectedSessionId}/combat/debug?limit=12`, auth)
          .catch((): CombatDebugEventsResponse => ({ events: [] }))
        : Promise.resolve({ events: [] })
      const [core, campaign, debug] = await Promise.all([
        apiFetch<BestiaryListResponse>(baseUrl, '/api/bestiary/core', auth),
        selectedCampaignId
          ? apiFetch<BestiaryListResponse>(baseUrl, `/api/campaigns/${selectedCampaignId}/bestiary`, auth)
          : Promise.resolve(null),
        debugRequest,
      ])
      const nextRows = [
        ...normalizeRows(campaign, 'campaign'),
        ...normalizeRows(core, 'core_bestiary'),
      ]
      setRows(nextRows)
      setDebugEvents(debug.events ?? [])
      setSelectedKey((current) => current && nextRows.some((row) => row.key === current) ? current : nextRows[0]?.key ?? '')
      setStatus(`${nextRows.length} creatures loaded`)
    } catch (requestError) {
      setError(errorMessage(requestError))
    } finally {
      setLoading(false)
    }
  }, [auth, baseUrl, canUseOperatorTools, selectedCampaignId, selectedSessionId])

  useEffect(() => {
    const timeoutId = window.setTimeout(() => {
      void loadBestiary()
    }, 0)
    return () => window.clearTimeout(timeoutId)
  }, [loadBestiary])

  const sourceOptions = useMemo(
    () => ['all', ...Array.from(new Set(rows.map((row) => row.source))).sort()],
    [rows],
  )
  const filteredRows = useMemo(() => {
    const normalizedQuery = query.trim().toLowerCase()
    return rows.filter((row) => {
      if (sourceFilter !== 'all' && row.source !== sourceFilter) return false
      if (!normalizedQuery) return true
      return [
        row.name,
        row.source,
        row.scope,
        row.tier,
        row.role,
        row.type,
        ...row.tags,
      ].some((value) => value.toLowerCase().includes(normalizedQuery))
    })
  }, [query, rows, sourceFilter])
  const selected = filteredRows.find((row) => row.key === selectedKey) ?? filteredRows[0] ?? rows.find((row) => row.key === selectedKey) ?? rows[0] ?? null
  const selectedAbilities = Array.isArray(selected?.creature.abilities) ? selected.creature.abilities.slice(0, 4) : []
  const selectedBehavior = selected?.creature.behavior ?? {}
  const selectedBalance = selected?.creature.balance ?? {}

  async function seedCampaignPack() {
    if (!selectedCampaignId) return
    setSeeding(true)
    setError('')
    try {
      const themes = packTheme.split(',').map((theme) => theme.trim()).filter(Boolean)
      const response = await apiFetch<CampaignPackGenerateResponse>(
        baseUrl,
        `/api/campaigns/${selectedCampaignId}/bestiary/generate-pack`,
        auth,
        {
          method: 'POST',
          body: JSON.stringify({ themes: themes.length ? themes : ['campaign'], count: 6 }),
        },
      )
      setStatus(`Seeded ${response.entries.length || response.creatures.length} campaign creatures`)
      await loadBestiary()
    } catch (requestError) {
      setError(errorMessage(requestError))
    } finally {
      setSeeding(false)
    }
  }

  return (
    <section className="inspector-box bestiary-debug-panel" aria-label="Bestiary debug panel">
      <div className="box-title">
        <h3>Bestiary</h3>
        <span>{loading ? 'Loading' : `${filteredRows.length}/${rows.length}`}</span>
      </div>

      <div className="bestiary-player-surface" aria-label="Player bestiary">
        <div className="bestiary-toolbar">
          <label className="bestiary-search">
            <Search size={13} aria-hidden="true" />
            <input
              type="search"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="Search creatures"
              aria-label="Search bestiary"
            />
          </label>
          <select
            value={sourceFilter}
            onChange={(event) => setSourceFilter(event.target.value)}
            aria-label="Filter bestiary source"
          >
            {sourceOptions.map((source) => (
              <option key={source} value={source}>
                {source.replace(/_/g, ' ')}
              </option>
            ))}
          </select>
          <button type="button" className="icon-action" onClick={loadBestiary} disabled={loading} aria-label="Refresh bestiary">
            <RefreshCcw size={14} aria-hidden="true" />
          </button>
        </div>

        {error ? <div className="bestiary-message error" role="alert">{error}</div> : null}
        {status && !error ? <div className="bestiary-message">{status}</div> : null}

        <div className="bestiary-layout">
          <ul className="bestiary-list" aria-label="Bestiary creatures">
            {filteredRows.slice(0, 14).map((row) => (
              <li key={row.key}>
                <button
                  type="button"
                  className={row.key === selected?.key ? 'selected' : ''}
                  onClick={() => setSelectedKey(row.key)}
                  aria-pressed={row.key === selected?.key}
                >
                  <span>{row.name}</span>
                  <small>{row.source.replace(/_/g, ' ')} / {row.tier}</small>
                </button>
              </li>
            ))}
            {!filteredRows.length ? <li className="empty-row">No creatures match.</li> : null}
          </ul>

          {selected ? (
            <article className="creature-debug-card">
              <header>
                <div>
                  <strong>{selected.name}</strong>
                  <small>{selected.type} / {selected.role} / {selected.tier}</small>
                </div>
                <span>{selected.source.replace(/_/g, ' ')}</span>
              </header>
              <p>{selected.creature.descriptionShort}</p>
              <div className="creature-stat-grid">
                <span>HP <strong>{String(selected.creature.stats?.maxHp ?? '—')}</strong></span>
                <span>AC <strong>{String(selected.creature.stats?.armorClass ?? '—')}</strong></span>
                <span>DPR <strong>{String(selectedBalance.estimatedDamagePerRound ?? '—')}</strong></span>
                <span>Morale <strong>{String(selectedBehavior.morale ?? '—')}</strong></span>
              </div>
              <div className="creature-tags">
                {selected.tags.slice(0, 8).map((tag) => <span key={tag}>{tag}</span>)}
              </div>
              <div className="creature-ability-list">
                {selectedAbilities.map((ability) => (
                  <span key={String(ability.id || ability.name)}>
                    {String(ability.name || ability.id)}
                    <small>{String(ability.type || 'ability')}</small>
                  </span>
                ))}
              </div>
            </article>
          ) : null}
        </div>
      </div>

      {canUseOperatorTools ? (
        <div className="bestiary-operator-surface" aria-label="Bestiary operator tools">
          <div className="bestiary-seed-row">
            <input
              value={packTheme}
              onChange={(event) => setPackTheme(event.target.value)}
              placeholder="pack themes"
              aria-label="Campaign pack themes"
              disabled={!selectedCampaignId || seeding}
            />
            <button type="button" onClick={seedCampaignPack} disabled={!selectedCampaignId || seeding}>
              <Sparkles size={13} aria-hidden="true" />
              Seed
            </button>
          </div>

          {debugEvents.length ? (
            <details className="combat-debug-events">
              <summary><Bug size={13} aria-hidden="true" /> Combat debug ({debugEvents.length})</summary>
              <div>
                {debugEvents.slice(0, 6).map((event) => (
                  <span key={String(event.debug_event_id ?? event.created_at ?? debugSummary(event))}>
                    {debugSummary(event)}
                  </span>
                ))}
              </div>
            </details>
          ) : null}
        </div>
      ) : null}
    </section>
  )
}
