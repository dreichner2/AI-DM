# Gameplay Systems and Mechanical Boundaries

This document describes the implemented gameplay contract. It is intentionally
more conservative than the data schema: a field is not called functional unless
the normal player flow validates, applies, persists, projects, and restores it.

## Authoritative Turn Flow

Player input follows one state path:

1. A typed action intent or bounded text heuristic declares the attempted action.
2. Pre-narration validation resolves the actor, target, scene, equipment,
   resources, combat turn, and action economy.
3. Safe deterministic changes apply to `Session.state_snapshot` and the owning
   `Player` rows before narration.
4. The DM receives current structured state plus the already-applied result.
5. Post-narration extraction may propose additional changes. Validation may
   accept, modify, reject, or request clarification; prose never writes gameplay
   state directly.
6. Stable change IDs and the state, interactable-action, gameplay-event, and
   combat-reward ledgers prevent retry/reconnect duplication.
7. Player projections remove private character detail, hidden campaign-pack
   state, and enemy-planning data.

An explicit typed item, travel, rest, spell, class-capability, or scene-object
action that the rules validator marks invalid is persisted as a deterministic
rules rejection and stops before the DM provider. This prevents narration from
making an exhausted spell, stale item, unavailable capability, contradictory
object transition, blocked journey, or illegal rest sound successful.

Live scene state outranks canon and remembered narration. Canon projection may
initialize or repair a legacy location with no authoritative turn marker, but
it cannot overwrite a validated scene transition. Story threads may project
their own journal hooks; they cannot reactivate or complete mechanical quests.

## Fully Functional Slices

### Character foundations

- New characters require a nonblank class and all six ability scores in a legal
  point-buy allocation. Flat and nested score payloads use the same validator;
  classless, incomplete, out-of-range, and over-budget profiles fail closed.
- Curated backgrounds are authoritative packages of skill, tool, and language
  proficiencies. Unknown new background IDs fail closed; legacy free text remains
  display-only.
- Class, level, ability scores, proficiency bonus, background/race skill grants,
  saving-throw proficiencies, explicit stored expertise, wounds, and equipment
  feed authoritative roll or derived-stat logic. Race also contributes its
  implemented language, skill, and innate-spell grants.
- Class hit dice determine initial and later maximum HP. Level changes preserve
  current damage and never revive a dead character. Profile level increases
  cannot exceed the level earned by already-persisted XP.
- A class/race respec removes obsolete automatic spell provenance and stale
  resource/feature state before rebuilding the new grants. Story-learned and
  source-less legacy spells survive; a respec cannot accumulate both classes'
  automatic catalogs.
- Generic profile PATCH requests cannot replace stats, character sheets,
  inventory, or weapon proficiencies. Those mechanical changes must use the
  validated progression, action, equipment, or inventory paths.
- Duplicate proficiency sources do not stack. Explicit expertise is the only
  normal double-proficiency path.

### Spell resources and rest

- Known spells and preparation policy are normalized by class. A spell must be
  known and, for preparation-based classes, prepared before it can be cast.
- Standard slots, pact slots, Mystic Arcanum, concentration ownership, and the selected
  casting resource are stored in the existing character-sheet JSON and restored
  into the session actor on reload.
- Casts consume once. Exhausted spells fail before narration. During combat a
  legal cast also consumes the actor's persisted action, or its bonus action
  when the spell definition sets `castingTime: 'bonus_action'` (no catalog spell
  does yet); a cast is rejected once that budget is spent.
- Short rest restores pact slots and short-rest abilities. Long rest restores
  standard and pact slots, Arcanum, long-rest abilities, HP, and temporary-HP
  state according to the implemented policy. Rest does not erase persistent
  conditions, except that it wakes a `stable` character: a short rest brings it
  to 1 HP, a long rest to full HP, and both remove `stable` and `unconscious`.
  Dying and dead characters cannot rest.
