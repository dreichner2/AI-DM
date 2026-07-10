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
| `fallback` | `deterministic-v1` | No external credential | Intended for deterministic local/test behavior, not model-equivalent narration |

`AIDM_LLM_MODEL` selects a non-default model. Model identifiers in the catalog
describe current application configuration, not a guarantee that an external
account can access the model indefinitely. Verify provider availability before a
release or benchmark.

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
calls intentionally use the designated home so refreshed authentication can
persist, while ignore-user-config and explicit overrides prevent that home from
changing model behavior. Access to saved auth is serialized to avoid concurrent
CLI sessions corrupting it. Structured CLI events are parsed fail closed:
unexpected tool activity or malformed terminal output is an error.

The provider advertises streaming compatibility to the turn engine, but it is
not progressive token streaming: narration is yielded only after the CLI call
finishes successfully.

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
