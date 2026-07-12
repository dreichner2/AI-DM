// @vitest-environment jsdom
import { beforeEach, describe, expect, it } from 'vitest'
import { trustBackendOrigin, writeOriginScopedStorage } from './api'
import {
  buildSessionSocketConnection,
  isTerminalSessionSocketError,
  normalizeRollRequiredPayload,
  normalizeRollResolvedPayload,
  normalizeTurnDuplicatePayload,
} from './useSessionSocket'

describe('session socket backend origin credential boundary', () => {
  beforeEach(() => {
    localStorage.clear()
    sessionStorage.clear()
  })

  it('omits all Socket.IO auth and browser credentials for an untrusted backend', () => {
    const savedBackend = 'https://saved-backend.example.test'
    localStorage.setItem('aidm:baseUrl', savedBackend)
    trustBackendOrigin(savedBackend)
    writeOriginScopedStorage(sessionStorage, 'aidm:workspaceToken', 'saved-workspace-token', savedBackend)
    writeOriginScopedStorage(localStorage, 'aidm:workspaceId', 'saved-workspace', savedBackend)

    const { socketOptions } = buildSessionSocketConnection(
      'https://untrusted-tunnel.ngrok-free.app',
      'saved-account-token',
    )

    expect(socketOptions.auth).toBeUndefined()
    expect(socketOptions.withCredentials).toBe(false)
    expect(socketOptions.extraHeaders).toEqual({ 'ngrok-skip-browser-warning': 'true' })
  })

  it('includes only credentials scoped to a trusted backend and retains ngrok support', () => {
    const backend = 'https://trusted-tunnel.ngrok-free.app'
    trustBackendOrigin(backend)
    writeOriginScopedStorage(sessionStorage, 'aidm:workspaceToken', 'trusted-workspace-token', backend)
    writeOriginScopedStorage(localStorage, 'aidm:workspaceId', 'trusted-workspace', backend)

    const { socketOptions } = buildSessionSocketConnection(backend, 'trusted-account-token')

    expect(socketOptions.auth).toEqual({
      account_token: 'trusted-account-token',
      workspace_token: 'trusted-workspace-token',
    })
    expect(socketOptions.withCredentials).toBeUndefined()
    expect(socketOptions.extraHeaders).toEqual({ 'ngrok-skip-browser-warning': 'true' })
  })
})

describe('session socket lifecycle errors', () => {
  it('treats unavailable session, campaign, and player bindings as terminal', () => {
    expect(isTerminalSessionSocketError('session_archived')).toBe(true)
    expect(isTerminalSessionSocketError('campaign_deleted')).toBe(true)
    expect(isTerminalSessionSocketError('player_identity_mismatch')).toBe(true)
    expect(isTerminalSessionSocketError('roll_required')).toBe(false)
    expect(isTerminalSessionSocketError('rate_limited')).toBe(false)
  })
})

describe('authoritative socket payload validation', () => {
  const resolvedRoll = {
    session_id: 20,
    turn_id: 81,
    player_id: 30,
    client_message_id: 'client-roll-1',
    pending_turn_id: null,
    rule_type: 'ability_check',
    die: 'd20',
    mode: 'advantage',
    rolls: [7, 18],
    kept: 18,
    modifier: 5,
    total: 23,
    reason: 'STR check',
    result_visibility: 'hidden_until_landed',
    ability: { key: 'strength', label: 'STR', score: 16, modifier: 3 },
    proficiency: { bonus: 2, skills: ['athletics'], multiplier: 2 },
    modifier_breakdown: {
      ability_modifier: 3,
      proficiency_bonus: 2,
      proficiency_multiplier: 2,
      wound_penalty: 0,
      total: 5,
    },
    authoritative: true,
  }

  it('accepts a consistent authoritative roll and duplicate receipt', () => {
    expect(normalizeRollResolvedPayload(resolvedRoll)).toMatchObject({
      client_message_id: 'client-roll-1',
      rolls: [7, 18],
      kept: 18,
      modifier: 5,
      total: 23,
      proficiency: { bonus: 2, skills: ['athletics'], multiplier: 2 },
      modifier_breakdown: expect.objectContaining({ proficiency_multiplier: 2 }),
      authoritative: true,
    })
    expect(normalizeTurnDuplicatePayload({
      session_id: 20,
      turn_id: 81,
      client_message_id: 'client-roll-1',
    })).toEqual({ session_id: 20, turn_id: 81, client_message_id: 'client-roll-1' })
  })

  it('accepts the public room payload without private character provenance', () => {
    const publicRoll: Record<string, unknown> = { ...resolvedRoll }
    delete publicRoll.ability
    delete publicRoll.proficiency
    delete publicRoll.modifier_breakdown

    const normalized = normalizeRollResolvedPayload(publicRoll)

    expect(normalized).toMatchObject({
      rolls: [7, 18],
      kept: 18,
      modifier: 5,
      total: 23,
      authoritative: true,
    })
    expect(normalized).not.toHaveProperty('ability')
    expect(normalized).not.toHaveProperty('proficiency')
    expect(normalized).not.toHaveProperty('modifier_breakdown')
  })

  it('rejects client-like, unauthoritative, or internally inconsistent results', () => {
    expect(normalizeRollResolvedPayload({ ...resolvedRoll, authoritative: false })).toBeNull()
    expect(normalizeRollResolvedPayload({ ...resolvedRoll, total: 999 })).toBeNull()
    expect(normalizeRollResolvedPayload({ ...resolvedRoll, rolls: [21] })).toBeNull()
    expect(normalizeRollResolvedPayload({ ...resolvedRoll, proficiency: { bonus: 2 } })).toBeNull()
  })

  it('normalizes server roll guidance while dropping private or malformed fields', () => {
    expect(normalizeRollRequiredPayload({
      session_id: 20,
      pending_turn_id: 80,
      rule_type: 'stealth',
      dc_hint: 'DC 14',
      prompt: 'Roll to slip past the sentry.',
      remaining_player_ids: [30, '31', -1],
      roll_spec: {
        die: 'd20',
        mode: 'disadvantage',
        rule_type: 'stealth',
        reason: 'Sneak past the sentry',
        result_visibility: 'hidden_until_landed',
        ability: { key: 'dexterity', label: 'DEX', score: 18, modifier: 4 },
        attack: { private: true },
      },
    })).toEqual({
      sessionId: 20,
      pendingTurnId: 80,
      ruleType: 'stealth',
      dcHint: 'DC 14',
      prompt: 'Roll to slip past the sentry.',
      remainingPlayerIds: [30, 31],
      rollSpec: {
        die: 'd20',
        mode: 'disadvantage',
        ruleType: 'stealth',
        reason: 'Sneak past the sentry',
        resultVisibility: 'hidden_until_landed',
        ability: { key: 'dexterity', label: 'DEX' },
      },
    })

    expect(normalizeRollRequiredPayload({ pending_turn_id: 80 })).toBeNull()
  })
})
