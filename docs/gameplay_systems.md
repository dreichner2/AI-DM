# Gameplay Systems and Mechanical Boundaries

This document describes the implemented gameplay contract: the rules a player
can rely on. It is intentionally more conservative than the data schema: a
field is not called functional unless the normal player flow validates,
applies, persists, projects, and restores it. The sections "Partial gameplay"
and "Narration-only areas" at the end list what is not rules-backed yet.

## Turn Lifecycle and Recovery

Player input follows one state path:

1. A typed action intent, or free-text words that name one action (see "How a
   Player Acts"), declares the attempted action.
2. Pre-narration validation resolves the actor, target, scene, equipment,
   resources, combat turn, and action economy.
3. Safe deterministic changes apply to `Session.state_snapshot` and the owning
   `Player` rows before narration.
4. The narrator receives current structured state plus the already-applied
   result.
5. Post-narration extraction may propose additional changes. Validation may
   accept, modify, reject, or request clarification; prose never writes gameplay
   state directly.
6. Stable change IDs and the state, interactable-action, gameplay-event, and
   combat-reward ledgers prevent retry/reconnect duplication.
7. Player projections remove private character detail, hidden campaign-pack
   state, and enemy-planning data.

An explicit typed item, travel, rest, spell, class-capability, shop, or
scene-object action that the rules validator marks invalid is persisted as a
deterministic rules rejection and stops before the narrator. Narration cannot
make an exhausted spell, stale item, unavailable capability, contradictory
object transition, blocked journey, failed trade, or illegal rest sound
successful.

Live scene state outranks canon and remembered narration. Canon projection may
initialize or repair a legacy location with no authoritative turn marker, but
it cannot overwrite a validated scene transition. Story threads may project
their own journal hooks; they cannot reactivate or complete mechanical quests.

### Every accepted turn finishes exactly once

- Every turn row carries a durable lifecycle marker
  (`metadata.lifecycle.phase`), written in the same transaction as the write
  that reaches the phase: `accepted` → `rules_applied` → `narration_saved` →
  `effects_applied` → `finished`. Rules rejections, failures, clarifications and
  group-roll waits are terminal and written as `finished`. Turns stored before
  the marker existed count as finished.
- A turn left unfinished by a stopped process resumes from its last phase and
  never repeats one: a turn with saved narration does not call the narrator
  again; one with applied effects skips straight to the structured-turn advance
  (itself recorded as done, so it never runs twice), canon enqueue and the final
  save.
- Three things resume an unfinished turn: the same player re-sending the same
  `client_message_id`; any new submission to the session, which first finishes
  up to three abandoned turns, oldest first, while it holds the turn lock; and
  joining the session, which starts a background recovery that takes the turn
  locks only when they are free. After three failed resume attempts the turn is
  marked failed behind the operator recovery gate.
- A failure while saving the narration keeps the narration as
  `narration_saved`, so the retry the player is told to make resumes the turn
  instead of being answered as a duplicate.
- Turns serialize per campaign: a turn or state mutation locks its session, its
  campaign-pack sibling sessions, and every other live (active or unlabelled)
  session of the same campaign, in ascending session-id order, because they
  share Player rows. Post-narration effects are applied onto freshly read
  character rows, so an edit made while narration was streaming survives.
- When a turn finishes cleanly its pre-turn snapshot copies are pruned
  (`state_pipeline.snapshotsPruned`); unfinished, failed and recovery-gated
  turns keep them.
- An absent player cannot block the table. When the player holding the
  structured turn has disconnected, others are told
  `{name} has the turn but is away. Their turn is skipped in {n}s if they do not return.`
  After `AIDM_ABSENT_TURN_GRACE_SECONDS` (default 60, at least 10) the turn is
  skipped, a `{name} is away; their turn was skipped.` line is logged, and the
  waiting action is judged against the new turn holder. Nothing is ever queued.
- The opening of an adventure is a marked turn (`message_kind: "opening"`): it
  never asks for a roll, makes no helper-model calls, and the narrator opens the
  scene from the pack's opening brief.
- If both post-narration state paths fail, the narration stays in the
  transcript but the turn is `failed` and later turns pause behind the
  recovery gate (see [runtime state boundaries](runtime_state_boundaries.md)).

## Chapters

A campaign is one continuing adventure for one party; each session is a
chapter of it, and exactly one chapter is current.

- Starting a chapter (`POST /api/sessions/start`, the Story menu's "Start next
  chapter") with `continuity: "continue"` (the default) carries the world
  forward: everything that describes the world and the party — the scene and
  place, places and their saved scene state (scene objects, their states and
  loose items), known NPCs, quests and objectives, flags, clues, factions, maps,
  handouts, lore, the campaign-pack mirror and progress, the day clock, the
  merchants and their remaining stock, content settings, and the ledgers.
  What belongs to one sitting starts fresh: combat, turn control, presence,
  pending rolls, recovery gates and per-chapter bookkeeping. `continuity:
  "fresh"` starts all of it again; a campaign created from a pack is re-seeded
  from the pack's opening (its starting clock and full shop stock).
- The previous chapter becomes `ended`: it stays readable (state, log, export,
  chronicle) but every live action and socket join returns 409
  `session_ended` with the current chapter's id. A closing line, `Chapter closed.
  The adventure continues in the next chapter.`, is logged in it.
- A new chapter opens with `**Chapter {n} begins.**` and, when there is one, a
  `Previously:` line: the previous chapter's saved recap (at most 1,200
  characters, cut at a sentence), otherwise `{place} — {first sentence of its
  last narration}` (the title's continue line); without either the line is
  left out. The rolling summary is the narrator's memory and is never shown to
  a player: not here, not in session lists, state reads, recaps, player saves
  or the chronicle.
- An unfinished fight, pending roll or running turn blocks a new chapter with
  409 `chapter_unsettled` (`Finish the current fight or roll before starting a
  new chapter.`); only an operator may force it, and transient state is then
  not carried.
- Play Now and the title's Continue open the current chapter; when a campaign
  has only ended chapters, a continued chapter is started.
- The heroes' word (`promises`), the players' plans, the director's clocks and
  used nudges carry into a continued chapter.

### Where a chapter ends, and coming back

- A pack checkpoint with `chapterBreak` ends the chapter where the story does:
  the turn that completes it carries a `chapter_end` story beat holding the
  chapter-end entry (`END OF CHAPTER 1 · The Empty Caravan`, `WHAT YOU DECIDED`,
  `STILL OPEN`, the teaser; `docs/campaign_packs.md`), kept in the log. The next
  action, anyone's, begins the next chapter (`POST
  /api/sessions/{id}/next-chapter`, open to players, once: a second request
  replays the first; 409 `no_chapter_break` before a break, `chapter_unsettled`
  while a fight or roll holds it) and is sent there.
- Plans: `/plan <words>` in the action box, or the Actions menu's `Note a plan`,
  notes one (`POST /api/sessions/{id}/plans`); the journal lists them under
  `PLANS`, each with `Done` (`DELETE /api/sessions/{id}/plans/{planId}`). With a
  narrating model, words to a person the pre-narration helper reads as an
  intention for later become a plan (beat `Noted as a plan — …`), at most one a
  sitting. A player leaving by `Exit to title` with no plan noted this sitting is
  asked `Before you go — what do you mean to do next?` (`Skip`).
- Coming back: when the last turn is more than thirty minutes old, the viewer
  sees above the action box, until their first action, `WHERE YOU STAND` (the
  place, the room and the active objective in one sentence, and up to two people
  whose memory of the viewer's heroes or regard changed in the last sitting) and
  `YOU MEANT TO` (the plans): `GET /api/sessions/{id}/coming-back`. A sitting is
  a run of turns with no gap longer than thirty minutes.
- The director note (one a turn, outside a fight: a callback, a clock step,
  clarify, a nudge, a breath) and pack clocks are described in
  `docs/campaign_packs.md`.
- The ending card adds `WHAT BECAME OF THEM`, `YOUR WORD` and `Play it again` (a
  new adventure from the same ready-made pack). Reaching an ending writes the
  account's (or local workspace's) record for the pack; the title screen's `Your
  chronicle` (`GET /api/sessions/chronicle`) lists finished adventures with the
  endings reached and `Things you never found: n`.

## How a Player Acts

### Typed words that name one action run that action

Free text is read against what the acting hero can see before anything else
happens, in every narrator mode. When the words clearly name an action, the
turn takes exactly the path that action's button takes:

- sentence-level mapping: the message's first two sentences are split into
  clauses on ` and `, ` then `, `, then` and `;`, and each clause is mapped on
  its own. Within a clause the verb may follow a lead-in of up to eight words
  (`I crouch and search…`, `Carefully, I open…`) or sit in a purpose phrase
  (`climb into the lead wagon to search it`); `it`, `them` or `that` after the
  verb is the thing named last before it in the message, else the single scene
  object or item the session's previous turn acted on, else nothing;
- a pickup (`take`, `pick up`, `grab`, `pocket`, `scoop up`, `gather up`,
  `help myself to`, `collect`, ... the key; `take the coins`; `take
  everything`), an object action (`search the bench`, `read the ledger`, `open
  the strongbox`, `force the grate`, or an offered action's own label), travel
  to a known neighbouring place (`go`, `head`, `walk`, `travel`, `ride`,
  `march`, `hike`, `set out`, `set off`, `start`, `make my way`, `make for`,
  `leave for`, `return`, `go back`, `follow`, `take`, with an optional `to`,
  `toward`, `up`, `down`, `along`, `for`, `onto`, `into`, `through`, `out on`,
  `out along` or `back to`: `I head up the old north road`), and a short or
  long rest (`short rest`, `make camp`, ...) become the same typed intent their
  controls send; looking around and talking to someone present are recognised
  for the narrator;
- naming rule: case-insensitive, leading articles and possessive `'s` ignored;
  the phrase must be the whole name or a run of whole words from it (at least
  one word of three letters or more) that matches exactly one visible
  candidate. Two candidates, or none, map nothing and the turn is narrated as
  free text;
- a clause maps nothing when it is negated or hypothetical (`not`, `n't`,
  `never`, `without`, `before I`, `if I`, `should I`, `could I`, `can I`,
  `would`, a question mark), when its verb sits inside quotation marks
  (speech), or when its lead-in names someone present or directs or watches
  someone else (`tell`, `let`, `order`, `watch`; `let me` is the hero's own);
- a pickup of something that is not there is refused (`There is no lantern here
  to take.`);
- only the first mapped action of a message is taken; every later one is a
  deferred action, by its offered label, and is not taken. The Safe Mode reply
  ends naming the first of them: `One thing at a time — the lead wagon is still
  there to search.`; an AI narrator sees them under "Not done" in the engine
  brief;
- with the scripted narrator, words that map to nothing get a one-time hint
  beat pointing to the suggestions and to Settings;
- a captive the party holds (see "Captives") is questioned like a person
  present: `question`, `interrogate`, `ask` or `talk to` it.

### Talking with people

`Talk to {Name}` opens a conversation with someone in the hero's room (words
aimed at someone next door step the hero in first): their greeting the first
time, `{Name} turns to you.` after. While it lasts the hero is offered up to four
of the person's topics (`Ask about the caravan`, then `say` topics such as
`Promise to bring word of the drivers`), `Say something else` and `Leave it
there`. Typed words outside a fight that talk to someone, or are said to the
person the hero talks with (or names in their room), become a `talk` intent and
are never read for a roll; words that name another action are that action and
end the conversation, as do leaving, travel, a fight starting or stepping into
another room. Two heroes may talk to two people at once.

The engine holds every conversation turn (`conversations.py`): a chosen topic's
check is rolled by the engine and its line told with its effects (flags, clues,
places, items, disposition moves of this person and others, promises,
memories); typed words choose a topic by its label or keywords, or are one of
six speech acts recognised by cue words (`speech_acts.py`) — with an AI
narrator, words with no cue go once to the pre-narration helper, which only
names the act — answered by the person's authored reaction or the engine's
defaults; other words are free speech and change nothing (`{Name} has nothing to
say to that.` in Safe Mode). A clicked topic, `Talk to` and free speech reach
only the narrator (free speech with no cue the helper once as well); `Leave it
there` is told by the engine and reaches no model; the post-narration helper is
never asked about a conversation turn. People keep a disposition from −2 to 2
(the journal shows its word), what they remember of each hero (eight at most,
two lines in the journal), and callbacks that bring a remembered act back once,
opening the narration. A narrating model gets the person's personality,
disposition, the lines told, what they remember and `May tell if asked:` (the
available `ask` topics' lines; a secret never, unless an available topic tells
it), under rule 17. See People You Can Talk To in `campaign_packs.md`.

### Typed words in a fight

In a fight, typed words that are not a roll are decided by the fight's own
rules (`free_text_actions.fight_words`) before anything is spent; the phrase
list that reads a message for a roll is not consulted:

- **Speech is free** on any turn: a message that begins with a quotation mark,
  ends with `?`, or whose first verb is say, ask, tell, shout, yell, call,
  whisper or taunt costs nothing, changes no state (the narration is not read
  for state changes) and does not end the turn; an AI narrator answers in the
  scene in at most three sentences, and the Safe Mode reply is the turn line.
- **Not the hero's turn**: anything but speech is refused before narration,
  `It is Thalion's turn.`, and nothing is spent.
- **On the hero's turn** a clause (split and read as above, lead-in, negation
  and quotation rules included) that names a fight action by its verb, any
  tense, takes exactly that action's button path: **attack** — attack, hit,
  strike, stab, slash, shoot, fire at, swing at, cut; **grapple** — grab,
  grapple, tackle, wrestle, pin, hold down, seize, restrain, jump on; **shove
  down** — shove, push, trip, knock down, knock over, throw down, sweep;
  **bind** — bind, tie (up), truss, cuff, subdue, capture, take alive, knock
  out; **demand surrender** — surrender, yield, give up, stand down, drop it,
  lay down; and the other actions by their labels (close in, advance, fall
  back, dodge, hide, help, disengage, dash, stabilize, end turn). `Close in and
  tackle him` is the grapple that closes in first. A blow aimed at a thing
  (`I cut the rope`) names no attack.
