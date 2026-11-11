# AIDM Roadmap

AIDM is a closed beta in beta-hardening territory: the core gameplay, campaign,
player, session, world, map, Socket.IO, state pipeline, canon memory, and
metrics surfaces exist, and the Story overhaul made the ready-made adventure
fully playable by the rules.
The highest-return work now is the open items under "Remaining After the Story
Overhaul" and measuring play with a live AI provider, while keeping security,
runtime, and maintenance risk low.

## Current Status - 2026-11-10 (the Story overhaul)

The Story overhaul reworked play end to end (see Done). The ready-made
adventure plays from the title screen to an ending in Safe Mode, verified by the
backend suite, the end-to-end release smoke (`make play-now-smoke`) and live
play-throughs in a real browser. No live AI provider was exercised during the
overhaul; the open items are listed under "Remaining After the Story Overhaul".

## Platform Status - 2026-07-12

- The current implementation includes the intended closed-beta gameplay
  surface, executable local release/readiness gates, hosted cookie
  authentication, the single-worker production topology, and
  PostgreSQL-backed production guards. This is not a readiness declaration;
  hosted evidence, backup proof, and operator signoff remain separate gates.
- The dated technology upgrade report records a completed hosted-staging
  PostgreSQL 17-to-18 upgrade with rollback and forward-restore evidence. The
  repository cannot re-verify that external state, and a production database
  cutover remains a separate maintenance and operator-signoff decision.
- The provider registry currently supports DeepSeek, Codex CLI, Gemini,
  NVIDIA/Kimi, and the deterministic fallback. Codex CLI routes its current
  default selection through GPT-5.6 Sol Medium and runs without host tool
  access; tool events fail closed.
- Release state is determined by `docs/release_checklist.md` and generated RC
  evidence, not by the Done list in this roadmap.

Archived review material lives in:

- `docs/archive/improvements_suggestions_legacy.md`
- `docs/archive/organized_improvement_suggestions_legacy.md`

## Done

### The Story overhaul

- Story view as the default play screen: title screen with continue, the
  ready-made adventure and a hero choice; story feed with per-turn story beats;
  server-offered suggestions and an action tray (spells, abilities, items,
  objects, rest); party dock; combat ribbon; drawer with character, pack, magic,
  journal and an illustrated map; sheets for settings (including the narrator),
  account, adventures and new adventures; guided hero creation. The classic
  console remains as Table tools.
- The player's-eyes view (`view=player&player_id`), offered actions with plain
  refusal sentences, take labels, the journal block, story beats on the turn and
  its log entry, the title's continue line, and a fresh Play Now.
- Typed words that name one action run that action; a failed check can be
  retried by taking time.
- Turn integrity: a durable lifecycle marker with exactly-once resume, a
  campaign-wide turn lock, post-narration effects on fresh character rows,
  pruned per-turn snapshots, absent players skipped after a grace period, and
  marked opening turns.
- Sessions as chapters of one continuing campaign, summary list payloads with
  gap-free paging, and a versioned save document that archives or restores.
- Rules: dying, death saves, stable and dead; the condition and modifier engine;
  conditions that wear off; range bands, engagement, the universal combat
  actions, reactions and opportunity attacks; 61 rules-backed spells with
  upcasting; class features for levels 1–5 of the twelve core classes;
  consumables; hit-dice rests, the day clock and rest safety; shops; party
  scaling; enemies that fight back and bosses that hold; defeat and waking, and a
  lost or fled fight never ending the story.
- Campaign packs: self-starting encounters, object checks and consequences,
  story flags, merchants, the world map, rest policy, read-aloud setups,
  adjustments from what the party knows, peaceful resolution by object, action
  labels and text, ending variants, a starting clock; *The Road of Unremembered
  Kings* rewritten to be fully mechanical and lint-clean under `--strict`.
- Narration: shorter prompts within a fixed context budget, an opening brief, a
  scripted narrator that plays the ready-made adventure offline, no network call
  in Safe Mode, and helper calls only where they can matter.
- Tooling: the toolchain check accepts current Python, Node.js and npm releases;
  `scripts/solo_balance.py`; the Play Now release smoke.

