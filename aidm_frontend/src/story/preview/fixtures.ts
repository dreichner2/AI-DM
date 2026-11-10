import { createRef } from 'react'
import { DEFAULT_CONTENT_SETTINGS } from '../../contentSettings'
import type { ActionComposerProps } from '../../ActionComposer'
import type {
  CombatLegalAction,
  CombatLegalTarget,
  CombatParticipantSummary,
  CombatStatePanel,
  WorldStatePanel,
  WorldMap,
} from '../../gameSelectors'
import type { GameplayControlState, GameplayTarget } from '../../gameplayControlState'
import type { InspectorPanelProps } from '../../InspectorPanel'
import type { SceneDisplayState } from '../../sceneState'
import type { SessionBoardProps } from '../../SessionBoard'
import type { ActivePlayer, Campaign, JsonRecord, Player, TimelineEntry } from '../../types'

/**
 * Development fixtures for the Story mode previews. Every callback is a no-op;
 * the content is a believable mid-campaign session so typography is judged on
 * real prose rather than lorem ipsum.
 */

export type StoryPreviewState =
  | 'empty'
  | 'opening'
  | 'exploration'
  | 'streaming'
  | 'combat'
  | 'ended'
  | 'dying'
  | 'trade'
  | 'rest'
  | 'ending'

export const STORY_PREVIEW_STATES: readonly StoryPreviewState[] = [
  'empty',
  'opening',
  'exploration',
  'streaming',
  'combat',
  'ended',
  'dying',
  'trade',
  'rest',
  'ending',
]

/** The viewer's character is in a fight (`dying`: down in one). */
function inCombat(state: StoryPreviewState) {
  return state === 'combat' || state === 'dying'
}

export function isStoryPreviewState(value: unknown): value is StoryPreviewState {
  return typeof value === 'string' && (STORY_PREVIEW_STATES as readonly string[]).includes(value)
}

const noop = () => {}
const asyncNoop = async () => {}

const SESSION_ID = 214
const CAMPAIGN_ID = 37
const VIEWER_PLAYER_ID = 30
const CHAPTER_NUMBER = 3
const CHAPTER_RECAP =
  'The mill bell rang at midnight and the party followed the driverless wagons north. Six lanterns went dark; one name in the ledger was still wet.'

export const FIXTURE_CAMPAIGN_TITLE = 'The Lantern Road'
export const FIXTURE_SESSION_TITLE = 'The Drowned Lantern'

/** A mixed-state road map for the map and Story development previews. */
export function mapFixture(terrain = 'moor'): WorldMap {
  return {
    title: 'The Western Roadlands', terrain, seed: 1107,
    nodes: [
      { id: 'inn', name: 'The Drowned Lantern', icon: 'inn', x: 19, y: 76, state: 'current' },
      { id: 'road', name: 'The Old North Road', icon: 'road', x: 37, y: 57, state: 'visited' },
      { id: 'ford', name: 'Reedwater Ford', icon: 'bridge', x: 53, y: 78, state: 'known' },
      { id: 'village', name: 'Hollin Cross', icon: 'town', x: 58, y: 42, state: 'known' },
      { id: 'tower', name: 'The Watcher’s Tower', icon: 'tower', x: 28, y: 26, state: 'visited' },
      { id: 'cairns', name: '?', icon: 'unknown', x: 78, y: 27, state: 'rumoured' },
      { id: 'ruin', name: '?', icon: 'unknown', x: 82, y: 72, state: 'rumoured' },
    ],
    routes: [
      { from: 'inn', to: 'road', style: 'road', travelable: true },
      { from: 'inn', to: 'ford', style: 'trail', travelable: true },
      { from: 'road', to: 'village', style: 'road', travelable: false },
      { from: 'road', to: 'tower', style: 'trail', travelable: false },
      { from: 'tower', to: 'village', style: 'hidden', travelable: false },
      { from: 'village', to: 'cairns', style: 'trail', travelable: false },
      { from: 'ford', to: 'ruin', style: 'road', travelable: false },
    ],
  }
}

const campaignDescription =
  'Four strangers walk the old pilgrim road north through the Fenmarch, where the lanterns along the causeway have started going out one by one — and the travellers who lit them have stopped coming home.'

const OPENING_NARRATION = [
  'Rain has followed you since the ford at Hollin Cross, and by the time the causeway lifts out of the marsh it has stopped pretending to be anything but a flood falling sideways. The lantern posts that should mark the road stand dark, one after another, their glass streaked and empty. Only one light holds ahead: a long, low inn with its back to the reeds and its shutters bleeding amber through the cracks.',
  'A painted board swings over the door — a lantern sinking beneath green water — and someone has nailed a horseshoe above it, points up, the way the fen folk do when they want their luck to stay in. Smoke leans out of the chimney and is torn away before it can rise.',
  'In the yard, three wagons sit hitched and waiting. The mules stand in their traces with their heads low, steaming, untouched by anyone for hours. The canvas covers are tied down tight. The drivers’ benches are wet and empty, and on the lead wagon a whip lies across the seat as if its owner set it down to light a pipe and simply never came back for it.',
  'The inn door opens before you knock. A broad woman in a flour-dusted apron fills the frame, a poker in one hand and a lamp in the other. She looks at your boots, then your faces, then past you at the wagons, and something in her shoulders settles. “You’re not them,” she says. “Good. Come in out of it, then, before the fen decides you’re part of the scenery.”',
].join('\n\n')

const MARTA_NARRATION = [
  'Marta Vell sets the lamp down on the bar and does not let go of the poker. The common room is warmer than it has any right to be — a peat fire the size of a cart, a dozen benches, and only two other souls: an old man asleep with his chin on his chest, and a young brother of the Lamplighters’ order who has not touched his soup.',
  '“They came in at noon,” Marta says, nodding toward the yard. “Grey Caravan out of Saltmere, three wagons, six drivers. Paid for stabling and a hot meal and asked me which way the causeway lanterns were still lit. I told them none past the mill. They laughed about it.” She turns the poker over in her hands. “At dusk I went out to tell them supper was on, and the yard was just as you see it. Mules still in harness. Nobody.”',
  'Brother Ansel looks up from his soup for the first time. “Six lanterns went dark tonight,” he says quietly. “I counted them from the window. One for each.”',
].join('\n\n')

const WAGON_NARRATION = [
  'Wren is gone through the kitchen before anyone can argue, and the rain swallows the sound of the back door. The lead wagon smells of wet canvas and tallow. Under the driver’s bench, wedged where the boards meet, her fingers find a waxed leather ledger, its cover warm as if someone had been holding it a moment ago.',
  'The last page is a list of names — six of them, each in a different hand — and beside every name the same small drawing: a lantern with its flame turned downward. The ink of the final name is still wet. It reads, in careful capitals, MARROW.',
].join('\n\n')

const HEARTH_NARRATION = [
  'Ysolde carries the ledger to the hearth and the room goes quiet around her. The old man by the fire is awake now, though nobody saw him wake. He watches the book, not her.',
  '“That’s Teodor,” Marta murmurs. “Ran the ferry at Hollin until the river took it. He doesn’t talk much.”',
  '“I talk when there’s something worth saying,” Teodor says. His voice is dry as old reeds. “The lanterns on the causeway aren’t there to light the road, girl. They’re there to show the road to something else. When a lantern goes out, it’s because whatever was looking has found what it wanted.” He holds out a hand for the ledger, palm up, patient. “Your name’s in that book. I’d like to know how it got there before the mill bell rings.”',
].join('\n\n')

