import { describe, expect, it } from 'vitest'
import type { JsonRecord, TimelineEntry } from '../types'
import { buildStoryBeats, deathSaveRolls, isEmptyStory, rollFromEntry, storyChanges, type StoryBeat } from './storyBeats'

function entry(
  id: string,
  role: TimelineEntry['role'],
  speaker: string,
  text: string,
  metadata: JsonRecord = {},
  streaming?: boolean,
): TimelineEntry {
  return { id, role, speaker, text, timestamp: null, metadata, ...(streaming ? { streaming } : {}) }
}

const welcome = entry('log-1', 'system', 'System', 'Welcome to the table. Choose your opening move when you are ready.', {
  kind: 'session_welcome',
})

function kinds(beats: StoryBeat[]) {
  return beats.map((beat) => beat.kind)
}

describe('buildStoryBeats', () => {
  it('reads every beat type from a timeline', () => {
    const beats = buildStoryBeats(
      [
        welcome,
        entry('log-2', 'dm', 'DM', 'Rain hammers the inn roof.', { turn_id: 1 }),
        entry('log-3', 'player', 'Ysolde', 'I ask Marta about the wagons.', { turn_id: 2 }),
        entry('log-4', 'player', 'Ysolde', 'I roll a d20+3 for insight: 11 = 14', { turn_id: 3 }),
        entry('log-5', 'dm', 'DM', '"They came at noon," Marta says.', { turn_id: 3 }),
        entry('log-6', 'system', 'System', 'State updated:\n- Added 12 gp.', {
          turn_id: 3,
          state_log: { lines: [{ status: 'applied', message: 'Added 12 gp.', changeType: 'currency.add' }] },
        }),
        entry('log-7', 'system', 'System', 'Brannoc joined the table.'),
      ],
      { selectedPlayerName: 'Ysolde' },
    )
    expect(kinds(beats)).toEqual(['chapter', 'narration', 'action', 'roll', 'narration', 'changes', 'notice'])
    expect(beats[2]).toMatchObject({ kind: 'action', speaker: 'Ysolde', own: true, delivery: 'sent' })
    expect(beats[6]).toMatchObject({ kind: 'notice', text: 'Brannoc joined the table.' })
  })

  it('drops internal bookkeeping lines entirely', () => {
    const beats = buildStoryBeats([
      entry('log-1', 'dm', 'DM', 'The bell tolls.', { turn_id: 1 }),
      entry('log-2', 'system', 'System', 'State updated'),
      entry('log-3', 'system', 'System', 'Canon applied for turn 1.'),
      entry('log-4', 'system', 'System', 'Segment Triggered: The Mill Bell'),
      entry('log-5', 'system', 'System', 'Saved to the chronicle.'),
      entry('log-6', 'system', 'System', 'Session ended.'),
    ])
    expect(kinds(beats)).toEqual(['narration', 'notice'])
  })

  it('reads a server roll from metadata and merges the authoritative system line into the player line', () => {
    const authoritative = {
      rule_type: 'ability_check',
      die: 'd20',
      mode: 'normal',
      rolls: [14],
      kept: 14,
      modifier: 5,
      total: 19,
      reason: 'investigation',
      task_dc: 15,
    }
    const beats = buildStoryBeats([
      entry('log-1', 'system', 'System', 'Check Resolved: turn 3 resolved with roll 19.', {
        turn_id: 4,
        roll_value: 19,
        authoritative_roll: authoritative,
      }),
      entry('log-2', 'player', 'Wren', 'I search the bench.\nI roll a d20+5 for investigation: 14 = 19', {
        turn_id: 4,
        action_intent: { kind: 'roll', roll: { ...authoritative, task_dc: undefined } },
      }),
    ])
    expect(kinds(beats)).toEqual(['action', 'roll'])
    expect(beats[0]).toMatchObject({ kind: 'action', text: 'I search the bench.' })
    expect(beats[1]).toMatchObject({
      kind: 'roll',
      roll: {
        name: 'Wren',
        checkLabel: 'Investigation',
        die: 'd20',
        kept: 14,
        modifier: 5,
        total: 19,
        dc: 15,
        outcome: 'success',
        natural: null,
      },
    })
  })

  it('parses a roll from the text when no roll metadata exists', () => {
    const roll = rollFromEntry(
      entry('log-1', 'player', 'Ysolde', 'I roll a d20-1 for dexterity saving throw: 1 = 0', { dc_hint: 'DC 12' }),
    )
    expect(roll).toMatchObject({
      name: 'Ysolde',
      checkLabel: 'Dexterity saving throw',
      kept: 1,
      modifier: -1,
      total: 0,
      dc: 12,
      outcome: 'failure',
      natural: 'one',
    })
    const advantage = rollFromEntry(
      entry('log-2', 'player', 'Ysolde', 'I roll a d20+6 for longbow attack: 20 (advantage; rolls 20, 4) = 26'),
    )
    expect(advantage).toMatchObject({ faces: [20, 4], kept: 20, mode: 'advantage', natural: 'twenty', outcome: null })
    const resolved = rollFromEntry(entry('log-3', 'system', 'System', 'Roll Resolved: authoritative result 17.'))
    expect(resolved).toMatchObject({ name: '', total: 17, kept: null })
    expect(rollFromEntry(entry('log-4', 'player', 'Wren', 'I roll the barrel down the hill.'))).toBeNull()
  })

  it('reads Bardic Inspiration from the roll record and from the canonical sentence', () => {
    const inspired = rollFromEntry(
      entry('log-1', 'player', 'Wren', 'I roll a d20+3 for stealth: 9 + Bardic Inspiration d6 (4) = 16', {
        turn_id: 5,
        action_intent: {
          kind: 'roll',
          roll: { die: 'd20', mode: 'normal', rolls: [9], kept: 9, modifier: 3, total: 16, task_dc: 15, inspiration: { die: 'd6', roll: 4 } },
        },
      }),
    )
    expect(inspired).toMatchObject({ kept: 9, total: 16, inspiration: { die: 'd6', roll: 4 }, outcome: 'success' })

    const fromText = rollFromEntry(
      entry('log-2', 'player', 'Wren', 'I roll a d20+3 for stealth: 9 (advantage; rolls 9, 2) 1d4 (2) + Bardic Inspiration d8 (5) = 19'),
    )
    expect(fromText).toMatchObject({ faces: [9, 2], kept: 9, mode: 'advantage', total: 19, inspiration: { die: 'd8', roll: 5 } })

    expect(rollFromEntry(entry('log-3', 'player', 'Wren', 'I roll a d20+3 for stealth: 9 = 12'))).toMatchObject({
      inspiration: null,
    })
  })

  it('keeps a system roll without a matching player line as its own beat', () => {
    const beats = buildStoryBeats([
      entry('log-1', 'system', 'System', 'Check Resolved: turn 2 resolved with roll 12.', { turn_id: 3 }),
    ])
    expect(beats).toHaveLength(1)
    expect(beats[0]).toMatchObject({ kind: 'roll', roll: { total: 12, name: '' } })
  })

  it('opens with a chapter beat, numbered when the welcome names a chapter', () => {
    const plain = buildStoryBeats([welcome, entry('log-2', 'dm', 'DM', 'Rain.', { turn_id: 1 })])
    expect(plain[0]).toMatchObject({ kind: 'chapter', chapterLabel: null })
    expect(plain[1]).toMatchObject({ kind: 'narration', opening: true })

    const numbered = buildStoryBeats([entry('log-1', 'system', 'System', '**Chapter 3: The Drowned Mill**')])
    expect(numbered[0]).toMatchObject({ kind: 'chapter', chapterLabel: 'Chapter 3' })
    expect(isEmptyStory(numbered)).toBe(true)

    const later = buildStoryBeats([entry('log-1', 'dm', 'DM', 'Rain.'), welcome])
    expect(kinds(later)).toEqual(['narration', 'notice'])
  })

  it('gives the drop cap only to the session’s first narration and only when history is complete', () => {
    const entries = [entry('log-1', 'dm', 'DM', 'One.', { turn_id: 1 }), entry('log-2', 'dm', 'DM', 'Two.', { turn_id: 2 })]
    const complete = buildStoryBeats(entries)
    expect(complete.map((beat) => beat.kind === 'narration' && beat.opening)).toEqual([true, false])
    const partial = buildStoryBeats(entries, { historyComplete: false })
    expect(partial.some((beat) => beat.kind === 'narration' && beat.opening)).toBe(false)
  })

  it('marks optimistic and failed actions and keeps their key when the log entry replaces them', () => {
    const pending = buildStoryBeats([
      entry('local-1', 'player', 'Ysolde', 'I bar the door.', { client_message_id: 'c-1', persistence_status: 'pending' }),
      entry('local-2', 'player', 'Ysolde', 'I bar the window.', { client_message_id: 'c-2', persistence_status: 'failed' }),
    ])
    expect(pending.map((beat) => beat.kind === 'action' && beat.delivery)).toEqual(['pending', 'failed'])
    const logged = buildStoryBeats([
      entry('log-9', 'player', 'Ysolde', 'I bar the door.', { client_message_id: 'c-1', turn_id: 7 }),
    ])
    expect(logged[0].key).toBe(pending[0].key)
  })

  it('keeps one narration when a streamed turn has already been logged, with a stable key', () => {
    const streaming = entry('stream-5', 'dm', 'DM', 'The bell', { turn_id: 5 }, true)
    const during = buildStoryBeats([streaming])
    expect(during[0]).toMatchObject({ kind: 'narration', streaming: true, text: 'The bell' })
    const logged = entry('log-12', 'dm', 'DM', 'The bell tolls.', { turn_id: 5 })
    const after = buildStoryBeats([logged, streaming])
    expect(after).toHaveLength(1)
    expect(after[0]).toMatchObject({ streaming: false, text: 'The bell tolls.' })
    expect(after[0].key).toBe(during[0].key)
    expect(buildStoryBeats([entry('stream-6', 'dm', 'DM', '...', { turn_id: 6 }, true)])[0]).toMatchObject({ text: '' })
  })

  it('flags narration whose persistence failed', () => {
    const beats = buildStoryBeats([entry('log-1', 'dm', 'DM', 'Rain.', { turn_id: 1, persistence_status: 'failed' })])
    expect(beats[0]).toMatchObject({ kind: 'narration', notSaved: true })
  })

  it('spaces beats by speaker and turn', () => {
    const beats = buildStoryBeats([
      entry('log-1', 'dm', 'DM', 'Rain.', { turn_id: 1 }),
      entry('log-2', 'system', 'System', 'State updated:', {
        state_log: { lines: [{ message: 'Took 3 damage.', changeType: 'health.damage' }] },
      }),
      entry('log-3', 'player', 'Wren', 'I duck.', { turn_id: 2 }),
      entry('log-4', 'player', 'Wren', 'I roll a d20+2 for stealth: 9 = 11', { turn_id: 2 }),
      entry('log-5', 'player', 'Ysolde', 'I follow her.', { turn_id: 3 }),
      entry('log-6', 'dm', 'DM', 'You both slip away.', { turn_id: 3 }),
    ])
    expect(beats.map((beat) => beat.gap)).toEqual(['none', 'tight', 'turn', 'tight', 'speaker', 'speaker'])
  })
})

