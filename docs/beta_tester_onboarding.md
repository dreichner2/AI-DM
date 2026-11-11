# Closed Beta Tester Onboarding

Use this guide when inviting a controlled tester group. Keep the group small
until the current RC gates, hosted readiness proof, and backup/restore proof are
all attached to the release evidence.

## Before Inviting Testers

- Run `make closed-beta-rc` and attach `tmp/release/rc-evidence.md` to the RC
  issue or release notes.
- Run the hosted deployment-readiness command against the actual target URL.
- Run a backup/restore drill for the target database or document the managed
  provider restore proof.
- Confirm hosted auth mode from `docs/auth_modes.md`.
- Confirm `AIDM_ALERT_OWNER` and `AIDM_OBSERVABILITY_PROVIDER` are set.
- Confirm `/api/health` reports the intended environment and a configured
  provider/model. Deterministic fallback must be an explicit safe-mode choice,
  not an unnoticed hosted default.
- With operator authentication, confirm `/api/metrics` and
  `/api/metrics/prometheus` respond on the target environment.
- When the backend serves the frontend, confirm `/` and one built `/assets/*`
  file load through the deployed edge; the API deployment-readiness probe does
  not validate the SPA build.

## Tester Invite Template

Send testers:

- Beta URL.
- Account/table setup instructions.
- Known limitations link.
- What data may be logged: account name, workspace/table ID, campaign/session
  IDs, player ID, turn IDs, provider/model, latency, bad-turn reports, state
  mutation/audit references, and operational errors.
- Support request format:
  - What happened:
  - What you expected:
  - Campaign/session/player:
  - Approximate time:
  - Turn text or screenshot if safe to share:
  - Did you press a bad-turn/feedback button:

## First Session Walkthrough (Story View)

Send testers this walkthrough with the invite.

1. **Title screen.** Sign in, then pick one of the four ready-made heroes under
   *The Road of Unremembered Kings* and press **Play now as …**. Returning
   testers see **Continue your adventure** with where they left off; **Play a
   ready-made adventure** starts the adventure again as a new campaign, and
   **New adventure** starts another bundled adventure, or a blank page when an AI
   narrator is connected, with a hero made in the guided creator.
2. **The opening.** The Story view opens on the authored scene: an empty caravan
   at a roadside inn at dusk. The narration is the story; the quiet lines under
   it say what changed (a clue, a check, damage, a new place, an item taken).
3. **Acting.** Tap a suggestion above the composer, open the action tray with
   the **Actions** button or `/` (spells, abilities, items, objects, rest), or
   type what you do. Words that name one thing ("read the ledger", "take the
   key", "go to the old north road") do exactly what the button does. A
   suggestion is always an action the rules allow; a refused action says why in
   one sentence.
4. **Checks.** Some actions roll a check on the server; the result line shows the
   total and success or failure. A failed search can be tried again by taking
   your time, which costs part of the day.
5. **Your character.** **Open character panel** (or `C`) opens the drawer:
   character, pack, magic, journal (`J`: quest, objectives, clues, people,
   places) and the map (`M`).
6. **Fights.** A fight starts on its own when the story calls for it. On your
   turn the combat actions show what each costs and why one is unavailable; end
   your turn when you are done. If every hero falls, the party wakes later with
   1 hit point and can try again; a lost fight never ends the story.
7. **Rest and trade.** Rest from the tray where it is safe; buy and sell with
   Mara at the inn and, later, Brokk at the mason's yard.
8. **The menu.** The adventure menu holds the next chapter, your other
   adventures, the Chronicle and save export, Share, text size, Settings
   (including which narrator runs the table) and Table tools.
9. **Feedback.** Rate a turn from the feed (**Rate this turn**) whenever the
   narrator contradicts the story, gets a rule wrong, or stalls.

If the title screen footnote reads "Scripted narrator — no AI provider is
connected", the table is in Safe Mode: the ready-made adventure plays fully, but
free-form actions that name nothing the game knows get only a short note.

## Tester Rules

- Do not enter private secrets, payment details, medical/legal/financial
  personal data, or real-world credentials into game text.
- Expect occasional degraded responses while provider/model settings are being
  validated.
- Report bad turns when the DM contradicts known state, applies rules
  incorrectly, loses continuity, or stalls.
- Use the Beta feedback prompt after meaningful DM responses. It records
  coherence, fun, and rules scores for the active turn.
- Keep sessions short enough that operators can review incidents after each run.
- Ask an operator before using the same account/table from multiple browsers.
- Dice animation displays the result already generated and recorded by the
  server; closing the animation does not cancel a submitted turn or change its
  result.
- If the connection drops before confirmation, wait for reconnection and use
  **Retry safely**. It reuses the original request identity so the turn or roll
  cannot be applied twice. Do not retype the action as a new turn unless the
  original draft has been deliberately cleared.
- Report a roll that changes after reconnect, a repeated narration/state change,
  or a retry that never reconciles. Include the approximate time and session,
  but never copy account tokens or private character data into the report.

## Known Limitations

- Hosted beta is not a public SaaS launch.
- Hosted beta is single-worker only. Sticky and message-queue worker models are
  unsupported for RC1 even with staging proof because presence and music state
  remain process-local.
- Cookie-only account auth is the hosted default. Domain, HTTPS, SameSite, and
  secure-cookie behavior must be re-proven for the signed-off target whenever
  its origin, proxy, cookie configuration, or release candidate changes; local
  request/socket smokes are not browser proof.
- The scripted narrator (Safe Mode) plays the ready-made adventure fully but does
  not improvise: free-form actions it cannot map to an action get a short note.
  Narration quality and latency with a live AI provider have not been measured
  yet.
- Signed-out guests do not get the ready-made hero choice on the title screen.
- TTS depends on provider configuration and browser autoplay behavior.
- The top runtime notice strip is reserved for current player-impacting
  provider outages. Optional narration availability appears with the TTS
  control, and the account menu contains a compact Beta information disclosure.

## Operator Follow-Up After Each Session

- Review `/api/beta/slo`.
- Review `/api/beta/incidents`.
- Review the selected-session Session Quality card in the operator Ops tab, or
  request `/api/beta/session-quality?session_id=<id>`, for provider/model,
  latency, failed turns, canon failures, reports, clarification, and audit counts.
- Export a session support bundle from the operator Ops tab, or request
  `/api/beta/support-bundle?session_id=<id>`, for sessions with reported issues.
  Review the export confirmation before continuing: bundles can contain session
  IDs, provider/model metadata, and audit references and must be handled as
  operator data.
  For hosted/operator automation, use `make export-support-bundle
  EXPORT_SUPPORT_BUNDLE_ARGS="--target-url <target-url> --auth-token <token>
  --workspace-id <workspace-id> --session-id <session-id>"`.
- Record unresolved issues in the beta incident log.
- Note whether the issue was provider behavior, rules/state extraction,
  persistence, Socket.IO delivery, auth/session, frontend UX, or tester setup.