export const STREAMING_NARRATION = [
  'Somewhere out past the reeds, faint and wrong, a bell begins to toll. Not the brisk clang of a village alarm but a slow, drowned sound, as if it were ringing from the bottom of a well. Brother Ansel is on his feet before the second stroke, soup forgotten, one hand pressed flat to the cold window glass.',
  '“The mill,” he breathes. “There hasn’t been a bell at the mill in forty years.”',
  'In the yard the mules lift their heads all at once. Every one of them turns to face the causeway, ears pricked toward the dark, and the lead mule begins — very softly — to pull the empty wagon north.',
].join('\n\n')

const COMBAT_OPENING = [
  'The thing that climbs out of the drainage ditch was a man once — a caravan driver, by the leather apron and the whip-callused hands — but the marsh has filled him up. Water pours from his sleeves. Where his face should be there is only a hollow of black glass, and inside it, very small, a lantern flame burns upside down.',
  'Two more rise behind him from the reeds, moving with the patient, puppet-jointed certainty of things that do not need to breathe. The nearest raises a hand, and the rain stops falling on you — it hangs in the air, every drop catching the drowned light, and then it comes at you all at once like thrown gravel.',
].join('\n\n')

const COMBAT_REPLY = [
  'Your arrow takes the lead lanternman under the jaw and the black glass of his face cracks like river ice. Light spills out of the fracture — not warm, but cold and green — and he staggers, clawing at it, as though he can push the flame back in.',
  'The second one does not hesitate. It is on Brannoc before the dwarf can set his shield, and a fist of marsh water hammers into his side hard enough to fold him over the wagon wheel. Behind them both, the third lanternman lifts its burning hand toward you, and the cold finds your ribs.',
].join('\n\n')

const DOWN_NARRATION = [
  'The burning hand closes. Cold pours into you the way the river pours into a holed boat, and the causeway tilts up to meet your cheek. The longbow slides out of your fingers and over the edge into the reeds.',
  'Somewhere above you Brannoc is shouting your name. It reaches you from a long way off, muffled, as if through ice.',
].join('\n\n')

const TRADE_NARRATION = [
  'Marta wipes her hands on her apron and lifts the hatch behind the bar. “If you are walking that causeway tonight you will want more than courage,” she says, and sets out what she has: a coil of good hemp rope, a hooded lantern, lamp oil, and two stoppered vials of something red that glows faintly when the firelight finds it.',
  '“Fen prices,” she adds. “Coin first. I will buy back anything you would rather not carry.”',
].join('\n\n')

const ENDING_NARRATION = [
  'At first light the last lantern on the causeway catches by itself — a small, ordinary flame, yellow and warm — and then the next, and the next, all the way back to the inn. The mill bell does not ring again.',
  'Six drivers walk up the road out of the mist, wet to the knee and blinking, with no memory of the night. Teodor counts them twice. Then he opens the ledger, finds the last name on the page, and lets you strike it out yourself.',
].join('\n\n')

export const FIXTURE_ENDING = {
  title: 'The Lanterns Relit',
  summary: 'The drowned bell is silent, the six drivers came home along a lit road, and the name in the ledger was struck out by your own hand.',
}

const ENDED_CLOSING = [
  'By the time the mill bell falls silent, the eastern sky has gone the colour of pewter and the rain has thinned to a fine, apologetic mist. The wagons are still in the yard. The mules have stopped pulling. Teodor sits on the inn step with the ledger closed across his knees and does not say whether he expected you back.',
  'Six names, six lanterns, one bell — and one name that should not be written there at all. Marta brings out bread and hot cider without being asked. Nobody talks about the causeway. Everybody keeps looking at it.',
].join('\n\n')

const sessionRecap =
  'The party crossed the flooded ford at Hollin Cross after Brannoc talked the toll-keeper down to half a crown. Wren lost a boot to the marsh and swears she will get it back. At dusk the causeway lanterns began to go dark, one by one, behind and ahead of you, and the only light left on the road was the Drowned Lantern inn.'

function entry(
  id: string,
  role: TimelineEntry['role'],
  speaker: string,
  text: string,
  minute: number,
  metadata: JsonRecord = {},
): TimelineEntry {
  const timestamp = new Date(Date.UTC(2026, 10, 9, 19, minute)).toISOString()
  return { id, role, speaker, text, timestamp, metadata }
}

function welcomeEntry(): TimelineEntry {
  return entry('log-1', 'system', 'System', 'Welcome to the table. Choose your opening move when you are ready.', 0, {
    kind: 'session_welcome',
  })
}

function dmEntry(id: string, turnId: number, text: string, minute: number): TimelineEntry {
  return entry(id, 'dm', 'DM', text, minute, {
    turn_id: turnId,
    turn_number: turnId,
    persistence_status: 'saved',
    outcome_status: 'resolved',
  })
}

function playerEntry(id: string, turnId: number, speaker: string, text: string, minute: number): TimelineEntry {
  return entry(id, 'player', speaker, text, minute, {
    turn_id: turnId,
    turn_number: turnId,
    persistence_status: 'saved',
  })
}

function rollEntries(
  idBase: number,
  turnId: number,
  speaker: string,
  roll: { reason: string; ruleType: string; die: string; face: number; modifier: number; dc: number | null },
  minute: number,
): TimelineEntry[] {
  const total = roll.face + roll.modifier
  const modifierText = roll.modifier ? `${roll.modifier >= 0 ? '+' : ''}${roll.modifier}` : ''
  const authoritativeRoll = {
    rule_type: roll.ruleType,
    die: roll.die,
    mode: 'normal',
    rolls: [roll.face],
    kept: roll.face,
    modifier: roll.modifier,
    total,
    reason: roll.reason,
    result_visibility: 'visible',
    modifier_breakdown: {
      ability_modifier: roll.modifier,
      proficiency_bonus: 0,
      wound_penalty: 0,
      total: roll.modifier,
    },
    task_dc: roll.dc,
    authoritative: true,
  }
  return [
    entry(`log-${idBase}`, 'system', 'System', `Check Resolved: turn ${turnId - 1} resolved with roll ${total}.`, minute, {
      turn_id: turnId,
      turn_number: turnId,
      resolved_turn_id: turnId - 1,
      roll_value: total,
      rule_type: roll.ruleType,
      authoritative_roll: authoritativeRoll,
    }),
    entry(
      `log-${idBase + 1}`,
      'player',
      speaker,
      `I roll a ${roll.die}${modifierText} for ${roll.reason}: ${roll.face} = ${total}`,
      minute,
      {
        turn_id: turnId,
        turn_number: turnId,
        persistence_status: 'saved',
        action_intent: {
          kind: 'roll',
          source: 'composer',
          roll: authoritativeRoll,
        },
      },
    ),
  ]
}

function deathSaveEntry(id: string, turnId: number, saves: Array<[number, string, number, number]>, minute: number): TimelineEntry {
  return stateLogEntry(
    id,
    turnId,
    saves.map(([roll, result, successes, failures]) => [
      'combat.death_save',
      `Death saving throw: Ysolde Marrow rolled ${roll} (${result}); ${successes} success, ${failures} failure.`,
    ]),
    minute,
  )
}

function stateLogEntry(id: string, turnId: number, lines: Array<[string, string]>, minute: number): TimelineEntry {
  return entry(
    id,
    'system',
    'System',
    `State updated:\n${lines.map(([, message]) => `- ${message}`).join('\n')}`,
    minute,
    {
      turn_id: turnId,
      state_log: {
        lines: lines.map(([changeType, message]) => ({
          status: 'applied',
          message,
          changeType,
          visibleToPlayer: true,
        })),
      },
    },
  )
}

function noticeEntry(id: string, text: string, minute: number, metadata: JsonRecord = {}): TimelineEntry {
  return entry(id, 'system', 'System', text, minute, metadata)
}

type Timeline = { rows: TimelineEntry[]; current: TimelineEntry | null }

