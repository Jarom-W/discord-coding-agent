# discord-coding-agent

Converse with the **real Codex CLI on an always-on Raspberry Pi through Discord**. The Pi reads your selected repository, edits files and runs commands locally; model inference is remote. Close your laptop and continue from your phone.

This is an independent community project, **not an official OpenAI or Discord product**. The display label is configurable (for example, **TARS**). Availability depends on the Pi, internet, Discord and valid Codex authentication. There is no offline inference, unlimited usage, or guarantee that a subscription covers every workload.

Version 0.2 supports one owner/server, **channel-specific repositories and named sessions**, and one active coding task across the bot. It uses Python/asyncio, discord.py's outbound Gateway and `codex app-server` JSON lines over local stdin/stdout. It needs no public server, webhook, tunnel or router port forwarding. It stays available for your messages; it does not invent coding tasks or run an autonomous coding schedule. An optional local updater deploys CI-verified merges when the bot is idle.

## Quickstart

Use a regular Linux user. Keep this bridge separate from the repository Codex edits. Existing personal TARS services should use separate config/state/unit names and must be left alone.

1. **On the Pi:** follow [Pi, Python and Codex setup](docs/pi-codex.md). Use 64-bit Linux and **Codex CLI 0.153.4**, this release's checked compatibility baseline. Python **3.11–3.14** is supported; Python 3.13 is explicitly tested. See [verification scope](docs/compatibility.md).
2. **In the Developer Portal and Discord:** complete the ordered [bot setup guide](docs/discord.md), including Message Content Intent, a private channel, permissions and numeric IDs.
3. **On the Pi:** install the bridge in its own directory:

   ```bash
   mkdir -p "$HOME/services"
   git clone https://github.com/Jarom-W/discord-coding-agent.git "$HOME/services/discord-coding-agent"
   cd "$HOME/services/discord-coding-agent"
   python3 -m venv .venv
   .venv/bin/python -m pip install --require-hashes -r requirements.lock
   .venv/bin/python -m pip install --no-deps .
   .venv/bin/discord-coding-agent setup
   .venv/bin/discord-coding-agent run --connection-only
   ```

   Enter your token locally in the hidden setup prompt. The default config is `~/.config/discord-coding-agent/config.toml`, mode 600. Never put it in either Git repository. Setup asks for the **other** repository's absolute path.
4. **In Discord:** send `!ping` in the selected channel. Expect `pong` without a model call. Stop the foreground connection test with Ctrl+C **on the Pi**.
5. **On the Pi:** check Codex, then start normal operation:

   ```bash
   codex login status
   .venv/bin/discord-coding-agent doctor --probe
   .venv/bin/discord-coding-agent run
   ```

   **In Discord:** send `Read-only: inspect git status and summarize this repository. Do not edit files.` Then ask a follow-up. Ordinary follow-ups reuse the saved thread, including after restart. Use `!new` for a fresh conversation.

After foreground checks, follow [systemd installation](docs/service.md) to run while SSH/laptop sessions are closed. The installer validates its generated unit, captures npm/nvm PATH, and never automatically starts or enables a service.

## Everyday use

`!help` lists all commands directly in chat, split into short messages you can read on mobile without downloading a file. `!status` shows operational state and last observed activity. `!stop` interrupts work, with bounded child-process escalation; it does not undo completed effects. `!last` retrieves the last completed result saved before delivery. Busy messages are explicitly rejected as **not submitted**, and duplicate message IDs are ignored.

Tasks have **no bridge time limit by default**. For an enforced cap, send `!run 30m Inspect and fix the failing tests`; seconds, minutes and hours are supported. `!run unlimited …` removes the task deadline for that request. Both continue the selected conversation. Natural-language rules are passed to Codex unchanged; use `!run` when a timer must be enforced by the bridge. Work ends when Codex completes, fails, is interrupted, or reaches an explicit deadline; approval requirements and other timeouts still apply. This does not extend account usage limits or automatically start another turn.

