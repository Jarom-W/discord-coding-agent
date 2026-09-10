# Command and configuration reference

## Discord commands

Only the configured owner in the configured server can invoke commands. The initial text channel is configured locally; additional private text channels are bound with `!repo PATH`. Other users, servers, DMs and unsupported channel types are ignored. Ordinary text in unbound channels is ignored until an explicit setup command is sent. Prefix commands are interpreted by the bridge; `!run` submits only its trailing task text to Codex. Commands and duration suffixes are case-insensitive; mode names are lowercase. Session names are case-insensitive within a channel.

| Command | Effect |
| --- | --- |
| `!help` | Show commands and scope/limits in consecutive inline chat messages, readable on mobile without a download. |
| `!ping` | Receive/send connectivity check, no Codex or model call. |
| `!status` | Task phase, Gateway state, thread, selected mode, last verification, elapsed time, last observed event and age, pending IDs, interruption and delivery status. |
| `!run 30m task text` | Submit a task with a bridge-enforced deadline. Use a positive number followed by `s`, `m` or `h` (e.g. `90s`, `30m`, `1.5h`). Overrides the configured default for this task only. |
| `!run unlimited task text` | Submit a task with no full-task deadline, overriding the configured default for this task only. |
| `!dirs [PATH]` | List allowed workspace roots, or immediate directories under a root. No file contents/model call. |
| `!repo PATH` | Select a Git working-tree root for this channel, returning to its most recently selected saved session or creating one. Idle only. |
| `!repo --fresh PATH` | Deliberately create a new session for that path, including after repository identity changed; old history is preserved. Idle only. |
| `!sessions` | List this channel's saved names, repositories, modes and thread IDs. |
| `!session NAME` | Select a saved conversation and its repository/mode. Idle only. |
| `!name NAME` | Rename the selected session without changing its thread. Idle only. |
| `!new [NAME]` | Create a saved conversation in the selected repository, keeping the selected mode. Omitted name is generated. Old sessions remain selectable. Idle only. |
| `!new auto [NAME]` | New named conversation with Codex automatic review, verified before submission. Idle only. |
| `!new manual [NAME]` | New named conversation with human review of eligible requests. Idle only. |
| `!approvals` | Explain selected mode, sandbox/policy and what was verified. |
| `!approve ID` | Approve once; same validation/decision path as green button. |
| `!deny ID` | Decline (or cancel when that is the offered denial). Same path as red button. |
| `!answer ID text` | Answer a single question. |
| `!answer ID {"q1":["answer"],"q2":["answer"]}` | Answer multiple questions in one JSON object. Use the exact IDs in the supplied details; answer all questions. Each value is a nonempty list of nonempty strings. |
| `!stop` | Interrupt the active turn (including in another bound channel), then terminate/kill its bridge-owned process group within configured stages. |
| `!last` | Send the selected session's last completed saved result again. No model invocation. |

`ID` is the request ID displayed by this bridge, not a thread/item/message ID. Read the full details/attachments before deciding. Requests expire after `user_wait` seconds or when the turn ends, resolves, stops or the service restarts. Reconnects within the same running process retain still-pending requests. Stale controls remain unable to authorize anything even if Discord cannot remove their buttons immediately.

Help preserves Unicode punctuation, including em dashes, in normal Discord message text. Large coding results and approval details still use complete text attachments; each file includes a UTF-8 encoding signature for text viewers. When reading an attachment programmatically, use `utf-8-sig` to decode that signature. No repository text is rewritten to replace punctuation.

Ordinary messages submitted after completion resume the saved conversation. Requests arriving while busy are **not submitted**, not queued. The last 512 accepted/handled owner message IDs are deduplicated and persisted, including across process restart. Old deliveries outside that window are not guaranteed deduplicated. Attachments/stickers reject the entire incoming message; paths must be sent as ordinary text. Images, audio and uploaded files are not read.

Asking Codex in ordinary prose to start a new chat does not switch bridge routing. Use `!new`, `!session` or `!repo`. Repository/session/mode/name changes are rejected while any channel is working or maintenance owns the shared lock. See [workspace setup](workspaces.md) for naming, directory roots, additional channels and migration from 0.1.x.

`!run` uses the same conversation, authorization, deduplication and atomic reservation as ordinary text. It cannot modify the deadline of an active task: busy requests are rejected without submission. The next ordinary message uses the configured default again. `!status` and the acceptance message show the effective deadline (or `none (unlimited)`). The full-task timer includes preparation and human wait; expiry requests interruption and then bounded process cleanup, which can take additional shutdown time. Cancellation never undoes effects.

Natural-language constraints such as “only work for 30 minutes” reach Codex unchanged; the bridge does not parse or guarantee enforcement of arbitrary prose. Use `!run 30m …` for a hard timer, and include any other stopping conditions in the task text. A completed turn ends the task even with no deadline. The bridge does not repeatedly prompt the model to manufacture more work. Requests requiring oversight still wait for your decision under the separate `user_wait` policy.

## Local CLI — on the Pi

```text
discord-coding-agent [--config PATH] setup
discord-coding-agent [--config PATH] run [--connection-only]
discord-coding-agent [--config PATH] doctor [--probe] [--discord]
discord-coding-agent [--config PATH] service ACTION [--unit-name NAME]
discord-coding-agent [--config PATH] deploy install --repository OWNER/REPO [--directory PATH] [--unit-name NAME]
discord-coding-agent [--config PATH] deploy check [--retry]
discord-coding-agent [--config PATH] deploy {enable,disable,status,rollback,uninstall}
discord-coding-agent --version
```