function openingTimeline(): Timeline {
  return { rows: [welcomeEntry()], current: dmEntry('log-2', 1, OPENING_NARRATION, 2) }
}

function explorationRows(): TimelineEntry[] {
  return [
    welcomeEntry(),
    dmEntry('log-2', 1, OPENING_NARRATION, 2),
    playerEntry(
      'log-3',
      2,
      'Ysolde Marrow',
      'I shake the rain from my cloak, keep my bow where Marta can see it, and ask who left the wagons standing in the yard.',
      5,
    ),
    dmEntry('log-4', 2, MARTA_NARRATION, 6),
    playerEntry(
      'log-5',
      3,
      'Wren Tallowmere',
      'I slip out through the kitchen to look over the lead wagon — the harness, the bench, anything the drivers left behind.',
      9,
    ),
    ...rollEntries(6, 4, 'Wren Tallowmere', {
      reason: 'investigation',
      ruleType: 'ability_check',
      die: 'd20',
      face: 14,
      modifier: 5,
      dc: 15,
    }, 10),
    dmEntry('log-8', 4, WAGON_NARRATION, 11),
    stateLogEntry('log-9', 4, [
      ['inventory.add', 'Added Waxed caravan ledger x1.'],
      ['quest.add', 'Added quest: The Drivers of the Grey Caravan.'],
      ['currency.add', 'Added 12 gp.'],
    ], 11),
    noticeEntry('log-10', 'Brannoc Deepvale joined the table.', 12, { kind: 'presence' }),
    noticeEntry('log-11', 'State updated', 12, { kind: 'state_update' }),
    playerEntry(
      'log-12',
      5,
      'Ysolde Marrow',
      'I carry the ledger to the hearth and read the last page aloud, slowly, watching the room while I do.',
      14,
    ),
  ]
}

function explorationTimeline(): Timeline {
  return { rows: explorationRows(), current: dmEntry('log-13', 5, HEARTH_NARRATION, 15) }
}

function streamingTimeline(): Timeline {
  const rows = [
    ...explorationRows(),
    dmEntry('log-13', 5, HEARTH_NARRATION, 15),
    playerEntry(
      'log-14',
      6,
      'Ysolde Marrow',
      '“It got there tonight,” I tell him, and hand over the ledger. “So tell me what rings the mill bell.”',
      17,
    ),
  ]
  const streaming: TimelineEntry = {
    id: 'stream-6',
    role: 'dm',
    speaker: 'DM',
    text: STREAMING_NARRATION.slice(0, 120),
    timestamp: null,
    metadata: { turn_id: 6, turn_number: 6, persistence_status: 'streaming' },
    streaming: true,
  }
  return { rows, current: streaming }
}

function combatRows(): TimelineEntry[] {
  return [
    welcomeEntry(),
    dmEntry('log-2', 1, COMBAT_OPENING, 2),
    noticeEntry('log-3', 'Combat started. Roll for initiative.', 3, { kind: 'combat_start' }),
    playerEntry(
      'log-4',
      2,
      'Ysolde Marrow',
      'I put my back to the wagon, draw, and loose at the lead one — right at the glass where its face should be.',
      4,
    ),
    ...rollEntries(5, 3, 'Ysolde Marrow', {
      reason: 'longbow attack',
      ruleType: 'attack',
      die: 'd20',
      face: 20,
      modifier: 6,
      dc: 13,
    }, 5),
    dmEntry('log-7', 3, COMBAT_REPLY, 6),
    stateLogEntry('log-8', 3, [
      ['combat.participant.update', 'Lanternman HP is now 4/22.'],
      ['health.damage', 'Took 7 damage.'],
      ['spell.slot', 'Spell slot used: Shield of Faith (1st level).'],
    ], 6),
  ]
}

function combatTimeline(): Timeline {
  return { rows: combatRows(), current: null }
}

function dyingTimeline(): Timeline {
  const rows = [
    ...combatRows(),
    dmEntry('log-9', 4, DOWN_NARRATION, 8),
    stateLogEntry('log-10', 4, [['health.damage', 'Took 12 damage.']], 8),
    deathSaveEntry('log-11', 5, [[6, 'failure', 0, 1]], 10),
    deathSaveEntry('log-12', 6, [[14, 'success', 1, 1]], 12),
  ]
  return { rows, current: null }
}

function tradeTimeline(): Timeline {
  const attempt = 'Ysolde Marrow buys Hooded lantern from Marta Vell.'
  const rows = [
    ...explorationRows().slice(0, -1),
    entry('log-12', 'player', 'Ysolde Marrow', attempt, 14, {
      turn_id: 5,
      turn_number: 5,
      persistence_status: 'saved',
      action_intent: {
        kind: 'shop',
        text: attempt,
        shop: { action: 'buy', merchant_id: 'marta-larder', stock_id: 'marta-1', quantity: 1 },
      },
    }),
    entry('log-13', 'system', 'System', 'State updated', 14, {
      turn_id: 5,
      state_log: {
        lines: [{
          status: 'rejected',
          changeType: 'shop.buy',
          message: `${attempt} rejected: Marta Vell is not open for trade right now.`,
          visibleToPlayer: true,
        }],
      },
    }),
  ]
  return { rows, current: dmEntry('log-14', 6, TRADE_NARRATION, 15) }
}

function endingTimeline(): Timeline {
  const rows = [
    ...explorationRows(),
    dmEntry('log-13', 5, HEARTH_NARRATION, 15),
    playerEntry('log-14', 6, 'Ysolde Marrow', 'I take the pen from Teodor and strike my own name out of the ledger.', 50),
  ]
  return { rows, current: dmEntry('log-15', 6, ENDING_NARRATION, 52) }
}

function endedTimeline(): Timeline {
  const rows = [
    ...explorationRows(),
    dmEntry('log-13', 5, HEARTH_NARRATION, 15),
    dmEntry('log-14', 6, ENDED_CLOSING, 58),
    noticeEntry('log-15', 'Session ended.', 59, { kind: 'session_ended' }),
  ]
  return { rows, current: null }
}

function timelineFor(state: StoryPreviewState): Timeline {
  switch (state) {
    case 'empty':
      return { rows: [welcomeEntry()], current: null }
    case 'opening':
      return openingTimeline()
    case 'exploration':
    case 'rest':
      return explorationTimeline()
    case 'streaming':
      return streamingTimeline()
    case 'combat':
      return combatTimeline()
    case 'ended':
      return endedTimeline()
    case 'dying':
      return dyingTimeline()
    case 'trade':
      return tradeTimeline()
    case 'ending':
      return endingTimeline()
  }
}

function health(currentHp: number, maxHp: number): ActivePlayer['health'] {
  const ratio = maxHp > 0 ? currentHp / maxHp : 0
  const tone = currentHp <= 0 ? 'dead' : ratio >= 1 ? 'uninjured' : ratio > 0.5 ? 'wounded' : 'badly-wounded'
  const label =
    tone === 'dead' ? 'Down' : tone === 'uninjured' ? 'Unhurt' : tone === 'wounded' ? 'Wounded' : 'Badly wounded'
  return { tone, label, currentHp, maxHp }
}