- The target is the enemy named; with `him`, `her`, `it`, `them` or `the last
  one`, the only one there is; else the hero's last target; else the only enemy
  standing. An action that is not legal is refused with its reason and no
  narration (`Bind` on an unhurt enemy: `It is still on its feet and
  fighting.`).
- On the hero's turn, words that name no fight action are also **speech**
  (free, as above) when their first verb is question, interrogate, demand,
  threaten, warn, beg, plead, answer, reply, talk, speak, greet, curse, swear,
  laugh, thank, insult, mock or taunt (`Question the Ashmarked Cutthroat`), and
  a **look** when it is look, watch, study, examine, read, listen, check, eye,
  glance or "search for": free like speech, it rolls nothing and changes
  nothing; the scripted narrator answers with where everyone stands
  (`The ashmarked cutthroat is on you, unhurt; Corin Ashby is a few strides
  off.`, from each participant's band and health), an AI narrator describes
  what the hero sees in at most two sentences.
- **Anything else** typed on the hero's turn is an **improvised action**: it
  costs the action and rolls the better of Strength (Athletics) or Dexterity
  (Acrobatics) against DC 12; on a success against a named (or the only) enemy
  that enemy is `off_balance` until the end of its next turn (its next attack
  has disadvantage). Its result is in the turn's "Did" and a check beat, and the
  narration tells what the hero tried from that result; a failed attempt says
  the hero tries (`Thalion tries to kick the lantern at it, but it does not come
  off.`), as every failed attempt does (`You try to break the strongbox open. It
  will not give.`).

### A roll with no check pending

A roll the player makes on their own from the dice tray, with no pending check,
roll gate or contest, is not a check: it has no DC and no success or failure.
(A roll sent with words that name a check, `I persuade the guard` with the
dice, stays that check.)
It adds the ability modifier only when the player picked an ability. Its beat
reads `d20: 10` (`Strength: 13 (d20 10 + 3)`) and the scripted narrator answers
`You roll a 10.` (`You roll a 13 for Strength.`). A roll that answers a pending
check is that check, judged against its DC.

### The engine brief and the narration hygiene pass (AI narrators)

An AI narrator never resolves what only the engine may resolve. Directly above
`PLAYER INPUT` its prompt carries an `ENGINE THIS TURN` block built from the
turn's real results (`state_pipeline.engineBrief`, never from the model):
`Did:` each executed result in plain words (an object action with its check,
its description or action text and what it turned up, things taken, arrival,
rests, spells, attacks and enemy turns, the turn's beats such as coins, XP,
items received, clues and places, refused actions, the roll this turn
resolves); `Not done:` the deferred actions and each engine-owned thing the
player's words name (same naming rule) that has no result, by its labels;
`Here:` the visible objects with their available labels, loose items, ways
onward and shops, and any captives held there (their enemy record and
`captiveKnows`, as people present); and the place the party is at. In a fight
"Did" carries the hero's resolved action with its check or attack numbers and
outcome, each enemy's resolved turn, and any yield, capture, breaking free or
opportunity attack, or else `No enemy has acted since the last narration.` Ten
rules follow it: tell each result faithfully and add none; for anything not
done, at most one sentence of the hero starting toward it; the party stays put
unless it arrived; no rolls for anything the engine owns; found is not taken,
and gains only when "Did" says so; speak to the hero as "you", no closing
question, no markdown; people speak from what they know; resolve what the hero
attempted from "Did", never leaving it "still to be done", handing the decision
back or ending on an available action; never say whose turn it is (the engine
adds that); enemies do only what "Did" says, otherwise they hold, watch or
brace. Five more set the pace: be brisk (in a fight at most 60 words, outside
one 90, arriving somewhere new or opening a chapter 140, an ending may run
long); open on what the hero does or perceives, not on the weather twice
running; end on what matters now, usually one concrete thing that presses for a
response, but after a win, a discovery, a kindness or a joke let that moment
land and end there, never on a question to the players, a list of options or
the name of a button; when someone present would plainly react, give them one
short spoken line in their own voice; and never start a fight or bring in a
creature, enemy or new danger unless "Did" says a fight began. On a turn whose
rolls are shown as roll cards (`state_pipeline.storyRolls`) a last rule says
the dice are shown and the narration restates no totals, ACs, DCs or damage
numbers. Each enemy's `Bark:` line (see "Enemy barks") sits under its result,
to be used word for word when it is speech. In a place with rooms (see "Rooms
and areas") a `Where:` line says who stands where (`Thalion is in the yard with
Osric Hale and Tovin Bell. Next door, in the common room: Mara Reedwake.`),
"Here" holds only what is in the acting hero's zone, and "Did" tells a step into
another zone (with its description the first time) or a call to someone next
door; in a fight a `Positions:` line gives how far everyone standing is from the
acting hero in reach words. A rule says that someone or something in another
area is not here, and that a hand weapon does not strike beyond 5 ft unless
"Did" says the hero closed in.

Scene objects reach the model's context with their player description, state
and available labels only; an object's authored description, contents and
action text appear only in the "Did" result of the turn that reveals them.

After the model answers, and before the reply is stored and sent, a
deterministic hygiene pass cleans it: a closing `What do you do?` (or
`What now?`, quoted or not) is dropped outside the opening; `*`, `_` emphasis,
backticks and leading `#` are removed, keeping the words; a sentence that only
restates gains the turn's beats already report (`Arden Vale gains 100 XP, 25 gp,
and one Ash-Marked Token.`) is dropped, and so is a `You gain/take/receive …`
sentence about coins or items the results do not contain (`You gain 15 gp.`
before they are taken); and a roll request for a scene object, loose item,
travel, a purchase, an attack or a spell is dropped with no roll gate opened,
unless the engine itself waits on that roll. Roll requests for improvised
actions stay. In a fight, a sentence that says whose turn it is or that a turn
is over (`Corin Ashby, … it is your turn.`, `… is up next.`, `Your turn is
spent.`, `the next move`) is dropped unless it is spoken, and the reply ends
with the engine's own turn line, the one the scripted narrator prints (`It is
still Thalion's turn.` / `Corin Ashby is up next.`). In and out of a fight, a
final sentence that is only the label of an action offered this turn (`Search
the strongbox.`) is dropped. On a turn that sets a finishing blow the reply
ends with the engine's own closing question after the turn line (see "The
finishing blow"); the model's copies of it are dropped so it is asked once.
Last, a reply outside an opening, an arrival and the adventure's ending that is
longer than twice its limit (120 words in a fight, 180 outside) is cut at the
last sentence end within that, never mid-sentence or inside a quotation
(telemetry `narration.hygiene.trimmed_total`). The scripted narrator is not
touched by either.

### Numbers live on the roll cards

On a turn whose pipeline carries roll cards (`storyRolls`), the scripted
narrator tells each rolled result without its totals, ACs, DCs or damage:
`Your longsword bites deep into the wardbound beast.`, `The wardbound beast's
pounce bears Seren Ashgrove down hard.`, `The pale lantern's lure slides off
Arden Vale.` Each rolled result has three sentences per outcome (hit, miss,
critical hit, save made, save failed), chosen from the turn, alone and in
party play; a manoeuvre, breaking free, an opportunity attack, a spell, healing,
a rest and a checked roll keep their sentences without the numbers, and a kill,
a yield or a break-free keep theirs. Without roll cards the sentences keep
their numbers. Story beats keep their numbers either way.

### Offered actions and refusal sentences

Every visible scene object (`currentScene.interactables[]` and `hazards[]`) in
the player view carries `offeredActions`: one entry per action the object
supports, in the order search, use, trigger, open, unlock, inspect, close, lock,
break, disarm, reset. Each entry has a `label` (the pack's `actionLabels`
wording, else `Examine {name}`, `Search {name}`, `Open {name}`, ...), `legal`
for the selected hero, `done` once taking it again would show nothing new,
`check` when it asks for a roll (with `dc` only when the pack sets `showDc`),
`choice: true` for a pack's alternative fates, `retry: true` after a failed
check, and, when not legal, a plain `reason`. Every loose item carries a
`takeLabel` (`Take the Caravan Strongbox Key`, `Take 15 gp`). The Story view
shows these as suggestions; it offers nothing the server would refuse.

A refused object action is one plain sentence, the same text as the offered
entry's `reason`, never a rule code: `That can't be done with the strongbox.`,
`The strongbox is locked.`, `The door is already open.`, `It is locked, and you
carry no key that fits.`, `You have nothing that will break the grate.`, `You
don't know enough to do that yet.`, `That can no longer be done.`, `Not while
the fight is on.`, and so on for every legality code. A name beginning "The" is
written `the …` inside a sentence; a proper name takes no article.

### The player's-eyes view

Every read the Story view makes (`state`, `log`, `events`, `export`, pack
progress, the campaign workspace) asks for `view=player&player_id=<hero>`: the
player-safe projection, told from that hero's side, even for the table's
operator. Offered actions are judged for that hero, private details (inventory,
spells, resources) are that hero's, hidden objects and enemy hit points are
not shown, and the journal block lists only what the party knows: the quest and
current stage, completed objectives and the active stage's open ones (never
future ones), discovered clues, people met, and places known. Table tools reads
the full operator projection. An operator may view as any hero of the
campaign; anyone else only as a hero their account controls.

### Story beats

Under each Dungeon Master turn the Story view shows quiet one-line beats that
say what changed. They are computed from the turn's before and after state in
the player projection (so they are right whichever code path made the change)
and stored on the turn, its log entry and the final `turn_status` event, in
this order:

| Kind | Text |
| --- | --- |
| `check` | `Investigation check: 14 — success`, naming the skill or ability once, `Check: 14 — success` when neither is known (` (DC 10)` only when the DC is shown; ` (taking your time)` after a retry); a fight maneuver or improvised action always shows its DC: `Athletics check: 17 — success (DC 12)`; a roll made with no check pending is not a check: `d20: 10`, or `Strength: 13 (d20 10 + 3)` with the ability picked |
| `hp` | `Took 7 damage` / `Healed 5` |
| `condition` | `You are prone` / `No longer prone` (only when a condition starts or ends); `You are out of the fight`; `You come round` for a hero who wakes; for an enemy newly held, captured or yielding: `Grappled — Ashmarked Cutthroat`, `Captured — Ashmarked Cutthroat`, `Ashmarked Cutthroat yields` |
| `death_save` | `Death save: 12 — success` |
| `combat_end` | `Victory`, `The fight ends — they yield`, `The fight ends — they flee`, `You got away` |
| `item_gained` / `item_lost` | `Taken — {item}` / `Received — {item}` (`×N` when more than one); `Used`, `Given`, `Sold — {item}` |
| `coins` | `+15 gp` / `−50 gp` |
| `clue`, `objective_complete`, `objective_new`, `place` | `Clue — {title}`, `Objective complete — {title}`, `New objective — {title}`, `New place on the map — {name}` |
| `xp`, `level` | `+100 XP`, `Level 2 reached` |
| `defeat` | `Defeated — you wake later with 1 HP` / `You come to — 1 HP` |
| `thread` | `A thread of your own — Edda Vale` (a seeking line; any other reads `A thread of your own`); it opens the Journal |
| `inspiration` | `Inspiration — mercy` |
| `ending` | `The story ends — {ending title}` |

In party play (more than one hero in the session) one story is shown to every
player, so each beat about a hero names them: `Seren Ashgrove took 7 damage`,
`Arden Vale is out of the fight`, `Arden Vale comes round`, `Seren Ashgrove —
Investigation check: 15 — success`, `Mara Fen: death save: 12 — success`,
`Arden Vale took the Caravan Strongbox Key`, `Arden Vale +6 gp`, `Seren Ashgrove
reached level 2`; a coin or XP gain every hero received alike is one beat,
`+10 gp each` / `+75 XP each`. Beats about the party or the world read the same.
The narration does the same: the scripted narrator names the acting hero
(`Seren Ashgrove closes in on the wardbound beast. Their mace lands …`,
`It is still Seren Ashgrove's turn.` while nobody else has acted since, `It is
Seren Ashgrove's turn again.` once the round has come back to them; alone it is
always `It is your turn.`), and the AI narrator's rule 6 tells it to name every
hero and never write "you" for one. When a hero's turn ends with nothing else to
tell, the narration is only the line saying who acts next.

## Character Foundations

- New characters are made through guided creation (who, calling, class skills,
  review) and require a nonblank class and all six ability scores in a legal
  point-buy allocation. Classless, incomplete, out-of-range, and over-budget
  profiles fail closed. Creation asks for the class's skill choices, a rogue's
  two Expertise skills, and a fighting style where the class has one. A created
  hero starts with their class's kit and 15 gp; the ready-made heroes carry
  their own.
- Curated backgrounds are authoritative packages of skill, tool, and language
  proficiencies. Unknown new background IDs fail closed; legacy free text remains
  display-only.
- Class, level, ability scores, proficiency bonus, background/race skill grants,
  saving-throw proficiencies, stored expertise, wounds, and equipment feed
  authoritative roll or derived-stat logic. Race also contributes its
  implemented language, skill, and innate-spell grants.
- Class hit dice determine initial and later maximum HP. Level changes preserve
  current damage and never revive a dead character. Profile level increases
  cannot exceed the level earned by already-persisted XP.
- A class/race respec removes obsolete automatic spell provenance and stale
  resource/feature state before rebuilding the new grants.
- Generic profile PATCH requests cannot replace stats, character sheets,
  inventory, or weapon proficiencies. Those mechanical changes must use the
  validated progression, action, equipment, or inventory paths.
- Duplicate proficiency sources do not stack. Expertise is the only
  double-proficiency path.
- A hero below three quarters of their HP rolls d20s at −1 (half or less −2, a
  quarter or less −4): the wound penalty. Every d20 roll it moves carries
  `modifiers: [{"label": "Wounded", "value": -2}]` on its roll event and
  persisted roll, and the scripted narrator names it (`— your wounds cost you
  2`) on the hero's first attack it changes.

### Your hero in the story: own threads and Inspiration

- A hero may carry three threads of their own (`ties` on the character sheet,
  all optional): what they want (`desire`: one of `to find someone`, `to be
  recognised`, `to clear a debt`, `to keep a duty`, `to learn what is
  forbidden`, `to prove someone wrong`, or one line of their own of at most 80
  characters), who or what they would not abandon (`attachment`: a name of 2–40
  characters and a relation — sister, brother, mother, father, daughter, son,
  friend, mentor, old comrade, the one who wronged you, a place, an order, a
  keepsake; `friend` by default) and what they carry (`burden`: one sentence of
  at most 120 characters). They are set on the creator's Review step (`Your own
  threads`) and in the Edit character sheet (`ties` in the create and update
  payloads; any other sheet field still cannot be written directly), shown on
  the Character tab, and are part of the player view of one's own hero only.
  The ready-made heroes carry authored ones (Arden Vale would not abandon his
  sister Edda Vale and wants to keep a duty).
- The adventure tells them through its tie lines (see `campaign_packs.md`). A
  line whose moment comes (an object action that succeeds, a first arrival, an
  encounter starting, a long rest) fires once per adventure, for a present hero
  who has that tie: the hero who acted when they have it, else the one with the
  fewest lines told, the earliest first. No hero with the tie: nothing fires and
  nothing is shown. A fired line is its own paragraph in the scripted
  narration, and is given to a narrating model as `Personal thread — tell it in
  one or two sentences, facts unchanged: "…"`; it makes the `thread` beat, is
  listed in that player's Journal under `Your threads`, and grants the hero
  Inspiration. The engine brief's `Hero:` line gives a narrating model the
  acting hero's desire and attachment; scripted play uses only authored lines.
  An ending's line follows the ending text for each hero whose seeking line
  fired.
- Inspiration: a hero holds at most one (`inspiration` on the hero, kept on the
  character row; the dock shows a small mark beside their name). It is earned
  by what a hero does, never by how much they say: a tie line of theirs firing
  (`a thread of your own`), ending a fight without killing — a surrender or a
  negotiation on their own turn — or sparing a foe at the finishing blow
  (`mercy`), and any object action the pack marks `inspires` (its reason). Every
  grant goes through `hero_threads.grant_inspiration`, which returns the
  `hero.inspiration` change or nothing when the hero already holds one; only
  the engine makes `hero.inspiration` and `hero.thread` changes.
- `Take heart (Inspiration)` spends it: free, in a fight (a fight action that
  never keeps a turn open, so a hero holding Inspiration still has their turn
  ended for them) or out of one, told without a model (`You take a breath and
  remember why you came.`). The hero gains the active effect `inspired`
  (consumed on `d20`): their next attack roll, ability check or saving throw —
  their own or one the engine makes for their action — is made with advantage,
  then it is spent. Leaving the action alone keeps the Inspiration.

## Dying, Death Saves, Stable and Out of the Fight

- A hero is beaten, never killed: where a creature would die (a third failed
  death save, a failure added by damage at 0 HP, or massive damage) a player
  character is instead out of the fight until it ends, and then wakes with 1 HP.
  While the fight is on nothing raises them, and they take no turns and are never
  targeted.
- Player characters at 0 HP fall `unconscious` and `dying` (out of combat:
  `stable`) instead of leaving the game. Damage left over at 0 HP that reaches
  the maximum HP puts them out of the fight at once; other damage at 0 HP adds a
  death-save failure (two on a critical). Out of the fight is the conditions
  `unconscious`, `stable` and `out_of_fight`, death saves reset to 0/0, still
  alive; an old save's `dead` hero is read as out of the fight.
- When an ending turn passes a dying character's initiative slot, the engine
  rolls its d20 death save (10+ success, natural 1 two failures, natural 20 back
  at 1 HP; three successes `stable`, three failures out of the fight) and
  records it as a visible `combat.death_save` change. Vitality conditions and
  death-save tallies persist on the Player row. Enemies and NPC allies are
  simply defeated at 0 HP.
- Healing a downed character (spells, Lay on Hands, potions, narrated healing)
  wakes it, unless it is out of the fight: healing and stabilising restore
  nothing to them, they are not offered as a target, and a typed attempt is
  refused with `They are out of this fight.` Stabilize (an action, Wisdom
  (Medicine) DC 10), Spare the Dying and a Healer's Kit make a dying ally
  `stable`.
- A hero who is down (dying, stable, unconscious or out of the fight) cannot act: anything
  other than an out-of-character message, or a rest for a stable hero outside a
  fight, is refused before narration with `You are down and cannot act.`

### Defeat and waking

- When a fight ends for any reason with at least one hero standing, every hero
  who is down (dying, stable or out of the fight) comes round with 1 HP at once,
  no longer prone, with no time passing; the turn carries `… comes round` for
  each of them.
- When every present player character is down (dying, stable and out of the
  fight all count), combat ends with
  `objective_failed` and the party is beaten, never killed, whether or not the
  fight is lethal. At the end of the fight the clock advances one phase, every
  hero wakes with 1 HP (no longer dying, stable, out of the fight, unconscious or
  prone; death saves reset), the enemies leave the scene, and the encounter
  records the outcome `defeat`, pays nothing, and is re-armed so its trigger
  can fire again. The turn carries the beat
  `Defeated — you wake later with 1 HP`. When the enemies' opening turns end
  the fight inside an operator's combat start, the party wakes before that
  request returns, in the same state revision and audit record.
- A lost or fled fight never ends the story. A beaten party's defeat fails no
  checkpoint. `lethalDefeat: true` on the combat or its encounter only means
  the defeat is decisive: it fails the checkpoint that names the encounter in
  `failWhen` and the story takes that checkpoint's failure route (the heroes
  still wake as above). A checkpoint failure, such as fleeing, never
  advances onto a terminal checkpoint: when the only way on is the ending, the
  checkpoint stays active to be tried again. The ending is reached only when the
  checkpoint before it completes; in the Play Now adventure, by choosing the
  road-stone's fate.
- Outside a fight the same waking applies at once, whatever caused it: when no
  combat is active and every present hero is down, each one comes to with 1 HP
  a phase later, before their next action is judged; the turn carries
  `You come to — 1 HP`.

## Conditions

One modifier engine (`aidm_server/combat/modifiers.py`) is the only place
condition rules live; every weapon, spell and enemy attack, saving throw, and
pending-roll check reads it. Advantage and disadvantage from any number of
sources cancel.

| Condition | Effect |
| --- | --- |
| blinded | Its attacks have disadvantage; attacks against it have advantage. |
| charmed | It cannot attack its charmer or target it with harmful spells. A spell's charm ends when the charmer's side damages it. |
| frightened | Disadvantage on attacks and ability checks while the source of its fear is present. |
| grappled | Speed 0. |
| incapacitated | Takes no turns, actions or reactions. |
| invisible | Its attacks have advantage; attacks against it have disadvantage. The Invisibility spell ends when its bearer attacks or casts. |
| paralyzed | Incapacitated, speed 0, fails Strength and Dexterity saves; attacks against it have advantage and a melee hit is a critical. |
| petrified | Incapacitated, fails Strength and Dexterity saves, resistant to all damage; attacks against it have advantage. |
| poisoned | Disadvantage on attacks and ability checks. |
| prone | Its attacks have disadvantage; melee attacks against it have advantage and ranged ones disadvantage. A prone creature that can act stands at the start of its turn, spending its movement. |
| restrained | Speed 0, disadvantage on its attacks and Dexterity saves; attacks against it have advantage. |
| stunned | Incapacitated, fails Strength and Dexterity saves; attacks against it have advantage. |
| unconscious | Incapacitated and not a legal target (the down are skipped); fails Strength and Dexterity saves. |
| slowed | Speed 0. |
| exhausted | Disadvantage on ability checks. |
| dodging | Attacks against it have disadvantage, advantage on its Dexterity saves; ends at its next turn. |
| hidden | Its next attack has advantage and ends the hiding; it cannot be targeted. |
| turned | It only moves away; ends when it takes damage. |
| dead, fled, retreated, surrendered, yielded, absent | Out of the fight: no turns, not targetable. |

Cover adds +2 (half) or +5 (three-quarters) to armour class; full cover blocks
targeting. "Melee band" rules use the coarse range bands below: a melee attack
is in the melee band, and a ranged attack is when either side stands in it.

### Active effects and modifiers

- Standard `activeEffects` records carry attack/save/check bonus dice, AC bonus
  and floor, roll modes, bonus damage, resistances, speed zero, cannot act,
  cannot react, no healing, temporary HP per turn and damage per turn, with
  round durations, concentration, and `consumeOn` effects that are spent the
  first time they apply. Providers registered with the engine (class features,
  for example) add further modifier and extra-damage sources.
- Resistance, vulnerability and immunity apply to every damage component.
- Effects tick at the start or end of the turn their record names; the turn
  advance processes every turn end and start it crosses, including skipped
  initiative slots.
- Effects that last until a long rest (Mage Armor, Aid, out-of-combat buffs)
  live on the character, persist in `Player.stats.active_effects`, shape armour
  class, and end on a long rest. Encounter-scoped effects end when combat ends,
  together with concentration that no longer holds any effect.

### Conditions wear off

A condition an enemy ability applies lasts until the end of the target's next
turn, or for the ability's `durationRounds` (1–10) of the target's turns. When
the ability has a save, the target repeats it (the party-scaled DC) at the end
of each of those turns, and success ends every condition of that use at once.
The narrator says `You shake off {ability}.` (`{Name} shakes off …` for another
hero) and the beat reads `No longer charmed`. Spell conditions follow the
spell's duration and repeat saves (see the spell table).

## Combat

### Starting a fight and initiative

- Hostile actions, pack encounter triggers (`on_enter`, `on_checkpoint`,
  `on_flag`, `on_interactable`, gated by `when`/`unless` and fired once unless
  `once: false`), narration and the operator API all start combat with stable
  participants and seeded, modifier-aware initiative. An encounter an object
  action settled never fires again.
- In an adventure with a campaign pack, narration alone never starts a fight:
  a reply that reads like one starting does so only when a pack encounter's
  trigger is due that turn, or when the acting hero's own message attacked a
  person present (a validated attack, or words that open with an attack verb
  aimed at someone in the scene). A hero who attacks someone present fights
  that person only, through the creature resolver, never a pack encounter's
  enemy groups. Nor does narration alone make the scene a fight: the
  post-narration reading never sets its `combatState` to `active`, its scene
  type to `combat` or its danger to a fight's (8); that follows only a fight
  the engine started. Adventures without a pack are unchanged.
- Initiative never rests on a participant the engine plays: enemies who win
  initiative take their opening turns in the same turn that starts the fight,
  before or right after narration. The action that started the fight is not
  resolved out of order; the character attacks on their own turn.
- A pack encounter's read-aloud `setup` is told when its fight starts, before
  the first blows, followed by the line of every adjustment that applied (see
  "What the party knows" below).
- Initiative, round, active actor, economy, participant HP, and combat end state
  survive database expiry, disconnect, and reconnect.

### Turn economy and actions

- `combat.flags.turnEconomy` persists action, bonus action, reaction, and
  movement budgets. Only the current present, conscious, living, non-fled,
  non-surrendered actor can spend them. Movement does not end a turn: attack and
  spell actions require an explicit End Turn, which advances through the enemy
  block and resets economy for the next round, unless the turn ends itself (see
  "Turn flow: End turn, the enemy phase and the time to act").
- Every character's action bundle carries the universal actions, each with a
  `cost` (`action`, `bonusAction`, `movement`, `reaction`) and, when unavailable,
  a plain reason: Advance (movement; one band inward, or close into melee on a
  chosen enemy), Fall back (movement; one band outward), Dash (action; one more
  movement this turn), Disengage (action; no opportunity attacks this turn),
  Dodge (action; `dodging` until the actor's next turn), Help (action; an ally in
  the same or an adjacent band gets advantage on its next attack), Hide (action;
  not in melee without cover; Dexterity (Stealth) against the highest passive
  Perception of the conscious enemies) and Stabilize (action; a dying ally in the
  same or an adjacent band; Wisdom (Medicine) DC 10). Class features that grant
  Dash, Disengage or Hide at another cost reuse these actions.
- Four maneuvers (`combat/maneuvers.py`), each costing the action and needing
  the enemy in melee reach (offered as `Close in and …` with the move unspent,
  as an attack is), rolled by the engine like Hide and reported by a check beat:
  Grapple (`combat.grapple`, `Grapple {enemy}`; Strength (Athletics) against DC
  10 + the higher of the enemy's Strength or Dexterity modifier; the enemy is
  `grappled`, `grappledBy` the hero, and cannot advance, fall back, retreat or
  flee until the grappler goes down or leaves melee or it breaks free), Shove
  down (`combat.shove`, `Shove {enemy} down`; the same check; `prone` until the
  end of its next turn, standing up automatically then), Bind (`combat.subdue`,
  `Bind {enemy}`; only an enemy that is grappled, prone, yielded or at a quarter
  of its HP or less; the better of Athletics or Dexterity (Sleight of Hand)
  against DC 10, no roll against a yielded enemy; it is `captured`, out of the
  fight and alive) and Demand surrender (`combat.demand_surrender`, `Demand
  {enemy}'s surrender`; an enemy at half its HP or less, grappled or prone, and
  never a boss above a quarter; Charisma (Intimidation) against DC 12, DC 8 at a
  quarter or less or grappled; it yields). When every enemy left is captured or
  has yielded the fight ends `enemies_surrendered`, with the pack's surrender
  outcome and full rewards.