### Earlier beta hardening

- Backend app factory, central config, request IDs, CORS scoping, auth context,
  rate limiting, migration/bootstrap checks, and production schema guardrails.
- Backend-owned DTO contracts with generated frontend TypeScript types.
- Session event spine, turn persistence, turn status events, state application,
  canon job queue, and projection repair tooling.
- Frontend TypeScript strictness, split CSS files, extracted hooks/components,
  browser smoke tests, bundle budget checks, and generated API contract usage.
- Account login hardening for passwordless legacy accounts: existing accounts
  now require a valid saved account token or a high-entropy operator-issued
  replacement to set a password; names alone are not account-recovery proof.
- Workspace-password target limiting now isolates the cross-IP ceiling by
  authenticated account and canonical workspace while retaining IP+workspace
  and IP-wide abuse controls.
- Read-only player detail fetches: starting inventory/spell repair now lives
  behind an explicit repair endpoint instead of writing during `GET`.
- Session start idempotency validation rejects overlong client keys instead of
  truncating them.
- Deploy bootstrap refuses production server startup through Werkzeug and
  requires database-backed rate limiting and turn coordination in production.
- Remaining App dialog surfaces are componentized: archive/restore managers,
  campaign chooser, player edit/delete, world manager/delete, and
  create-campaign dialogs now live outside `aidm_frontend/src/App.tsx`.
- Extracted dialogs use a shared modal shell plus the common focus-trap hook, so
  focus placement, Escape handling, Tab loops, and focus return are maintained in
  one frontend path.
- Campaign, session, and player archive/delete lifecycle orchestration lives in
  service modules with focused data-integrity tests.
- Modal accessibility regressions cover focus placement, Escape close, focus
  trapping, danger confirmation cancellation, and dialog descriptions.
- Production bootstrap now requires declared observability ownership
  (`AIDM_OBSERVABILITY_PROVIDER`, `AIDM_ALERT_OWNER`) and the supported
  `AIDM_SOCKETIO_WORKER_MODEL=single` topology.
- Multi-worker Socket.IO is deferred until presence/music state is shared and
  both affinity and shared-queue delivery are proven under the real topology.
- Hosted same-origin account auth can use server-issued `HttpOnly` account
  cookies, suppress raw account tokens in JSON responses, and enforce a
  companion CSRF token on unsafe cookie-authenticated REST requests.
- Player dice are generated on the server, derived from persisted character and
  pending-roll state, committed as durable turn evidence, and then broadcast for
  client presentation. Client roll totals and natural-language claims are not
  mechanical authority.
- Server-issued `roll_required` events now carry viewer-safe roll guidance and
  remaining-player state. The composer opens the authoritative check in one
  action, removes the rejected optimistic entry, and preserves the player's
  draft until the roll resolves.
- The combat HUD keeps unavailable known targets visible as disabled options
  with server-issued legality reasons instead of presenting only successful
  choices.
- Uncertain frontend turn retries reuse the original idempotency key, duplicate
  acknowledgments reconcile cleanly, and both automatic and manual reconnects
  reload the persisted session snapshot.
- A turn interrupted after its incoming commit can resume from persisted
  pre-DM pipeline state without rerolling or replaying the incoming mutation.
- Non-admin session projections preserve the requester's complete character but
  redact peer character sheets, inventory, spells, resources, statistics, and
  other private mechanics while retaining bounded party combat status.
- Raw segment reads are DM-only; player campaign workspaces and socket events
  expose revealed story content without trigger conditions or recipes.
- Authored maps persist explicit player/DM visibility. Player REST, workspace,
  browser export, and inspector projections fail closed on DM-only or stale
  cached rows, while admins can reveal or hide maps without rewriting content.
- Frontend campaign/session lifecycle, campaign-pack progress, director,
  authoring, and admin-composer mutations are shown only to actors with operator
  capability. Compatibility memory snippets are labeled "Recent Memory" rather
  than durable canon.
- Archived/deleted sessions and campaigns reject live joins and mutations;
  lifecycle commits are fenced against active turns. Hidden campaign-pack
  checkpoint IDs stay out of all player progress projections unless the author
  supplies an explicit player-safe alias.
