# Troubleshooting and safe recovery

Start with `!ping`, `!status`, and `!last` in the configured channel. On the Pi, run from the bridge directory:

```bash
.venv/bin/discord-coding-agent doctor
.venv/bin/discord-coding-agent doctor --probe
.venv/bin/discord-coding-agent doctor --discord
journalctl --user -u discord-coding-agent.service -n 200 --no-pager -o short-iso
```

`doctor` shows versions, platform, configured paths, timeout values and capability failures without tokens. `--probe` creates its own Codex child for non-model handshake/config/account-presence checks; it does not load or run the bridge's conversation. `--discord` tests REST token/channel access, not Gateway intents or send permissions. Connection-only `!ping` tests receive/send. No production credentials belong in CI.

## Symptoms and next steps

| Symptom | Check and recovery |
| --- | --- |
| No response to `!ping` | Follow the ordered [Discord guide](discord.md): token, same application, saved Message Content Intent, owner/guild/channel IDs, bot membership, text-channel and category overrides. |
| Replies download as text files on mobile | Current replies are inline, including long results. Check the running commit with `deploy status` if using CD, then send `!help` or `!last` for a fresh copy. Existing posted files stay unchanged. |
| Em dashes or other Unicode look garbled | Inline replies preserve Unicode. Use `!last` for a fresh chat copy of a saved result. If a new message is garbled, report the running bridge version and a short non-sensitive example. |
| Wrong/stale repository or chat | Use `!status`, `!sessions`, `!session NAME` or `!repo PATH` in that channel. Only bridge commands change routing; follow [workspace setup](workspaces.md). |
| Directory outside roots / non-Git folder | `!dirs` lists allowed host roots. Configure `WORKSPACE_ROOTS` locally (including the initial repo), then restart idle. Selection requires an existing Git working-tree root; create/clone it locally first. Symlink escapes are refused. |
| Busy in another channel | `!status` reports the active coding channel. Work is serialized across the bot; the rejected message was not submitted. Wait, or `!stop` from a bound channel. Selection changes also wait during deployment. |
| Old saved repository identity | Restore its original Git directory or deliberately start a new session with `!repo --fresh PATH`. Old history is preserved and never silently rebound to replacement files. |
| Update does not arrive / failed deployment | Check `deploy status`, main's exact `ci.yml` push run, timer/user-bus/linger, bot readiness and updater journal. See [deployment troubleshooting](deployment.md). PR success alone is insufficient; active tasks defer restarts. |
| Invalid token | Reset the bot token, update private config locally, restart. Do not send old or new tokens to an issue. |
| `PrivilegedIntentsRequired` | Enable and save Message Content Intent on the token's application. Do not enable member/presence intents. |
| Bot invisible or cannot send replies | Grant View Channels, Send Messages and Read Message History on that channel/category. Fix access, then use `!last` to recover saved output. Attach Files is not required. |
| Unknown protocol enum/request, malformed stdout | Check `codex --version` and configured executable. Require 0.153.4, regenerate schema for a proposed new adapter, and use exact per-field enum fixtures. A wrapper must not print banners to stdout. Unknown requests receive errors; `!stop` and handle unsupported features locally. |
| Auto reviewer unsupported, mismatched or unverified | `doctor --probe` checks process config only; thread verification happens before submission. Managed requirements or unsupported CLI may reject overrides. Do not bypass restrictions or replace strings globally. On the checked CLI, deliberately choose `!new manual` if allowed; no silent fallback occurs. |
| Automatic-review rejection/timeout | Read the reported action/rationale and final Codex explanation. Choose a safer request or handle it locally; auto mode does not mean blanket acceptance. Some review failures may arrive only as a general turn error, not a structured rejection. |
| Typing indicator disappears or fails | Cosmetic HTTP errors are isolated. Use last observed activity and task phase; typing is not proof of progress. |
| Quiet logs | No idle-read timeout is imposed. A long tool/model call may be quiet. Inspect event age, Pi resources and explicit deadline; use `!stop` if you decide to interrupt. |
| Timeout | Read the named operation and actual seconds. Initialization, RPC, partial transport frame, whole task, human wait, delivery and shutdown are different limits. See [reference](reference.md). Adjust the right TOML field and restart while idle. A request timeout may follow completed edits: never blindly resend. |
| Thread start/resume times out despite an unlimited task | `!run unlimited` does not disable startup checks. Inspect the preparation step and initialization budget in `!status`; see [slow conversation startup](#slow-conversation-startup) below. |
| Still stops after one hour | Check `!status` and the private `[timeouts]` table. Set an explicit `task = 3600` to `task = 0` and restart while idle. `!run unlimited task text` overrides the default for one new task. Codex can still complete/fail earlier; this does not override account limits. |
| Authentication error | Run the same configured executable's `login status` as the service user. Complete headless login locally; confirm service HOME/Codex authentication location and managed account requirements. Do not copy caches into Discord. |
| Rate/usage limit | Check your Codex/account usage and service status through official account tools. Wait or adjust workload/auth plan as appropriate. Subscription coverage and limits are not guaranteed. The bridge does not loop-replay a failed turn. |
| Wi-Fi/internet interruption | Gateway may reconnect while the coding task continues. Any enabled task deadline still applies. Final output persists before delivery; reconnect or `!last` recovers it. Check Pi network, DNS and clock before blaming Codex. |
| Low memory/storage on Pi | Check `free -h`, `df -h` and `journalctl -k` for OOM/storage errors. Reduce build concurrency and task size. A Pi 4 is resource-constrained. Do not delete state/rollouts to fix space without preserving recovery information. |
| systemd `bad-setting` | Reinstall this project's generated unit and run `service validate`. WorkingDirectory must not have shell-style surrounding quotes; ExecStart has different escaping. Inspect `systemctl --user cat …`. Do not overwrite an unrelated TARS unit. |
| `Failed to connect to bus`, runtime/user-bus errors | Log in directly by SSH as the service user, not sudo/root; inspect `loginctl user-status "$USER"` and distro user-session setup. Offline unit syntax validation is possible in containers but does not prove service startup. |
| Stops after logout / doesn't start on boot | Enable the user unit and confirm `loginctl show-user "$USER" -p Linger`. Linger requires an explicit administrative choice; see [service guide](service.md). |
| Process lock held | A running service or foreground instance owns it. Check status/PID in the private `process.lock` file and inspect that process before stopping **your bridge instance**. Never unlink the lock while a process may own it: that creates a second lock inode. A stale file alone is harmless; the kernel releases flock on exit. |
| Expired/old buttons | Use `!status` and the current request ID. Old buttons cannot decide a different request, even after restart. If Discord was disconnected they may remain visible until cleanup; do not reuse their IDs. |
| Undelivered complete result | Fix Discord connectivity/permissions, reconnect, or `!last`. Delivery retries are bounded and may need this explicit recovery. No model task is repeated. |
| Empty thread cannot resume (`no rollout found`) | Codex has no persisted history for that thread, often because initialization succeeded but no turn/history was written. Inspect first; when appropriate choose `!new`. Do not silently recreate/replay the uncertain task. |

## Slow conversation startup

Codex [resumes a stored thread before a later `turn/start` submits new input](https://developers.openai.com/codex/app-server). A simple question can therefore fail during conversation loading, before Codex sees it. The bridge’s initialization budget covers this loading alongside the other preparation steps. A timeout alone does not establish whether the delay came from history loading, host resources, network access or configured integrations.

1. **In Discord:** read `!status` and record the session/thread, preparation step, timeout type/limit and task ID. Keep that session selected; a new conversation is not required merely to increase its startup budget.
2. **On the Pi:** collect the journal and `doctor --probe` output using the commands at the top of this guide. A successful probe checks handshake/config/account access; it does **not** resume the selected conversation or prove that its tools can start.
3. If startup is progressing but needs more time, edit **the existing `[timeouts]` table** in private config, for example `initialization = 180`, then restart while idle. The full-task setting can stay `task = 0`; ordinary request timeouts remain separate. A larger budget will not repair a permanently stalled process or unavailable dependency.
4. After resolving the cause, send an explicit continuation. No failed prompt is automatically replayed. If the failed operation was `turn/start`, the prompt may already have been accepted: inspect repository status/diffs and any external effects first.

If a running bot still reports `RPC thread/resume ... configured limit 45s` rather than an initialization-budget failure, verify the deployed commit with `deploy status` and install the current fix. Consult the [release notes](../CHANGELOG.md) for the temporary workaround on affected releases. Never publish raw Codex logs, authentication caches, private state or full prompts while collecting evidence.

## Interrupted-task recovery

1. Let `!stop` finish or stop the managed bridge service. Read `!status` and `!last`; the latter is the last **completed** result, which may precede the interrupted task.
2. On the Pi, inspect `git -C /your/target status` and `git -C /your/target diff`, plus any tools or external systems affected by the task. Cancellation does not undo effects. Do not use a destructive reset as a default recovery step.
3. Restart the bridge if needed. It reports interruption and resumes no coding work automatically.
4. Send an explicit continuation describing what already happened and what you want next, or `!new` for a different conversation. The next message can reuse the existing Codex thread, but should not ask it to blindly repeat the uncertain action.

## Corrupt state, repository mismatch or schema mismatch

Stop the bridge first and copy the entire state/config directories to a private backup, as shown in the service guide. Do not edit/delete an active lock or publish state JSON. A corrupt state file is left unchanged. Unknown schemas are copied to `state.json.backup-TIMESTAMP` and rejected; use the release that understands that schema. This project does not import prototype state automatically.

For a repository mismatch, restore the original path/Git directory if possible, or select a new `STATE_DIR` for the new repository. Do not change the saved repository identity by hand to suppress a warning; the old conversation may refer to different files.

For the workspace catalog, back up `workspaces.json` together with all `sessions/` state. Unknown catalog schemas are backed up; malformed catalogs are left untouched. `!repo --fresh PATH` can create a new conversation for a deliberately recreated repository, but it does not repair corrupted catalog JSON. Restore a known-good catalog/state set or use a new state directory. See [workspace recovery](workspaces.md).

If you deliberately abandon an unusable bridge state, preserve the directory, choose a new empty `STATE_DIR` outside repositories in private configuration, and restart. This starts a fresh bridge conversation without deleting Codex authentication, rollouts or the repository. Review uncertain edits before the first request. Restoring an older state file also restores older dedup/delivery information, so duplicates are possible; inspect the channel and never replay tasks automatically.

## Share sanitized diagnostics

Use the bug-report template. Include bridge/Python/Codex/discord.py versions, architecture, OS, operation, exact timeout **type and limit**, the approximate UTC time, correlation IDs and a minimal reproduction in a disposable repository. Before sharing doctor/journal output, replace personal paths/IDs and check for private information. Default logs omit prompt/token/reasoning bodies and raw child stderr; error code and RPC start lines help locate the failed operation. Reproduce a Codex-only failure locally in a disposable repo if the generic RPC error needs more detail. Inspect any Codex-local logs privately before extracting a minimal sanitized error.

Never attach bot tokens, API keys, auth caches, complete state/transcripts, raw protocol dumps, environment dumps or production repository files. If sensitive data was already exposed, rotate the affected credential first and follow [SECURITY.md](../SECURITY.md).
