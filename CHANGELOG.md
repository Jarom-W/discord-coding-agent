# Release notes

## 0.1.0 — 2026-09-09

Initial community release: owner-only Discord Gateway bridge to the real Codex 0.153.4 app-server; persistent conversations; explicit manual/automatic review; complete approval details and guarded buttons; question answers; atomic task reservation and deduplication; bounded interruption; saved results and independent outbound delivery; private state/lock; setup/run/doctor/systemd CLI; standalone onboarding and recovery guides.

Includes strict generated protocol fixtures, actual non-model Codex process tests, Python 3.11–3.14 CI, packaging, lint/typing, and service-unit validation. The existing public `discord-coding-agent` repository and history are retained rather than creating/renaming to `codex-discord-pi`.

Compatibility is deliberately restricted to Codex CLI 0.153.4. Live Discord/model interactions, automatic-review tool behavior and physical ARM64/Pi operation are unverified in this environment; see [the compatibility record](docs/compatibility.md). This release does not import prototype state or touch personal TARS installations.

State schema: 1. No previous published bridge schema exists. Future migrations must preserve the source state before changes. Unknown schemas currently fail with a backup; corruption/repository mismatch fail without silently replacing state. Updates preserve private configuration and state; follow [upgrade and rollback instructions](docs/service.md).