- A double failure in post-DM state application now retains narration for audit
  but marks mechanics unapplied, fails the turn, and blocks structured turn
  advancement and canon enqueue.
- Candidate release-checklist output now identifies current, stale, dirty,
  unsigned, or unavailable RC evidence instead of allowing old local proof to
  read as current.
- Release archive inspection rejects unresolved Git LFS pointers, and hosted
  beta-SLO evidence must contain positive DM/provider samples plus an explicit
  tester-expansion decision before the checklist can pass it.

## Beta Hardening

- Keep account recovery explicit. If a legacy account has no password and the
  saved account token is gone, verify the requester out of band and issue a
  replacement with `scripts/issue_legacy_recovery_code.py`. Never treat names
  alone as recovery authority or send the raw code through issue trackers/logs.
- Keep mutating repair behavior behind explicit commands or POST endpoints.
  Avoid hidden writes in diagnostics, browser refreshes, and smoke tests.
- Keep CI drift checks active: generated API types, backend tests, frontend
  tests/build, browser smoke, bundle budget, secret scan, Python audit, and
  focused Ruff correctness lint.
- Tester bad-turn reports now persist provider/model snapshots and feed the
  operator-only beta incident endpoint and inspector Ops tab alongside failed
  turns and canon jobs.
- Operator audit APIs now expose recent session-state mutation diffs and
  operator authoring actions for workspace admins. Equipment, combat, and
  campaign-pack progress writes produce durable mutation audit rows; bestiary
  create/generate/evolve-save, campaign/session archive/restore/delete,
  session import, and campaign-pack import writes produce operator-action audit
  rows.
- Campaign-pack progress service entrypoints now serialize through the same
  reentrant per-session turn coordinator used by active turn processing.
- The frontend shows a safe-mode banner when the deterministic fallback provider
  is active so playtesters know a live LLM is not serving turns.
- Deterministic scenario regressions now cover opening narration, impossible
  action boundaries, combat roll prompts, item use, checkpoint triggers, active
  NPC continuity, and durable canon recall with provider/model recorded per
  scenario.
- Run production bootstrap in `--check-only` mode before deployment, then start
  the app with a real Socket.IO-capable production server.
- Hosted closed-beta deployment readiness has an executable gate:
  `scripts/deployment_readiness_check.py` validates production env choices,
  required security/auth/observability settings, optional live health/metrics
  endpoints, required security headers, and a forced WebSocket upgrade through
  the deployed edge. Unsupported multi-worker models fail this gate.
- The local Prometheus/Grafana observability bundle has an executable validator
  (`scripts/check_observability_bundle.py`) that checks required files,
  dashboard metrics, provisioning paths, and optionally `docker compose config`
  where Docker is available.
- Local/private SQLite beta data now has an executable backup/restore drill
  (`scripts/backup_restore_drill.py`, `make backup-restore-drill`) that creates
  a backup and verifies a restored copy without mutating the source database.
- Hosted PostgreSQL data has a separate guarded custom-archive drill
  (`scripts/postgres_backup_restore_drill.py`,
  `make postgres-backup-restore-drill`) that requires a distinct, explicitly
  supplied empty target and compares restored schema/data evidence.
- The Codex CLI provider uses isolated, tool-free execution, dedicated saved
  authentication or an access token, bounded timeouts, validated event order,
  and deployment-readiness checks.
- Fixed-input model evaluation is available through
  `scripts/compare_helper_profiles.py`,
  `scripts/compare_tactics_compilers.py`, and
  `scripts/evaluate_combat_helpers.py`; these tools compare helper/compiler
  profiles without changing runtime defaults.

## Deployment Actions

- Set the hosted `AIDM_OBSERVABILITY_PROVIDER` and `AIDM_ALERT_OWNER` values in
  the target environment, then run the deployment-readiness gate and prove
  metrics/alert ingestion in staging.
- For hosted RC1, use the `single` worker-model decision in
  `docs/socketio_worker_model.md`, then attach hosted process evidence showing
  exactly one backend process/replica. Do not override this decision until the
  shared-state and multi-worker work recorded in that decision document lands.
