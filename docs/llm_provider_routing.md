# LLM Provider Routing

AIDM has two related routing layers: the main provider used for DM narration and
task-specific helper providers used for race/creature generation and combat
planning. `aidm_server/provider_registry.py` defines the configured main catalog;
`aidm_server/llm_providers.py` implements both layers.

## Main Provider Catalog

`AIDM_LLM_PROVIDER` accepts only the following configured values. `codex` is not
a supported configuration alias; use `codex_cli`.

| Provider | Default configured model | Required credential or condition | Provider-specific settings |
| --- | --- | --- | --- |
| `deepseek` | `deepseek-v4-pro` | `AIDM_DEEPSEEK_API_KEY` or `DEEPSEEK_API_KEY` | `AIDM_DEEPSEEK_BASE_URL`, timeout, token, sampling, thinking, and reasoning settings |
| `codex_cli` | `gpt-5.6-sol-medium` | Configured Codex executable and usable access-token or saved CLI auth | `AIDM_CODEX_EXECUTABLE`, `AIDM_CODEX_HOME`, `AIDM_CODEX_ACCESS_TOKEN`, timeout, reasoning effort, and service tier |
| `gemini` | `models/gemini-3-flash-preview` | `GOOGLE_GENAI_API_KEY` | `AIDM_LLM_FALLBACK_MODELS` and shared generation settings |
| `nvidia` | `moonshotai/kimi-k2.5` | `AIDM_NVIDIA_API_KEY` or `NVIDIA_API_KEY` | `AIDM_NVIDIA_INVOKE_URL`, timeout, token, sampling, and thinking settings |
| `kimi` | `moonshotai/kimi-k2.5` | Same NVIDIA-compatible credential path | Same NVIDIA-compatible settings |
| `fallback` | `deterministic-v1` | No external credential | Safe Mode: the scripted narrator (`aidm_server/scripted_narrator.py`), not model-equivalent narration |

`AIDM_LLM_MODEL` selects a non-default model. Model identifiers in the catalog
describe current application configuration, not a guarantee that an external
account can access the model indefinitely. Verify provider availability before a
release or benchmark.

### DeepSeek narration defaults

DeepSeek narration runs without hidden reasoning by default so the first words
arrive quickly:

| Setting | Default | Notes |
| --- | --- | --- |
| `AIDM_DEEPSEEK_THINKING` | `false` | `true` enables DeepSeek thinking mode. |
| `AIDM_DEEPSEEK_MAX_TOKENS` | `2048` | With thinking enabled the effective limit is at least `8192`, because hidden reasoning shares the completion budget. |

Both settings are read from the environment, so existing overrides keep
working.

### Narration and background canon jobs

Canon extraction runs on the main provider in a background job. Narration on an
HTTP provider (`deepseek`, `gemini`, `nvidia`, `kimi`) never waits for a running
canon job; new canon jobs still wait until narration has finished. Narration on
`codex_cli` waits for a running canon job first, because saved-login Codex calls
are serialized process-wide.

### Safe Mode (`fallback`)

With `AIDM_LLM_PROVIDER=fallback` nothing calls a network or CLI model. The
scripted narrator narrates engine results (combat, spells, class capabilities,
travel, scene objects, items, rests, shops, rewards and resolved rolls) and
authored content (the opening scene, object `actionText`, an encounter's
read-aloud `setup` and adjustment lines, ending text) deterministically, in the
second person and without quoting the player's words back. It asks for a roll
only when the turn's rules hint requires one that has not been made yet, and
leaves objective progress, quest completion and XP to the turn's story beats. Every helper and task profile resolves to the deterministic/heuristic
path (`helper_provider_name` returns `fallback` whenever the main provider is),
and canon extraction applies the heuristic patch without a provider call.

The Story view's Settings → Narrator lists the scripted narrator and every
provider the server has a key for (`GET /api/llm/config`); switching narrator is
a workspace-administrator change (`PATCH /api/llm/config`), which on a local
no-auth run is the person at the keyboard. Keys are only ever read from the
server environment.

### Narration context

`aidm_server/llm_context.py` builds the narrator's CONTEXT JSON within a
30,000-character budget (`MAX_CONTEXT_CHARS`). When it is over, sections are
dropped in this order until it fits: `dormant_threads`, `emergent_memory.facts`
beyond 10, `live_world_state.recentKnownNpcs`, `live_world_state.recentLocations`
beyond 4, `campaign_pack_director.relevantRecords.enemies`, `recent_turns`
beyond the latest 5, and `campaign_pack_director.nextCheckpoints` beyond 2. Each
drop is recorded in the `llm.context.trimmed` telemetry event. The rolling
summary appears once (`session_memory.session_recap`), the RULES_HINT copy in the
prompt omits the state summary already in CONTEXT, and NPC `personality`,
`motivation`, `knows` and `secret` are marked `dmOnly`.

An opening turn (`message_kind: "opening"`) adds an `OPENING BRIEF` block built
from the opening scene, the present NPCs, the pack's GM-only starting brief and
the active checkpoint, and never asks for a roll.

### Helper calls during a turn

- The turn-conductor helper (table flow between several players) runs only when
  `AIDM_TURN_CONDUCTOR_HELPER_ENABLED` is true (the default), at least two
  players are connected to the session, and the main provider is not
  `fallback`; otherwise only the deterministic decision applies.
- The state-pipeline helpers (pre-narration action extraction and
  post-narration outcome extraction) run when
  `AIDM_STATE_PIPELINE_HELPER_ENABLED` is true (the default); the opening turn
  makes no conductor or pre-narration extraction call, and free-text words that
  clearly name one action are mapped by the server first. In Safe Mode both take
  their deterministic path.