- Hit dice (`actor.hitDice = {current, max, die}`, max = level, die from class,
  persisted as `Player.stats.hit_dice`) heal on a short rest: the typed rest
  intent's `hit_dice` says how many to spend (default: as many as needed to reach
  full HP); each heals one server-rolled die + CON modifier, minimum 1, and the
  rolls are replayed before the result is accepted. A long rest regains half of
  the maximum (minimum 1).
- The party day clock (`snapshot.clock = {day, phase, shortRestsSinceLong,
  lastLongRestDay}`, phases morning/afternoon/evening/night, default day 1
  morning) advances one phase on a short rest or structured travel and to the
  next morning on a long rest. At most two short rests count between long rests
  ("You need a long rest before another short rest helps.") and one long rest is
  allowed per day ("You have already rested today."). The clock is in the player
  projection and the DM context.
- `currentScene.restPolicy` (`safe` | `risky` | `none`, authored on locations,
  otherwise danger ≥ 3 is `risky` and ≥ 6 is `none`): `none` rejects any rest,
  `risky` allows only a short rest. Combat still blocks rest. A long rest also
  ends effects that last "until the next rest" and restocks merchants.

### Structured combat lifecycle

- Hostile/campaign encounter triggers materialize stable participants and
  explicit deterministic initiative.
- `combat.flags.turnEconomy` persists action, bonus action, reaction, and
  movement budgets. Only the current present, conscious, living, non-fled,
  non-surrendered actor can spend the relevant budget.
- Server-issued action/target IDs are re-resolved under the turn lock. Equipment,
  range bands, cover, target state, and remaining resources constrain legality.