- Enable `AIDM_ACCOUNT_COOKIE_AUTH_ENABLED=true` and
  `AIDM_ACCOUNT_TOKEN_RESPONSE_ENABLED=false` for hosted same-origin cookie-only
  auth when the deployment threat model calls for it.
- For hosted databases, document and rehearse the provider-specific
  snapshot/PITR path in addition to the guarded PostgreSQL custom-archive
  drill. A local or staging restore comparison does not replace provider-level
  production recovery proof.
- Before changing helper or compiler model defaults, run the fixed-input
  comparison tools, preserve their JSON evidence, and review quality, latency,
  parse failures, and cost outside this roadmap.

## Remaining After the Story Overhaul

Known open items and decisions, in no particular order:

- **Chapter carry-over bug.** Starting the next chapter does not carry the day
  clock or the campaign pack's merchants: the new chapter starts on day 1 in the
  morning and the pack's shops stop trading. Add `clock` and `merchants` to the
  carried snapshot keys in `services/session_chapters.py`, with a test.
- **State-change ledger growth.** `stateChangeLedger` grows without bound and
  needs compaction; per-turn snapshot copies are already pruned.
- **Multi-worker sockets.** Hosting stays single-worker until presence and
  music state are shared (see `docs/socketio_worker_model.md`).
- **Scene music.** One long track carries every scene tag, so the music never
  changes with the scene.
- **Live AI provider latency.** The first live session (DeepSeek, commit
  `a88f45f`) measured about 8–18 s a turn on DeepSeek V4 Pro and 5–7 s on
  DeepSeek V4 Flash, so V4 Flash is the faster choice for play. The other
  providers are still unmeasured: record time to first text with each before
  widening the beta. The defects that session found (the narrator resolving
  searches, travel and gains only the engine may resolve) are addressed by the
  engine brief, the narration hygiene pass and sentence-level mapping (see
  `docs/gameplay_systems.md`, "How a Player Acts") and await a live re-test.
- **Narrator voice (OpenAI Realtime).** Built and tested against fakes; not yet
  heard live in the app. `AIDM_TTS_PROVIDER` chooses OpenAI or Deepgram (unset:
  OpenAI when its key is set, else Deepgram). OpenAI's older speech models are
  removed on 6 January 2027, so OpenAI narrates through `gpt-realtime-2.1`
  (**Best**, about 6 cents for a ~40-second narrated turn at list prices) or
  `gpt-realtime-2.1-mini` (**Faster**, under 2 cents), with voice, accent and
  quality chosen per browser. Keys live only in the server environment.
- **Guest hero picker.** Signed-out guests on the hosted beta do not get the
  ready-made hero choice, because the hero list requires sign-in; making it
  public means adding it to the public-endpoint inventory. Decision pending.
- **Engine-controlled allies** (party NPCs in a fight) only pass their turn;
  the engine plans turns for enemies alone.
- **Classes beyond the twelve core classes**, subclasses other than Champion and
  Hunter, and features above level 5 have no mechanics yet.
- **Middle-earth example packs.** *Shadow over the Greenway* and *Shadow Under
  Eryn Luin* describe themselves as Middle-earth campaigns and use Tolkien's
  places and peoples; they need an IP review before a public host offers them.
- A new adventure from a bundled pack goes straight to hero creation; the
  ready-made heroes are offered only by the title's Play now.

## Not Now

- Grid, pathfinding and line-of-sight rules remain a separate gameplay project;
  combat uses coarse range bands.
- A richer campaign resume dashboard remains a high-value UX follow-up.
- Class-derived and migrated legacy weapon proficiencies are now persisted in a
  private server-owned player profile. Operator UI and rules support for custom
  proficiencies acquired later from ancestry, feats, training, or magic remain
  future work.
- Authored map visibility is currently whole-map `player` or `dm` visibility;
  per-layer and per-token reveal controls remain future work.
- Do not replace the backend-owned TypeScript contract with OpenAPI yet. The
  current contract generator is low-friction; the immediate win is drift
  checking and response tests.
- Before adding more gameplay surface, settle the open items above and measure
  live provider play.