export function makeActivePlayers(state: StoryPreviewState = 'exploration'): ActivePlayer[] {
  const combat = inCombat(state)
  const viewerHp = state === 'dying' ? 0 : combat ? 19 : state === 'rest' ? 13 : 24
  return [
    {
      id: VIEWER_PLAYER_ID,
      character_name: 'Ysolde Marrow',
      name: 'Danny',
      race: 'Elf',
      sex: 'female',
      profile_image: '/profile-icons/elf_female.png',
      char_class: 'Ranger',
      health: health(viewerHp, 24),
    },
    {
      id: 31,
      character_name: 'Brannoc Deepvale',
      name: 'Imogen',
      race: 'Dwarf',
      sex: 'male',
      profile_image: '/profile-icons/dwarf_male.png',
      char_class: 'Cleric',
      health: health(combat ? 9 : 27, 27),
      is_typing: state === 'exploration',
    },
    {
      id: 32,
      character_name: 'Wren Tallowmere',
      name: 'Sol',
      race: 'Halfling',
      sex: 'female',
      profile_image: '/profile-icons/halfling_female.png',
      char_class: 'Rogue',
      health: health(18, 20),
    },
    {
      id: 33,
      character_name: 'Corin Ashby',
      name: 'Ade',
      race: 'Human',
      sex: 'male',
      profile_image: '',
      char_class: 'Wizard',
      health: health(14, 14),
    },
    {
      id: 34,
      character_name: 'Pell',
      name: 'Kit',
      race: 'Human',
      sex: 'male',
      profile_image: '/profile-icons/human_male.png',
      char_class: 'Bard',
      health: health(16, 16),
    },
  ]
}

const viewerPlayer: Player = {
  player_id: VIEWER_PLAYER_ID,
  workspace_id: 'preview',
  account_id: 7,
  username: 'danny',
  campaign_id: CAMPAIGN_ID,
  name: 'Danny',
  character_name: 'Ysolde Marrow',
  race: 'Elf',
  sex: 'female',
  profile_image: '/profile-icons/elf_female.png',
  class_: 'Ranger',
  char_class: 'Ranger',
  level: 3,
  created_at: '2026-10-28T18:00:00.000Z',
  updated_at: '2026-11-09T19:00:00.000Z',
}

const campaign: Campaign = {
  campaign_id: CAMPAIGN_ID,
  title: FIXTURE_CAMPAIGN_TITLE,
  description: campaignDescription,
  world_id: 5,
  world_name: 'The Fenmarch',
  created_at: '2026-10-28T18:00:00.000Z',
  updated_at: '2026-11-09T19:00:00.000Z',
  status: 'active',
  is_archived: false,
  current_quest: 'The Drivers of the Grey Caravan',
  location: 'The Drowned Lantern',
  session_count: 3,
  latest_session_id: SESSION_ID,
  latest_activity_at: '2026-11-09T19:15:00.000Z',
}

function sceneStateFor(state: StoryPreviewState): SceneDisplayState {
  if (inCombat(state)) {
    return {
      sessionId: SESSION_ID,
      locationId: 'mill-causeway',
      locationName: 'The Mill Causeway',
      sceneType: 'combat',
      mood: 'dread',
      dangerLevel: 4,
      combatState: 'active',
      inCombat: true,
      musicTag: 'combat',
      actingPlayerId: VIEWER_PLAYER_ID,
    }
  }
  return {
    sessionId: SESSION_ID,
    locationId: 'drowned-lantern',
    locationName: 'The Drowned Lantern',
    sceneType: 'social',
    mood: 'uneasy',
    dangerLevel: 2,
    combatState: 'none',
    inCombat: false,
    musicTag: 'town',
    actingPlayerId: VIEWER_PLAYER_ID,
  }
}

function combatant(
  id: string,
  name: string,
  team: 'enemy' | 'ally',
  health: string,
  healthTone: CombatParticipantSummary['healthTone'],
  extra: Partial<CombatParticipantSummary> = {},
): CombatParticipantSummary {
  return {
    id,
    name,
    team,
    kind: team === 'enemy' ? 'creature' : 'player',
    source: team === 'enemy' ? 'bestiary' : 'party',
    health,
    healthTone,
    conditions: [],
    morale: team === 'enemy' ? 'steady' : '',
    moraleEvents: [],
    intent: '',
    telegraph: '',
    tacticSource: '',
    brainSource: '',
    position: 'near',
    selectionScore: '',
    selectionMethod: '',
    ...extra,
  }
}

function legalAction(
  id: string,
  type: string,
  label: string,
  description: string,
  targets: CombatLegalTarget[],
  extra: Partial<CombatLegalAction> = {},
): CombatLegalAction {
  return {
    id,
    type,
    label,
    description,
    message: description,
    available: true,
    reason: '',
    requiresTarget: targets.length > 0,
    authoritative: true,
    economy: {
      action: 1,
      movement: '0 ft',
      endsTurn: false,
      tracking: 'per_turn',
      reactionTracked: true,
      subTurnCountersTracked: true,
    },
    targets,
    rangeClassification: 'ranged',
    allowedRangeBands: ['near', 'far'],
    weaponName: '',
    ...extra,
  }
}

const NO_COST = { action: 0, bonusAction: 0, movement: 0, reaction: 0 }