**Upgrading from 0.1.0:** an existing `[timeouts] task = 3600` remains a one-hour limit. After [updating the installed package](docs/service.md#update-without-losing-credentials-or-conversation), set `task = 0` in your private config and restart while idle to remove that default. No personal installation/configuration is changed automatically.

Manual mode displays green **Approve** and red **Deny** buttons for requests that need you, with complete details/diffs when supplied. `!approve ID` and `!deny ID` use the same decision path. `!answer ID …` answers questions. Manual mode does **not** prompt for every sandbox-allowed edit.

`!new auto` selects Codex's automatic approval reviewer with `on-request` and `workspace-write`. Eligible requests may be approved **or rejected**. Effective process and thread settings are checked before submission; this does not prove every tool's runtime behavior. Unsupported settings fail clearly. `!new manual` is an explicit alternative. Session/mode switching is rejected while busy. Asking the model in prose to “open a new chat” never changes the bridge's active thread.

## Repositories, channels and named sessions

**In Discord**, after upgrading to 0.2.0:

```text
!dirs
!dirs ~/work
!repo ~/work/my-project
!name backend fixes
!new auto release planning
!sessions
!session backend fixes
```

Replace `~/work/my-project` with an existing Git working-tree root on the Pi. `!dirs` shows the allowed roots, which default to the initial repository's parent. Set `WORKSPACE_ROOTS` locally to allow additional project directories. Paths may contain spaces; session names are case-insensitive and may also contain spaces. Each saved session keeps its repository, thread, approval mode and last result, including after restart.

For a second project, create another private text channel **in the same server**, grant the same bot its four required channel permissions, and send `!repo ~/work/another-project` there. No second application, token or process is needed. Names and selected conversations are separate per channel. Only the configured owner can operate the bot; coding work is serialized across all channels. See [workspace setup and recovery](docs/workspaces.md).

## Automatic updates after merges

GitHub Actions already tests pushes and PRs. The opt-in updater checks GitHub over outbound HTTPS, requires successful `ci.yml` **push** CI for the exact `main` commit, prepares a separate release/venv, waits for all coding work to finish, then restarts the managed bot. It checks Gateway readiness and restores the previous unit if startup fails. Credentials and sessions stay in their existing private locations.

**On the Pi**, first [manually update to 0.2.0](docs/service.md#update-without-losing-credentials-or-conversation), install/start the managed bot service, and confirm `!ping`. Then:

```bash
cd "$HOME/services/discord-coding-agent"
.venv/bin/discord-coding-agent deploy install --repository Jarom-W/discord-coding-agent
.venv/bin/discord-coding-agent deploy check
.venv/bin/discord-coding-agent deploy enable
.venv/bin/discord-coding-agent deploy status
```

Use your own `OWNER/REPO` if deploying a fork: **merges in that repository authorize code to run as your Linux user**. The default poll interval is five minutes plus jitter; deployment waits indefinitely while a coding task is active. These commands enable updates only for the selected managed instance. No GitHub runner, Pi login secret in Actions, public endpoint, or Codex CLI upgrade is required. Read [deployment, rollback and troubleshooting](docs/deployment.md) before enabling.

## Guides

- [Discord application, intents, permissions and first ping](docs/discord.md)
- [Pi installation, Codex authentication and repository setup](docs/pi-codex.md)
- [End-to-end walkthrough, including restart and phone use](docs/walkthrough.md)
- [Commands, configuration and timeout reference](docs/reference.md)
- [Directory browsing, channel workspaces and named sessions](docs/workspaces.md)
- [CI-gated automatic deployment and rollback](docs/deployment.md)
- [systemd, updates, rollback and uninstall](docs/service.md)
- [Troubleshooting and recovery](docs/troubleshooting.md)
- [Architecture and reliability](docs/architecture.md)
- [Supported/tested versions and limitations](docs/compatibility.md)
- [Security boundaries](SECURITY.md), [contributing](CONTRIBUTING.md), [release notes](CHANGELOG.md)

The intended new-repository name was `codex-discord-pi`; this checkout already had the public `discord-coding-agent` remote. Its name and history are preserved.
