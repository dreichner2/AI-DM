import { describe, expect, it } from 'vitest'
import type { CombatStatePanel, WorldStatePanel } from '../gameSelectors'
import type { SceneDisplayState } from '../sceneState'
import { storyMood } from './storyMood'

function combat(active: boolean): CombatStatePanel {
  return {
    active,
    status: active ? 'active' : 'none',
    round: '1',
    battlefield: '',
    goal: '',
    creatureSource: '',
    resolverMethod: '',
    tacticalLevel: 'normal',
    endReason: '',
    combatStartedBy: '',
    enemyGroupSummary: '',
    initiativeRequired: false,
    debugEnabled: false,
    enemies: [],
    allies: [],
    telegraphs: [],
    legalActionBundles: [],
  }
}

function world(overrides: Partial<WorldStatePanel> = {}): WorldStatePanel {
  return {
    sceneName: 'Somewhere',
    sceneDescription: '',
    sceneType: 'exploration',
    mood: 'neutral',
    dangerLevel: '1',
    activeQuests: [],
    presentNpcs: [],
    sceneItems: [],
    availableExits: [],
    knownLocations: [],
    knownNpcs: [],
    combat: combat(false),
    ...overrides,
  }
}

function scene(overrides: Partial<SceneDisplayState> = {}): SceneDisplayState {
  return {
    sessionId: 1,
    locationId: null,
    locationName: 'Somewhere',
    sceneType: 'exploration',
    mood: null,
    dangerLevel: 1,
    combatState: 'none',
    inCombat: false,
    musicTag: 'calm',
    actingPlayerId: null,
    ...overrides,
  }
}

describe('storyMood', () => {
  it('is combat whenever combat is active, whatever the scene says', () => {
    expect(storyMood(world({ sceneType: 'tavern' }), scene({ sceneType: 'tavern' }), combat(true))).toBe('combat')
    expect(storyMood(world(), scene({ inCombat: true }), combat(false))).toBe('combat')
  })

  it('reads rest, hearth, wild and dread from scene type, mood or music tag', () => {
    expect(storyMood(world(), scene({ sceneType: 'camp' }), combat(false))).toBe('rest')
    expect(storyMood(world({ mood: 'sleepy' }), null, null)).toBe('rest')
    expect(storyMood(world(), scene({ musicTag: 'town' }), combat(false))).toBe('hearth')
    expect(storyMood(world({ sceneType: 'Roadside inn' }), null, null)).toBe('hearth')
    expect(storyMood(world(), scene({ sceneType: 'market_square' }), null)).toBe('hearth')
    expect(storyMood(world(), scene({ musicTag: 'forest' }), null)).toBe('wild')
    expect(storyMood(world({ sceneType: 'river crossing' }), null, null)).toBe('wild')
    expect(storyMood(world(), scene({ musicTag: 'dungeon' }), null)).toBe('dread')
    expect(storyMood(world({ mood: 'haunted' }), null, null)).toBe('dread')
  })

  it('does not mistake words that merely contain a keyword', () => {
    expect(storyMood(world({ sceneType: 'deep forest', mood: 'quiet' }), null, null)).toBe('wild')
    expect(storyMood(world({ sceneType: 'beginning', mood: 'dinner' }), null, null)).toBe('calm')
  })

  it('is tense when danger is 3 or higher and no keyword matched', () => {
    expect(storyMood(world(), scene({ dangerLevel: 3 }), null)).toBe('tense')
    expect(storyMood(world({ dangerLevel: '4' }), null, null)).toBe('tense')
  })

  it('is calm otherwise', () => {
    expect(storyMood(world(), scene({ dangerLevel: 2 }), combat(false))).toBe('calm')
    expect(storyMood(null, null, null)).toBe('calm')
  })
})