- Both stay off inside the test suite (`AIDM_ENV=test`) unless a test opts in
  with `AIDM_TURN_CONDUCTOR_HELPER_IN_TESTS` or
  `AIDM_STATE_PIPELINE_HELPER_IN_TESTS`.

## Codex CLI Isolation

The Codex provider invokes the configured CLI in a disposable workspace. It
passes `--ignore-user-config` plus explicit behavior overrides, ignores user
project rules, supplies a constrained environment allowlist, disables model
tool/app/plugin/skill/MCP and multi-agent access, and denies host
shell/filesystem/network tool access. The repository is not the model
workspace.

Authentication is supplied from `AIDM_CODEX_ACCESS_TOKEN` or
`CODEX_ACCESS_TOKEN`, or from saved `auth.json` under `AIDM_CODEX_HOME` /
`CODEX_HOME`. Token-based calls receive a disposable `CODEX_HOME`. Saved-auth
completion calls use the designated home with `--ignore-user-config`. Streaming
calls copy only `auth.json` into a disposable home, then atomically persist a
valid rotated auth file after the process exits. This prevents app-server from
loading operator config while preserving refresh-token rotation. Saved-auth
access is serialized to avoid concurrent CLI sessions corrupting it. Structured
events are parsed fail closed: unexpected tool activity, malformed lifecycle
output, or a final response that disagrees with streamed text is an error.

DM narration uses Codex app-server's `item/agentMessage/delta` notifications for
progressive text delivery and treats the completed agent item as authoritative.
Current Codex models can omit `phase` on the player-facing item until completion;
the isolated DM adapter streams that legacy form under its return-only-in-world
prompt contract, while explicit commentary, reasoning, and tool items are never
forwarded as narration. A later phase mismatch fails the turn instead of being
silently reconciled.
If app-server is unavailable before the first delta, the provider falls back to
the isolated `codex exec --json` completion path without fabricating provider
deltas. A failure after partial output is surfaced instead of replaying a second
response over the partial text.

## Helper Tasks And Defaults

Built-in task routing currently selects these profiles:

| Helper task | Task prefix | Built-in profile |
| --- | --- | --- |
| `custom_race` | `AIDM_CUSTOM_RACE_HELPER` | `codex_56_sol_medium` |
| `creature_generation` | `AIDM_CREATURE_HELPER` | `codex_56_sol_medium` |
| `sentient_enemy_brain` | `AIDM_SENTIENT_ENEMY_BRAIN_HELPER` | `codex_56_sol_medium` |
| `enemy_tactics_planner` | `AIDM_ENEMY_TACTICS_PLANNER_HELPER` | `codex_56_sol_medium` |
| `enemy_tactics_compiler` | `AIDM_ENEMY_TACTICS_COMPILER_HELPER` | `fast` |
| `boss_tactics` | `AIDM_BOSS_TACTICS_HELPER` | `codex_56_sol_medium` |
| `boss_tactics_planner` | `AIDM_BOSS_TACTICS_PLANNER_HELPER` | `codex_56_sol_medium` |

Profile selection precedence is:

1. `AIDM_HELPER_PROFILE_<TASK>`;
2. `<TASK_PREFIX>_PROFILE`;
3. `AIDM_HELPER_PROFILE_DEFAULT`; then
4. the built-in task-to-profile map above.

For an individual setting, `<TASK_PREFIX>_<SETTING>` wins. An explicitly chosen
profile supplies its settings next, then missing values fall through to the
task defaults and finally global `AIDM_HELPER_<SETTING>` values. For a built-in
mapped profile, its provider/model replace the task defaults, while task
defaults retain priority for other settings and remaining profile values fill
gaps.

Current named profiles are:

- DeepSeek: `fast`, `deepseek_pro`;
- Codex baseline: `codex`, `codex_low`, `codex_medium`, `codex_high`,
  `codex_extra_high`; and
- Codex evaluation variants: `codex_56_sol_medium`, `codex_56_sol_high`,
  `codex_56_terra_medium`, `codex_56_terra_medium_fast`,
  `codex_56_terra_light_fast`, `codex_56_luna_medium`,
  `codex_56_luna_high`, and `codex_56_luna_high_fast`.

These names are implementation configuration, not a stable public API or a
claim that every referenced evaluation model is available. An unknown explicit
profile contributes no profile values; task-specific defaults and global
fallbacks still apply.

## Provider And Evaluation Tools

Run tools from the repository root with `.venv/bin/python`. The two comparison
scripts load runtime environment files unless `--no-env` is supplied;
`evaluate_combat_helpers.py` uses only the existing process environment.

- `scripts/check_llm_provider.py` makes one real request through the configured
  main provider when run without arguments. It can consume quota or incur cost;
  `--help` is side-effect-free and unknown arguments are rejected before the
  runtime environment is loaded or a provider is constructed.
- `scripts/list_gemini_models.py` calls the Gemini models API and requires
  `GOOGLE_GENAI_API_KEY` when run without arguments. Its `--help` and argument
  validation also complete before environment loading or Gemini API access.
- `scripts/compare_helper_profiles.py` can make many helper calls across profiles
  and fixtures. `--include-dm` adds narration calls; `--save-outputs` records raw
  prompts and provider output that can contain campaign data.
- `scripts/compare_tactics_compilers.py` makes live helper calls for each
  selected profile/case and can write raw output to its requested output file.
- `scripts/evaluate_combat_helpers.py` evaluates supplied snapshots and may call
  configured helpers when snapshot settings enable them; treat it as potentially
  live and billable.

Use ignored paths under `tmp/` for raw evaluation output, review it for secrets
or campaign content, and never commit provider credentials. See
[operator tools](operator_tools.md) for broader script safety boundaries.
