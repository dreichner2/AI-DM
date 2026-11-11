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
  beat pointing to the suggestions and to Settings.

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
onward and shops; and the place the party is at. Seven rules follow it: tell
each result faithfully and add none; for anything not done, at most one
sentence of the hero starting toward it and a closing sentence naming the
action still open; the party stays put unless it arrived; no rolls for anything
the engine owns; found is not taken, and gains only when "Did" says so; speak
to the hero as "you", no closing question, no markdown; people speak from what
they know.

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
actions stay. The scripted narrator is not touched by either.

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
| `check` | `Investigation check: 14 — success`, naming the skill or ability once, `Check: 14 — success` when neither is known (` (DC 10)` only when the DC is shown; ` (taking your time)` after a retry); a roll made with no check pending is not a check: `d20: 10`, or `Strength: 13 (d20 10 + 3)` with the ability picked |
| `hp` | `Took 7 damage` / `Healed 5` |
| `condition` | `You are prone` / `No longer prone` (only when a condition starts or ends); `You are out of the fight`; `You come round` for a hero who wakes |
| `death_save` | `Death save: 12 — success` |
| `combat_end` | `Victory`, `The fight ends — they yield`, `The fight ends — they flee`, `You got away` |
| `item_gained` / `item_lost` | `Taken — {item}` / `Received — {item}` (`×N` when more than one); `Used`, `Given`, `Sold — {item}` |
| `coins` | `+15 gp` / `−50 gp` |
| `clue`, `objective_complete`, `objective_new`, `place` | `Clue — {title}`, `Objective complete — {title}`, `New objective — {title}`, `New place on the map — {name}` |
| `xp`, `level` | `+100 XP`, `Level 2 reached` |
| `defeat` | `Defeated — you wake later with 1 HP` / `You come to — 1 HP` |
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
`It is still Seren Ashgrove's turn.`), and the AI narrator's rule 6 tells it to
name every hero and never write "you" for one. When a hero's turn ends with
nothing else to tell, the narration is only the line saying who acts next.

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
  block and resets economy for the next round.
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

### Reactions

Each participant has one reaction (`reactionRemaining`), back at the start of
its own turn; a participant that cannot act or has `cannotReact` takes none.
Reaction providers answer the `attack_hit`, `leaves_melee` and `damage_taken`
events, at most one reaction per reactor per event. The built-in provider makes
opportunity attacks: leaving melee while engaged, unless disengaged this turn,
draws one melee attack from each engaged enemy that can react (a player
character's main-hand melee weapon, automatically unless its sheet sets
`autoReactions: false`). Shield and Uncanny Dodge are automatic reactions too
(see below).

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
- Engine-selected retreat, surrender and negotiation apply authoritative
  conditions and end the fight through the same end-condition engine as defeat;
  narration cannot substitute a different ending. The scripted narrator tells
  such turns in the world's words (`… gives ground.`, `… throws down its weapon
  and yields.`, `… breaks and runs.`), never by an intent's name.
- `recharge_5_6` abilities used earlier roll a d6 at the enemy's turn start (5-6
  recharges). Limited-use resources are revalidated before each use.

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
is `combat.flags.partyScaling`. `scripts/solo_balance.py` plays every ready-made
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