- Player weapon attacks use server-owned attack and damage rolls. The modifier
  engine sets the roll mode (advantage or disadvantage adds a second d20 from
  the engine's deterministic roller; both dice and the mode are recorded),
  attack bonus dice, cover (half +2, three-quarters +5) and effect AC changes,
  automatic criticals, extra damage from effects, and resistance,
  vulnerability, and immunity on every damage component. Criticals, temporary
  HP, HP, defeat, and engine-owned end conditions apply before the DM narrates
  the result.
- One modifier engine (`combat/modifiers.py`) is the only place condition rules
  live, and every weapon, spell and enemy attack, saving throw, and pending-roll
  check reads it. Blinded, charmed (no attacks or harmful spells against the
  charmer), frightened (while its source is present), grappled, incapacitated,
  invisible, paralyzed, petrified, poisoned, prone, restrained, stunned,
  unconscious, slowed, dodging, hidden, and exhausted have their tabletop roll,
  movement, and turn effects; advantage and disadvantage from any number of
  sources cancel. The coarse bands decide "melee band": a melee attack is in it,
  and a ranged attack is when either side stands in the `melee` band. Standard
  `activeEffects` records carry attack/save/check bonus dice, AC bonus and
  floor, roll modes, bonus damage, resistances, speed zero, cannot act, no
  healing, and temporary HP per turn, with round durations, concentration, and
  `consumeOn` effects that are spent the first time they apply. Providers can
  register further modifier and extra-damage sources.
- Player characters at 0 HP fall `unconscious` and `dying` (out of combat:
  `stable`) instead of leaving the game; only the `dead` condition makes one not
  alive. Damage left over at 0 HP that reaches the maximum HP kills at once;
  other damage at 0 HP adds a death-save failure (two on a critical). When an
  ending turn passes a dying character's initiative slot, the engine rolls its
  d20 death save (10+ success, natural 1 two failures, natural 20 back at 1 HP;
  three successes `stable`, three failures `dead`) and records it as a visible
  `combat.death_save` change in the narration context and turn log. Healing a
  downed character who is not dead (spells, Lay on Hands, narrated healing)
  wakes it; the dead cannot be healed. Vitality conditions and death-save
  tallies persist on the Player row. Enemies and NPC allies are simply defeated
  at 0 HP.
- When every present player character is down, combat ends with
  `objective_failed` and the dying are left stable, unless the encounter flags
  set `lethalDefeat: true`. Any other combat end also stabilizes the dying.
- Movement does not end a turn automatically. Attack and spell actions require
  an explicit End Turn, which advances through the legal enemy block and then
  resets economy for the next round. The advance processes every turn end and
  start it crosses, each enemy of a grouped block included: effect durations
  tick, the actor's reaction returns, temporary HP per turn is granted,
  `dodging` ends, a prone creature that can act stands (spending its movement),
  and `recharge_5_6` abilities used earlier roll a deterministic d6 (5-6
  recharges; unavailable until then).
- Range bands and engagement (`combat/positioning.py`): every participant
  stands in `melee`, `near`, `far` or `distant`. The melee band is the press of
  close combat; a participant in it is engaged with the enemies it closed on or
  that closed on it (`position.engagedWith`, kept on both sides). Closing on a
  target from `near` (or from inside melee) puts both in melee and engages them;
  the target must stand in `melee` or `near`. Any move out of melee ends all of
  the mover's engagements. Melee weapon attacks need the target engaged with
  the attacker or both in melee; weapons tagged `reach` may target the `melee`
  and `near` bands. Ranged weapons reach every band; a ranged weapon or ranged
  spell attack made while engaged with an enemy that can act has disadvantage.
  Down, hidden and fully covered targets cannot be targeted.
- Every character's HUD bundle carries the universal actions, each with a
  `cost` (`action`, `bonusAction`, `movement`, `reaction`, each 0 or 1) and,
  when unavailable, a plain reason: Advance (movement; one band inward, or close
  into melee on a chosen enemy), Fall back (`combat.reposition`; movement; one
  band outward), Dash (action; one more movement this turn), Disengage (action;
  no opportunity attacks this turn, no movement of its own), Dodge (action;
  `dodging` until the actor's next turn), Help (action; an ally in the same or
  an adjacent band gets advantage on its next attack before the helper's next
  turn), Hide (action; not in melee without cover; server-rolled Dexterity
  (Stealth) against the highest passive Perception, 10 + WIS modifier, of the
  conscious enemies; success makes the actor `hidden`) and Stabilize (action; a
  dying ally in the same or an adjacent band; server-rolled Wisdom (Medicine)
  DC 10; success makes it `stable`). Standing from prone stays automatic. The
  pipeline re-resolves each action under the turn lock, applies its trusted
  changes before narration and hands the DM a `playerResolvedCombatAction`
  block with the exact outcome, check and reactions. Actions dispatch on their
  type, so class features that grant Dash, Disengage or Hide at another cost
  reuse them. Replaying the same turn record never spends or applies twice.
- Extra Attack: the first weapon attack spends the action and sets
  `turnEconomy.attacksRemaining` to the actor's attacks per action minus one
  (1 by default; class features raise it with
  `register_attacks_per_action_provider`); later attacks spend those.
  Two-weapon fighting: with a light melee weapon in each hand and an attack
  already made with one of them this turn, `combat.offhand_attack` strikes with
  the other as a bonus action, adding the ability modifier to damage only when
  it is negative.
- Reactions (`combat/reactions.py`): each participant has one reaction
  (`reactionRemaining`), back at the start of its own turn and spent by every
  reaction; a participant that cannot act or has `cannotReact` takes none.
  Providers registered with `register_reaction_provider` answer the
  `attack_hit`, `leaves_melee` and `damage_taken` events raised by player and
  enemy attacks and movement; at most one reaction per reactor per event (first
  provider wins), each recorded with its changes in the turn's results. The
  built-in provider makes opportunity attacks: leaving melee while engaged,
  unless disengaged this turn, draws one melee attack from each engaged enemy
  that can react (an enemy's first ready melee attack ability; a player
  character's main-hand melee weapon, automatically unless its sheet sets
  `autoReactions: false`). A mover dropped by the strike stays where it is.
- Enemy execution shares the turn engine's present/actionable/targetable
  predicates, revalidates limited-use resources, and supports bounded
  single-target attacks, saving throws, damage, conditions and healing
  (`healing.dice` with `targetType` self or single: the enemy itself or its most
  wounded ally). The block resolves in initiative order on one working copy, so
  later enemies see earlier moves, reactions and heals. An enemy with a melee
  attack that is not in reach moves inward one band and attacks when that
  brings the target into reach, or spends its turn on a second move otherwise;
  a retreating enemy moves one band outward (provoking as above) before it
  flees. Enemy targeting, in planning and at resolution, applies the same
  range-band, engagement, cover and hidden rules as the player HUD, using each
  ability's declared range. Unsupported delivery shapes fail closed and are
  removed from narration context instead of becoming invented damage. Player
  projections expose public turn/economy state, the initiative ribbon
  (`combat.turnOrder`), each participant's band and engagements with visible
  participants, but not spent-action IDs or private planning data.
- Engine-selected enemy retreat/flee, surrender, and negotiation intents apply
  authoritative conditions and feed the same combat end-condition engine as
  defeat; narration cannot substitute a different ending.
- Terminal combat outcomes can derive authored encounter rewards and
  consequences. XP, currency, and item rewards allocate only to exact present
  player participants; defeated or unconscious participants remain eligible,
  while absent participants do not. Common rewards apply only to successful
  outcomes unless a defeat or retreat reward is explicitly authored.
- Combat reward changes and linked quest events receive IDs derived from the
  authoritative `combat.end` change. A `combat.reward.finalize` marker is
  accepted only after every required output is in the state ledger. Replaying a
  partially applied `combat.end` derives only missing outputs, so XP, currency,
  items, flags, consequences, and quest progress do not duplicate.
- Initiative, round, active actor, economy, participant HP, and combat end state
  survive database expiry, disconnect, and reconnect. Encounters started from
  the operator API use the same seeded, modifier-aware initiative as pack and
  player-started encounters; only stored legacy combats keep roster order.
- Resolved player rolls report `dc` (the stated DC, else the roll type's base
  DC), `dcSource` (`stated` or `default`) and `outcome`, except initiative and
  death saves; landed roll payloads expose them to players. Pending checks,
  saves, and attacks take their mode from the modifier engine unless the roll
  gate forces one, and roll and show effect bonus dice.

### Mechanical quests and campaign branches

- Quests support active, blocked, completed, and failed gameplay state;
  objectives support blocked/open/completed/failed state and prerequisite
  objective IDs.
- `completeWhen` and `failWhen` rules evaluate applied mechanical events, not a
  completion sentence in narration. Completion policy can require all or any
  required objectives.
- XP, currency, items, flags, and authored consequences use ledger-stable IDs and
  apply exactly once with the terminal transition.
- Legacy journal-only quests remain readable, but cannot gain mechanical terminal
  status from narration alone.
- Campaign-pack checkpoints may use optional `branchWhen: {flagKey, equals}`.
  Selection reads persisted authoritative flags, falls through to an unconditional
  authored route, and remains revision/idempotency safe.

### Travel and exact scene-item movement

- `world.travel` requires an existing, visible, accessible adjacent destination
  and is rejected during active combat.
- Location-local items, NPC presence, quest IDs, description, danger, and spatial
  scene data are cached and restored when the party returns.
- Typed pickup and drop resolve exact item IDs, move one item between one source
  and one destination, and fail closed for stale IDs. Duplicate names require an
  exact selection or clarification. Partial-stack moves receive a deterministic
  derived identity with `splitFromItemId`, so one exact item ID never occupies
  both source and destination.
- A free-form DM sentence cannot mint a new inventory item. An untyped legacy
  pickup is accepted only when the same validated batch removes that exact item
  from the authoritative scene.

## Bounded Authoritative Mechanics With Player Controls

These mechanics are connected to the typed request, validation, application,
persistence, projection, AI-context, and standard session UI paths. The
implemented catalogs are intentionally bounded; each entry describes the
mechanics the server currently applies, rather than full tabletop coverage.

### Targeted spell effects

- Fire Bolt, Magic Missile, Sacred Flame, Cure Wounds, Healing Word, Entangle,
  Ray of Frost, Burning Hands, Shatter, Fireball, Lightning Bolt, Eldritch Blast
  Scorching Ray, Shocking Grasp, Chill Touch, Poison Spray, Vicious Mockery,
  Produce Flame, Inflict Wounds, Guiding Bolt, Bless, Bane, Shield of Faith,
  and False Life have
  server-owned effect definitions. A typed cast with exact
  target IDs validates turn ownership, presence, relation, range, cover, target
  count, spell knowledge/preparation, and casting resources before rolling.
- The resolver owns attack rolls, target saving throws, automatic delivery,
  damage, healing, temporary HP, condition add/remove, bounded durations, and
  concentration replacement, with modes, bonus dice, automatic save failures,
  cover, and defenses from the modifier engine. Healing and ally spells may
  target a player character at 0 HP who is not dead; damage spells may not
  target the defeated. Direct health-damage changes and resolved spell damage
  trigger server-owned concentration checks; turn advancement expires supported
  timed effects.
- Fire Bolt, Ray of Frost and Sacred Flame scale their damage dice with caster
  level. Cure Wounds and Healing Word add the casting modifier and gain one die
  per higher slot level; they can also heal the active party outside combat.
  Healing Word uses a bonus action in combat. The selected slot level reaches
  validation and replay, and at most one levelled spell is allowed per turn.
- The four area damage spells above take one primary target, include other legal
  enemies in the same range band and zone up to their target limit, share one
  damage roll, and resolve each enemy's save separately. Allies are excluded.
- Magic Missile rolls each dart separately and distributes darts round-robin
  across the selected targets, with one extra dart per higher slot. Scorching
  Ray rolls each attack separately and adds a ray per higher slot; Eldritch
  Blast gains beams at caster-level tiers.
- Newly generated class spellbooks start with three AIDM-original sample spells;
  those samples are marked as narrated. Existing known spells are preserved.
- Slot/action consumption, combat effects, concentration state, player HP, and
  the stable cast-resolution ledger persist together through the normal state
  application path. A repeated cast ID returns the existing resolution rather
  than rolling or applying effects again.
- A rules-backed spell without an exact target pauses for a legal target choice
  before narration or spending a slot. Named free-form casts use the same path.
  Spells marked narrated still rely on the Dungeon Master for their effects.

### Typed class capabilities

- The mechanically complete catalog is intentionally small: Fighter Second
  Wind, Fighter Action Surge, and Paladin Lay on Hands.
- Passive rules also grant Extra Attack at level 5 to Barbarians, Fighters,
  Monks, Paladins and Rangers; Barbarian Danger Sense from level 2; and the
  Barbarian and Monk unarmored-defense formulas, including their different
  shield restrictions. For multiclass labels these features use the primary
  class only.
- Typed capability use resolves an exact actor and target, revalidates the
  current combat actor and action/bonus-action budget, applies server-owned
  healing or action restoration, consumes the persisted class-feature pool,
  and refreshes only on its declared short or long rest. Lay on Hands can
  restore an ally at 0 HP who is not dead; Second Wind is impossible while
  unconscious.
- Capability resolution is generated before narration and replay-validated
  from its trusted rolls and result. Post-DM extraction cannot manufacture a
  capability use or change its target, cost, or healing amount.
- The Mechanical actions panel shows exact legal recipients, requested Lay on
  Hands points, persisted remaining uses, action/bonus-action limits, and rest
  refresh policy.

### Consumable item effects

- `aidm_server/item_effects.py` holds the catalog: Potions of Healing, Greater
  and Superior Healing, Antitoxin, Healer's Kit (10 uses in
  `metadata.usesRemaining`), Holy Water, Alchemist's Fire, Acid, and spell
  scrolls. An item has an effect when `metadata.effectId` names an entry or its
  normalised name/alias matches one (scrolls: `Scroll of <Spell>` or
  `metadata.spellId`). Starter kits carry a Potion of Healing; clerics and
  druids also carry a Healer's Kit.
