# Troubleshooting and safe recovery

Start with `!ping`, `!status full`, and `!last` in the configured channel. On the Pi, run from the bridge directory:

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
| Failed task, but Gateway connected | The bridge can be running while its Codex task failed. Read the saved error in `!status full`; `!last` is the previous completed result. With no active task, a fresh ordinary message can start work in the same session after the cause is addressed. See [failed-task diagnostics](#failed-task-diagnostics). |
| RPC-reader error after a successful result | Closing the owned Codex child ends stdout normally. Current releases log that as `transport=closed reason=owned_shutdown`; an unexpected EOF during work still reports an error. Correlate the error with the task transition rather than treating every historical reader-error line as a failed task. |
| No response to `!ping` | Follow the ordered [Discord guide](discord.md): token, same application, saved Message Content Intent, owner/guild/channel IDs, bot membership, text-channel and category overrides. |
| Replies download as text files on mobile | Current bridge code cannot upload files. Check the new message's author, timestamp and delivery marker, then the running version/reply format in `!ping`. Follow [running-installation verification](deployment.md#verify-the-running-bot); a checkout's HEAD or saved deployment SHA alone is insufficient. After correcting the installation, `!last` sends a fresh inline copy without rerunning Codex. Existing posted files stay unchanged. |
| Em dashes or other Unicode look garbled | Inline replies preserve Unicode. Use `!last` for a fresh chat copy of a saved result. If a new message is garbled, report the running bridge version and a short non-sensitive example. |
| Wrong/stale repository or chat | Use `!status`, `!sessions`, `!session NAME` or `!repo PATH` in that channel. Only bridge commands change routing; follow [workspace setup](workspaces.md). |
| Directory outside roots / non-Git folder | `!dirs` lists allowed host roots. Configure `WORKSPACE_ROOTS` locally (including the initial repo), then restart idle. Selection requires an existing Git working-tree root; create/clone it locally first. Symlink escapes are refused. |
| Follow-up received but not accepted yet | Startup may still be running or an earlier follow-up awaits acknowledgement. Use `!status full`. A receipt is not proof Codex has seen the input. Maximum eight waiting/in-flight messages; excess messages are explicitly not submitted. |
| Follow-up acceptance unknown / turn ended | Do not resend blindly. The input may already have reached Codex. Read the final result/`!last`, review the repository, and decide how to continue. Healthy original work continues after a steer-only timeout; remaining unsent input is discarded. `!stop` remains available. |
| Follow-up unsupported/rejected | Run `doctor` and check the configured CLI baseline. The bridge does not start a second turn as a fallback. Wait for completion or use `!stop`, inspect the result, then explicitly send a new task. See [chat controls](chat.md). |
| Busy in another channel | `!status` reports the active coding channel. Work is serialized across the bot; the rejected message was not submitted. Wait, or `!stop` from a bound channel. Selection changes also wait during deployment. |
| Old saved repository identity | Restore its original Git directory or deliberately start a new session with `!repo --fresh PATH`. Old history is preserved and never silently rebound to replacement files. |
| Update does not arrive / failed deployment | Check `deploy status`, main's exact `ci.yml` push run, timer/user-bus/linger, bot readiness and updater journal. See [deployment troubleshooting](deployment.md). PR success alone is insufficient; active tasks defer restarts. |
| Service is active, but updater says “Bot is not ready” | `ActiveState=active` means the process runs; `health-status complete` only means the systemd query completed. Neither verifies Gateway readiness. A disconnected, unvalidated or stale process record blocks CD. See [readiness recovery](#service-active-but-updater-not-ready), including reconnect recovery. |
| Invalid token | Reset the bot token, update private config locally, restart. Do not send old or new tokens to an issue. |
| `PrivilegedIntentsRequired` | Enable and save Message Content Intent on the token's application. Do not enable member/presence intents. |
| Bot invisible or cannot send replies | Grant View Channels, Send Messages and Read Message History on that channel/category. Fix access, then use `!last` to recover saved output. Attach Files is not required. |
| Unknown protocol enum/request, malformed stdout | Check `codex --version` and configured executable. Require 0.153.4, regenerate schema for a proposed new adapter, and use exact per-field enum fixtures. A wrapper must not print banners to stdout. Unknown requests receive errors; `!stop` and handle unsupported features locally. |
| Auto reviewer unsupported, mismatched or unverified | `doctor --probe` checks process config only; thread verification happens before submission. Managed requirements or unsupported CLI may reject overrides. Do not bypass restrictions or replace strings globally. On the checked CLI, deliberately choose `!new manual` if allowed; no silent fallback occurs. |
| Automatic-review rejection/timeout | Read the reported action/rationale and final Codex explanation. Choose a safer request or handle it locally; auto mode does not mean blanket acceptance. Some review failures may arrive only as a general turn error, not a structured rejection. |
| Typing indicator disappears or fails | Cosmetic HTTP errors are isolated. Use last observed activity and task phase; typing is not proof of progress. |
| Quiet logs | No idle-read timeout is imposed. A long tool/model call may be quiet. Inspect event age, Pi resources and explicit deadline; use `!stop` if you decide to interrupt. |
| Timeout | Read the named operation and actual seconds. Initialization, RPC, partial transport frame, whole task, human wait, delivery and shutdown are different limits. See [reference](reference.md). Adjust the right TOML field and restart while idle. A request timeout may follow completed edits: never blindly resend. |
| Thread start/resume times out despite an unlimited task | `!run unlimited` does not disable startup checks. Inspect the preparation step and initialization budget in `!status full`; see [slow conversation startup](#slow-conversation-startup) below. |
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

## Failed-task diagnostics

`State: failed` describes the last coding task. `Gateway: connected` and a responding `!status` show that Discord commands still reach the bridge; they do not prove that Codex initialized. `verified: False` after an initialization failure means reviewer verification was not completed for that attempt, not that automatic review rejected a command. Once cleanup finishes and no coding task is active, send a fresh ordinary message to explicitly try again in the same conversation. A closed follow-up buffer belongs to the finished task; it does not disable future messages.

Read the **Last task failure** in `!status full` before continuing. The error survives restart and failed notifications; it does not replace the last completed result. Preparation failures report that the new prompt was not submitted. If turn submission was attempted, acceptance or effects may be uncertain: inspect the repository and affected external systems before deciding what to request next. The elapsed task value includes preparation and cleanup; use the saved error's named operation and configured limit to diagnose a timeout. Old interruptions that lack a saved reason still require their original journal entries.

If `journalctl --user` reports **No journal files were found**, confirm the host/account and query the system journal with the user-unit filter. **On the Pi, as the account running the bot** (replace the unit name for a custom instance):

```bash
hostname
id -un
systemctl --user show discord-coding-agent.service --no-pager -p ActiveState -p MainPID -p ExecStart
sudo journalctl _SYSTEMD_USER_UNIT=discord-coding-agent.service _UID="$(id -u)" -n 80 --no-pager -o short-iso
```

These commands inspect existing state and logs; they do not restart the bot. The privileged journal query may be needed when logs reside in the system journal. If it also has no entries, check the unit's `StandardOutput`/`StandardError` with `systemctl --user cat discord-coding-agent.service` and whether the journal has retained the relevant boot/time. Missing logs are not proof the bot stopped. Capture a new failure with the filtered journal following it (`-f`), then share only sanitized output and whether a fresh Discord message was accepted, rejected or silent.

## Service active but updater not ready

The updater checks both systemd process identity and the bridge's private readiness record. It stops before preparing/installing a candidate when the Gateway is not ready, its primary channel fails validation, or PID/invocation/configuration do not match. Enabling the timer or retrying a revision does not bypass this check. `deployment readiness=ready` or `not_ready` reports the actual outcome; the earlier `health-status ... complete` log describes only the systemd query.

Discord can recover a dropped connection by [resuming its existing Gateway session](https://docs.discord.com/developers/events/gateway#resuming). That finishes with `RESUMED`, which is distinct from initial `READY`. The bridge handles both events, revalidates the primary channel and restores readiness, connection status and pending delivery. Coding work is not submitted again, active approvals retain their identities, and the activity lock still defers deployment until work is idle. Look for `gateway=ready source_event=resumed` after a disconnect.

For an active process whose readiness remains false:

1. **In Discord:** inspect `!status`. Wait for active coding work to finish, or deliberately use `!stop` and review its effects before restarting.
2. **On the Pi:** collect the bot journal, then restart only your managed bridge:

   ```bash
   journalctl --user -u discord-coding-agent.service -n 80 --no-pager -o short-iso
   systemctl --user restart discord-coding-agent.service
   ```

3. **In Discord:** wait for the connection message and confirm `!ping` responds. A new connection rebuilds the readiness record; do not edit `ready.json` or remove a live lock to force a pass.
4. **On the Pi:** attempt deployment and inspect the configured interpreter without a pager:

   ```bash
   "$HOME/services/discord-coding-agent/.venv/bin/discord-coding-agent" deploy check --retry
   systemctl --user show discord-coding-agent.service --no-pager -p ActiveState -p MainPID -p ExecStart
   ```

Use your own unit/install path for a custom instance. The absolute CLI path also works after a new SSH login lands in `~`; `.venv/bin/...` is relative to your current directory. An SSH connection reset is evidence of a dropped SSH connection, not proof that the Discord Gateway disconnected. Use the bot journal to establish that sequence.

If readiness still fails, inspect `gateway=configuration_error` / `permissions_missing`, primary channel access, network connectivity and the service's configuration path. Preserve the logs and use the [manual update procedure](service.md#update-without-losing-credentials-or-conversation) if an older bot cannot become eligible for CD. A successful ping tests message round-trip delivery; the updater also validates the configured primary channel and process identity. Do not assume a fresh conversation or reinstalling Codex will repair readiness.

## Slow conversation startup

Codex [resumes a stored thread before a later `turn/start` submits new input](https://developers.openai.com/codex/app-server). A simple question can therefore fail during conversation loading, before Codex sees it. The bridge’s initialization budget covers this loading alongside the other preparation steps. A timeout alone does not establish whether the delay came from history loading, host resources, network access or configured integrations.

1. **In Discord:** read `!status full` and record the session/thread, preparation step, timeout type/limit and task ID. Keep that session selected; a new conversation is not required merely to increase its startup budget.
2. **On the Pi:** collect the journal and `doctor --probe` output using the commands at the top of this guide. A successful probe checks handshake/config/account access; it does **not** resume the selected conversation or prove that its tools can start.
3. If startup is progressing but needs more time, edit **the existing `[timeouts]` table** in private config, for example `initialization = 180`, then restart while idle. The full-task setting can stay `task = 0`; ordinary request timeouts remain separate. A larger budget will not repair a permanently stalled process or unavailable dependency.
4. After resolving the cause, send an explicit continuation. No failed prompt is automatically replayed. If the failed operation was `turn/start`, the prompt may already have been accepted: inspect repository status/diffs and any external effects first.

If a running bot still reports `RPC thread/resume ... configured limit 45s` rather than an initialization-budget failure, verify the deployed commit with `deploy status` and install the current fix. Consult the [release notes](../CHANGELOG.md) for the temporary workaround on affected releases. Never publish raw Codex logs, authentication caches, private state or full prompts while collecting evidence.

### Check host resource stalls

If startup repeatedly fails, collect host evidence **on the Pi** before changing timeout defaults:

```bash
uptime
free -h
ps -eo pid,ppid,stat,pcpu,pmem,comm --sort=-pcpu | head -n 15
vmstat 1 5
cat /proc/pressure/io
sudo journalctl -k --since "1 hour ago" -p warning --no-pager -n 60
```

A `D` process state is an uninterruptible wait; it can indicate I/O blocking and is not by itself proof of a failed disk. Check the kernel warnings for storage errors and [I/O pressure measurements](https://docs.kernel.org/accounting/psi.html) for ongoing stalls. A low `free` memory column is not an out-of-memory diagnosis: also read `available`; occupied swap alone does not prove current swap activity. If `apt`/`dpkg` is running, let it finish and inspect its progress instead of killing it, deleting package locks or rebooting during the operation. Recheck Codex startup after the host settles; preserve evidence if the wait persists.

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
