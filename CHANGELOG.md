# Release notes

## 0.2.3 — 2026-09-10

Fixed a premature timeout during `thread/start`, `thread/resume` and preparation-time `config/read`: they now use the initialization budget (120 seconds by default), with one outer deadline covering all preparation. They no longer inherit the ordinary 45-second RPC limit. Later `turn/start` acknowledgements still use `request`; explicit task deadlines and `!stop` remain effective during startup. No prompt is replayed and saved sessions/results are retained after failure.

Status and logs identify the preparation step, and doctor displays configured timeouts. Initialization deadline errors distinguish an unsubmitted prompt from an uncertain turn submission and point to the appropriate setting and diagnostics. This fixes the bridge’s early cutoff; it does not identify or guarantee a remedy for a particular host’s underlying Codex delay.

For affected releases through 0.2.2, increasing `initialization` alone does not remove the nested `request` cutoff. A temporary workaround is to set both values to 180 in the existing private `[timeouts]` table, then restart while idle; that also lengthens ordinary RPC waits. After installing this fix, `request = 45` can be restored independently. Do not add another `[timeouts]` table or disable transport/sandbox checks. See [startup troubleshooting](docs/troubleshooting.md#slow-conversation-startup).

No state/config schema, dependency, Codex compatibility baseline or updater protocol changes. See [validation scope](docs/compatibility.md).

## 0.2.2 — 2026-09-10

All bot replies now appear inline in Discord, including long coding results, approval details, directory/session listings and `!last`. Removed the text-file fallback and help-only page cap. Pagination preserves Unicode and source text, splits at line/word boundaries, and closes/reopens ordinary fenced code blocks across pages. Attach Files is no longer a required bot permission.

Large replies are paginated lazily within the existing result/queue bounds. Delivery yields between pages so approval controls and status replies can take priority; controls appear only after all request details arrive. Each page retains retry/reconciliation, and delivery failures leave the completed result available through `!last` without rerunning Codex.

README and setup guides describe current instructions; historical migration notes remain here. No config/state-schema, Codex baseline or updater-protocol change is needed. See [validation scope](docs/compatibility.md). PRs remain open until explicitly authorized for merge, since main deployments can restart the bot.

## 0.2.1 — 2026-09-10

`!help` now appears directly in Discord chat as ordered messages, including the channel workspace commands, so mobile users can read it without downloading a text file. Pagination keeps complete lines where possible, preserves Unicode punctuation, and leaves room for Discord delivery markers. Pages share one bounded delivery job with per-page retry/reconciliation and message deduplication.

Large results and approval details retain complete file delivery. Each text attachment now includes a UTF-8 signature so viewers can recognize em dashes, accented characters and other Unicode correctly; the size bound includes that signature. No prompt, repository content or saved result is rewritten. Existing attachments remain unchanged; send `!help` or `!last` again after deployment for the new format.

No configuration, state-schema, Codex baseline or updater-protocol change is required. Existing CD installations can deploy this release after main CI succeeds and coding work is idle. See [validation scope](docs/compatibility.md); mobile-client rendering is not claimed as a live automated test.

## 0.2.0 — 2026-09-09

Added channel workspaces and persistent named conversations: `!dirs`, `!repo [--fresh] PATH`, `!sessions`, `!session NAME`, `!name NAME`, and `!new [auto|manual] [NAME]`. One owner can bind additional private text channels in the configured server using the same bot. Each channel retains its selected repository/session across restarts, with session-labelled replies and per-session results/modes. `WORKSPACE_ROOTS` bounds directory selection; existing Git worktrees are required. Eight channels and 64 sessions are supported, with one coding task across the bot. Cross-channel `!stop` remains available; approvals remain scoped to their own channel/session/turn.

Added opt-in pull deployment through `deploy install/enable/disable/check/status/rollback/uninstall`. The Pi checks public GitHub CI for the exact main push commit, prepares a separate release/venv, waits for the shared activity lock, backs up state/config/unit and switches only its managed bot. Startup checks match Gateway readiness to the service PID/invocation; failures restore the previous unit and block repeated attempts at the failed revision. Interrupted switches have durable recovery metadata. Retention protects current/previous releases and registered coding repositories. No inbound endpoint, Actions runner on the Pi, credentials in CI, or automatic Codex CLI upgrade is used.

Original session state schema stays 1. A separate `workspaces.json` catalog indexes the original initial-channel state as `main`; other session states are stored under opaque IDs. Existing config/token/auth and the initial thread remain intact. Updater protocol 1 requires a manual bootstrap update to this release before auto-deployment can be enabled. See [workspace setup/recovery](docs/workspaces.md), [deployment setup/rollback](docs/deployment.md), and [validation scope](docs/compatibility.md). No personal TARS service was modified during implementation.

For a manual rollback to 0.1.x, preserve `workspaces.json` and all session directories, and remove the unsupported `WORKSPACE_ROOTS` key from a private backed-up config. The older release exposes only the initial channel’s original session. Returning to a release with workspace support restores access to the retained catalog; do not restore old state to replay interrupted work.

## 0.1.1 — 2026-09-09

Removed the default one-hour full-task deadline. `timeouts.task = 0` now means no full-task timer; explicit positive existing settings remain effective. Added `!run 30m task text` (positive `s`/`m`/`h` durations) and `!run unlimited task text` to override the default for one task in the selected conversation. Active task limits cannot be changed by another message. Acceptance, status and interrupted-task metadata report the chosen limit.

Approval requirements, human-input expiry, initialization/RPC/transport/delivery/shutdown limits and `!stop` are retained. Natural-language constraints are forwarded unchanged; use the explicit command for a bridge-enforced timer. A completed/failed turn is never automatically continued just because its deadline is disabled. No account usage limits are removed.

Upgrade/reinstall, change an explicit `[timeouts] task = 3600` to `task = 0` if desired, then restart while idle; see [update/rollback instructions](docs/service.md#task-limits-and-saved-conversations). State schema remains 1, with optional task-limit metadata inside existing active/interrupted records. Before rollback to 0.1.0, replace `task = 0` with a positive value or remove the key. No personal service/configuration is modified by this release.

Regression coverage includes a simulated scheduler advance beyond one hour, timed interruption during human wait, unlimited-task approval/stop behavior, duration validation, busy/deduplicated/unauthorized requests, and per-task override isolation. See [validation evidence](docs/compatibility.md).

## 0.1.0 — 2026-09-09

Initial community release: owner-only Discord Gateway bridge to the real Codex 0.153.4 app-server; persistent conversations; explicit manual/automatic review; complete approval details and guarded buttons; question answers; atomic task reservation and deduplication; bounded interruption; saved results and independent outbound delivery; private state/lock; setup/run/doctor/systemd CLI; standalone onboarding and recovery guides.

Includes strict generated protocol fixtures, actual non-model Codex process tests, Python 3.11–3.14 CI, packaging, lint/typing, and service-unit validation. The existing public `discord-coding-agent` repository and history are retained rather than creating/renaming to `codex-discord-pi`.

Compatibility is deliberately restricted to Codex CLI 0.153.4. Live Discord/model interactions, automatic-review tool behavior and physical ARM64/Pi operation are unverified in this environment; see [the compatibility record](docs/compatibility.md). This release does not import prototype state or touch personal TARS installations.

State schema: 1. No previous published bridge schema exists. Future migrations must preserve the source state before changes. Unknown schemas currently fail with a backup; corruption/repository mismatch fail without silently replacing state. Updates preserve private configuration and state; follow [upgrade and rollback instructions](docs/service.md).