function combatStateFor(state: StoryPreviewState): CombatStatePanel {
  const active = inCombat(state)
  const dying = state === 'dying'
  const lanternTargets: CombatLegalTarget[] = [
    { id: 'lanternman-1', name: 'Cracked lanternman', rangeBand: 'melee', available: true, reason: '' },
    { id: 'lanternman-2', name: 'Lanternman', rangeBand: 'near', available: true, reason: '' },
    { id: 'lanternman-3', name: 'Burning-hand lanternman', rangeBand: 'far', available: true, reason: '' },
  ]
  const self = { rangeClassification: 'self', allowedRangeBands: [] }
  const viewer = `player-${VIEWER_PLAYER_ID}`
  return {
    active,
    status: active ? 'active' : 'none',
    round: active ? '2' : '—',
    battlefield: active ? 'The flooded causeway below the mill' : '—',
    goal: active ? 'Survive the lanternmen' : '—',
    creatureSource: active ? 'bestiary' : '',
    resolverMethod: active ? 'rules_engine' : '',
    tacticalLevel: 'normal',
    endReason: '',
    combatStartedBy: active ? 'ambush' : '',
    enemyGroupSummary: active ? 'Three lanternmen' : '',
    initiativeRequired: false,
    debugEnabled: false,
    enemies: active
      ? [
          combatant('lanternman-1', 'Cracked lanternman', 'enemy', '4 / 22', 'critical', {
            conditions: ['marked'],
            morale: 'shaken',
            rangeBand: 'melee',
            engagedWith: dying ? [] : [viewer],
          }),
          combatant('lanternman-2', 'Lanternman', 'enemy', '22 / 22', 'healthy', {
            intent: 'Press the dwarf against the wagon',
            rangeBand: 'melee',
            engagedWith: ['player-31'],
          }),
          combatant('lanternman-3', 'Burning-hand lanternman', 'enemy', '22 / 22', 'healthy', {
            position: 'far',
            rangeBand: 'far',
            telegraph: 'Its hand is lifting toward Ysolde',
          }),
        ]
      : [],
    allies: active
      ? [
          dying
            ? combatant(viewer, 'Ysolde Marrow', 'ally', 'Down', 'down', {
                conditions: ['unconscious', 'dying'],
                rangeBand: 'melee',
                deathSaves: { successes: 1, failures: 1 },
              })
            : combatant(viewer, 'Ysolde Marrow', 'ally', '19 / 24', 'hurt', {
                rangeBand: 'melee',
                engagedWith: ['lanternman-1'],
              }),
          combatant('player-31', 'Brannoc Deepvale', 'ally', '9 / 27', 'critical', {
            conditions: ['prone'],
            rangeBand: 'melee',
            engagedWith: ['lanternman-2'],
          }),
          combatant('player-32', 'Wren Tallowmere', 'ally', '18 / 20', 'healthy', { position: 'far', rangeBand: 'far' }),
        ]
      : [],
    telegraphs: active ? ['The burning-hand lanternman is gathering cold light.'] : [],
    turnOrder: active
      ? [
          { id: 'player-32', name: 'Wren Tallowmere', team: 'player', isCurrent: false },
          { id: viewer, name: 'Ysolde Marrow', team: 'player', isCurrent: !dying },
          { id: 'lanternman-1', name: 'Cracked lanternman', team: 'enemy', isCurrent: false },
          { id: 'player-31', name: 'Brannoc Deepvale', team: 'player', isCurrent: false },
          { id: 'lanternman-2', name: 'Lanternman', team: 'enemy', isCurrent: false },
          { id: 'lanternman-3', name: 'Burning-hand lanternman', team: 'enemy', isCurrent: dying },
        ]
      : [],
    legalActionBundles: active
      ? [
          {
            schemaVersion: 1,
            playerId: VIEWER_PLAYER_ID,
            actorId: viewer,
            actorName: 'Ysolde Marrow',
            round: dying ? 3 : 2,
            currentActorId: dying ? 'lanternman-3' : viewer,
            currentActorName: dying ? 'Burning-hand lanternman' : 'Ysolde Marrow',
            isCurrentActor: !dying,
            economyTracking: 'per_turn',
            subTurnCountersTracked: true,
            actionRemaining: 1,
            bonusActionRemaining: 1,
            movementRemaining: 0,
            reactionRemaining: 1,
            attacksRemaining: 0,
            actions: [
              legalAction('combat.attack.longbow', 'attack', 'Attack with Longbow', 'Loose an arrow at a lanternman.', lanternTargets, {
                weaponName: 'Longbow',
                cost: { ...NO_COST, action: 1 },
              }),
              legalAction('combat.advance', 'advance', 'Advance', 'Close into melee with an enemy.', [], {
                ...self,
                available: false,
                reason: 'Your movement is already spent.',
                cost: { ...NO_COST, movement: 1 },
              }),
              legalAction('combat.reposition', 'reposition', 'Fall back', 'Move one band away from the fight.', [], {
                ...self,
                available: false,
                reason: 'Your movement is already spent.',
                cost: { ...NO_COST, movement: 1 },
              }),
              legalAction('combat.dash', 'dash', 'Dash', 'Gain one more movement this turn.', [], {
                ...self,
                cost: { ...NO_COST, action: 1 },
              }),
              legalAction('combat.disengage', 'disengage', 'Disengage', 'Move without provoking opportunity attacks.', [], {
                ...self,
                cost: { ...NO_COST, action: 1 },
              }),
              legalAction('combat.dodge', 'dodge', 'Dodge', 'Attacks against you have disadvantage until your next turn.', [], {
                ...self,
                cost: { ...NO_COST, action: 1 },
              }),
              legalAction('combat.help', 'help', 'Help', 'Give an ally advantage on their next attack.', [], {
                ...self,
                cost: { ...NO_COST, action: 1 },
              }),
              legalAction('combat.hide', 'hide', 'Hide', 'Try to slip out of sight.', [], {
                ...self,
                available: false,
                reason: 'You cannot hide in melee without cover.',
                cost: { ...NO_COST, action: 1 },
              }),
              legalAction('item.use.healing-potion', 'item', 'Use Potion of Healing', 'Restores 2d4+2 hit points.', [], {
                ...self,
                cost: { ...NO_COST, bonusAction: 1 },
              }),
              legalAction('combat.end_turn', 'end_turn', 'End turn', 'Take no further action and pass the combat turn.', [], {
                ...self,
                message: 'Ysolde Marrow ends their turn.',
                cost: NO_COST,
              }),
            ],
          },
        ]
      : [],
  }
}

function gameplayTarget(id: string, name: string, team: string, currentHp: number, maxHp: number): GameplayTarget {
  return { id, name, team, rangeBand: 'near', currentHp, maxHp, conditions: [], available: true, reason: '' }
}

function gameplayControlsFor(state: StoryPreviewState): GameplayControlState {
  const combat = inCombat(state)
  const enemyTargets = combat
    ? [
        gameplayTarget('lanternman-1', 'Cracked lanternman', 'enemy', 4, 22),
        gameplayTarget('lanternman-2', 'Lanternman', 'enemy', 22, 22),
      ]
    : []
  const party = [
    gameplayTarget(`player-${VIEWER_PLAYER_ID}`, 'Ysolde Marrow', 'player', state === 'rest' ? 13 : 24, 24),
    gameplayTarget('player-31', 'Brannoc Deepvale', 'player', combat ? 9 : 27, 27),
  ]
  const potion = {
    id: 'healing-potion',
    name: 'Potion of Healing',
    quantity: 2,
    effectSummary: 'Restores 2d4+2 hit points.',
    cost: combat ? 'bonus action' : 'bonus action; action for an ally',
    available: true,
    reason: '',
    targets: combat ? party.slice(0, 1) : party,
    ...(combat ? { actionId: 'item.use.healing-potion' } : {}),
  }
  return {
    actorId: `player-${VIEWER_PLAYER_ID}`,
    actorName: 'Ysolde Marrow',
    inCombat: combat,
    activeActorId: `player-${VIEWER_PLAYER_ID}`,
    isActorTurn: true,
    actionRemaining: combat ? 1 : null,
    bonusActionRemaining: combat ? 1 : null,
    concentration: '',
    spells: [
      {
        id: 'hunters-mark',
        name: "Hunter's Mark",
        level: 1,
        description: 'Mark a creature as your quarry; your weapon hits deal an extra 1d6 damage to it.',
        delivery: 'automatic',
        castingTime: 'bonus_action',
        castLevels: [1],
        effectLabel: 'your weapon hits deal +1d6 to the target · concentration',
        effectSummary: 'your weapon hits deal +1d6 to the target · concentration',
        rulesBacked: true,
        outOfCombat: false,
        relation: 'enemy',
        rangeBands: ['near', 'far'],
        minTargets: 1,
        maxTargets: 1,
        concentration: true,
        prepared: true,
        resourceLabel: 'Level 1 slot · 2 left',
        available: combat,
        reason: combat ? '' : 'No hostile creature in sight.',
        targets: enemyTargets,
      },
      {
        id: 'cure-wounds',
        name: 'Cure Wounds',
        level: 1,
        description: 'A creature you touch regains hit points.',
        delivery: 'automatic',
        castingTime: 'action',
        castLevels: [1],
        effectLabel: 'heals 1d8 + your casting modifier',
        effectSummary: 'heals 1d8 + your casting modifier',
        rulesBacked: true,
        outOfCombat: true,
        relation: 'ally',
        rangeBands: ['melee'],
        minTargets: 1,
        maxTargets: 1,
        concentration: false,
        prepared: true,
        resourceLabel: 'Level 1 slot · 2 left',
        available: true,
        reason: '',
        targets: party,
      },
    ],
    narratedSpells: [
      {
        id: 'fog-cloud',
        name: 'Fog Cloud',
        level: 1,
        castingTime: 'action',
        effectSummary: 'A 20-foot sphere of fog that heavily obscures the area.',
        concentration: true,
        rulesBacked: false,
        resourceLabel: 'Level 1 slot · 2 left',
        available: true,
        reason: '',
      },
    ],
    capabilities: [
      {
        id: 'favored-foe',
        name: 'Favored Foe',
        description: 'Mark a creature you hit and deal an extra 1d4 damage to it.',
        actionEconomy: 'reaction',
        targetPolicy: 'hostile',
        effectType: 'damage_bonus',
        current: 2,
        max: 2,
        refreshesOn: 'long rest',
        available: combat,
        reason: combat ? '' : 'Only in combat.',
        targets: enemyTargets,
        poolId: 'favored_foe',
        poolName: 'Favored Foe',
        poolCost: 1,
      },
    ],
    items: [potion],
    rest: {
      shortAvailable: !combat,
      shortReason: combat ? 'Rest is unavailable while combat is active.' : '',
      longAvailable: !combat && state !== 'rest',
      longReason: combat
        ? 'Rest is unavailable while combat is active.'
        : state === 'rest'
          ? 'You have already rested today.'
          : '',
      hitDice: { current: state === 'rest' ? 2 : 3, max: 3, die: 'd10' },
    },
    currency: { gp: 43, sp: 6 },
    tradeGoods: [
      { id: 'arrows', name: 'Arrows', quantity: 17, price: { cp: 5 } },
      { id: 'healing-potion', name: 'Potion of Healing', quantity: 2, price: { gp: 50 } },
      { id: 'ledger', name: 'Waxed caravan ledger', quantity: 1, price: null },
    ],
    interactables: combat
      ? []
      : [
          {
            id: 'lead-wagon',
            name: 'Lead wagon',
            kind: 'vehicle',
            description: 'A hitched caravan wagon, canvas tied down, whip on the bench.',
            revision: 2,
            states: ['searched'],
            available: true,
            reason: '',
            actions: [
              { id: 'inspect', label: 'Inspect', available: true, reason: '' },
              { id: 'search', label: 'Search', available: true, reason: '' },
            ],
          },
          {
            id: 'inn-hearth',
            name: 'Peat hearth',
            kind: 'fixture',
            description: 'A fire the size of a cart, banked high against the rain.',
            revision: 1,
            states: [],
            available: true,
            reason: '',
            actions: [{ id: 'use', label: 'Warm up', available: true, reason: '' }],
          },
        ],
  }
}