- Using a catalog item (typed `inventory_action: use` with an exact item id and
  optional `target_id`, a free-text request that resolves to exactly one item, or
  the combat HUD's `item.use.<itemId>` action) produces one `item.use` change:
  server-rolled dice, one quantity or use removed together with the effect, the
  combat cost charged by that change, replay-checked before it is accepted.
  Potions cost a bonus action on oneself and an action for an ally in the same or
  an adjacent range band; healing follows the vitality rules (a dying ally wakes,
  the dead cannot be healed). Thrown items make a DEX + proficiency ranged attack
  through the modifier engine. Scrolls cast their rules-backed spell at base
  level without a slot, only for readers whose class list has the spell.
- Items without an effect keep the DM-adjudicated path, and narration never
  repeats the removal of a server-resolved item.

### Shops

- `aidm_server/commerce.py` reads `snapshot.merchants` (written by the campaign
  pack importer). A merchant is open when its NPC is present in the scene (or,
  without an NPC, when the party is at its location) and no combat is running;
  only open merchants are projected to players (`currentScene.merchants`) and
  given to the DM.
- `shop.buy` and `shop.sell` settle in one atomic, replay-safe change: prices come
  from the server (stock entries or `aidm_server/price_list.py` for
  `{"catalog": key}` entries), funds are checked across all denominations with
  change given in gp/sp/cp, stock is decremented, and bought items keep their
  `effectId`/`metadata`. Selling pays the listed price × `buyRate`, rounded down
  to the copper; unpriced items and equipped items are refused.
- A free-text buy or sell is routed to an open merchant that trades the item; with
  none, the DM adjudicates and coins change only by the amount the narration
  states. A client-supplied `cost_gold` is never used.

### Persistent scene interactables

- Exact-ID doors, locks, containers, objects, and hazards support inspect, open,
  close, lock, unlock, search, break, use, disarm, trigger, and reset actions.
  Legal transitions may require exact items, tools, class capabilities,
  authoritative checks, flags, or other object states.
- Successful transitions atomically update `currentScene` and the active
  location's `sceneState`, including open/locked/broken/searched/used/depleted
  state, uses remaining, revealed objects or contents, controlled secondary
  objects, revision, and last-interaction metadata.
- Interactable IDs are globally unique across current and cached locations.
  Hidden or GM-only targets fail as not found for players. An optional expected
  revision rejects stale selections, and `interactableActionLedger` makes a
  same-ID replay idempotent while rejecting reuse for a different action.
- Player projections remove requirements, secrets, hidden fields, authored
  transitions, and GM-only events. Validated public events can drive mechanical
  quest objectives without allowing narration to complete them.
- The Mechanical actions panel submits the exact public object ID, selected
  transition, and snapshot revision. Candidate availability is inferred only
  from public state; hidden requirements still fail closed on the server. Object
  actions remain disabled during combat until a turn-economy cost is defined.

## AI Context

- The DM context separates current structured state, recent validated changes,
  pending checks, present NPC IDs, player spell resources, quest predicates,
  combat initiative/economy, authoritative spell/capability results,
  interactable state/events, and legal tactic summaries.
- Prompt rules state that only `currentScene.activeNpcIds` may speak or act and
  that structured state outranks canon, memory, and narration.
- A spell cast is not automatically a generic caster check. Exact-target
  catalog spells use server-owned attack or save resolution. A legacy spell
  attack without a complete effect definition may still request the supported
  player attack-roll path; a non-attack resource-only cast does not ask the
  caster for an unrelated ability check.

## Partial Gameplay

- The character picker presents a much broader class/subclass and race catalog
  than the authoritative feature catalog. Subclass names are stored, but most do
  not yet select structured mechanics. The normal creator also lacks class-skill
  and expertise selection; explicit stored expertise resolves correctly when
  present.
- Many ancestry traits remain descriptive or data-only. Implemented race skills,
  languages, and innate spells work, but catalog movement modes, damage
  resistances, and most active ancestry abilities are not yet compiled into
  combat actors.
- The authoritative class-capability catalog contains only Second Wind, Action
  Surge, and Lay on Hands. Apart from the passive rules listed above, other
  class features remain descriptive or resource-only.
- Thirteen curated spells have server-owned target/effect definitions, with some
  tabletop details still simplified. Other known/prepared spells consume the correct resource
  but remain resource-only or narration-assisted for their effects. The
  Mechanical actions panel lists the authoritative definitions and their exact
  legal targets.
- Combat has coarse range bands, cover, action budgets, enemy tactics, morale
  data, surrender/flee states, and several end labels. It does not yet implement
  a full grid/pathfinder, opportunity attacks, or general reactions, and no
  player action yet grants Dodge, Hide, or Help (the conditions work when
  applied). Range is one band per participant, so "melee band" effects (prone,
  automatic criticals) use the band simplification above. Enemy targeting does
  not yet apply the player-HUD spatial model for ability-specific range or
  hidden targets, and enemies never target downed characters; unsupported enemy
  ability deliveries fail closed instead of inventing damage. With
  `lethalDefeat`, the dying stay dying after combat ends, and nothing outside
  combat rolls their death saves.
- Equipment affects armor class and weapon actions, but armor proficiency is not
  yet a first-class class rule. Only the catalog items above have rules
  effects; Alchemist's Fire records its burning effect on the target, but the
  start-of-turn damage it describes is not yet applied by the combat turn loop.
- Merchants have unlimited coin, sold items do not enter their stock, and prices
  are fixed (no haggling). Trades outside a merchant record stay narration-only.
- Actor-to-actor transfers are atomic when a recipient is resolved, but the
  standard item composer does not yet provide a complete item-plus-recipient UI.
- The scene-object UI infers candidate transitions from player-visible state;
  hidden item/tool/check/flag requirements are not exposed and are enforced only
  by the authoritative server. Authored `mechanicalEffects` are emitted as
  trusted event metadata but are not automatically translated into arbitrary
  HP, inventory, or other secondary state mutations. Revealed container contents
  also do not move into an inventory until a separate exact item action occurs.
- Encounter rewards are authored rather than inferred from arbitrary enemy
  narration or a general creature-loot economy. Generated encounters require a
  stable encounter ID/definition before the exact-once reward engine can run.
- Campaign packs support mechanical checkpoints, outcomes, and flag branches;
  they are not a general-purpose arbitrary predicate language.
- `abandoned` remains a recognized terminal compatibility value, but there is
  not yet a complete player-facing abandon-quest action.

## Data-Only or Narration-Only Areas

- Relationships and many social consequences can be stored, but most do not yet
  feed a complete contested social-resolution system.
- General reinforcements, negotiation objectives, surrender terms, and retreat
  costs have state seams but not one complete player-facing rules workflow for
  every encounter.

## Persistence and Compatibility

The new spell, background, capability, interactable, quest, combat, and reward
fields live in existing JSON-backed character-sheet/session state. Normalizers
lazily supply missing defaults for older campaigns; no relational database
migration is required. Legacy quests, background strings, combat snapshots,
spell-slot shapes, and locations without interactable collections remain
readable. The complete six-score requirement applies to new character creation;
existing partial legacy profiles remain loadable, but the generic profile PATCH
can no longer replace their mechanical state directly.

The executable runtime coverage is concentrated in:

- `tests/test_gameplay_runtime_scenarios.py`
- `tests/test_combat_vertical_slice.py`
- `tests/test_spell_action_flow.py`
- `tests/test_spell_effects.py`
- `tests/test_class_capabilities.py`
- `tests/test_interactables.py`
- `tests/test_combat_rewards.py`
- `tests/test_combat_invariant_regressions.py`
- `tests/test_world_actions.py`
- `tests/test_quest_engine.py`
- `tests/test_character_creation_mechanics.py`