`service` actions: `render`, `validate`, `install`, `start`, `stop`, `restart`, `enable`, `disable`, `status`, `uninstall`. Custom names must match `discord-coding-agent-INSTANCE.service`; the default is `discord-coding-agent.service`. Installation does not start or enable the unit. `render` prints no token. `validate` runs systemd-analyze without installing anything. `doctor --probe` starts its own diagnostic Codex process but no thread/model turn; `doctor --discord` uses REST without sending messages. Only connection-only `!ping` tests the full Gateway receive/send route.

`deploy` manages the opt-in CI-gated updater. Installation validates units but does not enable/start the timer. `check` can deploy a revision; `--retry` explicitly retries a failed one. Other commands read private `deployment.toml`. See [deployment settings, retention and rollback](deployment.md). The `{...}` notation above means choose one action, not literal braces.

## Config precedence and paths

Default config: `$XDG_CONFIG_HOME/discord-coding-agent/config.toml`, falling back to `~/.config/discord-coding-agent/config.toml`. Default state: `$XDG_STATE_HOME/discord-coding-agent`, falling back to `~/.local/state/discord-coding-agent`. Use absolute XDG/config/state paths. Files must be owned by the service user and private (600); state directory is 700. Configuration/state should be outside both repositories. No `.env` auto-loading, shell evaluation, or global Codex configuration rewrite occurs.

Top-level environment variables override corresponding TOML fields. Timeouts come from the TOML `[timeouts]` table. Unknown keys and invalid values fail startup. Required numeric IDs may be TOML integers or quoted decimal strings. Environment-only configuration is possible but avoid passing the token in shell history or service unit contents; setup's private TOML is preferred.

| Key | Required/default | Meaning |
| --- | --- | --- |
| `DISCORD_TOKEN` | Required | Bot token, hidden in setup/diagnostics; excluded from child environment. |
| `DISCORD_OWNER_ID` | Required | Authorized human user ID. |
| `DISCORD_GUILD_ID` | Required | One server ID. |
| `DISCORD_CHANNEL_ID` | Required | Initial normal text channel; remains the primary startup/readiness channel. Other channels use `!repo`. |
| `CODEX_REPO` | Required | Initial absolute Git working-tree root. Keep it accessible; use Discord workspace commands for later selections. |
| `WORKSPACE_ROOTS` | Initial repository's parent | TOML array of existing absolute project directories (or JSON array in the environment). Must include the initial repo. Browsing/selection resolves symlinks and stays within these roots. |
| `CODEX_EXECUTABLE` | `codex` | Executable path or PATH name. Exact 0.153.4 check before model work. |
| `DISPLAY_NAME` | `Coding Agent` | 1–32 printable characters for bridge greeting; configure Discord username in Developer Portal. |
| `APPROVAL_MODE` | `manual` | Initial mode only. Saved selected mode takes precedence after first run; use `!new auto/manual`. |
| `STATE_DIR` | Per-user state path | Change deliberately for a different repository/instance. |

Do not change the initial `CODEX_REPO` to switch ongoing work; use `!repo` in Discord. Repository identity remains attached to each saved session and is rechecked before coding. Moving/recreating the Git directory can invalidate it; a branch/commit change alone does not. `!repo --fresh PATH` deliberately starts independent history for a changed repository. Root restrictions govern selection, not confidential access by tools running under your Linux account.

## Timeout policy

All values are finite **seconds**. `task = 0` disables the full-task deadline; all other fields must be greater than zero. The defaults below match `Timeouts` in source:

| `[timeouts]` key | Default | Operation and outcome |
| --- | ---: | --- |
| `initialization` | 120 | Overall version/process/handshake/config/thread preparation. Failure interrupts reservation; no prompt replay. |
| `request` | 45 | One RPC response, including `turn/start` acknowledgement. A lost acknowledgement is uncertain work. Never resend automatically. |
| `transport` | 20 | Incomplete JSON frame after the first byte, or pipe write drain. There is **no idle stdout-read timer**. |
| `task` | 0 | No full-task deadline. A positive value sets the default complete task budget, including initialization and human input wait, starting when the task runner begins. `!run` overrides it for one task. |
| `user_wait` | 900 | Individual pending approval/question. Expiry sends an RPC error without approval and invalidates controls; Codex may continue/finish. |
| `delivery` | 30 | Individual Discord send/history/disable operation. Transient sends use at most three attempts with 1s/2s backoff and history reconciliation. |
| `shutdown` | 10 | Each interruption acknowledgement/wait/process-termination stage; service stop timeout is derived from these stages. |

A 45-second RPC timeout is separate from the optional full-task deadline. Errors name the operation, measured elapsed time, configured limit and diagnostic next step. Human wait counts against its own timeout and any enabled overall deadline. Disabling the task deadline does not disable approval checks, RPC/connection timeouts, `!stop`, Codex completion/failure, or account usage limits. Quiet logs alone never trigger cancellation. HTTP typing attempts have a cosmetic limit of at most 5 seconds and cannot cancel a task.

An explicit positive value in an existing config is preserved on upgrade. To lift a previous one-hour default, edit the existing `[timeouts]` table in your private config to `task = 0` and restart while idle. If no `[timeouts]` table exists, the new no-deadline default already applies after updating/restarting the installed package. See [the update guide](service.md#update-without-losing-credentials-or-conversation).