const CLOCKS: Partial<Record<StoryPreviewState, [number, 'morning' | 'afternoon' | 'evening' | 'night']>> = {
  combat: [2, 'night'],
  dying: [2, 'night'],
  ending: [3, 'morning'],
}

function clockFor(state: StoryPreviewState) {
  const [day, phase] = CLOCKS[state] ?? [2, 'evening']
  return { day, phase, label: `Day ${day} · ${phase.charAt(0).toUpperCase()}${phase.slice(1)}` }
}

function worldStateFor(state: StoryPreviewState): WorldStatePanel {
  const combat = inCombat(state)
  return {
    sceneName: combat ? 'The Mill Causeway' : 'The Drowned Lantern',
    sceneDescription: combat
      ? 'A raised causeway of black planks over the reeds, the mill a dark shape against the clouds.'
      : 'A low roadside inn with its back to the reeds, a peat fire, and three driverless wagons in the yard.',
    sceneType: combat ? 'combat' : 'social',
    mood: combat ? 'dread' : 'uneasy',
    dangerLevel: combat ? '4' : '2',
    activeQuests: [
      {
        id: 'grey-caravan',
        title: 'The Drivers of the Grey Caravan',
        status: 'active',
        stage: 'Find out where the drivers went',
        objective: 'Follow the dark lanterns toward the mill',
        objectives: [
          { id: 'ledger', description: 'Search the abandoned wagons', status: 'completed', optional: false },
          { id: 'lanterns', description: 'Follow the dark lanterns toward the mill', status: 'open', optional: false },
          { id: 'bell', description: 'Silence the drowned bell', status: 'blocked', optional: false },
          { id: 'whip', description: 'Return the driver’s whip to his family', status: 'open', optional: true },
        ],
      },
    ],
    finishedQuests: [
      { id: 'toll', title: 'The Toll at Hollin Cross', status: 'completed', stage: 'Paid half a crown', objective: '', objectives: [] },
      { id: 'boot', title: 'Wren’s Lost Boot', status: 'failed', stage: 'The marsh kept it', objective: '', objectives: [] },
    ],
    clues: [
      { id: 'clue-ledger', title: 'The wet name', summary: 'The last name in the drivers’ ledger is MARROW, and the ink was still wet.' },
      { id: 'clue-lanterns', title: 'Six lanterns', summary: 'Six causeway lanterns went dark at dusk — one for each missing driver.' },
    ],
    handouts: [
      {
        id: 'handout-ledger',
        title: 'Last page of the caravan ledger',
        summary: 'Six names in six hands, each beside a lantern drawn upside down.',
        text: 'Hask — Odile — Brennet — Tam Wicker — the widow Saul —\nMARROW\n\nBeside every name, the same small drawing: a lantern with its flame turned downward.',
      },
    ],
    lore: [
      {
        id: 'lore-lamplighters',
        title: 'The Lamplighters’ Oath',
        summary: 'The brothers of the causeway swear to keep one flame lit for every traveller still on the road, and to put none out until the traveller is home.',
      },
    ],
    clock: clockFor(state),
    merchants: state === 'trade'
      ? [
          {
            id: 'marta-larder',
            name: 'Marta Vell',
            npcId: 'marta',
            buyRate: 0.5,
            stock: [
              { stockId: 'marta-0', name: 'Potion of Healing', type: 'potion', price: { gp: 50 }, priceLabel: '50 gp', quantity: 2 },
              { stockId: 'marta-1', name: 'Hooded lantern', type: 'gear', price: { gp: 5 }, priceLabel: '5 gp', quantity: 1 },
              { stockId: 'marta-2', name: 'Lamp oil (flask)', type: 'gear', price: { sp: 1 }, priceLabel: '1 sp', quantity: null },
              { stockId: 'marta-3', name: 'Hempen rope, 50 feet', type: 'gear', price: { gp: 1 }, priceLabel: '1 gp', quantity: 0 },
              { stockId: 'marta-4', name: 'Fen-silver ring', type: 'trinket', price: { gp: 75 }, priceLabel: '75 gp', quantity: 1 },
            ],
          },
        ]
      : [],
    ending: state === 'ending' ? FIXTURE_ENDING : null,
    presentNpcs: [
      { id: 'marta', name: 'Marta Vell', race: 'Human', role: 'Innkeeper', disposition: 'wary', status: 'present' },
      { id: 'teodor', name: 'Old Teodor', race: 'Human', role: 'Ferryman', disposition: 'guarded', status: 'present' },
      { id: 'ansel', name: 'Brother Ansel', race: 'Human', role: 'Lamplighter', disposition: 'friendly', status: 'present' },
    ],
    sceneItems: [{ id: 'ledger', name: 'Waxed caravan ledger', quantity: 1, type: 'object' }],
    availableExits: [
      { id: 'causeway', name: 'North causeway', status: 'known', type: 'road' },
      { id: 'yard', name: 'Inn yard', status: 'visited', type: 'yard' },
    ],
    knownLocations: [
      { id: 'hollin', name: 'Hollin Cross', status: 'visited', type: 'ford' },
      { id: 'mill', name: 'The Drowned Mill', status: 'rumoured', type: 'ruin' },
    ],
    knownNpcs: [],
    combat: combatStateFor(state),
  }
}

/** The player-safe snapshot: the drawn map, and in a fight the participants' vitality. */
function snapshotFor(state: StoryPreviewState): JsonRecord {
  if (state !== 'dying') return { worldMap: mapFixture() }
  return {
    worldMap: mapFixture(),
    combat: {
      status: 'active',
      participants: [
        {
          id: `player-${VIEWER_PLAYER_ID}`,
          team: 'player',
          conditions: ['unconscious', 'dying'],
          deathSaves: { successes: 1, failures: 1 },
        },
        { id: 'player-31', team: 'player', conditions: ['prone'] },
        { id: 'player-32', team: 'player', conditions: [] },
      ],
    },
  }
}

