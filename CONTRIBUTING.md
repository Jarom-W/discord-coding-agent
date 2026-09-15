# Contributing

Keep the bridge understandable for one maintainer. Start with a small issue/PR describing the concrete problem, expected behavior and sanitized reproduction. Preserve existing repository history; never force-push main. Do not modify a personal TARS installation to test a contribution.

## Development — on a Linux development host

Use Python 3.11–3.14 and [uv](https://docs.astral.sh/uv/) 0.12.10 (the version used to generate this lock). Install uv through its documented method for your host. No production bot token or Codex auth is required for the default suite.

```bash
git clone https://github.com/Jarom-W/discord-coding-agent.git
cd discord-coding-agent
uv sync --frozen --python 3.13
uv run --frozen ruff check .
uv run --frozen ruff format --check .
uv run --frozen mypy
uv run --frozen pytest -q
uv build
```

`uv.lock` pins runtime and development dependencies. `requirements.lock` is the hash-pinned runtime export used by the README's pip installation. To deliberately update dependencies: `uv lock`, review the diff, then `uv export --frozen --no-dev --no-emit-project -o requirements.lock`. Run the supported Python matrix and packaging checks. Do not change locks casually as part of unrelated fixes.

Tests cover authorization, deduplication/exclusivity, mode/session persistence, simultaneous decisions, stale controls, questions, reviewer verification, realistic enum validation, RPC correlation/disconnects, output bounds, cancellation/deadlines, state damage, service escaping and cosmetic HTTP isolation. See [compatibility](docs/compatibility.md) for actual validation scope. Default tests use synthetic transports; they are not live Discord compatibility evidence.

Workspace tests use temporary Git repositories and exercise named sessions, legacy-state retention, channel isolation, symlink roots, shared task/maintenance leases, failed catalog writes and restart continuity. Deployment tests cover exact main/push/workflow/repository CI gating, newer failed reruns, preparation failure, idle deferral, readiness identity, failed startup, crash recovery, rollback, retention and unit syntax. They stub GitHub/service lifecycle operations and never adopt a production bot. A separate read-only public GitHub API probe is documented in the compatibility record; it is not a live deployment test.

## Codex protocol checks — explicit opt-in

When the exact installed CLI is available:

```bash
DCA_TEST_CODEX=1 uv run --frozen pytest -q tests/test_codex_installed.py
```

This runs non-model initialize/config/create/resume checks in a temporary unauthenticated Codex home. It appends a harmless history fixture with `thread/inject_items` to create a persisted rollout, then checks resume after child restart. It never sends `turn/start`. Codex may fetch service metadata, so network availability can affect it. Do not run these checks against production state.

Protocol fixture provenance and hashes are checked in under `tests/fixtures/codex-0.153.4`. Generate candidate schemas with:

```bash
codex --version
codex app-server generate-json-schema --experimental --out /tmp/codex-schema-candidate
```

Compare the required methods and enums. Add an explicit version adapter and strict fixtures before expanding compatibility. `on-request`, `workspace-write` and structured `workspaceWrite` are distinct values. Do not normalize them globally or make a fake server accept everything. Keep raw reasoning/auth/production content out of fixtures.

## Live checks — separate explicit opt-in

Follow [the walkthrough](docs/walkthrough.md) for live Discord/buttons/phone/hardware tests. Configure your own private bot locally; CI never uses it. The automated model smoke test requires your own Codex authentication and may incur usage:

```bash
DCA_TEST_LIVE_MODEL=1 DCA_TEST_LIVE_REPO="$HOME/work/codex-bridge-demo" uv run --frozen pytest -q tests/test_live_model.py
```

The target must be an intentionally disposable Git repository with a `.dca-disposable` marker. This test checks a read-only request and a subsequent conversation turn without Discord; it does not exercise live mid-turn steering. Use the disposable [follow-up walkthrough](docs/chat.md) for manual acceptance. Do not claim it tests the live Gateway or physical Pi. Never put production credentials in GitHub Actions secrets merely to run this project's CI.

## PR/release review

Keep the README and setup guides focused on the current release’s commands and behavior. Put historical changes and version-specific migration/rollback notes in `CHANGELOG.md`. Retain exact dependency and protocol versions where needed for compatibility.

Leave PRs open for review. Do not merge or enable auto-merge unless the maintainer explicitly requests it: merges into `main` can deploy automatically and restart TARS.

Describe the trigger and changed behavior, tests actually run and remaining uncertainty. Run lint/format, strict source typing, tests and wheel/sdist builds. Review diff/staged files for secrets. Check every changed setup command against the CLI. Update release/compatibility notes with OS/architecture and actual outcomes; an x86 test is not ARM64 hardware validation. CI action references are full verified commit pins with read-only workflow permissions. GitHub PRs are the normal review path for contributors; no forced history replacement.