- Player weapon attacks use server-owned attack and damage rolls. A HUD weapon
  attack's roll carries `target_ac`, the target's armour class with effects and
  cover; the engine compares the attack against that same number, so the roll
  card and the narration agree. Other resolved rolls report `dc`, `dcSource` and
  `outcome`.
- Extra Attack: the first weapon attack spends the action and leaves
  `turnEconomy.attacksRemaining` at the actor's attacks per action minus one.
  Two-weapon fighting: with a light melee weapon in each hand and an attack
  already made with one, the other strikes as a bonus action, adding the ability
  modifier to damage only when it is negative.
- Server-issued action/target IDs are re-resolved under the turn lock; replaying
  the same turn record never spends or applies twice.

### Range bands and engagement

- Every participant stands in `melee`, `near`, `far` or `distant`
  (`combat/positioning.py`). A participant in the melee band is engaged with the
  enemies it closed on or that closed on it (`position.engagedWith`, kept on
  both sides). Closing on a target from `near` puts both in melee and engages
  them. Any move out of melee ends all of the mover's engagements.
- Melee weapon attacks need the target engaged with the attacker or both in
  melee; `reach` weapons may target the `melee` and `near` bands. Ranged weapons
  reach every band; a ranged weapon or spell attack made while engaged with an
  enemy that can act has disadvantage. Down, hidden and fully covered targets
  cannot be targeted.
