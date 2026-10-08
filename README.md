# discord-coding-agent

Converse with the **real Codex CLI on your Linux server through Discord**. The server reads repositories, edits files and runs commands locally; model inference is remote. Close your laptop and continue from your phone.

The bridge supports one owner/server, channel-specific repositories and named sessions, with one active coding task across the bot. It uses discord.py's outbound Gateway and `codex app-server`; no public server, webhook, tunnel or port forwarding is needed.

This is an independent community project, **not an official OpenAI or Discord product**. The display label is configurable, for example **TARS**. Operation requires internet and valid Codex authentication; there is no offline inference, unlimited usage or guarantee that a subscription covers every workload.

## Quickstart

Use a regular Linux user and keep the bridge installation separate from the repositories Codex edits.

1. **Choose your host:** follow the detailed [Lenovo M720q / Ubuntu Server 24.04 installation and Pi migration guide](docs/m720q-ubuntu.md), or [Raspberry Pi setup](docs/pi-codex.md). Use 64-bit Linux, Python **3.11–3.14** and **Codex CLI 0.153.4**, the checked compatibility baseline. See [tested platforms and limitations](docs/compatibility.md).
2. **In the Developer Portal and Discord:** follow [bot setup](docs/discord.md) to create the bot, enable Message Content Intent, grant private-channel permissions and copy the numeric IDs.
3. **On the host:** install and configure the bridge:

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
4. **In Discord:** send `!ping`. Expect `pong` without a model call. Then press Ctrl+C in the host terminal to stop the connection test.
5. **On the host:** check Codex and start normal operation:

   ```bash
   codex login status
   .venv/bin/discord-coding-agent doctor --probe
   .venv/bin/discord-coding-agent run
   ```

   **In Discord:** send `Read-only: inspect git status and summarize this repository. Do not edit files.` Follow-up messages reuse the saved conversation, including after restart.

Follow [systemd installation](docs/service.md) to keep the bot running after logout and at boot. Stop the foreground bot before starting the service so it can acquire the state lock.

## Everyday use

Use `!debug` for host RAM, disk, load and task/process diagnostics. `!logs` shows recent operational logs; `!logs follow` streams bounded batches for ten minutes and `!logs stop` ends the feed. Logs rotate privately on disk and survive restart. See [diagnostics](docs/troubleshooting.md#discord-diagnostics-and-log-retention).

All replies appear inline in chat, including long results and code, split into readable pages without file downloads. `!help` groups the commands by purpose. `!ping` and `!status` identify the running bridge version and reply format; `!status` shows a compact task summary; `!status full` adds diagnostics and the last saved task error, even if its notification was lost. If new replies still arrive as files, [verify the running installation](docs/deployment.md#verify-the-running-bot). `!stop` interrupts work; it does not undo edits or external effects. `!last` retrieves the saved result. Send another ordinary message in the active channel to add instructions **while Codex is working**. The bot confirms when Codex accepts each follow-up; other channels remain busy.

See [chat controls and live follow-ups](docs/chat.md) for examples, receipts and mobile presentation.

Use `/models` to list the models available through Codex, then `/model MODEL` to choose one for this session. `/model` shows the saved selection; `!models` and `!model` work too. Changes require idle work, keep conversation history, and persist across restarts. See [model commands](docs/reference.md#model-selection) and [slash-command setup](docs/discord.md#model-slash-commands).

Tasks have **no bridge time limit by default**. Use `!run 30m Fix the failing tests` for an enforced cap, or `!run unlimited …` to remove the deadline for one task. Follow-ups keep the original deadline. Codex still stops when it finishes or fails; approval, connection and usage limits still apply. See the [command and timeout reference](docs/reference.md).

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
!delete old broken session
!delete confirm old broken session
```

Replace the path with an existing Git working-tree root on the host. Allowed paths default to the initial repository's parent; configure `WORKSPACE_ROOTS` locally for additional locations. Names and paths can contain spaces. `!delete NAME` previews deletion; confirm within 60 seconds to remove its bridge state and free a session slot. Repository files and Codex history remain. Deleting the selected session leaves that channel unselected.

For another project, grant the same bot access to another private text channel in the same server and send `!repo PATH` there. Each channel retains its selected repository and conversation. One task runs across all channels; session changes require idle work. Use bridge commands to switch sessions—asking Codex in prose to “open a new chat” does not change routing. See [workspace setup](docs/workspaces.md).

## Automatic updates

The optional Linux updater deploys an exact `main` commit after its push CI passes, waits until coding work is idle, and restarts the bot. Startup failure triggers rollback. Merges can therefore restart TARS; merge when you are ready for deployment.

**On the host**, with the managed bot service running and `!ping` working:

```bash
cd "$HOME/services/discord-coding-agent"
.venv/bin/discord-coding-agent deploy install --repository Jarom-W/discord-coding-agent
.venv/bin/discord-coding-agent deploy check
.venv/bin/discord-coding-agent deploy enable
.venv/bin/discord-coding-agent deploy status
```

`deploy check` attempts an update; `deploy enable` starts automatic polling, roughly every five minutes. Credentials and sessions stay in their private locations. Use your own `OWNER/REPO` for a fork; merged code runs as your Linux user. Read [deployment setup, verification and rollback](docs/deployment.md).

## Guides

- [M720q / Ubuntu installation and migration](docs/m720q-ubuntu.md) · [Discord setup](docs/discord.md) · [Pi and Codex setup](docs/pi-codex.md) · [End-to-end walkthrough](docs/walkthrough.md)
- [Chat controls and live follow-ups](docs/chat.md) · [Commands and configuration](docs/reference.md) · [Channel workspaces](docs/workspaces.md)
- [systemd and manual updates](docs/service.md) · [Automatic deployment](docs/deployment.md) · [Troubleshooting](docs/troubleshooting.md)
- [Architecture](docs/architecture.md) · [Compatibility and limitations](docs/compatibility.md) · [Security](SECURITY.md)
- [Contributing](CONTRIBUTING.md) · [Release history](CHANGELOG.md)
