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
  legal cast also consumes the actor's persisted action.
- Short rest restores pact slots and short-rest abilities. Long rest restores
  standard and pact slots, Arcanum, long-rest abilities, HP, and temporary-HP
  state according to the implemented policy. Rest does not erase persistent
  conditions.

### Structured combat lifecycle

- Hostile/campaign encounter triggers materialize stable participants and
  explicit deterministic initiative.
- `combat.flags.turnEconomy` persists action, bonus action, reaction, and
  movement budgets. Only the current present, conscious, living, non-fled,
  non-surrendered actor can spend the relevant budget.
- Server-issued action/target IDs are re-resolved under the turn lock. Equipment,
  range bands, cover, target state, and remaining resources constrain legality.
- Player weapon attacks use server-owned attack and damage rolls. Criticals,
  temporary HP, HP, defeat, and engine-owned end conditions apply before the DM
  narrates the result.
- Movement does not end a turn automatically. Attack and spell actions require
  an explicit End Turn, which advances through the legal enemy block and then
  resets economy for the next round.
- Enemy execution shares the turn engine's present/actionable/targetable
  predicates, revalidates limited-use resources, and supports bounded
  single-target attacks, saving throws, damage, and conditions. Unsupported
  delivery shapes fail closed and are removed from narration context instead of
  becoming invented damage. Player projections expose public turn/economy state
  but not spent-action IDs or private planning data.
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
  survive database expiry, disconnect, and reconnect.

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
implemented catalogs are intentionally bounded; the listed spells, capabilities,
and public scene-object actions are complete player-facing slices, not claims of
full tabletop rules coverage.

### Targeted spell effects

- Fire Bolt, Magic Missile, Sacred Flame, Cure Wounds, Healing Word, Entangle,
  and Ray of Frost have server-owned effect definitions. A typed cast with exact
  target IDs validates turn ownership, presence, relation, range, cover, target
  count, spell knowledge/preparation, and casting resources before rolling.
- The resolver owns attack rolls, target saving throws, automatic delivery,
  damage, healing, temporary HP, condition add/remove, bounded durations, and
  concentration replacement. Damage to a concentrating caster triggers a
  server-owned concentration check; turn advancement expires supported timed
  effects.
- Slot/action consumption, combat effects, concentration state, player HP, and
  the stable cast-resolution ledger persist together through the normal state
  application path. A repeated cast ID returns the existing resolution rather
  than rolling or applying effects again.
- Existing clients that omit exact target IDs retain the resource-only cast
  path. They do not receive authoritative damage, healing, save, or condition
  resolution merely because narration describes an effect.

### Typed class capabilities

- The mechanically complete catalog is intentionally small: Fighter Second
  Wind, Fighter Action Surge, and Paladin Lay on Hands.
- Typed capability use resolves an exact actor and target, revalidates the
  current combat actor and action/bonus-action budget, applies server-owned
  healing or action restoration, consumes the persisted class-feature pool,
  and refreshes only on its declared short or long rest.
- Capability resolution is generated before narration and replay-validated
  from its trusted rolls and result. Post-DM extraction cannot manufacture a
  capability use or change its target, cost, or healing amount.
- The Mechanical actions panel shows exact legal recipients, requested Lay on
  Hands points, persisted remaining uses, action/bonus-action limits, and rest
  refresh policy.

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
  Surge, and Lay on Hands. Other class features remain descriptive or
  resource-only.
- Only seven curated spells have complete server-owned target/effect
  definitions. Other known/prepared spells still consume the correct resource
  but remain resource-only or narration-assisted for their effects. The
  Mechanical actions panel intentionally lists only the seven fully authoritative
  definitions and their exact legal targets.
- Combat has coarse range bands, cover, action budgets, enemy tactics, morale
  data, surrender/flee states, and several end labels. It does not yet implement
  a full grid/pathfinder, opportunity attacks, general reactions, death saves,
  or every condition's rules effect. Enemy once-per-combat and rest-scoped
  abilities are now consumed authoritatively, but `recharge_5_6` does not yet
  have a persisted recharge-roll lifecycle. Enemy targeting also does not yet
  apply the full player-HUD spatial model for ability-specific range, hidden
  targets, cover AC, and disadvantage; unsupported enemy ability deliveries
  fail closed instead of inventing damage. Timed spell effects advance on the
  implemented actor-turn boundary, but a grouped block of consecutive enemy
  turns does not yet tick every skipped/source/target boundary independently.
- Equipment affects armor class and weapon actions, but armor proficiency is not
  yet a first-class class rule. Consumable removal is not yet atomically bound to
  a general catalog of validated item effects.
- Buy and sell retain the older DM-confirmed transaction path. Funds are checked,
  but merchant presence, stock, and server-authored prices are not yet a complete
  commerce model.
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

- Shop inventories, price lists, scarcity, and restocking are not authoritative.
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