- Attacks close the distance: a melee attack on a target out of reach is still
  offered, with `requiresMove: true` on the target (and on the action when every
  reachable target needs it), while the hero's move is unspent and closing
  would bring the target into reach. Taking it spends the move and the action
  and is told as the advance followed by the attack (`You close in on the
  ashmarked cutthroat.`). With the move spent it is not offered.
- Off the press, two participants in the same outer band (`far` with `far`)
  can close on each other in one move. A hero in the press (`melee` or `near`)
  whom no standing enemy holds engaged may advance on an enemy standing off at
  `far`: the move takes them out to it, and the next move closes in. An enemy group starts a pack fight at its `startBand` (`melee`, `near` or
  `far`; default `near`), and the fight's opening says where each group stands
  (`The pale lanterns are well out of reach; the wardbound beast is a move
  away.`).
- Distance as players read it: the bands measure distance from the press, not
  between every pair, so the player view gives each participant's `reach` from
  the viewing hero with approximate feet (`reachLabel`): `in reach` (engaged, or
  both in the press), `a move away · about 30 ft` (it can be closed on this
  turn), `out of reach this turn · 60 ft or more` (it cannot), or `far off`
  (`distant`). The fight ribbon shows those words in place of the band, and a
  hand weapon or hold that cannot reach a target says so in them: `The pale
  lantern is out of reach this turn. Advance first, or use something with
  range.` (typed words such as `I stab the lantern` are refused with the same
  reason; a stab or a cut is always a hand weapon's). An enemy coming in is told
  in distance words (`The wardbound beast crosses about thirty feet to you.`).

### Reactions

Each participant has one reaction (`reactionRemaining`), back at the start of
its own turn; a participant that cannot act or has `cannotReact` takes none.
Reaction providers answer the `attack_hit`, `leaves_melee` and `damage_taken`
events, at most one reaction per reactor per event. The built-in provider makes
opportunity attacks: leaving melee while engaged, unless disengaged this turn,
draws one melee attack from each engaged participant of the other side that can
react (a player character's main-hand melee weapon, automatically unless its
sheet sets `autoReactions: false`). An enemy that retreats or flees out of
melee provokes the heroes engaged with it the same way, before it leaves; one
that drops is defeated where it stands. Shield and Uncanny Dodge are automatic
reactions too (see below).

### Enemy turns

- Enemies act in initiative order on one working copy, so later enemies see
  earlier moves, reactions and heals. They support single-target attacks,
  saving throws, damage, conditions and healing; unsupported delivery shapes
  fail closed and are removed from narration context instead of becoming
  invented damage. An enemy out of reach moves inward one band and attacks when
  that brings its target into reach; enemy targeting applies the same
  range-band, engagement, cover and hidden rules as the player HUD.
- Enemies fight back. A turn that does nothing mechanically (completing an
  objective, delaying, using the environment, protecting an ally who is not
  below half HP) may be chosen at most once in any three of an enemy's turns
  and never on two turns in a row (`combat.flags.enemyTurnHistory`).
- No yielding before being hurt: an enemy may negotiate, surrender, yield or
  retreat only when its HP is at or below half, its side has lost a member in
  this fight, or it is round 4 or later and it has been hurt. An encounter's
  `allowParley: true` lifts this.
- Bosses hold: an enemy with `challengeTier: "boss"` never yields or retreats
  above a quarter of its HP.
- A held (grappled) enemy never moves, retreats or flees: on its turn it yields
  at a quarter of its HP or less or with no morale left, and otherwise spends
  its action trying to break free (d20 + the higher of its Strength or Dexterity
  modifier against 8 + the grappler's Strength modifier + proficiency bonus when
  they are proficient in Athletics). A captured or yielded enemy takes no turns.
- Engine-selected retreat, surrender and negotiation apply authoritative
  conditions and end the fight through the same end-condition engine as defeat;
  narration cannot substitute a different ending. The scripted narrator tells
  such turns in the world's words (`… gives ground.`, `… throws down its weapon
  and yields.`, `… breaks and runs.`), never by an intent's name.
- `recharge_5_6` abilities used earlier roll a d6 at the enemy's turn start (5-6
  recharges). Limited-use resources are revalidated before each use.

### Turn flow: End turn, the enemy phase and the time to act

`game_state/orchestration/turn_flow.py` decides who tells each turn.

- A model narrates a fight turn only when it holds an attack, a spell, a
  manoeuvre, an improvised action, words the hero said or a look, a finishing
  blow, an enemy phase, a fight starting or ending, or a roll. Every other fight
  action (End turn, Dodge, Disengage, Advance, Fall back, Dash, Help, Hide,
  Stabilize, a class feature, a potion) is told by the scripted narrator's
  sentences exactly as Safe Mode tells it, with no model call (`turnFlow.told`
  `scripted`, provider `fallback`); the post-narration helper never reads it
  (`postDmExtraction` skipped, reason `engine_told`).
- `End turn` with a hero next and nobody acting in between has no narration at
  all (`told` `turn_line`): no reply is streamed, the log gains only the engine's
  turn line (`Corin Ashby is up next.`), and the next hero's actions are offered
  as soon as the turn is saved.
- When enemies act before the next hero their whole phase is one reply: every
  enemy's resolved action, bark and condition change (the AI brief lists them
  all under `Did`, and the fight word limit is for the whole reply; the scripted
  narrator gives each one sentence), then the turn line.
- The turn ends itself. Each action carries the player's `End my turn for me`
  choice (Settings → Play, on by default; `auto_end_turn` on `send_message`).
  With it on, an action that leaves the hero only moving (Advance, Fall back) or
  ending the turn ends it in the same turn (`state_pipeline.autoEndTurn`): the
  enemy phase runs if it is next, and the one reply tells the hero's result, the
  enemies' and the turn line, with every roll card and a finishing blow's
  question. A still-legal attack, bonus action, free action or bonus-action
  spell (Second Wind, Healing Word, an off-hand attack) keeps the turn open;
  healing that nobody needs (a potion, Healing Word with every friend unhurt)
  does not, as Second Wind at full health is not offered. A roll the player has
  to press keeps the turn open until it resolves; then the same rule applies.
- A fight action taken from the offered actions is the engine's from roll to
  result, so the narrator is the only model it can reach. Its sentence
  (`Arden Vale attacks Pale Lantern with Longsword.`) is not sent to the
  pre-narration helper (`preDmExtraction` skipped, reason
  `engine_fight_action`), and in a pack adventure its narration is not read by
  the post-narration helper (`postDmExtraction` skipped, same reason). The
  turn ends itself even though those words read as an attack still waiting on
  a roll: the turn carries the roll.
- In a pack adventure the engine runs the table: free play outside a fight,
  initiative inside one. The table-flow conductor helper is consulted only for
  typed words in an adventure without a pack. Enemies act from their authored
  abilities and the engine's scoring (a boss still leads with its signature
  ability); no model picks an enemy's move unless the table sets
  `settings.combatDifficultyAI.enemyTacticsModel: true`
  (`pack_combat_difficulty_ai` in `combat/difficulty.py`, which sets
  `maxLlmCallsPerRound` to 0).
- Every finished turn records `timings` in its metadata: `engine_ms` (the
  message received to the engine's results sent), `narration_ms` (first to last
  narration chunk, 0 when no model was called), `settle_ms` (narration end to
  the turn finished), `model_called`, its `kind` (`explore`, `talk`,
  `fight_action`, `end_turn`, `enemy_phase`) and `time_to_act_ms` (received to
  finished). Telemetry adds each to the `turn.time_to_act_ms` timing by kind,
  and Table tools → Ops shows the session's median and 90th percentile by kind
  (`time_to_act` on `GET /api/beta/session-quality`).

### Party scaling

Encounters are authored for four heroes and scale to the heroes present
(`PARTY_SCALING` in `game_state/campaign_pack_encounters.py`):

| Heroes present | Enemy HP (rounded up) | Enemy damage rolls (rounded down, at least 1) | Enemy attack bonus and save DCs | At most |
| --- | --- | --- | --- | --- |
| 1 | × 0.30 | × 0.40 | −3 | 1 enemy |
| 2 | × 0.60 | × 0.70 | −1 | 3 enemies |
| 3 | × 0.80 | × 0.85 | ±0 | 4 enemies |
| 4 or more | unchanged | unchanged | unchanged | unchanged |

Each group keeps `max(1, round(count × heroes ÷ 4))` enemies; over the most,
whole groups drop from the end (never the first), then the last group loses
enemies. `scaling: false` on an encounter keeps the authored numbers. The record
is `combat.flags.partyScaling`.

Floors come last and hold for a party under four (recorded as
`partyScaling.floors`): an encounter's `minEnemies` (the first group regains
enemies up to it) and a group's `minHp`. An encounter with a `take_and_flee`
goal and no floors of its own gets the defaults: at least two enemies when it
also has a `silence` goal (else one), and a runner of at least 16 maximum HP for
two or three heroes, 12 for one, so a fight about something never ends in one
blow. `scripts/solo_balance.py` plays every ready-made
hero alone through every Play Now fight to check the one-hero row.

### What the party knows adjusts a fight

An encounter's `adjustments` apply when its combat is created, before party
scaling. Each whose flag holds either multiplies one enemy group's HP
(`hpMultiplier`, 0.25–1.5, together with the scaling multiplier) or keeps groups
away (`removeEnemyIds`). Their `text` lines are recorded on
`partyScaling.adjustments` and are told before the first blows; a narrating
model is given the same lines as ones it must convey.

### Ending a fight and rewards

- Terminal combat outcomes derive authored encounter rewards and consequences.
  XP, currency, and items allocate only to exact present player participants;
  downed participants remain eligible, absent ones do not. Common rewards apply
  only to successful outcomes unless a defeat or retreat reward is authored.
- Reward changes and linked quest events receive IDs derived from the
  authoritative `combat.end` change; a `combat.reward.finalize` marker is
  accepted only after every required output is in the ledger, so replaying a
  partially applied end derives only missing outputs.
- Narration never creates damage once the engine resolved the turn, and narrated
  damage lands only on a player character the sentence names; a damage claim
  whose target cannot be resolved is dropped and logged.

### Captives

An enemy that ends a fight captured or yielded stays in the scene as a captive
(`currentScene.captives`: `id`, `name`, `enemyId`; in the player view with the
offered talk action `Question {name}`), after the beat `Captured — Ashmarked
Cutthroat` or `Ashmarked Cutthroat yields`. Each question tells the next line of
the enemy record's `captiveKnows`, then `The ashmarked cutthroat glares and says
nothing more you can use.`; an AI narrator has the record and its lines in the
engine brief as a person present. Travelling on leaves captives behind: they
are removed, with `You leave the cutthroat bound for the reeve.` (`The party
leaves …` in party play).

### Enemy barks

An enemy record's `barks` (see `docs/campaign_packs.md`) give a fight's enemies
a voice: a `start` line in the fight's first narration (after the setup and
adjustment lines), a `hurt` line the first time one of the record's enemies is
at half its hit points or less, a `turning` line when one yields, flees or is
captured, and a `taunt` in place of an enemy turn that rolled nothing, dealt no
damage and applied no condition (`turns back to its work`, `hangs back,
watching`), at most once per enemy every two rounds. The line is chosen from
the turn, and no line repeats in a fight while another is unused (the turns
record theirs as `narration.barks`). Speech is printed with its speaker
(`"Not paid enough for this," the ashmarked cutthroat spits.`, the verb
rotating among says, spits, hisses and calls), a tell as written; an AI
narrator gets `Bark: …` under that enemy's result.

### The finishing blow

When the acting hero's own attack, spell or manoeuvre brings to 0 hit points an
enemy that is the last one standing, or whose record is `proper` or of a
`hard`, `deadly` or `boss` `challengeTier`, the fight resolves exactly as ever
(defeat, victory, rewards, beats) but the death is not told: the turn's
narration ends `The ashmarked cutthroat is finished. How do you end it?`
(party: `… finished. Corin Ashby, how do you end it?`), the one closing question
the narration asks, and the combat's flags record `pendingFinisher` (`heroId`,
`heroName`, `enemyId`, `enemyName`, `turnId`, `othersActed` and the
`waitingLine`, `Corin Ashby is deciding how it ends…`, for the other players).
There is only ever one; a second qualifying defeat is told as any other.

While it is pending that hero is offered `Describe how you end it` (kind
`finisher`, `prompt: "How do you end it?"`) and `Let the story tell it` first,
and every message they send is the finisher: never another action, never
rolled, costing nothing. The player view carries it as `combat.pendingFinisher`
(`heroId`, `heroName`, `enemyName`, `waitingLine`; the finishing hero's own view
adds the two actions as `offeredActions`), and while the fight goes on the
finishing hero's combat actions start with them (`finisher.describe`,
`finisher.story`: free, never ending the turn), taken as the hero's words. The scripted narrator follows their own line with
`The ashmarked cutthroat falls, and does not rise.`; an AI narrator is told to
tell their words faithfully in at most three sentences, adding no new facts or
dangers. Words that spare it (whole words: spare, mercy, alive, let him/her/it
live, knock out, knock him/her out, stay my hand) leave it alive and captured,
a captive like those a fight leaves (beat `Captured — …`): `You turn the blow.
The ashmarked cutthroat lives, and is yours.` It lapses, and that turn's
narration begins `The ashmarked cutthroat falls.`, when the hero lets the story
tell it, takes any other action, the party travels, or another hero has acted
twice since.

## Spells

### Catalog

The rules-backed catalog (`AUTHORITATIVE_SPELL_EFFECTS` in
`aidm_server/spellbook.py`) holds 61 spells of levels 0–3. Each definition
stores level, school, classes, casting time, concentration, out-of-combat
legality and its effects; the effect column below is the one-line
`effectSummary` every spell payload carries, generated from the definition.

| Spell | Level | Classes | Casting time | Effect | Higher-level slot |
| --- | --- | --- | --- | --- | --- |
| Chill Touch | Cantrip | Wizard, Sorcerer, Warlock | Action | Ranged spell attack · 1d8 necrotic · prevents healing · scales with level | — |
| Eldritch Blast | Cantrip | Warlock | Action | Ranged spell attack · 1 attack of 1d10 force · scales with level | — |
| Fire Bolt | Cantrip | Wizard, Sorcerer, Artificer | Action | Ranged spell attack · 1d10 fire · scales with level | — |
| Guidance | Cantrip | Cleric, Druid | Action | Automatic · +1d4 to one ability check · 1 minute · concentration | — |
| Poison Spray | Cantrip | Sorcerer, Warlock, Druid | Action | CON save · 1d12 poison · scales with level | — |
| Produce Flame | Cantrip | Druid | Action | Ranged spell attack · 1d8 fire · scales with level | — |
| Ray of Frost | Cantrip | Wizard, Sorcerer, Artificer | Action | Ranged spell attack · 1d8 cold · slowed · scales with level | — |
| Sacred Flame | Cantrip | Cleric | Action | DEX save · 1d8 radiant · scales with level | — |
| Shocking Grasp | Cantrip | Wizard, Sorcerer | Action | Melee spell attack · 1d8 lightning · prevents reactions · scales with level | — |
| Spare the Dying | Cantrip | Cleric | Action | Automatic · stabilizes a dying ally | — |
| Thorn Whip | Cantrip | Druid | Action | Melee spell attack · 1d6 piercing · pull one range band · scales with level | — |
| Toll the Dead | Cantrip | Cleric | Action | WIS save · 1d8 necrotic (1d12 against a wounded target) · scales with level | — |
| Vicious Mockery | Cantrip | Bard | Action | WIS save · 1d4 psychic · disadvantage on next attack · scales with level | — |
| Armor of Agathys | 1 | Warlock | Action | Automatic · 5 temporary HP | +5 per slot level |
| Bane | 1 | Cleric, Bard | Action | CHA save · -1d4 to attacks and saves · 1 minute · concentration | +1 target per slot level |
| Bless | 1 | Cleric, Paladin | Action | Automatic · 1d4 to attacks and saves · 1 minute · concentration | +1 target per slot level |
| Burning Hands | 1 | Wizard, Sorcerer | Action | DEX save, half on success · 3d6 fire · area: up to 3 enemies | +1d6 per slot level |
| Charm Person | 1 | Warlock, Bard | Action | WIS save · charmed · until the encounter ends | +1 target per slot level |
| Chromatic Orb | 1 | Wizard, Sorcerer | Action | Ranged spell attack · 3d8 acid/cold/fire/lightning/poison/thunder | +1d8 per slot level |
| Command | 1 | Cleric, Paladin | Action | WIS save · prevents the target acting on its next turn | +1 target per slot level |
| Cure Wounds | 1 | Cleric, Druid, Bard, Paladin, Ranger, Artificer | Action | Automatic · 1d8 + casting modifier healing | +1d8 per slot level |
| Dissonant Whispers | 1 | Bard | Action | WIS save, half on success · 3d6 psychic · push one range band | +1d6 per slot level |
| Divine Favor | 1 | Paladin | Bonus action | Automatic · 1d4 radiant weapon damage · 1 minute · concentration | — |
| Entangle | 1 | Druid, Ranger | Action | STR save · restrained · repeat STR save each turn to end it · area: up to 4 enemies · 1 minute · concentration | — |
| Faerie Fire | 1 | Druid, Bard, Artificer | Action | DEX save · advantage on attacks against target · removes invisible, hidden · area: up to 4 enemies · 1 minute · concentration | — |
| False Life | 1 | Wizard, Sorcerer, Artificer | Action | Automatic · 1d4+4 temporary HP | +5 per slot level |
| Grease | 1 | Artificer | Action | DEX save · prone · area: up to 3 enemies | — |
| Guiding Bolt | 1 | Cleric | Action | Ranged spell attack · 4d6 radiant · advantage on next attack against target | +1d6 per slot level |
| Healing Word | 1 | Cleric, Druid, Bard | Bonus action | Automatic · 1d4 + casting modifier healing | +1d4 per slot level |
| Heroism | 1 | Bard, Paladin | Action | Automatic · temporary HP each turn · immune to frightened · 1 minute · concentration | +1 target per slot level |
| Hex | 1 | Warlock | Bonus action | Automatic · +1d6 necrotic damage on your hits against the target · until the encounter ends · concentration | — |
| Hunter's Mark | 1 | Ranger | Bonus action | Automatic · +1d6 damage on your weapon hits against the target · until the encounter ends · concentration | — |
| Inflict Wounds | 1 | Cleric | Action | Melee spell attack · 3d10 necrotic | +1d10 per slot level |
| Mage Armor | 1 | Wizard, Sorcerer | Action | Automatic · armor class 13 + DEX without armour · until a long rest | — |
| Magic Missile | 1 | Wizard, Sorcerer | Action | Automatic · 3 darts of 1d4+1 force | +1 dart per slot level |
| Shield | 1 | Wizard, Sorcerer | Reaction | Automatic reaction when an attack would hit you · +5 armor class | — |
| Shield of Faith | 1 | Cleric, Paladin | Bonus action | Automatic · +2 armor class · 10 minutes · concentration | — |
| Sleep | 1 | Wizard, Sorcerer, Bard | Action | Automatic · 5d8 hit points of creatures fall asleep, weakest first · area: up to 4 enemies · 1 minute | +2d8 per slot level |
| Tasha's Hideous Laughter | 1 | Bard | Action | WIS save · prone · incapacitated · repeat WIS save each turn to end it · 1 minute · concentration | — |
| Thunderwave | 1 | Wizard, Sorcerer, Druid, Bard | Action | CON save, half on success · 2d8 thunder · push one range band · area: up to 3 enemies | +1d8 per slot level |
| Aid | 2 | Cleric, Paladin, Artificer | Action | Automatic · +5 maximum and current HP · until a long rest | +5 per slot level |
| Barkskin | 2 | Druid, Ranger | Action | Automatic · armor class at least 16 · until the encounter ends · concentration | — |
| Blindness/Deafness | 2 | Wizard, Sorcerer, Cleric, Bard | Action | CON save · blinded · repeat CON save each turn to end it · 1 minute | +1 target per slot level |
| Hold Person | 2 | Wizard, Sorcerer, Warlock, Cleric, Druid, Bard | Action | WIS save · paralyzed · repeat WIS save each turn to end it · 1 minute · concentration | +1 target per slot level |
| Invisibility | 2 | Wizard, Sorcerer, Warlock, Bard, Artificer | Action | Automatic · invisible · until the encounter ends · concentration | +1 target per slot level |
| Lesser Restoration | 2 | Cleric, Druid, Bard, Paladin, Ranger, Artificer | Action | Automatic · removes one of: blinded, deafened, paralyzed, poisoned | — |
| Misty Step | 2 | Wizard, Sorcerer, Warlock | Bonus action | Automatic · teleport to far range | — |
| Moonbeam | 2 | Druid | Action | CON save, half on success · 2d10 radiant · the save and damage repeat each turn · area: up to 3 enemies · 1 minute · concentration | +1d10 per slot level |
| Prayer of Healing | 2 | Cleric | Action | Automatic · 2d8 + casting modifier healing | +1d8 per slot level |
| Scorching Ray | 2 | Wizard, Sorcerer | Action | Ranged spell attack · 3 attacks of 2d6 fire | +1 ray per slot level |
| Shatter | 2 | Wizard, Sorcerer, Warlock, Bard | Action | CON save, half on success · 3d8 thunder · area: up to 4 enemies | +1d8 per slot level |
| Spiritual Weapon | 2 | Cleric | Bonus action | Melee spell attack · 1d8 + casting modifier force · attack again as a bonus action while it lasts · 1 minute | +1d8 per two slot levels |
| Web | 2 | Wizard, Sorcerer | Action | DEX save · restrained · repeat STR save each turn to end it · area: up to 4 enemies · 1 minute · concentration | — |
| Dispel Magic | 3 | Wizard, Sorcerer, Warlock, Cleric, Druid, Bard, Paladin | Action | Automatic · ends spell effects of the cast level or lower | — |
| Fear | 3 | Warlock, Bard | Action | WIS save · frightened · repeat WIS save each turn to end it · area: up to 4 enemies · 1 minute · concentration | — |
| Fireball | 3 | Wizard, Sorcerer | Action | DEX save, half on success · 8d6 fire · area: up to 6 enemies | +1d6 per slot level |
| Hypnotic Pattern | 3 | Wizard, Sorcerer, Warlock, Bard | Action | WIS save · incapacitated · charmed · area: up to 4 enemies · 1 minute · concentration | — |
| Lightning Bolt | 3 | Wizard, Sorcerer | Action | DEX save, half on success · 8d6 lightning · area: up to 3 enemies | +1d6 per slot level |
| Mass Healing Word | 3 | Cleric, Bard | Bonus action | Automatic · 1d4 + casting modifier healing | +1d4 per slot level |
| Revivify | 3 | Cleric, Paladin | Action | Automatic · returns a dead ally to life with 1 HP · consumes 300 gp | — |
| Spirit Guardians | 3 | Cleric | Action | Automatic · enemies near you take 3d8 radiant each turn (WIS save for half) · 10 minutes · concentration | +1d8 per slot level |

Castable outside a fight: Guidance, Spare the Dying, Armor of Agathys, Cure
Wounds, False Life, Healing Word, Mage Armor, Aid, Barkskin, Invisibility,
Lesser Restoration, Dispel Magic, Mass Healing Word, Revivify, and Prayer of
Healing (only outside a fight).

Narrated spells (`NARRATED_SPELLS`, 24 entries such as Mage Hand, Detect Magic,
Counterspell, Fly and Haste) stay on class lists with `narrated: true`;
levelled ones spend a slot and the Dungeon Master adjudicates them. Every spell
payload carries `rulesBacked` (alias `authoritativeEffect`), `castingTime`,
`level`, `concentration`, `outOfCombat` and `effectSummary`.

### Class lists and upcasting

- Class lists are built from each spell's `classes`: cantrips and level-1 spells
  at character level 1 (paladin and ranger level-1 spells at 2), level-2 spells
  at 3 (paladin, ranger and artificer at 5), level-3 spells at 5. Spells of level
  4+ remain narrated unlocks.
- Cantrip damage dice multiply by the caster tier (character levels 1–4, 5–10,
  11–16, 17+); Eldritch Blast adds beams instead.
- Casting with a higher slot adds what the table's last column says: dice to the
  first damage or healing effect (and to a recurring damage effect), targets,
  darts, rays or a flat amount. The selected slot level reaches validation,
  replay and the result's `castLevel`.

### Casting and resolution

- A spell must be known and, for preparation-based classes, prepared. Standard
  slots, pact slots, Mystic Arcanum and concentration are stored in the
  character sheet and restored on reload. Casts consume once; exhausted spells
  fail before narration. In combat a cast spends the action, or the bonus action
  for a `bonus_action` spell, and is rejected once that budget is spent.
- One levelled spell per turn: after a levelled spell the caster may cast only
  cantrips that turn. Spiritual Weapon's later attacks are bonus actions that
  spend no slot and do not count as casting.
- A typed cast with exact target IDs validates turn ownership, presence,
  relation, range, cover, target count, knowledge/preparation and resources
  before rolling. A rules-backed spell without a target pauses for a legal target
  choice before narration or spending a slot; named free-form casts (`cast fire
  bolt`) use the same path.
- The resolver owns attack rolls (ranged spell attacks into the melee band take
  the engaged disadvantage), saves, automatic delivery, damage, healing,
  temporary HP, conditions, effects, movement, condition removal, stabilising,
  revival, maximum-HP bonuses, the Sleep pool, recurring effects, dispelling and
  Spiritual Weapon's repeat attack. Every value the roller produced is recorded
  in `rollSequence`, which the change validator replays exactly; a repeated cast
  ID returns the existing resolution.
- Area spells take one primary target and add other legal enemies in the same
  range band and zone up to their limit, share one damage roll and roll each
  save separately; allies are never caught. Sleep puts creatures to sleep by
  current HP while its rolled pool lasts.
- Damage dealt to a concentrating creature triggers its concentration check,
  ends Sleep and Hypnotic Pattern, ends a charm when the charmer's side deals
  it, and gives Tasha's Hideous Laughter a save with advantage; weapon, item,
  hazard and ongoing damage run the same reactions.
- Recurring effects (repeat saves for Entangle, Hold Person, Web,
  Blindness/Deafness, Fear and Tasha's; Moonbeam's and Spirit Guardians'
  save-or-take-damage) run as each affected creature starts its turn.
- Shield is cast automatically when an enemy's hit total is below the
  character's AC + 5 and the character has Shield ready, a slot, its reaction and
  `autoReactions` not off: the hit misses, a slot is spent, and +5 AC lasts until
  the start of the character's next turn.
- Hunter's Mark and Hex mark the target on the caster's own effect; Chromatic
  Orb takes the intent's `damage_type`; Charm Person's save has advantage in
  combat; Mage Armor sets an armour base; Revivify needs a dead target and
  consumes 300 gp, rejecting the cast when the caster cannot pay.

## Class Features

Levels 1–5 of the twelve core classes, single class. On a multiclass label such
as "Fighter / Wizard" every feature comes from the first class only.
`aidm_server/class_features.py` holds the catalog, pools and passive rules;
`aidm_server/class_capabilities.py` resolves uses and registers the combat
actions. Guided creation shows the same list.

| Class | Level | Feature |
| --- | --- | --- |
| Barbarian | 1 | Rage (bonus action; 2 uses, 3 from level 3; long rest): 10 rounds of +2 damage on Strength melee attacks, resistance to bludgeoning, piercing and slashing, advantage on Strength checks and saves, no spellcasting; ends early if you neither attacked nor took damage since your last turn, or drop. Unarmored Defense: AC 10 + DEX + CON without armour. |
| Barbarian | 2 | Reckless Attack (free, before your first attack): advantage on melee Strength attacks this turn, attacks against you have advantage until your next turn. Danger Sense: advantage on Dexterity saves unless blinded, deafened or incapacitated. |
| Barbarian | 5 | Extra Attack. |
| Bard | 1 | Bardic Inspiration (bonus action; Charisma modifier uses, at least 1; long rest): an ally's d6 is added to their next attack, check or save that would fail. Spellcasting. |
| Bard | 2 | Jack of All Trades: half proficiency on untrained checks. |
| Bard | 5 | The inspiration die becomes a d8 and uses return on a short rest. |
| Cleric | 1 | Spellcasting. |
| Cleric | 2 | Channel Divinity (1 use; short rest): Turn Undead, a Wisdom save against your spell DC or the undead is turned for 10 rounds. |
| Cleric | 5 | Turned undead of challenge 1/2 or lower are destroyed. |
| Druid | 1 | Spellcasting. Wild Shape stays narrated. |
| Fighter | 1 | Second Wind (bonus action; short rest): 1d10 + fighter level HP. Fighting Style: Defense (+1 AC in armour), Dueling (+2 damage, one-handed melee weapon and no other weapon), Great Weapon Fighting (reroll 1s and 2s on two-handed melee damage) or Archery (+2 to ranged weapon attacks). |
| Fighter | 2 | Action Surge (free; short rest): regain the action this turn. |
| Fighter | 3 | Champion: Improved Critical, critical hits on 19–20. |
| Fighter | 5 | Extra Attack. |
| Monk | 1 | Martial Arts: unarmed strikes (1d4) and monk weapons may use Dexterity; one bonus unarmed strike after the Attack action. Unarmored Defense: AC 10 + DEX + WIS without armour or shield. |
| Monk | 2 | Ki (monk level points; short rest): Flurry of Blows (two bonus strikes), Patient Defense (Dodge as a bonus action), Step of the Wind (Dash or Disengage as a bonus action). |
| Monk | 5 | Stunning Strike (1 ki on a hit: Constitution save or stunned until the end of your next turn). Unarmed strikes 1d6. Extra Attack. |
| Paladin | 1 | Lay on Hands (action; 5 points per level; long rest): restore HP, or 5 points to cure poison. |
| Paladin | 2 | Divine Smite (on a melee hit, spend a slot: +2d8 radiant, +1d8 per slot level above 1st, at most 5d8, +1d8 against undead or fiends). Fighting Style (Defense, Dueling or Great Weapon Fighting). Spellcasting. |
| Paladin | 5 | Extra Attack. |
| Ranger | 2 | Fighting Style (Archery, Defense or Dueling). Spellcasting. |
| Ranger | 3 | Hunter: Colossus Slayer, once per turn +1d8 to a wounded foe. |
| Ranger | 5 | Extra Attack. |
| Rogue | 1 | Sneak Attack: once per turn, +1d6 per two levels (rounded up) with a finesse or ranged weapon when you have advantage, or an ally is engaged with the target and you do not have disadvantage. Expertise: double proficiency in two chosen skills (default Stealth and your first other skill). |
| Rogue | 2 | Cunning Action: Dash, Disengage or Hide as a bonus action. |
| Rogue | 5 | Uncanny Dodge: your reaction halves an attack's damage (automatic unless reactions are off). |
| Sorcerer | 1 | Spellcasting. |
| Sorcerer | 2 | Sorcery points (sorcerer level; long rest). Flexible Casting: a slot becomes points equal to its level, or 2/3/5 points make a missing 1st/2nd/3rd-level slot. |
| Sorcerer | 3 | Quickened Spell (2 points: the next action spell this turn costs a bonus action). Twinned Spell (points equal to the spell level, 1 for a cantrip: the next single-target spell takes a second target). |
| Warlock | 1 | Pact Magic: slots that return on a short rest. |
| Warlock | 2 | Agonizing Blast: Charisma modifier added to every Eldritch Blast beam. |
| Wizard | 1 | Arcane Recovery (once per long rest, after a short rest, out of combat): recover slots totalling half your wizard level rounded up, none above 3rd. Spellcasting. |

- Pools live in `classFeatureState` (`{"current", "max", "refreshesOn"}`). A
  level-up grows a pool by the difference only, never refilling spent uses.
- Every use is resolved before narration with its cost, target and dice,
  replay-validated, and applied as one `class_feature.use` change. In combat the
  action bundle offers every capability as `class.use.<id>` with its cost, pool,
  legal targets and plain reason; attack riders (Divine Smite, Stunning Strike)
  are declared with a melee attack and spent only on a hit.
- Fighting styles come from `character_sheet.fightingStyle`, else are defaulted
  from equipment.

## Consumables

`aidm_server/item_effects.py` holds the catalog. An item has an effect when
`metadata.effectId` names an entry or its name matches one; starter kits carry a
Potion of Healing, and clerics and druids also carry a Healer's Kit.

| Item | Effect |
| --- | --- |
| Potion of Healing / Greater / Superior | Heals 2d4+2 / 4d4+4 / 8d4+8. |
| Antitoxin | Ends poisoned and grants advantage on Constitution saves until the encounter ends (out of combat, until the next rest). |
| Healer's Kit | Stabilizes a dying ally nearby; 10 uses (`metadata.usesRemaining`). |
| Holy Water | Thrown: 2d6 radiant to undead or fiends only. |
| Alchemist's Fire | Thrown: 1d4 fire, then 1d4 fire at the start of each of the target's next two turns. |
| Acid | Thrown: 2d6 acid. |
| Spell scroll (`Scroll of <Spell>`) | Casts its rules-backed spell at base level without a slot, for readers whose class list has the spell. |

- Using an item (the action tray, the combat action `item.use.<itemId>`, typed
  `inventory_action: use`, or words naming exactly one item) produces one
  `item.use` change: server-rolled dice and one quantity or use removed together
  with the effect, replay-checked. Potions cost a bonus action on oneself and an
  action for an ally in the same or an adjacent range band; healing follows the
  vitality rules. Thrown items make a Dexterity + proficiency ranged attack.
- Items without an effect keep the DM-adjudicated path, and narration never
  repeats the removal of a server-resolved item.

## Rests, Hit Dice and the Clock

- The party clock (`snapshot.clock = {day, phase, shortRestsSinceLong,
  lastLongRestDay}`, phases morning, afternoon, evening, night; default day 1
  morning, or the pack's `startingState.clock`) advances one phase on a short
  rest, on structured travel, when the party takes its time over a check and
  when a beaten party wakes; a long rest moves to the next morning. The clock is
  shown to players and given to the narrator.
- Hit dice (`hitDice = {current, max, die}`, max = level, die from class,
  persisted as `Player.stats.hit_dice`) heal on a short rest: the rest says how
  many to spend (default: enough to reach full HP); each heals one server-rolled
  die + CON modifier, minimum 1. A long rest regains half the maximum (minimum 1).
- A short rest restores pact slots and short-rest abilities. A long rest
  restores standard and pact slots, Arcanum, long-rest abilities and HP, ends
  effects that last until a long rest, and restocks merchants. Rest wakes a
  `stable` character (a short rest to 1 HP, a long rest to full); dying and dead
  characters cannot rest.
- Rest safety: at most two short rests count between long rests (`You need a
  long rest before another short rest helps.`); one long rest per day (`You have
  already rested today.`). `currentScene.restPolicy` (`safe`, `risky` or `none`,
  authored on locations, otherwise danger 3+ is `risky` and 6+ is `none`):
  `none` refuses any rest (`It is too dangerous to rest here.`), `risky` allows
  only a short rest. No rest during combat.

## Shops

- `aidm_server/commerce.py` reads `snapshot.merchants` (written by the campaign
  pack importer). A merchant is open when its NPC is present in the scene (or,
  without an NPC, when the party is at its location) and no combat is running;
  only open merchants are shown to players and given to the narrator.
- `shop.buy` and `shop.sell` settle in one atomic, replay-safe change: prices
  come from the server (stock entries or `aidm_server/price_list.py`), funds are
  checked across all denominations with change given in gp/sp/cp, stock is
  decremented, and bought items keep their `effectId`/`metadata`. Selling pays
  the listed price × `buyRate`, rounded down to the copper; unpriced and
  equipped items are refused. A failed trade rejects the turn before narration.
- A free-text buy or sell is routed to an open merchant that trades the item;
  with none, the narrator adjudicates and coins change only by the amount the
  narration states. A client-supplied price is never used.

## Scene Objects

- Exact-ID doors, locks, containers, objects, and hazards support inspect, open,
  close, lock, unlock, search, break, use, disarm, trigger, and reset. Legal
  transitions may require exact items, tools, class capabilities, checks, flags,
  or other object states. A flag requirement of `false` holds while the flag is
  unset or false, so an action can be offered only until something happens.
- A requirement `check` is rolled when the action is submitted (one d20 plus
  the character's modifier, proficiency, wounds and modifier-engine effects);
  the roll, total, DC and outcome are stored on the event and shown as a check
  beat.
- Taking your time after a failed check: a failed check is never a dead end.
  Outside a fight the offered entry becomes `legal: true, retry: true`; sending
  the same action again with `take_time: true` (or words that name it again)
  advances the clock one phase and resolves the check as if the die showed 20
  (success when 20 plus the modifier meets the DC; otherwise it stays
  retryable). The narration first says `You take your time over it; the light
  has changed by the time you are done.` Without `take_time` the repeat is
  refused.
- Successful transitions atomically update `currentScene` and the location's
  `sceneState` (open, locked, broken, searched, used, uses remaining, revealed
  objects and contents). Revealed contents become scene items that can be taken
  with the exact-id pickup; coin becomes a pile named for its amount. Discovered
  clues, flags and places are applied with the action. Authored `actionText` is
  told when the action succeeds, never after a failed attempt.
- Searching takes what it finds (`game_state/orchestration/search_take.py`): a
  successful search or opening outside a fight then takes everything it revealed
  into the searching hero's pack in the same turn, through the same pickup (coin
  into their purse), so the beats read `Taken — …` and `+6 gp` and the narration
  `You find and take 6 gp and a Black-Feathered Arrow.` (party play: `Seren
  Ashgrove finds and takes …`). An item marked `"leave": true` stays in the
  scene with its `Take …` suggestion, and so does anything found while a fight
  is on, a search that starts one included. `Take everything here` takes every
  loose item in one turn.
- During a fight only an object's `usableInCombat` actions can be taken, on the
  hero's own turn while their action is unspent; taking one spends the action.
  Every other object action is refused with `Not while the fight is on.`, and
  loose items cannot be taken in a fight.
- An object action can settle an encounter peacefully
  (`resolvesEncounter: {<action>: {encounterId, outcome}}`): the encounter is
  completed with that outcome through the same transaction a combat ending uses,
  its combat ends at once, and its trigger never fires again.
- Interactable IDs are globally unique across current and cached locations.
  Hidden or GM-only targets fail as not found for players; an optional expected
  revision rejects stale selections, and `interactableActionLedger` makes a
  same-ID replay idempotent.

## Plans and Stakes

Plans change the scene; stakes make a fight about something besides hit points.
Engine: `game_state/orchestration/scene_plans.py` (plans) and
`game_state/orchestration/fight_stakes.py` (stakes); pack fields in
`docs/campaign_packs.md`, Plans and Stakes.

### Effects

- Nine effects, each with one engine meaning: `obstruct` (a way is `blocked`;
  whoever needs it spends an action clearing it, Strength or Dexterity against
  DC 12), `distract` (people or enemies in the zone are `distracted` to the end
  of the next round: no reactions, advantage on the next attack or check against
  them), `expose` (hidden objects and enemies in the zone are revealed;
  `a secret uncovered` Inspiration), `conceal` (a hero hides; an item cannot be
  taken by an enemy until it spends an action searching), `separate` (`cut_off`
  to the end of the next round; a person cut off from the others may be told a
  conversation's `private` topics), `restrain` (W7's grapple or bind, or
  `restrained` by an object until it breaks free), `destroy` (`broken` for good;
  a light leaves the zone `dark`, a rope looses what it held, a barrier opens the
  way), `protect` (the hero is `guarding` a person or item until their next turn:
  attacks go to the hero, a grab is a contest against their Athletics; a person
  guarded remembers it as `protected`) and `take`.
- Without an authored affordance an effect on a tagged thing in reach resolves
  by the table in `scene_plans.TABLE` (check engine-rolled, DC 12); a pair it
  does not hold falls to the improvised floor. An object or person in the room
  next door is stepped to first, as an action is; one further off is refused
  with `{Name} is in the loft. Go there first.`
- Every resolved effect adds a scene fact (`currentScene.facts`, one sentence:
  the affordance's `fact` or a built one, `The yard gate is barred.`), kept with
  the place and restored on return, a `change` beat with the same sentence, and
  in the Story view the Scene tab's `What has changed`. A fight's passing effects
  (distracted, cut off, guarding, restrained, hidden) end with it, and when the
  party leaves the place. A zone made `dark` is `currentScene.zoneConditions` and
  the zone's `conditions` in the player view.
- How a plan is recognised: offered affordances are clicked (in a fight as the
  fight action `plan.<objectId>.<affordanceId>`; at most two are `suggested`);
  typed words choose one by its `keywords` plus the object's name or a second
  keyword; failing that the verb families (`bar`, `snuff`, `hide`, `shield`, …)
  with a thing in reach named in the clause. With a narrating model, words the
  first pass could not place (not speech, outside a fight, in a place offering
  something to plan with) go once to the pre-narration helper, which may only
  name one of the nine effects and the ids of the things in the hero's own room;
  any other answer is ignored and the words fall to the old floor.
- Words naming a thing still hidden (its name, or two of its name words) are
  read before anything else: the offered action whose authored `reveals` link
  uncovers it is taken (stepping next door first), else nothing is attempted, no
  roll is asked for and the line is `You see no saddle cache here.` (`Arden Vale
  sees no …` in party play; a model is told `Did: nothing — the hero finds no
  such thing in sight.`).
- An improvised attempt outside a fight (the rules-hint roll) changes nothing:
  the scripted narrator says `You try it. It comes to nothing.` or `You manage
  it, for what it is worth.`; a model is given `Improvised — "<words>": Athletics
  19 vs DC 14, success. The engine changed nothing.` and rule 20, and its
  narration is not read for state (reason `improvised`).
- A narrating model is told a plan under `Did` (`Effect — obstruct: the yard
  gate is barred (Athletics 19 vs DC 10)`), with rule 19 (`Tell what the plan
  changed in the scene …`) on that turn and `Now true here:` with the place's
  facts on every turn. An affordance's authored `result.text`, `failResult.text`
  and an object's `onFail.text` are told as written by the engine; a turn holding
  nothing else makes no model call, and a model narrating the rest is given them
  under `Already told this turn`. A clicked affordance makes at most one model
  call (the narrator); typed words the helper places make two.
- Every check a plan rolls is a roll card (`storyRolls`), as are a fight's
  clearing, grabbing past a guard and wresting (`against: {"kind":
  "Athletics"}` for a contest) and a conversation's checks.

### Stakes

- An encounter's `stakes` (an item, a person, a route) are kept in
  `combat.flags.stakes` during the fight (`combat.stakes` in the player view, in
  words; the runner and the goals stay the engine's) and, once it ends, at
  `stakes.<encounterId>.<stakeId>` in the session state. Item: `in_place`,
  `held_by_hero`, `on_ground`, `held_by_enemy`, `taken`, `secured`; person:
  `safe`, `threatened`, `hurt` (out of play to the next long rest, never killed),
  `taken`; route: `open`, `blocked`.
- `take_and_flee`: the runner (the first enemy, chosen again if it falls)
  holding the item moves a band outward and from `far` with the route open
  escapes (`taken`); a blocked route costs its turn clearing it; an item in reach
  it takes; a hero holding it is attacked, and a hit on a hero at half HP or less
  may wrest it instead (its Strength against the hero's Athletics); a hero who
  drops holding it drops it. When the item is `taken` the others withdraw and
  the fight ends `enemy_goal_met` (the `flee` reward, else half the XP).
  `silence`: another enemy goes for the person each turn it is not engaged; a
  guard redirects it. No model picks these moves.
- At the end the states are final (`held_by_hero`, `on_ground`, `in_place` →
  `secured`) and each `results` entry whose `when` holds applies: flags, a scene
  fact, dispositions, a lead (session state `leads: [{"text", "followed"?}]`,
  the journal's `LEADS`, beat `A lead — …`) and its `text`, told as written.
- `onDefeat` replaces the defeat re-arm: the party wakes in `wakeZoneId`, its
  `text` is told after the fall, its flags and stakes apply, and the encounter is
  complete.
- `onFail` on an object action or affordance: a failed check applies it instead
  of coming up empty; with `noRetry` the take-your-time retry is not offered.
- The Story view's fight strip carries one quiet line above the turn order, `AT
  STAKE — the road-stone fragment (the cutthroat has it) · Wenna Pike (safe) ·
  the yard gate (open)`, a state that changed this turn in the accent; a model is
  given the same as `At stake:`, as it stands once the turn's enemy moves apply.

## Quests, Branches and Travel

- Quests support active, blocked, completed, and failed state; objectives
  support blocked/open/completed/failed state and prerequisites.
  `completeWhen` and `failWhen` evaluate applied mechanical events, never a
  completion sentence in narration. XP, currency, items, flags, and consequences
  apply exactly once with the terminal transition.
- Checkpoints may use `branchWhen: {flagKey, equals}`; selection reads persisted
  flags and falls through to an unconditional route. A terminal checkpoint is the
  ending: its `endingVariants` choose the ending title and text by flag, shown in
  the journal, the ending screen and the `The story ends — …` beat.
- `world.travel` requires an existing, visible, accessible neighbouring place
  and is rejected during combat. Location items, NPC presence, quest IDs,
  description, danger, and scene data are cached and restored when the party
  returns.

### Rooms and areas

- A pack place may divide into `zones` (see `docs/campaign_packs.md`, Rooms
  and Areas; engine `aidm_server/game_state/zones.py`). Each hero stands in one
  (`currentScene.playerZones`); arrival stands everyone in the place's start
  zone, and the party may split.
- A hero acts only on what stands in their own zone. Outside a fight an action,
  a conversation or typed words aimed at someone or something next door step
  the hero in first, in the same turn and narration
  (`game_state/orchestration/zone_turns.py`); a step that starts something
  stops there. Anything two or more zones off, or next door during a fight, is
  refused before narration with where it is (`Mara Reedwake is in the common
  room. Go there first.`), as is a rest outside the rest zones. Travel is
  never refused for the room a hero stands in: the party gathers and sets out.
  Words said to someone next door are a
  call, answered in one line with nothing new.
- Stepping into a zone (`scene.zone_move`, the `zone_move` intent or typed
  words such as `I go into the common room`) takes a turn and no clock time.
  The engine tells it with any narrator and calls no model, before or after
  narration: `You step into the common room.`, the zone's description the
  first time, then who is there; the first visit's beat is `Found — The Common
  Room` (`place_within`).
- A fight takes in every hero at the place: those in other zones start `far`
  off and the opening says they come running (`Corin Ashby comes running from
  the common room.`). No zone moves are made during it, and when it ends every
  hero, and its captives, stand in its zone.
- The player view lists the known zones (stood in or next to) as
  `currentScene.zones`, marks objects and merchants with their `zoneId`, leaves
  out objects in zones nobody knows yet and merchants out of reach. The Story
  view shows a where-line under the scene's name (the viewer's zone first, in
  the accent; a zone next door is a link that steps in; at phone width only the
  viewer's zone, the rest on a tap), offers what stands next door after what is
  here, marked with its zone (`Talk to Mara Reedwake · common room`), then the
  moves next door, and lists the zones under `THIS PLACE` on the Map tab.
- Typed pickup and drop resolve exact item IDs and fail closed for stale IDs;
  partial-stack moves receive a derived identity with `splitFromItemId`. A
  free-form narrated sentence cannot mint a new inventory item.

## AI Context

- The narrator's context separates current structured state, recent validated
  changes, pending checks, present NPC IDs, player spell resources, quest
  predicates, combat initiative/economy, engine results, interactable
  state/events, and legal tactic summaries, within a fixed size budget (see
  [LLM provider routing](llm_provider_routing.md)).
- Prompt rules state that only `currentScene.activeNpcIds` may speak or act and
  that structured state outranks canon, memory, and narration.
- With `AIDM_LLM_PROVIDER=fallback` the scripted narrator tells exactly what the
  engine resolved and the pack authored; it never invents an outcome, never
  quotes the player's words back, and asks for a roll only when the turn's rules
  require one that has not been made.

## Partial Gameplay

- The character picker presents a broader class/subclass and race catalog than
  the feature catalog. Subclass names are stored, but only Champion (fighter)
  and Hunter (ranger) select mechanics. Classes outside the twelve core classes
  have no class features.
- Many ancestry traits remain descriptive or data-only. Implemented race skills,
  languages, and innate spells work, but movement modes, damage resistances and
  most active ancestry abilities are not compiled into combat actors.
- Class features stop at level 5. Quickened and Twinned Spell shape only
  rules-backed spells. Druid Wild Shape is narrated.
- 61 spells of levels 0–3 are rules-backed, with some tabletop details
  simplified (areas count enemies in one band rather than measuring shapes).
  Narrated spells and spells of level 4+ spend the right resource, but the
  narrator adjudicates their effects.
- Combat uses coarse range bands, not a grid, pathfinding or line of sight.
  Unconscious creatures (including enemies put to sleep) cannot be targeted, and
  enemies never attack a downed character.
- Non-player allies in a fight (party NPCs) only pass their turn; the engine
  plans and resolves turns for enemies alone.
- Losing a fight never kills the party: a beaten party always wakes at the
  fight's end (`lethalDefeat` only sends the story down the failure route), so
  only damage that kills outright, or three failed death saves inside a fight,
  ends a character.
- Equipment affects armour class and weapon actions, but armour proficiency is
  not a class rule. Only the catalog items above have rules effects.
- Merchants have unlimited coin, sold items do not enter their stock, and prices
  are fixed (no haggling). Trades outside a merchant record stay narration-only.
- Actor-to-actor transfers are atomic when a recipient is resolved, but the
  standard item composer does not provide a complete item-plus-recipient UI.
- Authored `mechanicalEffects` on objects apply damage and conditions only;
  other secondary state is not translated automatically.
- Encounter rewards are authored rather than inferred from narration.
  Generated encounters require a stable encounter ID before the exact-once reward
  engine can run.
- Campaign packs support mechanical checkpoints, outcomes, and flag branches;
  they are not a general-purpose predicate language.
- `abandoned` remains a recognized terminal compatibility value, but there is no
  player-facing abandon-quest action.

## Narration-Only Areas

- Free-form actions that map to no typed action (persuading, sneaking past,
  bargaining outside a shop) are narrated, with a roll asked for when the rules
  call for one; the scripted narrator tells only what the engine and the pack
  provide.
- Relationships and many social consequences can be stored, but most do not
  feed a complete contested social-resolution system. Narration can set only
  the story flags a pack declares, and only to a declared value.
- General reinforcements, negotiation objectives, surrender terms, and retreat
  costs have state seams but not one complete player-facing rules workflow for
  every encounter.

## Persistence and Compatibility

The spell, background, capability, interactable, quest, combat, clock, merchant
and reward fields live in existing JSON-backed character-sheet/session state.
Normalizers lazily supply missing defaults for older campaigns; no relational
database migration is required. Legacy quests, background strings, combat
snapshots, spell-slot shapes, and locations without interactable collections
remain readable. The complete six-score requirement applies to new character
creation; existing partial legacy profiles remain loadable.

The executable runtime coverage is concentrated in:

- `tests/test_flagship_adventure.py` (the Play Now adventure through the socket
  pipeline, by two routes)
- `tests/test_gameplay_runtime_scenarios.py`
- `tests/test_turn_lifecycle_recovery.py`, `tests/test_session_chapters.py`
- `tests/test_free_text_actions.py`, `tests/test_story_beats.py`
- `tests/test_combat_vertical_slice.py`, `tests/test_combat_actions.py`,
  `tests/test_combat_modifiers.py`, `tests/test_vitality.py`
- `tests/test_spell_action_flow.py`, `tests/test_spell_effects.py`
- `tests/test_class_capabilities.py`, `tests/test_class_features.py`
- `tests/test_interactables.py`, `tests/test_combat_rewards.py`
- `tests/test_world_actions.py`, `tests/test_quest_engine.py`
- `tests/test_character_creation_mechanics.py`
