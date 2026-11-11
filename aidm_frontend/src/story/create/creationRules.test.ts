import { afterEach, describe, expect, it, vi } from 'vitest'
import { backgroundFromValue } from '../../backgroundCatalog'
import recorded from './creationOptions.fixture.json'
import {
  CREATION_OPTIONS_PATH,
  FALLBACK_RULES,
  loadCreationRules,
  rulesFromResponse,
  type ClassRules,
  type CreationRules,
} from './creationRules'

/** The built-in tables in the endpoint's response shape, as the server would send them. */
function asResponse(rules: CreationRules) {
  return {
    classes: rules.classes.map((entry: ClassRules) => ({
      id: entry.id,
      name: entry.name,
      hitDie: entry.hitDie,
      savingThrows: entry.saves,
      skillChoices: { count: entry.skillCount, options: [...entry.skills].sort() },
      ...(entry.fightingStyle ? { fightingStyles: { level: entry.fightingStyle.fromLevel, options: entry.fightingStyle.options } } : {}),
      ...(entry.expertise ? { expertise: { count: entry.expertise } } : {}),
      startingKit: entry.kit,
      cantrips: entry.cantrips,
      spells: entry.spells,
      levelOneFeatures: entry.start.map((feature) => feature.name),
      laterFeatures: entry.later.map((feature) => ({ level: feature.level, name: feature.name })),
    })),
    backgrounds: rules.backgrounds.map((entry) => ({
      id: entry.id,
      name: backgroundFromValue(entry.id)?.name,
      skills: entry.skills,
    })),
  }
}

/** The recorded response with feature summaries set aside (their wording may differ). */
function mechanical(response: typeof recorded) {
  return {
    classes: response.classes.map(({ levelOneFeatures, laterFeatures, skillChoices, ...entry }) => ({
      ...entry,
      skillChoices: { ...skillChoices, options: [...skillChoices.options].sort() },
      levelOneFeatures: levelOneFeatures.map((feature) => feature.name),
      laterFeatures: laterFeatures.map((feature) => ({ level: feature.level, name: feature.name })),
    })),
    backgrounds: response.backgrounds,
  }
}

const byId = <T extends { id: string }>(entries: T[]) => [...entries].sort((left, right) => left.id.localeCompare(right.id))

