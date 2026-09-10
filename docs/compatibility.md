# Compatibility and validation record

Current release **0.2.4**, checked **2026-09-10 UTC**: **200 passed, 3 opt-in skips on each** of Python 3.11.16, 3.12.14, 3.13.15 and 3.14.7 on Linux x86-64. Lint/format, strict typing, wheel/sdist build, runtime-lock export comparison and isolated wheel installation passed. A disposable prepared-release layout also verified that the installed wheel identifies its own revision and inline reply policy. Tests cover saved-result recovery as complete inline pages after restart, version/status replies in bound and unbound channels, and saved deployment history being insufficient when the running package differs or has no identity metadata. These are local tests; no particular Pi’s attachment origin was established.

Initialization regression tests exercise the real bridge/RPC timers with schema-validated gated responses: preparation survives the ordinary 45-second cutoff, stages share the 120-second default budget, explicit task deadlines and stop still work, and later turn acknowledgements retain the ordinary timeout. Tests advance the scheduler clock; they do not simulate a live Pi’s underlying startup delay.

The installed **Codex CLI 0.153.4** passed **2 non-model checks** in isolated temporary state on 2026-09-10: manual/auto configuration, thread creation and resume after process restart. All 13 fixture hashes also matched freshly generated schemas. These checks require metadata-host network access; they never submit a model turn. No live Discord/mobile/Pi session was tested or modified, and no particular host’s resume delay was diagnosed from its logs. A checked configuration setting is not a guarantee of every tool, account or workload’s behavior.

| Component | Supported / checked baseline | Evidence and limits |
| --- | --- | --- |
| Python | `>=3.11,<3.15` | Default suite passed on all four supported minor versions as recorded above. GitHub Actions runs the same four-minor-version matrix, including lint, typing, tests, build, lock-export verification and wheel installation. |
| discord.py | 2.7.1 in `uv.lock` / runtime export | Adapter/unit tests; real Gateway, token, live button interaction and phone conversation were not exercised in the implementation environment. Package metadata permits compatible 2.x; reproducible installs use the lock. |
| Codex CLI | **0.153.4 only** | Inspected installed executable/help and generated experimental JSON schemas. Exact version is checked before work; newer/older versions fail explicitly. No automatic CLI upgrade. |
| Codex protocol | v2 methods in that CLI | Actual unauthenticated temporary-process checks: initialize, initialized, config/read, thread/start, history fixture injection and thread/resume after process restart in **manual and auto** modes. **2 passed**; no model turn in these checks. The protocol adapter baseline remains 0.153.4. |
| Approval routing | `user` / `auto_review`, `on-request` | Process config and effective thread response verified for both modes. No real model/tool escalation review or account eligibility test was performed. Runtime rejection/unavailability remain possible. |
| Sandbox | `workspace-write` thread mode; `workspaceWrite` structured policy | Schema tests reject incorrect variants; effective real thread response checked. Managed policy is retained. |
| Linux x86-64 | Development host | Unit/integration, strict typing, lint, wheel/sdist build, clean wheel installation and CLI smoke checks. |
| Debian / Raspberry Pi OS ARM64 | Target platform, 64-bit, Pi 4/4 GB baseline | **Not physically verified on ARM64/Pi hardware here.** No x86 test is represented as hardware validation. Large builds remain resource-limited. |
| systemd | User unit with standard directives | Generated units verified with systemd-analyze 261, including unusual path characters. No user bus was available for service launch, logout, linger or reboot tests; syntax verification used the offline fallback. CI verifies generated units on Ubuntu. |
| Channel workspaces | One owner/server, up to eight channels and 64 saved sessions | Temporary Git repositories, catalog/restart continuity, names/modes, cross-channel exclusivity, root/symlink checks, stale controls and failed persistence are covered. No live multi-channel Discord test was performed here. |
| Pull deployment | Public GitHub `main`, exact successful `ci.yml` push run | Gate/failure/rollback/crash/lease/retention tests use simulated GitHub and service operations. A read-only GitHub API probe on 2026-09-09 found main with successful matching push CI. No live bot deployment/restart or ARM64 dependency installation is claimed. |