describe('storyChanges', () => {
  it('chooses glyph and tone by content and trims leading labels', () => {
    const changes = storyChanges(
      entry('log-1', 'system', 'System', 'State updated:', {
        state_log: {
          lines: [
            { status: 'applied', message: 'Took 4 damage.', changeType: 'health.damage' },
            { status: 'applied', message: 'Restored 3 HP.', changeType: 'health.heal' },
            { status: 'applied', message: 'Added 25 XP.', changeType: 'xp.add' },
            { status: 'applied', message: 'Added 12 gp.', changeType: 'currency.add' },
            { status: 'applied', message: 'Added Waxed ledger x1.', changeType: 'inventory.add' },
            { status: 'applied', message: 'Learned spell: Shield.', changeType: 'spell.learn' },
            { status: 'applied', message: 'Added quest: The Grey Caravan.', changeType: 'quest.add' },
            { status: 'applied', message: 'Hidden line.', visibleToPlayer: false },
            { status: 'applied', message: 'Poisoned until the end of your next turn.' },
          ],
        },
      }),
    )
    expect(changes).toEqual([
      { text: 'Took 4 damage', tone: 'fail', glyph: 'heart' },
      { text: 'Restored 3 HP', tone: 'success', glyph: 'heart' },
      { text: 'Added 25 XP', tone: 'gold', glyph: 'star' },
      { text: 'Added 12 gp', tone: 'gold', glyph: 'coin' },
      { text: 'Added Waxed ledger x1', tone: 'gold', glyph: 'pack' },
      { text: 'Shield', tone: 'arcane', glyph: 'spark' },
      { text: 'The Grey Caravan', tone: 'default', glyph: 'scroll' },
      { text: 'Poisoned until the end of your next turn', tone: 'arcane', glyph: 'spark' },
    ])
  })

  it('returns nothing for entries without a state log', () => {
    expect(storyChanges(entry('log-1', 'system', 'System', 'State updated'))).toEqual([])
  })

  it('drops a line that reads as a single lowercase word, such as a stored mood-only scene update', () => {
    const moodOnly = entry('log-2', 'system', 'System', 'State updated:\n- Updated scene mood: calm.', {
      turn_id: 1,
      state_log: { lines: [{ status: 'applied', message: 'Updated scene mood: calm.', changeType: 'scene.update' }] },
    })
    expect(storyChanges(moodOnly)).toEqual([])
    expect(kinds(buildStoryBeats([entry('log-1', 'dm', 'DM', 'The horses are calm.', { turn_id: 1 }), moodOnly]))).toEqual([
      'narration',
    ])

    const mixed = storyChanges(
      entry('log-3', 'system', 'System', 'State updated:', {
        state_log: {
          lines: [
            { status: 'applied', message: 'tense', changeType: 'scene.update' },
            { status: 'applied', message: 'Updated scene: The Lantern Post.', changeType: 'scene.update' },
            { status: 'applied', message: 'Learned spell: Shield.', changeType: 'spell.learn' },
          ],
        },
      }),
    )
    expect(mixed.map((change) => change.text)).toEqual(['The Lantern Post', 'Shield'])
  })
})

