# Command and configuration reference

## Discord commands

Only the configured owner in the configured server/text channel can invoke these commands or decide controls. Bot messages and other channels/DMs are ignored. Prefix commands are interpreted by the bridge and never sent to Codex. Commands are case-insensitive; mode names are lowercase.

| Command | Effect |
| --- | --- |
| `!help` | Show commands and scope/limits. |
| `!ping` | Receive/send connectivity check, no Codex or model call. |
| `!status` | Task phase, Gateway state, thread, selected mode, last verification, elapsed time, last observed event and age, pending IDs, interruption and delivery status. |
| `!new` | Discard the active bridge thread selection and start fresh on the next ordinary message, keeping the selected mode. Old Codex history is not deleted. Idle only. |
| `!new auto` | Fresh conversation with Codex automatic review, verified before submission. Idle only. |
| `!new manual` | Fresh conversation with human review of eligible requests. Idle only. |
| `!approvals` | Explain selected mode, sandbox/policy and what was verified. |
| `!approve ID` | Approve once; same validation/decision path as green button. |
| `!deny ID` | Decline (or cancel when that is the offered denial). Same path as red button. |
| `!answer ID text` | Answer a single question. |
| `!answer ID {"q1":["answer"],"q2":["answer"]}` | Answer multiple questions in one JSON object. Use the exact IDs in the supplied details; answer all questions. Each value is a nonempty list of nonempty strings. |
| `!stop` | Interrupt the active turn, then terminate/kill the bridge-owned process group within configured stages. |
| `!last` | Send the last completed saved result again. No model invocation. |

`ID` is the request ID displayed by this bridge, not a thread/item/message ID. Read the full details/attachments before deciding. Requests expire after `user_wait` seconds or when the turn ends, resolves, stops or the service restarts. Reconnects within the same running process retain still-pending requests. Stale controls remain unable to authorize anything even if Discord cannot remove their buttons immediately.

Ordinary messages submitted after completion resume the saved conversation. Requests arriving while busy are **not submitted**, not queued. The last 512 accepted/handled owner message IDs are deduplicated and persisted, including across process restart. Old deliveries outside that window are not guaranteed deduplicated. Attachments/stickers reject the entire incoming message; paths must be sent as ordinary text. Images, audio and uploaded files are not read.

Asking Codex in ordinary prose to start a new chat does not switch the bridge session. Only `!new` does. The bridge cannot recognize every natural-language request for a new conversation and does not pretend the coding model changed its own routing.

## Local CLI — on the Pi

```text
discord-coding-agent [--config PATH] setup
discord-coding-agent [--config PATH] run [--connection-only]
discord-coding-agent [--config PATH] doctor [--probe] [--discord]
discord-coding-agent [--config PATH] service ACTION [--unit-name NAME]
discord-coding-agent --version
```

`service` actions: `render`, `validate`, `install`, `start`, `stop`, `restart`, `enable`, `disable`, `status`, `uninstall`. Custom names must match `discord-coding-agent-INSTANCE.service`; the default is `discord-coding-agent.service`. Installation does not start or enable the unit. `render` prints no token. `validate` runs systemd-analyze without installing anything. `doctor --probe` starts its own diagnostic Codex process but no thread/model turn; `doctor --discord` uses REST without sending messages. Only connection-only `!ping` tests the full Gateway receive/send route.

## Config precedence and paths

Default config: `$XDG_CONFIG_HOME/discord-coding-agent/config.toml`, falling back to `~/.config/discord-coding-agent/config.toml`. Default state: `$XDG_STATE_HOME/discord-coding-agent`, falling back to `~/.local/state/discord-coding-agent`. Use absolute XDG/config/state paths. Files must be owned by the service user and private (600); state directory is 700. Configuration/state should be outside both repositories. No `.env` auto-loading, shell evaluation, or global Codex configuration rewrite occurs.

Top-level environment variables override corresponding TOML fields. Timeouts come from the TOML `[timeouts]` table. Unknown keys and invalid values fail startup. Required numeric IDs may be TOML integers or quoted decimal strings. Environment-only configuration is possible but avoid passing the token in shell history or service unit contents; setup's private TOML is preferred.

| Key | Required/default | Meaning |
| --- | --- | --- |
| `DISCORD_TOKEN` | Required | Bot token, hidden in setup/diagnostics; excluded from child environment. |
| `DISCORD_OWNER_ID` | Required | Authorized human user ID. |
| `DISCORD_GUILD_ID` | Required | One server ID. |
| `DISCORD_CHANNEL_ID` | Required | One normal server text channel ID. |
| `CODEX_REPO` | Required | Absolute Git working-tree root. Canonical path/Git directory and directory identity are persisted. |
| `CODEX_EXECUTABLE` | `codex` | Executable path or PATH name. Exact 0.153.4 check before model work. |
| `DISPLAY_NAME` | `Coding Agent` | 1–32 printable characters for bridge greeting; configure Discord username in Developer Portal. |
| `APPROVAL_MODE` | `manual` | Initial mode only. Saved selected mode takes precedence after first run; use `!new auto/manual`. |
| `STATE_DIR` | Per-user state path | Change deliberately for a different repository/instance. |

Changing `CODEX_REPO` while using existing state fails repository identity validation. Use a new state directory or follow the documented recovery after backing up. The bridge never silently attaches one repository's saved conversation to another repository. Moving/recreating the Git directory can also invalidate identity; a branch/commit change alone does not.

## Timeout policy

All values are **seconds**, finite and greater than zero. The defaults below match `Timeouts` in source:

| `[timeouts]` key | Default | Operation and outcome |
| --- | ---: | --- |
| `initialization` | 120 | Overall version/process/handshake/config/thread preparation. Failure interrupts reservation; no prompt replay. |
| `request` | 45 | One RPC response, including `turn/start` acknowledgement. A lost acknowledgement is uncertain work. Never resend automatically. |
| `transport` | 20 | Incomplete JSON frame after the first byte, or pipe write drain. There is **no idle stdout-read timer**. |
| `task` | 3600 | Complete task budget including initialization and human input wait; starts when the task runner begins. |
| `user_wait` | 900 | Individual pending approval/question. Expiry sends an RPC error without approval and invalidates controls; Codex may continue/finish. |
| `delivery` | 30 | Individual Discord send/history/disable operation. Transient sends use at most three attempts with 1s/2s backoff and history reconciliation. |
| `shutdown` | 10 | Each interruption acknowledgement/wait/process-termination stage; service stop timeout is derived from these stages. |

A 45-second RPC timeout is not a one-hour task deadline. Errors name the operation, measured elapsed time, configured limit and diagnostic next step. Human wait counts against both its own timeout and the overall deadline. Configure a longer `task` if you need longer work; quiet logs alone never trigger cancellation. HTTP typing attempts have a cosmetic limit of at most 5 seconds and cannot cancel a task.
