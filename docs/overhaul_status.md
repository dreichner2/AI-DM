# Story overhaul — status and plan (11 November 2026)

This page is the current picture of the `claude/everything-better` branch for anyone reviewing
it: what is in, how each part was checked, what is not checked, what is being built right now,
and the plan after that. Where it differs from the older sections of `docs/roadmap.md`, this page
is the newer one.

It describes the branch, not a running game. The build that serves a given play session can be
older than the branch head (a session-record feature that states the build is planned below).

## 1. What is in the branch, and how it was checked

"Tests" means the automated suites at that commit (backend `pytest`, frontend `vitest`, type
check, lint, build, bundle budget, strict lint of the three playable packs). "Played" means the
lead played it in real browsers through the app, with the scripted narrator and with a live AI
narrator (DeepSeek), usually two players in two browsers.

| Commit | What it added | Tests at that commit | Played |
| --- | --- | --- | --- |
| `0c9a40f` | Every roll as a card (attacks, damage, saves, checks, healing; the number flickers before landing); brisker narrator; enemies speak; the finishing blow, captives and questioning; optional ambience | backend 3617, frontend 945 | yes |
| `b72ae4a` | Less waiting: End turn costs no reply, turns end themselves, a search takes what it finds; first-run flow; the level-up moment. A clicked fight action reaches one model (was up to five) | backend 3658, frontend 994 | yes; measured 1.9–2.9 s server time for attack + enemy phase + hand-over on that path (was 7.7 s + 6.5 s) |
| `38161d0` | Rooms inside places and reach; distances in fights; a hero's own threads (desire, attachment, burden) and Inspiration; bringing a hero into another adventure; two players never block each other | backend 3754, frontend 1023 | yes |
| `5a9ce96` | Conversations: topics, speech acts in the player's own words (promise, show, threaten, offer, claim, apologise), dispositions, memory, callbacks. Written lines are told as written by the engine | backend 3818, frontend 1028 | yes |
| `b6e2b11` | Plans that change the scene (nine effects, affordances, lasting scene facts); fights about something (stakes, enemy goals, results, defeat that changes the situation); pacing by cause (clocks, one director note per turn); chapter ends, players' plans, coming back; ending card and chronicle | backend 3909, frontend 1047 | in part: typed actions on hidden things, conversation checks as roll cards, improvised attempts. The effects, stakes, clocks and chapter ends were covered by tests and by the implementers' screenshots at this commit, not yet by lead play |
| `e7b20b6` | First pass of the reworked opening chapter (the Lantern Post inn: yard plans, the watchers' clock, the theft with stakes, a defeat that wakes the party in debt, three ways through); a successful improvised attempt leaves a lasting, engine-checked scene fact | last full backend run 3958 passed and 2 failed; both failures were then fixed and their files re-run green (no full re-run after that); frontend 1047 | the theft was played once, solo, with a live narrator (see §3) |

## 2. What has not been verified

- The three intended ways through the opening chapter have not yet been played end to end with a
  live narrator in two browsers. They pass as automated play-throughs.
- Nothing here has been played by people other than the lead since `0c9a40f`. Whether any of it
  is more engaging is unproven.
- Long sessions, many chapters, and campaigns other than the three example packs.
- Generated adventures: the only generator in the branch is the old three-location Forge
  template. It does not produce rooms, topics, plans, stakes or clocks.
- The hosted beta and `main` are untouched; nothing from this branch is deployed.
- The dependency alerts reported for the default branch have not been reviewed.
- Startup size is at its limit (entry chunk 199.3 of 199.9 KiB gzip).

## 3. Problems found by playing the opening chapter, and their state

Found on `e7b20b6` with a live narrator:

- The model's telling of enemy turns contradicted the engine (a thief "over the hedge" while the
  engine had him at a barred gate), and a bystander voiced the secret of a person the party had
  not met, because DM-only pack content was in the narrator's context. **Being fixed now (§4,
  first task).**
- About fifteen authored details of the chapter could not be expressed by the engine (for
  example a plan whose result depends on how a person feels about the party; a callback that
  needs no memory; a scene fact set by a conversation). **Being fixed now (§4, second task).**
- A plan aimed at an enemy ignored reach; a plan did not end the turn; a surname matched an
  object keyword ("Tovin Bell" rang the bell); breaking a container did not show what was inside;
  numbered enemies read badly in prose ("the ashmarked cutthroat 1"). **Being fixed now.**
