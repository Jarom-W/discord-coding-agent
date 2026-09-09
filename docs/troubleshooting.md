# Troubleshooting and safe recovery

Start with `!ping`, `!status`, and `!last` in the configured channel. On the Pi, run from the bridge directory:

```bash
.venv/bin/discord-coding-agent doctor
.venv/bin/discord-coding-agent doctor --probe
.venv/bin/discord-coding-agent doctor --discord
journalctl --user -u discord-coding-agent.service -n 200 --no-pager -o short-iso
```

`doctor` shows versions, platform, configured paths and capability failures without tokens. `--probe` creates its own Codex child for non-model handshake/config/account-presence checks; it does not load or run the bridge's conversation. `--discord` tests REST token/channel access, not Gateway intents or send permissions. Connection-only `!ping` tests receive/send. No production credentials belong in CI.

## Symptoms and next steps

| Symptom | Check and recovery |
| --- | --- |
| No response to `!ping` | Follow the ordered [Discord guide](discord.md): token, same application, saved Message Content Intent, owner/guild/channel IDs, bot membership, text-channel and category overrides. |
| Wrong/stale repository or chat | Use `!status`, `!sessions`, `!session NAME` or `!repo PATH` in that channel. Only bridge commands change routing. Update to 0.2.0 for channel workspaces; follow [workspace setup](workspaces.md). |
| Directory outside roots / non-Git folder | `!dirs` lists allowed host roots. Configure `WORKSPACE_ROOTS` locally (including the initial repo), then restart idle. Selection requires an existing Git working-tree root; create/clone it locally first. Symlink escapes are refused. |
| Busy in another channel | `!status` reports the active coding channel. Work is serialized across the bot; the rejected message was not submitted. Wait, or `!stop` from a bound channel. Selection changes also wait during deployment. |
| Old saved repository identity | Restore its original Git directory or deliberately start a new session with `!repo --fresh PATH`. Old history is preserved and never silently rebound to replacement files. |
| Update does not arrive / failed deployment | Check `deploy status`, main's exact `ci.yml` push run, timer/user-bus/linger, bot readiness and updater journal. See [deployment troubleshooting](deployment.md). PR success alone is insufficient; active tasks defer restarts. |
| Invalid token | Reset the bot token, update private config locally, restart. Do not send old or new tokens to an issue. |
| `PrivilegedIntentsRequired` | Enable and save Message Content Intent on the token's application. Do not enable member/presence intents. |
| Bot invisible or cannot send attachments | Grant View Channels, Send Messages, Read Message History and Attach Files on that channel/category. Long replies require attachment permission. Fix access then `!last`. |
| Unknown protocol enum/request, malformed stdout | Check `codex --version` and configured executable. Require 0.153.4, regenerate schema for a proposed new adapter, and use exact per-field enum fixtures. A wrapper must not print banners to stdout. Unknown requests receive errors; `!stop` and handle unsupported features locally. |
| Auto reviewer unsupported, mismatched or unverified | `doctor --probe` checks process config only; thread verification happens before submission. Managed requirements or unsupported CLI may reject overrides. Do not bypass restrictions or replace strings globally. On the checked CLI, deliberately choose `!new manual` if allowed; no silent fallback occurs. |
| Automatic-review rejection/timeout | Read the reported action/rationale and final Codex explanation. Choose a safer request or handle it locally; auto mode does not mean blanket acceptance. Some review failures may arrive only as a general turn error, not a structured rejection. |
| Typing indicator disappears or fails | Cosmetic HTTP errors are isolated. Use last observed activity and task phase; typing is not proof of progress. |
| Quiet logs | No idle-read timeout is imposed. A long tool/model call may be quiet. Inspect event age, Pi resources and explicit deadline; use `!stop` if you decide to interrupt. |
| Timeout | Read the named operation and actual seconds. Initialization, RPC, partial transport frame, whole task, human wait, delivery and shutdown are different limits. See [reference](reference.md). Adjust the right TOML field and restart while idle. A request timeout may follow completed edits: never blindly resend. |
| Still stops after one hour | Check the bridge version and `!status`. Update/reinstall 0.1.1 or later, then set an existing `[timeouts] task = 3600` to `task = 0` in private config and restart while idle. `!run unlimited task text` overrides the default for one new task. Codex can still complete/fail earlier; this is not an account-limit override. |
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