describe('death saves', () => {
  it('reads the engine’s death saving throws as roll lines and keeps the other changes as chips', () => {
    const update = entry('log-9', 'system', 'System', 'State updated:', {
      turn_id: 7,
      state_log: {
        lines: [
          {
            status: 'applied',
            changeType: 'combat.death_save',
            message: 'Death saving throw: Ysolde Marrow rolled 14 (success); 1 success, 0 failure.',
          },
          {
            status: 'applied',
            changeType: 'combat.death_save',
            message: 'Death saving throw: Brannoc rolled 1 (critical failure); 0 success, 3 failure.',
          },
          { status: 'applied', changeType: 'health.damage', message: 'Took 4 damage.' },
        ],
      },
    })

    expect(deathSaveRolls(update)).toEqual([
      expect.objectContaining({ name: 'Ysolde Marrow', checkLabel: 'Death save', total: 14, outcome: 'success', natural: null }),
      expect.objectContaining({ name: 'Brannoc', checkLabel: 'Death save', total: 1, outcome: 'failure', natural: 'one' }),
    ])
    expect(storyChanges(update)).toEqual([{ text: 'Took 4 damage', tone: 'fail', glyph: 'heart' }])
    const beats = buildStoryBeats([entry('log-8', 'dm', 'DM', 'The goblin strikes.', { turn_id: 6 }), update])
    expect(kinds(beats)).toEqual(['narration', 'roll', 'roll', 'changes'])
  })

  it('shows a lone death save without an empty change row', () => {
    const update = entry('log-2', 'system', 'System', 'State updated:', {
      turn_id: 3,
      state_log: {
        lines: [{ status: 'applied', changeType: 'combat.death_save', message: 'Death saving throw: Wren rolled 20 (critical success); 0 success, 0 failure.' }],
      },
    })
    const beats = buildStoryBeats([update])
    expect(kinds(beats)).toEqual(['roll'])
    expect(beats[0]).toMatchObject({ roll: { name: 'Wren', total: 20, outcome: 'success', natural: 'twenty' } })
  })
})