function jsonResponse(payload: unknown, status = 200) {
  return new Response(JSON.stringify(payload), { status, headers: { 'Content-Type': 'application/json' } })
}

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('creationRules', () => {
  it('keeps the offline tables equal to the recorded server response', () => {
    const fallback = asResponse(FALLBACK_RULES)
    const server = mechanical(recorded)
    expect(byId(fallback.classes)).toEqual(byId(server.classes))
    expect(byId(fallback.backgrounds)).toEqual(byId(server.backgrounds))
    for (const entry of recorded.classes) {
      for (const feature of [...entry.levelOneFeatures, ...entry.laterFeatures]) {
        expect(feature.summary, `${entry.id}: ${feature.name}`).not.toBe('')
      }
    }
    for (const entry of FALLBACK_RULES.classes) {
      for (const feature of [...entry.start, ...entry.later]) {
        expect(feature.text, `${entry.id}: ${feature.name}`).not.toBe('')
      }
    }
  })

  it('reads the recorded response field by field and keeps the Calling order', () => {
    const rules = rulesFromResponse(recorded)!
    expect(rules.classes.map((entry) => entry.id)).toEqual(FALLBACK_RULES.classes.map((entry) => entry.id))
    const wizard = rules.classes.find((entry) => entry.id === 'wizard')!
    expect(wizard.start[0]).toEqual({
      name: 'Arcane Recovery',
      text: recorded.classes.find((entry) => entry.id === 'wizard')!.levelOneFeatures[0].summary,
    })
    expect(rules.classes.find((entry) => entry.id === 'rogue')?.expertise).toBe(2)
    expect(rules.classes.find((entry) => entry.id === 'ranger')?.fightingStyle).toEqual({
      fromLevel: 2,
      options: ['archery', 'defense', 'dueling'],
    })
    expect(rules.classes.find((entry) => entry.id === 'cleric')).not.toHaveProperty('fightingStyle')
  })

  it('lets the server change the rules and keeps the tables for whatever it garbles or omits', () => {
    const rules = rulesFromResponse({
      classes: [
        {
          id: 'Wizard',
          hitDie: 'six',
          savingThrows: ['INT', 'wis'],
          skillChoices: { count: 3, options: ['arcana', 'history', 'insight', 'Sleight of Hand', 'flying'] },
          startingKit: ['Staff', { name: 'Candle', quantity: 3 }, { quantity: 2 }, 7],
          spells: 'Fireball',
          levelOneFeatures: [{ name: 'Spellbook', summary: 'Write spells down.' }, { summary: 'nameless' }],
          laterFeatures: [{ level: 4, name: 'Bigger Book', summary: 'More pages.' }, { level: 'soon', name: 'Later' }],
          expertise: 1,
        },
        { id: 'fighter', fightingStyles: ['dueling', 'protection'] },
        { id: 'paladin', fightingStyles: { level: 3, options: ['unknown'] } },
        { id: 'artificer', hitDie: 8 },
      ],
      backgrounds: [
        { id: 'sage', skillProficiencies: ['arcana', 'investigation'] },
        { id: 'soldier', grantedSkills: 'athletics' },
        { id: 'noble', skills: ['history'] },
      ],
    })!
    const base = (id: string) => FALLBACK_RULES.classes.find((entry) => entry.id === id)!
    const wizard = rules.classes.find((entry) => entry.id === 'wizard')!
    expect(wizard).toMatchObject({
      hitDie: base('wizard').hitDie,
      saves: ['intelligence', 'wisdom'],
      skillCount: 3,
      skills: ['arcana', 'history', 'insight', 'sleight_of_hand'],
      kit: ['Staff', 'Candle ×3'],
      cantrips: base('wizard').cantrips,
      spells: base('wizard').spells,
      start: [{ name: 'Spellbook', text: 'Write spells down.' }],
      later: [{ level: 4, name: 'Bigger Book', text: 'More pages.' }],
      expertise: 1,
    })
    // A class without the field has no fighting style or expertise; an unreadable one keeps the table's.
    const fighter = rules.classes.find((entry) => entry.id === 'fighter')!
    expect(fighter.fightingStyle).toEqual({ fromLevel: 1, options: ['dueling'] })
    expect(fighter.expertise).toBe(0)
    expect(rules.classes.find((entry) => entry.id === 'paladin')?.fightingStyle).toEqual(base('paladin').fightingStyle)
    expect(rules.classes.find((entry) => entry.id === 'cleric')).toBe(base('cleric'))
    expect(rules.classes.some((entry) => entry.id === 'artificer')).toBe(false)
    expect(rules.backgrounds).toEqual(
      FALLBACK_RULES.backgrounds.map((entry) => (entry.id === 'sage' ? { id: 'sage', skills: ['arcana', 'investigation'] } : entry)),
    )
  })

  it('ignores a skill count the options cannot fill', () => {
    const rules = rulesFromResponse({ classes: [{ id: 'monk', skillChoices: { count: 3, options: ['stealth'] } }] })!
    const monk = rules.classes.find((entry) => entry.id === 'monk')!
    const base = FALLBACK_RULES.classes.find((entry) => entry.id === 'monk')!
    expect([monk.skillCount, monk.skills]).toEqual([base.skillCount, base.skills])
  })

  it('finds nothing usable in an empty, foreign or malformed answer', () => {
    expect(rulesFromResponse(null)).toBeNull()
    expect(rulesFromResponse('<html>')).toBeNull()
    expect(rulesFromResponse([recorded])).toBeNull()
    expect(rulesFromResponse({ classes: 'all', backgrounds: {} })).toBeNull()
    expect(rulesFromResponse({ classes: [{ id: 'gunslinger', hitDie: 10 }], backgrounds: [{ id: 'sage' }] })).toBeNull()
  })

  it('loads the rules from the endpoint, and gives none when it fails or answers garbage', async () => {
    const fetchMock = vi.fn<typeof fetch>().mockResolvedValueOnce(jsonResponse(recorded))
    vi.stubGlobal('fetch', fetchMock)
    const rules = await loadCreationRules('http://localhost:5050', '')
    expect(rules?.classes.find((entry) => entry.id === 'monk')?.start[0].name).toBe('Martial Arts Strike')
    expect(fetchMock.mock.calls[0]?.[0]).toBe(`http://localhost:5050${CREATION_OPTIONS_PATH}`)

    fetchMock.mockResolvedValueOnce(jsonResponse({ error: 'Not found' }, 404))
    expect(await loadCreationRules('http://localhost:5050', '')).toBeNull()
    fetchMock.mockRejectedValueOnce(new TypeError('Failed to fetch'))
    expect(await loadCreationRules('http://localhost:5050', '')).toBeNull()
    fetchMock.mockResolvedValueOnce(new Response('<html>offline</html>', { status: 200, headers: { 'Content-Type': 'text/html' } }))
    expect(await loadCreationRules('http://localhost:5050', '')).toBeNull()
  })
})