function actionComposerFixture(state: StoryPreviewState, activePlayers: ActivePlayer[]): ActionComposerProps {
  return {
    actionInputRef: createRef<HTMLTextAreaElement>(),
    actionText: '',
    adminPasscode: '',
    adminToolsUnlocked: false,
    canUseOperatorTools: false,
    setActionText: noop,
    updateActionText: noop,
    setAdminPasscode: noop,
    selectedCharacterName: viewerPlayer.character_name,
    selectedPlayerId: VIEWER_PLAYER_ID,
    activePlayers,
    composerMode: 'action',
    selectedDie: 'd20',
    sendPending: state === 'streaming',
    turnControl: {
      mode: inCombat(state) ? 'structured' : 'free',
      source: 'auto',
      activePlayerId: inCombat(state) ? VIEWER_PLAYER_ID : null,
      activePlayerName: inCombat(state) ? viewerPlayer.character_name : null,
    },
    turnControlStatusLabel: inCombat(state) ? 'Your turn' : 'Free play',
    selectedPlayerHasTurn: true,
    queuedActionText: '',
    retryRecoverableSubmission: () => false,
    clearQueuedAction: noop,
    updateTurnControl: noop,
    ttsEnabled: false,
    ttsStatusClassName: 'idle',
    ttsStatusLabel: 'Off',
    ttsLatencyLabel: '',
    canStopTts: false,
    stopTtsAudio: noop,
    submitAction: () => true,
    toggleAdminTools: noop,
    startDiceRoll: noop,
    preloadDiceRollDialog: noop,
    applyComposerMode: noop,
    updateSelectedDie: noop,
    rollMode: 'normal',
    setRollMode: noop,
    rollReason: '',
    setRollReason: noop,
    pendingRollOptions: [],
    rollTargetPendingTurnId: '',
    setRollTargetPendingTurnId: noop,
    spellName: '',
    selectedAbility: null,
    selectedAbilityKey: '',
    abilityOptions: [],
    updateRollAbilityKey: noop,
    updateSpellName: noop,
    interactionTargets: [],
    selectedInteractionTarget: null,
    selectedInteractionTargetId: '',
    selectedInteractionType: 'speak_to',
    setSelectedInteractionTargetId: noop,
    setSelectedInteractionType: noop,
    selectedInventoryAction: 'use',
    selectedItem: null,
    itemDraftName: '',
    itemQuantity: '1',
    itemCostGold: '',
    itemOptions: [],
    setSelectedItemId: noop,
    setItemQuantity: noop,
    updateSelectedInventoryAction: noop,
    updateItemDraftName: noop,
    updateItemCostGold: noop,
  }
}

/**
 * A complete SessionBoardProps for one preview state. Unknown values fall back
 * to `exploration` so a hand-typed `?state=` never breaks a preview page.
 */
export function makeBoardFixture(state: StoryPreviewState | string = 'exploration'): SessionBoardProps {
  const previewState: StoryPreviewState = isStoryPreviewState(state) ? state : 'exploration'
  const timeline = timelineFor(previewState)
  const activePlayers = makeActivePlayers(previewState)
  const latestDm = [...timeline.rows, ...(timeline.current ? [timeline.current] : [])]
    .reverse()
    .find((row) => row.role === 'dm')
  const ended = previewState === 'ended'
  return {
    activeSessionTitle: FIXTURE_SESSION_TITLE,
    campaignTitle: FIXTURE_CAMPAIGN_TITLE,
    sessionId: SESSION_ID,
    playerId: VIEWER_PLAYER_ID,
    showSceneMusicPlayer: false,
    duckMusicForNarration: false,
    sceneMusicSyncState: null,
    sceneState: sceneStateFor(previewState),
    onSceneMusicControl: noop,
    contentSettings: DEFAULT_CONTENT_SETTINGS,
    contentSettingsPending: false,
    canUseOperatorTools: false,
    canEditContentSettings: false,
    onContentRatingChange: noop,
    onContentToneTagsChange: noop,
    onBoardViewModeChange: noop,
    directorCommentary: null,
    sessionRecap: previewState === 'empty' ? '' : sessionRecap,
    onSpeakSessionRecap: noop,
    workspaceLoading: false,
    sessionLoading: false,
    mainTab: 'turns',
    setMainTab: noop,
    showMobilePresenceStrip: false,
    activePlayers,
    downloadCampaignChronicle: asyncNoop,
    downloadSessionChronicle: asyncNoop,
    downloadSessionJson: asyncNoop,
    sessionImportPending: false,
    sessionImportInputRef: createRef<HTMLInputElement>(),
    importSessionJson: asyncNoop,
    shareSession: noop,
    sessionMenuRef: createRef<HTMLDivElement>(),
    sessionMenuOpen: false,
    setSessionMenuOpen: noop,
    refreshCurrentWorkspace: asyncNoop,
    activeSession: {
      session_id: SESSION_ID,
      campaign_id: CAMPAIGN_ID,
      name: FIXTURE_SESSION_TITLE,
      created_at: '2026-11-09T19:00:00.000Z',
      status: ended ? 'ended' : 'active',
      updated_at: '2026-11-09T19:15:00.000Z',
      latest_activity_at: '2026-11-09T19:15:00.000Z',
      display_name: FIXTURE_SESSION_TITLE,
      turn_count: timeline.rows.length,
      summary_excerpt: sessionRecap,
      scene_title: null,
      is_archived: false,
      is_current: !ended,
      chapter_number: CHAPTER_NUMBER,
      ended_at: ended ? '2026-11-09T20:59:00.000Z' : null,
      chapter_recap: ended ? CHAPTER_RECAP : '',
      imported: false,
      play_now: false,
    },
    openRenameSessionDialog: noop,
    openDeleteSessionDialog: noop,
    notesCount: 0,
    turnFeedRef: createRef<HTMLElement>(),
    updateJumpToLatestVisibility: noop,
    sessionLogHasMore: previewState === 'exploration' || previewState === 'ended',
    olderLogLoading: false,
    loadOlderSessionLog: asyncNoop,
    turnRows: timeline.rows,
    dismissTimelineEntry: noop,
    reportedBadTurnIds: new Set(),
    reportingBadTurnIds: new Set(),
    reportBadTurn: noop,
    ratedTurnQualityIds: new Set(),
    ratingTurnQualityIds: new Set(),
    submitTurnQuality: noop,
    expandedTurnIds: new Set(),
    setExpandedTurnIds: noop,
    selectedPlayer: viewerPlayer,
    currentResponseEntry: timeline.current,
    latestDmText: latestDm?.text ?? '',
    sendPending: previewState === 'streaming',
    streamingTurnActive: previewState === 'streaming',
    pendingRollNotice: null,
    onPreparePendingRoll: noop,
    turnRecoveryGate: null,
    turnRecoveryPending: false,
    turnRecoveryError: '',
    turnRecoverySuccess: '',
    onResolveTurnRecovery: asyncNoop,
    combatState: combatStateFor(previewState),
    worldState: worldStateFor(previewState),
    gameplayControls: gameplayControlsFor(previewState),
    operationalError: '',
    dmExecutionStats: { tokens: 0, time: '', model: '', temperature: '' },
    welcomeText: 'Welcome to the table. Choose your opening move when you are ready.',
    showJumpToLatest: false,
    scrollTurnFeedToLatest: noop,
    questTitle: 'The Drivers of the Grey Caravan',
    sessionState: {
      session_id: SESSION_ID,
      campaign_id: CAMPAIGN_ID,
      status: ended ? 'ended' : 'active',
      is_current: !ended,
      chapter_number: CHAPTER_NUMBER,
      ended_at: ended ? '2026-11-09T20:59:00.000Z' : null,
      chapter_recap: ended ? CHAPTER_RECAP : '',
      current_location: 'The Drowned Lantern',
      current_quest: 'The Drivers of the Grey Caravan',
      rolling_summary: sessionRecap,
      active_segments: [],
      memory_snippets: [],
      state_snapshot: snapshotFor(previewState),
      updated_at: '2026-11-09T19:15:00.000Z',
    },
    campaign,
    recentMemory: [
      ['The Grey Caravan left three wagons in the inn yard and six drivers vanished.', 'Turn 2'],
      ['A ledger under the bench lists six names and, last of all, Marrow.', 'Turn 4'],
    ],
    clarificationRequest: null,
    resolveClarification: noop,
    onStartAdventure: noop,
    actionComposerProps: actionComposerFixture(previewState, activePlayers),
  }
}