- An improvised success that the engine's rules do not cover leaves a remembered fact but no
  mechanical change: climbing onto a roof does not put the hero on a roof. **Planned (§5, step 2).**

## 4. In progress (uncommitted at the time of writing)

- **Truth in fights.** With any narrator the engine tells what enemies do; a narrating model
  keeps only the acting hero's own action, in at most forty words. In pack adventures the model
  is given only what the table knows (no secrets, no DM notes, nobody unmet, nothing
  undiscovered). Plus the smaller fixes listed in §3.
- **The chapter as written.** One condition language used everywhere a pack tests state; richer
  plan results (variants by condition, leads, a darkened room, conditions on enemies, timed stake
  changes, plans that retire each other); callbacks without a memory; a scene fact or lead from a
  conversation; an object that refuses while a condition holds; a checkpoint that completes on
  leaving a place once a condition holds; a thief who forces the strongbox himself. Then the
  left-out chapter details are written and the chapter's proof is tightened.

## 5. Plan after that, in order

1. **What was actually played.** A build identity on the health endpoint, every turn and the
   Settings sheet; a downloadable session record (build, pack, narrator, settings, every turn
   with its reply, beats, roll cards and timings; nothing a player view hides); at most four
   suggestions with people before objects; the "Narrated magic" wording removed.
2. **Consequences you can stand on.** A successful improvised attempt returns one typed
   *ruling* that later actions obey: a new spot the hero is now in (a real room: others enter by
   the same check, it overlooks what it overlooks, a melee attacker has to get in first), a new
   way, a thing opened / broken / moved, a person's agreement (recorded, remembered, usable by
   pack conditions), one of the nine effects, or only a fact. When nothing fits, the game says so
   rather than pretend. Acceptance test: climb onto the roof; be there; the other player sees it
   and can follow; a fight respects it; leaving and returning keeps it.
3. **A campaign from your premise.** A model proposes the opening situation's *structure* in a
   strict schema (a few connected places with rooms; people who want things that conflict, with
   what they fear, ask and offer; things worth using; a problem in motion; opposition with a
   goal; hooks to the heroes' own threads; a frontier of named places). The engine validates it,
   asks once for a repair, drops or replaces what does not hold together, and gives every number
   and rule. Places are written when the party heads for them and never rewritten. No prescribed
   ending: a chapter ends when its problem is settled or left behind, with what was decided read
   from state; a goal of the players' own is given what it needs.
4. **Recorded trials.** Generations for at least three unseen premises (a familiar fantasy, a
   very different genre, an unusual protagonist or environment) plus one typed by the players on
   the day. Failures are kept; no rerolling for a better-looking one; no manual dressing between
   generation and play. Each is played solo and as a pair, attempting things nobody prepared.
5. **A scene view, as a switch.** A persistent schematic view of the current place (rooms,
   people, things, exits; select to act; text stays unrestricted) over the same engine, compared
   with text alone on fresh generated campaigns. Richer generated art only if that comparison
   favours the scene view. No action will ever wait for artwork.

Not planned for now: more hand-written depth for the opening chapter (it stays as the regression
reference), form changes for heroes, companions who fight, walking animation, per-turn image
generation, more spells and items.

## 6. Checking it yourself

```
.venv/bin/python -m pytest -q -p no:cacheprovider          # backend, about 13 minutes
cd aidm_frontend && npx vitest run && npx tsc -b --noEmit && npm run lint && npm run build && npm run bundle:budget
.venv/bin/python scripts/aidm_pack.py lint --strict docs/examples/the_road_of_unremembered_kings_campaign.json
```

To play without any AI provider (the scripted narrator), from the repository root after
`npm run build` in `aidm_frontend/`:

```
AIDM_SKIP_REPO_ENV_LOCAL=1 AIDM_LLM_PROVIDER=fallback AIDM_SERVE_FRONTEND=true \
AIDM_FRONTEND_DIST_DIR=aidm_frontend/dist AIDM_LOCAL_DATA_DIR=<an empty folder> \
.venv/bin/python scripts/deploy_bootstrap.py --port 5057
```

The opening chapter's automated play-throughs are `tests/test_lantern_post_chapter.py`; the
systems' own proofs are `tests/test_plans_and_stakes.py`, `tests/test_conversations.py`,
`tests/test_where_everyone_stands.py`, `tests/test_director_return.py`,
`tests/test_less_waiting.py` and `tests/test_improvised_facts.py`. Each finished turn stores its
timings (`timings` in the turn's metadata: engine, narration, settle, time to act, whether a
model was called).