See [CONTRIBUTING.md](../CONTRIBUTING.md) for exact test commands and [the walkthrough](walkthrough.md) for opt-in live acceptance. Automated Codex tests use a private temporary Codex home and no production authentication; the separate model test requires explicit flags and a disposable repo. CI does not install Codex or use credentials.

The deadline regression test advances the asyncio scheduler clock beyond one hour and confirms an unlimited task still completes normally. Other tests check explicit caps, approval wait, interruption, request isolation and retained initialization limits. This is deterministic local test evidence, not an hour-long live Discord/model uptime test. No personal TARS service was changed to validate it.

## Protocol evidence

`tests/fixtures/codex-0.153.4/manifest.json` records the generation command, CLI version and SHA-256 of each selected original schema file. The strict subprocess fixture validates requests against these schemas; it does not accept arbitrary enum spellings. Actual-process tests independently check the installed adapter. Generated schemas retain their upstream Apache-2.0 license; bridge code is MIT.

The important distinctions are:

| Field | Checked wire value |
| --- | --- |
| Thread `approvalPolicy` | `on-request` |
| Thread `sandbox` | `workspace-write` |
| Structured `sandboxPolicy.type` / returned `sandbox.type` | `workspaceWrite` |
| Process `approvals_reviewer` | `user` or `auto_review` |
| Thread `approvalsReviewer` | `user` or `auto_review` |

The bridge opts into experimental app-server capabilities for question/reviewer fields. It normally inherits the verified effective thread sandbox on `turn/start` rather than replacing it with a separately constructed per-turn policy. Website documentation is useful background; the release-specific schemas and real-process tests are the compatibility authority for this adapter.

A new thread without any history has no resumable rollout in this CLI. The non-model test therefore appends one harmless history item before restarting the child; it does not fake a model completion. A failed/uncertain real task is never automatically recreated just because resume fails.

## Known limitations

- One configured owner/server; up to eight normal text channels and 64 saved sessions, with one coding task across all channels. No multi-owner administration, task queue, autonomous coding schedule, voice, uploads, web dashboard or streaming token UI. The opt-in updater timer only checks/deploys bridge releases.
- Unsupported server requests (including legacy approval variants, MCP elicitation forms and dynamic tool calls) get explicit errors. Secret questions are rejected. Standard command/file/permission approvals and `requestUserInput` questions are supported. Some external tool workflows must be handled locally.
- No account/plan/price/usage guarantee. Inference requires internet/auth; gateway availability is independent of Codex service availability.
- Results and approval detail memory are bounded. Requests/results exceeding safety bounds fail clearly and require local inspection. `!last` retrieves the selected session's most recent completed result, not an unlimited transcript archive.
- All replies use chat pages. Very large results take longer to deliver under Discord rate limits; status/controls can interrupt them between pages. Reconciliation scans the most recent 100 channel messages for matching bot-authored markers. Duplicate delivery remains possible outside that window; coding work is never replayed for delivery recovery.
- A process-per-task design adds initialization latency but isolates child failure; conversation continuity is via persistent Codex threads. The selected reviewer is reapplied on every create/resume. Verification shown after a task refers to that task's now-closed child.
- A copied/recreated/moved Git directory requires restoring its identity or an explicit new conversation with `!repo --fresh PATH`. No prototype-state importer, automatic import of unrelated Codex threads, session deletion or guessed schema migration is implemented.
- Auto-deployment supports public GitHub sources and cooperating bridge protocol 1 only. It waits indefinitely for idle work, does not upgrade Codex or its own bootstrap venv, and requires a real systemd user session. CI success and Gateway readiness do not prove runtime model/tool behavior. Private/Enterprise deployment sources are not implemented.
- No physical Pi, real bot token, real model turn, runtime automatic-review decision, phone/laptop-disconnect or reboot result is claimed in this release record.