/** A complete InspectorPanelProps for Ysolde Marrow, the viewer's character. */
export function makeInspectorFixture(): InspectorPanelProps {
  return {
    inspectorTab: 'party',
    setInspectorTab: noop,
    setMainTab: noop,
    baseUrl: '',
    auth: '',
    canUseOperatorTools: false,
    displayCharacter: {
      name: viewerPlayer.character_name,
      ancestryClass: 'Elf Ranger',
      level: 3,
      detailId: `Character ${VIEWER_PLAYER_ID}`,
    },
    characterAvatarSrc: viewerPlayer.profile_image,
    xpProgress: { current: 1450, max: 2700, percent: 54, label: '1,450 / 2,700 XP' },
    playersCount: 5,
    activePlayers: makeActivePlayers('exploration'),
    selectedPlayerId: VIEWER_PLAYER_ID,
    loadPlayer: noop,
    createDefaultPlayer: asyncNoop,
    editSelectedPlayer: noop,
    deleteSelectedPlayer: noop,
    selectedCampaignId: CAMPAIGN_ID,
    selectedSessionId: SESSION_ID,
    createPlayerPending: false,
    statBlock: {
      hp: '24 / 24',
      ac: '15',
      init: '+3',
      speed: '35 ft',
      abilities: [
        ['STR', '12', '+1'],
        ['DEX', '17', '+3'],
        ['CON', '13', '+1'],
        ['INT', '10', '+0'],
        ['WIS', '15', '+2'],
        ['CHA', '8', '-1'],
      ],
      proficiency: '+2',
      inspiration: true,
      background: 'Outlander',
      skillProficiencies: ['Perception', 'Survival', 'Stealth', 'Athletics'],
      skillExpertise: [],
      toolProficiencies: ['Herbalism kit'],
      languages: ['Common', 'Elvish', 'Sylvan'],
      hitDie: 'd10',
      savingThrows: [
        { ability: 'STR', modifier: '+3', proficient: true },
        { ability: 'DEX', modifier: '+5', proficient: true },
        { ability: 'CON', modifier: '+1', proficient: false },
        { ability: 'INT', modifier: '+0', proficient: false },
        { ability: 'WIS', modifier: '+2', proficient: false },
        { ability: 'CHA', modifier: '-1', proficient: false },
      ],
      deathSaves: null,
      hitDice: { current: 3, max: 3, die: 'd10' },
      conditions: [],
    },
    spellbook: {
      knownSpells: [
        {
          id: 'hunters-mark',
          name: "Hunter's Mark",
          level: 1,
          levelLabel: '1st level',
          source: 'Ranger',
          sourceType: 'class',
          sourceDetail: 'Ranger spells known',
          description: 'You choose a creature you can see and mystically mark it as your quarry.',
          tags: ['concentration', 'bonus action'],
          catalog: 'srd',
          prepared: true,
        },
        {
          id: 'fog-cloud',
          name: 'Fog Cloud',
          level: 1,
          levelLabel: '1st level',
          source: 'Ranger',
          sourceType: 'class',
          sourceDetail: 'Ranger spells known',
          description: 'You create a 20-foot-radius sphere of fog that heavily obscures the area.',
          tags: ['concentration'],
          catalog: 'srd',
          prepared: true,
        },
      ],
      preparedSpellNames: ["Hunter's Mark", 'Fog Cloud'],
      sources: ['Ranger'],
    },
    spellResources: {
      castingMode: 'known',
      slots: [{ level: 1, current: 2, max: 3 }],
      pactSlot: null,
      arcanum: [],
      concentration: '',
    },
    characterTraits: [
      {
        id: 'favored-foe',
        name: 'Favored Foe',
        category: 'class',
        typeLabel: 'Class feature',
        source: 'Ranger 1',
        description: 'When you hit a creature with an attack roll, you can mark it and deal an extra 1d4 damage.',
        actionType: 'reaction',
        cooldown: 'Proficiency bonus per long rest',
        active: false,
      },
      {
        id: 'fey-ancestry',
        name: 'Fey Ancestry',
        category: 'ancestry',
        typeLabel: 'Ancestry trait',
        source: 'Elf',
        description: 'You have advantage on saving throws against being charmed, and magic cannot put you to sleep.',
        actionType: 'passive',
        cooldown: '',
        active: true,
      },
    ],
    inventoryRows: [
      {
        id: 'longbow',
        item: 'Longbow',
        count: '1',
        weight: '2 lb',
        icon: 'sword',
        weightValue: 2,
        type: 'weapon',
        subtype: 'martial ranged',
        equipped: true,
        slot: 'main_hand',
        equippable: true,
      },
      {
        id: 'arrows',
        item: 'Arrows',
        count: '17',
        weight: '0.9 lb',
        icon: 'spark',
        weightValue: 0.9,
        type: 'ammunition',
        subtype: '',
        equipped: false,
        slot: '',
        equippable: false,
      },
      {
        id: 'ledger',
        item: 'Waxed caravan ledger',
        count: '1',
        weight: '1 lb',
        icon: 'archive',
        weightValue: 1,
        type: 'quest',
        subtype: '',
        equipped: false,
        slot: '',
        equippable: false,
      },
      {
        id: 'healing-potion',
        item: 'Potion of Healing',
        count: '2',
        weight: '1 lb',
        icon: 'potion',
        weightValue: 1,
        type: 'consumable',
        subtype: 'potion',
        equipped: false,
        slot: '',
        equippable: false,
      },
    ],
    inventoryWeightLabel: '19.9 / 180 lb',
    inventoryGoldLabel: '43 gp',
    equipmentPendingItemKey: null,
    toggleInventoryEquipment: asyncNoop,
    memorySnippetCount: 2,
    visibleRecentMemory: [
      ['The Grey Caravan left three wagons in the inn yard and six drivers vanished.', 'Turn 2'],
      ['A ledger under the bench lists six names and, last of all, Marrow.', 'Turn 4'],
    ],
    worldStatePanel: worldStateFor('exploration'),
    mapPanelTitle: 'The Fenmarch Causeway',
    mapDescription: 'The pilgrim causeway from Hollin Cross north to the drowned mill.',
    mapMeta: { explored: '38%', threat: 'Rising', threatTone: 'medium', weather: 'Heavy rain' },
    questTitle: 'The Drivers of the Grey Caravan',
    selectedSegment: null,
    maps: [],
    createDefaultMap: asyncNoop,
    campaign,
    createMapPending: false,
    mapManagementForm: { title: '', description: '', visibility: 'player' },
    setMapManagementForm: noop,
    mapSavePending: false,
    saveMapManagement: asyncNoop,
    segments: [],
    segmentSavePending: false,
    activateSegment: asyncNoop,
    segmentDeletePendingId: null,
    deleteSegment: asyncNoop,
    segmentManagementForm: {
      title: '',
      description: '',
      triggerCondition: '',
      tags: '',
      isTriggered: false,
    },
    setSegmentManagementForm: noop,
    createSegment: asyncNoop,
    campaignPackSnapshot: null,
    campaignPackControlPending: null,
    controlCampaignPackProgress: asyncNoop,
  }
}