describe('server story beats', () => {
  const stateUpdate = (id: string, turnId: number, lines: Array<[string, string]>, metadata: JsonRecord = {}) =>
    entry(id, 'system', 'System', `State updated:\n${lines.map(([, message]) => `- ${message}`).join('\n')}`, {
      turn_id: turnId,
      state_log: { lines: lines.map(([changeType, message]) => ({ status: 'applied', changeType, message })) },
      ...metadata,
    })

  it('renders exactly the turn’s beats after its narration, and nothing derived from its changes', () => {
    const beats = buildStoryBeats([
      entry('log-1', 'player', 'Liora', 'Read the caravan ledger.', { turn_id: 4 }),
      entry('log-2', 'dm', 'DM', 'The ledger is a list of erased names.', {
        turn_id: 4,
        storyBeats: [
          { kind: 'clue', text: 'Clue — The erased caravan ledger', refId: 'clue_erased_caravan_ledger' },
          { kind: 'objective_complete', text: 'Objective complete — Read the ledger' },
          { kind: 'place', text: 'New place on the map — The Old North Road' },
          { kind: 'coins', text: '−5 gp' },
        ],
      }),
      stateUpdate('log-3', 4, [['scene.interactable.action', 'scene.interactable.action update applied'], ['currency.remove', 'Lost 5 gp.']]),
    ])

    expect(kinds(beats)).toEqual(['action', 'narration', 'turnBeats'])
    const turn = beats[2]
    if (turn.kind !== 'turnBeats') throw new Error('expected the turn beats')
    expect(turn.beats.map(({ kind, text, look, glyph, target, tone }) => ({ kind, text, look, glyph, target, tone }))).toEqual([
      { kind: 'clue', text: 'Clue — The erased caravan ledger', look: 'chip', glyph: 'scroll', target: 'journal', tone: 'default' },
      { kind: 'objective_complete', text: 'Objective complete — Read the ledger', look: 'chip', glyph: 'scroll', target: 'journal', tone: 'success' },
      { kind: 'place', text: 'New place on the map — The Old North Road', look: 'chip', glyph: 'place', target: 'map', tone: 'default' },
      { kind: 'coins', text: '−5 gp', look: 'chip', glyph: 'coin', target: null, tone: 'default' },
    ])
  })

  it('reads beats from the state update too, places them after the narration, and shows a turn with none as nothing', () => {
    const beats = buildStoryBeats([
      entry('log-1', 'dm', 'DM', 'The bell rings three times.', { turn_id: 5 }),
      stateUpdate('log-2', 5, [['inventory.add', 'Took the key.']], { storyBeats: [{ kind: 'item_gained', text: 'Taken — Caravan Strongbox Key' }] }),
      entry('log-3', 'dm', 'DM', 'Nothing more stirs.', { turn_id: 6, storyBeats: [] }),
      stateUpdate('log-4', 6, [['combat.death_save', 'Death saving throw: Liora rolled 12 (success); 1 success, 0 failures.'], ['xp.add', 'Gained 25 XP.']]),
    ])
    expect(kinds(beats)).toEqual(['narration', 'turnBeats', 'narration'])
    expect(beats[1]).toMatchObject({ kind: 'turnBeats', beats: [{ kind: 'item_gained', text: 'Taken — Caravan Strongbox Key' }] })
  })

  it('reads a check beat out on the roll it settles and keeps it out of the chips', () => {
    const beats = buildStoryBeats([
      entry('log-1', 'player', 'Liora', 'I roll a d20+3 for investigation: 11 = 14', { turn_id: 7, dc_hint: '10' }),
      entry('log-2', 'dm', 'DM', 'Under the bench, a key.', {
        turn_id: 7,
        storyBeats: [
          { kind: 'check', text: 'Investigation check: 14 — success' },
          { kind: 'item_gained', text: 'Taken — Caravan Strongbox Key' },
        ],
      }),
    ])
    expect(kinds(beats)).toEqual(['roll', 'narration', 'turnBeats'])
    expect(beats[0]).toMatchObject({ kind: 'roll', checkText: 'Investigation check: 14 — success' })
    expect(beats[2]).toMatchObject({ beats: [{ kind: 'item_gained' }] })
  })

  it('shows a hint as a quiet Settings line and any unknown kind as plain text, dropping empty beats', () => {
    const [, turn] = buildStoryBeats([
      entry('log-1', 'dm', 'DM', 'The road waits.', {
        turn_id: 8,
        storyBeats: [
          { kind: 'hint', text: 'Scripted narrator — use the suggestions, or connect an AI narrator in Settings for free-form play' },
          { kind: 'weather', text: 'Rain sets in' },
          { kind: 'xp', text: '' },
          { kind: 'hp', text: 'Took 7 damage' },
        ],
      }),
    ])
    if (turn.kind !== 'turnBeats') throw new Error('expected the turn beats')
    expect(turn.beats.map(({ kind, look, glyph, target, tone }) => ({ kind, look, glyph, target, tone }))).toEqual([
      { kind: 'hint', look: 'hint', glyph: null, target: 'settings', tone: 'default' },
      { kind: 'weather', look: 'plain', glyph: null, target: null, tone: 'default' },
      { kind: 'hp', look: 'chip', glyph: 'heart', target: null, tone: 'fail' },
    ])
  })

  it('never shows a raw change type: a turn whose only change is scene.interactable.action shows nothing', () => {
    const beats = buildStoryBeats([
      entry('log-1', 'dm', 'DM', 'You search the bench.', { turn_id: 9 }),
      stateUpdate('log-2', 9, [['scene.interactable.action', 'scene.interactable.action update applied']]),
      entry('log-3', 'system', 'System', 'combat.move update applied', { turn_id: 10 }),
    ])
    expect(kinds(beats)).toEqual(['narration'])
    expect(JSON.stringify(beats)).not.toMatch(/scene\.interactable|update applied|combat\.move/)
    expect(storyChanges(stateUpdate('log-4', 11, [['scene.item.add', 'Found the key.'], ['combat.move', 'combat.move update applied']]))).toEqual([
      { text: 'Found the key', tone: 'gold', glyph: 'pack' },
    ])
  })

  it('tones a beat that names its hero the same as one that does not', () => {
    const [, turn] = buildStoryBeats([
      entry('log-1', 'dm', 'DM', 'The beast lunges at Seren Ashgrove.', {
        turn_id: 13,
        storyBeats: [
          { kind: 'hp', text: 'Seren Ashgrove took 7 damage' },
          { kind: 'hp', text: 'Arden Vale healed 4' },
          { kind: 'coins', text: 'Arden Vale −6 gp' },
          { kind: 'coins', text: '+10 gp each' },
        ],
      }),
    ])
    if (turn.kind !== 'turnBeats') throw new Error('expected the turn beats')
    expect(turn.beats.map(({ tone }) => tone)).toEqual(['fail', 'success', 'default', 'gold'])
  })

  it('shows how a fight ended as a chip, a victory in the success tone', () => {
    const [, turn] = buildStoryBeats([
      entry('log-1', 'dm', 'DM', 'The last cutthroat falls.', {
        turn_id: 12,
        storyBeats: [
          { kind: 'combat_end', text: 'Victory' },
          { kind: 'combat_end', text: 'The fight ends — they flee' },
        ],
      }),
    ])
    if (turn.kind !== 'turnBeats') throw new Error('expected the turn beats')
    expect(turn.beats.map(({ look, glyph, tone }) => ({ look, glyph, tone }))).toEqual([
      { look: 'chip', glyph: 'swords', tone: 'success' },
      { look: 'chip', glyph: 'swords', tone: 'default' },
    ])
  })

  it('shows a condition taking hold in the arcane tone and one wearing off in the quiet tone', () => {
    const [, turn] = buildStoryBeats([
      entry('log-1', 'dm', 'DM', 'You shake off the Lantern-Lure.', {
        turn_id: 14,
        storyBeats: [
          { kind: 'condition', text: 'You are charmed' },
          { kind: 'condition', text: 'No longer charmed' },
          { kind: 'condition', text: 'Wren is no longer prone' },
        ],
      }),
    ])
    if (turn.kind !== 'turnBeats') throw new Error('expected the turn beats')
    expect(turn.beats.map(({ text, look, glyph, tone }) => ({ text, look, glyph, tone }))).toEqual([
      { text: 'You are charmed', look: 'chip', glyph: 'spark', tone: 'arcane' },
      { text: 'No longer charmed', look: 'chip', glyph: 'spark', tone: 'default' },
      { text: 'Wren is no longer prone', look: 'chip', glyph: 'spark', tone: 'default' },
    ])
  })

  it('reads a hint in sentence case', () => {
    const [, turn] = buildStoryBeats([
      entry('log-1', 'dm', 'DM', 'The road waits.', {
        turn_id: 8,
        storyBeats: [{ kind: 'hint', text: 'scripted narrator — use the suggestions' }],
      }),
    ])
    expect(turn).toMatchObject({ kind: 'turnBeats', beats: [{ kind: 'hint', text: 'Scripted narrator — use the suggestions' }] })
  })

  it('takes an attack’s AC only from the roll’s own target_ac, never from a hint or a requested DC', () => {
    const attack = (roll: JsonRecord, metadata: JsonRecord = {}) =>
      rollFromEntry(entry('log-1', 'player', 'Arden', 'I attack the cutthroat.', {
        turn_id: 3,
        authoritative_roll: { rule_type: 'attack', die: 'd20', rolls: [9], kept: 9, modifier: 4, total: 13, ...roll },
        ...metadata,
      }))
    expect(attack({ task_dc: 14, outcome: 'success', target_ac: 13 }, { dc_hint: 'AC 14' })).toMatchObject({ attack: true, dc: 13, outcome: 'success' })
    expect(attack({ task_dc: 14, outcome: 'success' }, { dc_hint: '14', target_ac: 13 })).toMatchObject({ dc: 13 })
    expect(attack({ task_dc: 14, outcome: 'failure' }, { dc_hint: '14' })).toMatchObject({ attack: true, dc: null, outcome: 'failure' })

    const fromText = (metadata: JsonRecord) =>
      rollFromEntry(entry('log-2', 'player', 'Arden', 'I roll a d20+4 for longsword attack: 9 = 13', { rule_type: 'attack', ...metadata }))
    expect(fromText({ dc_hint: '14', target_ac: 13 })).toMatchObject({ dc: 13, outcome: 'success' })
    expect(fromText({ dc_hint: '14' })).toMatchObject({ dc: null, outcome: null })
  })

  it('takes a DC only when the server states one', () => {
    const roll = (metadata: JsonRecord) =>
      rollFromEntry(entry('log-1', 'player', 'Liora', 'I roll a d20+3 for investigation: 11 = 14', metadata))
    expect(roll({ dc_hint: 'roll mod +4, INT 16 mod +3' })?.dc).toBeNull()
    expect(roll({ dc_hint: 'DC 13' })?.dc).toBe(13)
    expect(roll({ dc_hint: '12 (roll mod +4)' })?.dc).toBe(12)
    expect(roll({})?.dc).toBeNull()
  })
})
