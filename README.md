# discord-coding-agent

Converse with the **real Codex CLI on an always-on Raspberry Pi through Discord**. The Pi reads repositories, edits files and runs commands locally; model inference is remote. Close your laptop and continue from your phone.

The bridge supports one owner/server, channel-specific repositories and named sessions, with one active coding task across the bot. It uses discord.py's outbound Gateway and `codex app-server`; no public server, webhook, tunnel or port forwarding is needed.

This is an independent community project, **not an official OpenAI or Discord product**. The display label is configurable, for example **TARS**. Operation requires internet and valid Codex authentication; there is no offline inference, unlimited usage or guarantee that a subscription covers every workload.

## Quickstart

Use a regular Linux user and keep the bridge installation separate from the repositories Codex edits.

1. **On the Pi:** follow [Pi, Python and Codex setup](docs/pi-codex.md). Use 64-bit Linux, Python **3.11–3.14** and **Codex CLI 0.153.4**, the checked compatibility baseline. See [tested platforms and limitations](docs/compatibility.md).
2. **In the Developer Portal and Discord:** follow [bot setup](docs/discord.md) to create the bot, enable Message Content Intent, grant private-channel permissions and copy the numeric IDs.
3. **On the Pi:** install and configure the bridge:

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

   Enter the token in the hidden local prompt and select an existing Git repository outside the bridge installation. Configuration is stored privately at `~/.config/discord-coding-agent/config.toml`; never commit it.
4. **In Discord:** send `!ping`. Expect `pong` without a model call. Then press Ctrl+C in the Pi terminal to stop the connection test.
5. **On the Pi:** check Codex and start normal operation:

   ```bash
   codex login status
   .venv/bin/discord-coding-agent doctor --probe
   .venv/bin/discord-coding-agent run
   ```

   **In Discord:** send `Read-only: inspect git status and summarize this repository. Do not edit files.` Follow-up messages reuse the saved conversation, including after restart.

Follow [systemd installation](docs/service.md) to keep the bot running after logout and at boot. Stop the foreground bot before starting the service so it can acquire the state lock.

## Everyday use

All replies appear inline in chat, including long results and code, split into readable pages without file downloads. `!help` lists the commands. `!status` reports task state and last observed activity. `!stop` interrupts work; it does not undo edits or external effects. `!last` retrieves the saved result. Busy messages are explicitly rejected as **not submitted**.

Tasks have **no bridge time limit by default**. Use `!run 30m Fix the failing tests` for an enforced cap, or `!run unlimited …` to remove the deadline for one task. Codex still stops when it finishes or fails; approval, connection and usage limits still apply. See the [command and timeout reference](docs/reference.md).

Manual mode shows green **Approve** and red **Deny** buttons when your decision is needed. `!new auto` selects Codex's automatic approval reviewer while retaining the workspace sandbox; eligible requests can be approved or rejected. Settings are verified before work. Use `!new manual` for manual review.

## Repositories, channels and sessions

**In Discord:**

```text
!dirs
!repo ~/work/my-project
!name backend fixes
!new auto release planning
!sessions
!session backend fixes
```

Replace the path with an existing Git working-tree root on the Pi. Allowed paths default to the initial repository's parent; configure `WORKSPACE_ROOTS` locally for additional locations. Names and paths can contain spaces.

For another project, grant the same bot access to another private text channel in the same server and send `!repo PATH` there. Each channel retains its selected repository and conversation. One task runs across all channels; session changes require idle work. Use bridge commands to switch sessions—asking Codex in prose to “open a new chat” does not change routing. See [workspace setup](docs/workspaces.md).

## Automatic updates

The optional Pi updater deploys an exact `main` commit after its push CI passes, waits until coding work is idle, and restarts the bot. Startup failure triggers rollback. Merges can therefore restart TARS; merge when you are ready for deployment.

**On the Pi**, with the managed bot service running and `!ping` working:

```bash
cd "$HOME/services/discord-coding-agent"
.venv/bin/discord-coding-agent deploy install --repository Jarom-W/discord-coding-agent
.venv/bin/discord-coding-agent deploy check
.venv/bin/discord-coding-agent deploy enable
.venv/bin/discord-coding-agent deploy status
```

`deploy check` attempts an update; `deploy enable` starts automatic polling, roughly every five minutes. Credentials and sessions stay in their private locations. Use your own `OWNER/REPO` for a fork; merged code runs as your Linux user. Read [deployment setup, verification and rollback](docs/deployment.md).

## Guides

- [Discord setup](docs/discord.md) · [Pi and Codex setup](docs/pi-codex.md) · [End-to-end walkthrough](docs/walkthrough.md)
- [Commands and configuration](docs/reference.md) · [Channel workspaces](docs/workspaces.md)
- [systemd and manual updates](docs/service.md) · [Automatic deployment](docs/deployment.md) · [Troubleshooting](docs/troubleshooting.md)
- [Architecture](docs/architecture.md) · [Compatibility and limitations](docs/compatibility.md) · [Security](SECURITY.md)
- [Contributing](CONTRIBUTING.md) · [Release history](CHANGELOG.md)
