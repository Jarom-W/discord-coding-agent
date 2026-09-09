# Release notes

## 0.1.1 — 2026-09-09

Removed the default one-hour full-task deadline. `timeouts.task = 0` now means no full-task timer; explicit positive existing settings remain effective. Added `!run 30m task text` (positive `s`/`m`/`h` durations) and `!run unlimited task text` to override the default for one task in the selected conversation. Active task limits cannot be changed by another message. Acceptance, status and interrupted-task metadata report the chosen limit.

Approval requirements, human-input expiry, initialization/RPC/transport/delivery/shutdown limits and `!stop` are retained. Natural-language constraints are forwarded unchanged; use the explicit command for a bridge-enforced timer. A completed/failed turn is never automatically continued just because its deadline is disabled. No account usage limits are removed.

Upgrade/reinstall, change an explicit `[timeouts] task = 3600` to `task = 0` if desired, then restart while idle; see [update/rollback instructions](docs/service.md#removing-the-old-one-hour-limit-011). State schema remains 1, with optional task-limit metadata inside existing active/interrupted records. Before rollback to 0.1.0, replace `task = 0` with a positive value or remove the key. No personal service/configuration is modified by this release.

Regression coverage includes a simulated scheduler advance beyond one hour, timed interruption during human wait, unlimited-task approval/stop behavior, duration validation, busy/deduplicated/unauthorized requests, and per-task override isolation. See [validation evidence](docs/compatibility.md).

## 0.1.0 — 2026-09-09

Initial community release: owner-only Discord Gateway bridge to the real Codex 0.153.4 app-server; persistent conversations; explicit manual/automatic review; complete approval details and guarded buttons; question answers; atomic task reservation and deduplication; bounded interruption; saved results and independent outbound delivery; private state/lock; setup/run/doctor/systemd CLI; standalone onboarding and recovery guides.

Includes strict generated protocol fixtures, actual non-model Codex process tests, Python 3.11–3.14 CI, packaging, lint/typing, and service-unit validation. The existing public `discord-coding-agent` repository and history are retained rather than creating/renaming to `codex-discord-pi`.

Compatibility is deliberately restricted to Codex CLI 0.153.4. Live Discord/model interactions, automatic-review tool behavior and physical ARM64/Pi operation are unverified in this environment; see [the compatibility record](docs/compatibility.md). This release does not import prototype state or touch personal TARS installations.

State schema: 1. No previous published bridge schema exists. Future migrations must preserve the source state before changes. Unknown schemas currently fail with a backup; corruption/repository mismatch fail without silently replacing state. Updates preserve private configuration and state; follow [upgrade and rollback instructions](docs/service.md).
