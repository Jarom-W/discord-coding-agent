# Lenovo M720q: Ubuntu Server 24.04 LTS installation and Pi migration

This guide targets a **direct Ubuntu Server 24.04 LTS amd64 installation** on your Lenovo ThinkCentre M720q. Run commands over SSH as the regular Linux account that will run the bot. The bridge also supports ARM64 Raspberry Pi hosts; there is no Pi-specific runtime dependency.

Codex runs tools, repository scans and builds on this machine; model inference uses the remote service. More CPU, RAM and faster storage can reduce local delays, but cannot remove network/model latency. Check your actual installed hardware with the commands below; M720q configurations vary. [Lenovo's platform specifications](https://psref.lenovo.com/syspool/Sys/PDF/ThinkCentre/ThinkCentre_M720_Tiny/ThinkCentre_M720_Tiny_Spec.html) describe the hardware family. No GPU is required by this bridge. Put active repositories, the bridge and Codex's home on the local SSD if available.

## 1. Prepare Ubuntu and the service account

Use the account you created during Ubuntu installation, or create a dedicated regular account through your normal homelab administration. Log in directly over SSH as that account. Substitute your username and LAN hostname/IP:

```bash
ssh YOUR-USER@YOUR-M720Q
```

On the M720q:

```bash
uname -m
getconf LONG_BIT
cat /etc/os-release
lscpu
free -h
lsblk -o NAME,SIZE,TYPE,MOUNTPOINTS
df -h "$HOME"
sudo apt update
sudo apt install git curl ca-certificates python3 python3-venv python3-pip rsync
python3 --version
```

Expect `x86_64`, `64`, Ubuntu `24.04`, and Python `3.12.x`. Ubuntu's [python3-venv package](https://packages.ubuntu.com/noble/python3-venv) matches its default Python. This project supports Python 3.11–3.14. If a locked dependency must compile locally, install `build-essential python3-dev` after reviewing the pip error.

The bot needs outbound HTTPS/WebSocket access to Discord and Codex services. It requires no incoming listener, port forwarding, webhook or public hostname. Keep SSH under your existing homelab access policy. Use an SSD and leave RAM/disk headroom for your project's builds; the bridge allows one active coding task but that task can start multiple build processes.

## 2. Install the pinned x86-64 Codex executable

The bridge checks **Codex CLI 0.153.4** exactly. Do not install an unpinned latest CLI or copy the Pi's ARM64 executable. The standalone Linux binary avoids a Node dependency. Download from the [official versioned release](https://github.com/openai/codex/releases/tag/rust-v0.153.4):

```bash
mkdir -p "$HOME/tools/codex-0.153.4"
cd "$HOME/tools/codex-0.153.4"
curl --fail --location --proto '=https' --tlsv1.2 \
  --output codex-x86_64-unknown-linux-musl.tar.gz \
  https://github.com/openai/codex/releases/download/rust-v0.153.4/codex-x86_64-unknown-linux-musl.tar.gz
tar -tzf codex-x86_64-unknown-linux-musl.tar.gz
tar -xzf codex-x86_64-unknown-linux-musl.tar.gz
chmod 755 codex-x86_64-unknown-linux-musl
"$HOME/tools/codex-0.153.4/codex-x86_64-unknown-linux-musl" --version
```

Expected: `codex-cli 0.153.4`. Inspect the release's published verification material before extraction when available. If the download fails, inspect that release's asset names; do not substitute another version. The [Pi/Codex guide](pi-codex.md#install-the-checked-codex-version-separately) also documents a pinned npm installation if you already manage Node locally. Use the chosen executable's **absolute path** throughout setup.

Authenticate as the service user:

```bash
"$HOME/tools/codex-0.153.4/codex-x86_64-unknown-linux-musl" login --device-auth
"$HOME/tools/codex-0.153.4/codex-x86_64-unknown-linux-musl" login status
```

Open the URL shown by the CLI on your laptop/phone and complete sign-in. Device authentication may need enabling for your account/workspace; see [official headless authentication](https://developers.openai.com/codex/auth). Keep device codes and auth files out of Discord. This installs neither a local inference model nor a separate API billing requirement; use the authentication method supported by your account.

## 3. Prepare the coding repositories

Keep the bridge installation separate from every repository it edits. For committed work, clone your target with your usual Git authentication (replace the URL):

```bash
mkdir -p "$HOME/work" "$HOME/services"
git clone https://github.com/YOUR-ACCOUNT/YOUR-PROJECT.git "$HOME/work/my-project"
git -C "$HOME/work/my-project" status
```

Install that project's own compilers/runtimes and dependencies on Ubuntu. Recreate Python virtual environments, native build directories and `node_modules` for x86-64; ARM64 build products are not portable. If the Pi has uncommitted or untracked work, preserve it before cloning from GitHub: cloning only retrieves committed, pushed content. Section 7 covers the transfer and cutover.

## 4. Install and configure the bridge

```bash
git clone https://github.com/Jarom-W/discord-coding-agent.git "$HOME/services/discord-coding-agent"
cd "$HOME/services/discord-coding-agent"
python3 -m venv .venv
.venv/bin/python -m pip install --require-hashes -r requirements.lock
.venv/bin/python -m pip install --no-deps .
.venv/bin/discord-coding-agent --version
.venv/bin/discord-coding-agent setup
```

During setup, enter:

| Prompt | Value |
| --- | --- |
| Bot token | Your existing bot token, entered at the hidden prompt; no new Discord bot is needed |
| Owner/server/channel IDs | The same numeric IDs as the Pi installation |
| Initial repository | `/home/YOUR-USER/work/my-project` (actual absolute working-tree root) |
| Codex executable | `/home/YOUR-USER/tools/codex-0.153.4/codex-x86_64-unknown-linux-musl` |
| Display label | Your preferred name, such as `TARS` |

Setup stores private configuration in `~/.config/discord-coding-agent/config.toml`; state defaults to `~/.local/state/discord-coding-agent`. If you need additional repositories outside `~/work`, add `WORKSPACE_ROOTS` at the top level before `[timeouts]`, following [workspace setup](workspaces.md). Use fresh state on the M720q; section 7 explains why copied Pi session files cannot simply be resumed.

The existing Discord bot must have Message Content Intent, private-channel View Channel/Send Messages/Read Message History permissions, and the `applications.commands` installation scope for model slash commands. See [Discord setup](discord.md). Prefix diagnostics/session commands work without slash registration.

## 5. Test in the foreground

**First stop the Pi bot and its updater as described in section 7.** State locks only coordinate processes on one host; two hosts using the same bot can both act on a message.

```bash
cd "$HOME/services/discord-coding-agent"
.venv/bin/discord-coding-agent doctor --probe
.venv/bin/discord-coding-agent run --connection-only
```

In Discord, send `!ping`, `!debug`, and `!logs`. Expect a pong, the new host's `x86_64` diagnostics, and the running bridge version. Connection-only mode never submits a model task. Stop it with Ctrl+C and wait for the shell prompt, then:

```bash
.venv/bin/discord-coding-agent run
```

In Discord:

```text
!logs follow
Read-only: inspect git status and summarize this repository. Do not edit files.
!status full
!logs stop
```

Complete any manual approval if appropriate. Check that the result arrives, then send a follow-up to verify the conversation continues. `!debug` shows resource snapshots; `!logs` shows preparation/RPC timing. Distinguish time spent preparing/resuming from time spent waiting on remote inference. Do not increase timeouts merely to hide unexplained failures.

## 6. Run at boot with systemd

After the foreground task is idle, press Ctrl+C and wait for it to exit. In the same service-user SSH session:

```bash
cd "$HOME/services/discord-coding-agent"
.venv/bin/discord-coding-agent service validate
.venv/bin/discord-coding-agent service install
sudo loginctl enable-linger "$USER"
loginctl show-user "$USER" -p Linger
.venv/bin/discord-coding-agent service enable
.venv/bin/discord-coding-agent service start
.venv/bin/discord-coding-agent service status
journalctl --user -u discord-coding-agent.service -n 80 --no-pager -o short-iso
```

Expect `Linger=yes` and an active service. The unit captures your Python/Codex paths, uses private permissions, restarts after bridge failures, and cleans up its child processes on shutdown. Run the bot as the regular user, never with sudo. `systemctl --user` errors usually mean you are not in that user's direct login session; see [service troubleshooting](service.md#start-at-boot-and-after-logout).

Close SSH and verify `!ping` from your phone. Reboot during a homelab maintenance window, reconnect, and repeat `service status`, `!ping`, and `!debug` to verify boot persistence on your actual machine. The automated suite does not substitute for this hardware check.

Routine commands:

```bash
systemctl --user status discord-coding-agent.service --no-pager -l
journalctl --user -u discord-coding-agent.service -f -o short-iso
```

Ctrl+C exits the journal viewer without stopping the bot. To deliberately stop/restart it, use `systemctl --user stop discord-coding-agent.service` or `systemctl --user restart discord-coding-agent.service` while idle. See [manual updates and rollback](service.md) and [optional automatic updates](deployment.md).

## 7. Move from the Pi without losing work

Prepare the M720q through section 4 before the cutover. Keep the Pi installation as a rollback copy. These examples assume the default paths and unit name; substitute any custom XDG/STATE_DIR/CODEX_HOME/unit paths.

1. In Discord, use `!status`; wait for idle or send `!stop`. Save any conversation summaries you want to carry forward with `!last`. Deleting a session does not undo repository changes.
2. On the Pi, if the optional updater is installed, run its bootstrap command and wait for its service to finish:

   ```bash
   cd "$HOME/services/discord-coding-agent"
   .venv/bin/discord-coding-agent deploy disable
   systemctl --user status discord-coding-agent-update.service --no-pager
   ```

   The standard updater unit is shown above; use the name from `deploy status` for custom units. Skip these commands if you never installed the updater. Do not proceed while an update is still deploying.
3. Stop and disable the Pi bot so it cannot restart at boot:

   ```bash
   systemctl --user disable --now discord-coding-agent.service
   systemctl --user is-active discord-coding-agent.service
   ```

   Expect `inactive` (the command returns a nonzero exit status for inactive). Stop any foreground copy too. Never delete a process lock to force a second instance to start.
4. Back up the stopped Pi state/config and Codex history privately:

   ```bash
   umask 077
   dca_backup="$HOME/dca-migration-$(date +%Y%m%d-%H%M%S)"
   mkdir -p "$dca_backup"
   cp -a "$HOME/.config/discord-coding-agent" "$dca_backup/config"
   cp -a "$HOME/.local/state/discord-coding-agent" "$dca_backup/state"
   cp -a "$HOME/.codex" "$dca_backup/codex"
   git -C "$HOME/work/my-project" status --short
   ```

   Substitute your real project paths and repeat for all repositories. The backup contains credentials/history; keep it private. Preserve whole working trees (including `.git`, untracked files, submodule repositories and any external linked-worktree Git directories), not just `git diff` output. The last command inventories changes but does not back them up.
5. For an ordinary standalone working tree, you can copy from the Pi to a **new, empty destination** on the M720q over SSH. Run this on the M720q, replacing `PI-USER`, `PI-HOST` and the source/destination paths:

   ```bash
   mkdir -p "$HOME/work/imported-project"
   rsync -a --no-owner --no-group \
     --exclude='.venv/' --exclude='node_modules/' --exclude='__pycache__/' \
     PI-USER@PI-HOST:/home/PI-USER/work/my-project/ "$HOME/work/imported-project/"
   git -C "$HOME/work/imported-project" status
   ```

   This preserves `.git` and untracked work while leaving common architecture-specific dependencies behind. Review additional generated build artifacts yourself. Linked worktrees need their common Git directory/path references recreated through Git; do not blindly copy their `.git` pointer files. Keep the source untouched until the target is verified. If you choose this imported path as the initial repository, update `CODEX_REPO` locally before running the bot, or select it with `!repo PATH` afterward.
6. Recreate dependencies, authenticate Codex/Git locally, then complete sections 5–6 on the M720q. Start fresh Discord sessions for each repository/channel. The same Discord bot identity/channel history remains available.

**Session portability:** bridge repository identity includes the absolute worktree/Git-directory paths and filesystem device/inode. A new disk changes it even when paths are identical. Copying `state.json`/`workspaces.json` does not migrate those identities, and bridge state also depends on separate Codex history. There is no automatic cross-host history importer. This guide deliberately keeps the Pi state as a backup and starts new M720q sessions. Do not edit identity strings to bypass the check. Bring forward a reviewed summary of unfinished work, inspect the actual repository, then continue explicitly.

**Rollback to the Pi:** stop/disable the M720q bot and updater first. Preserve and reconcile any work performed on the new server before returning to the old checkouts. Then re-enable/start the Pi bot using its untouched configuration/state and re-enable its updater only if desired. Never run both hosts with the same token/channel at once.

## 8. Diagnose a disconnect from Discord

```text
!debug
!logs 50
!status full
!last
```

An unexpected stdout closure now includes the PID, exit code/signal and safe stderr hints. `SIGKILL` can indicate memory pressure, but can also be an administrator/service kill. On the server, correlate the UTC timestamp with:

```bash
journalctl --user -u discord-coding-agent.service --since '30 minutes ago' --no-pager -o short-iso
sudo journalctl -k --since '30 minutes ago' --no-pager
free -h
df -h "$HOME"
```

Look for kernel OOM/killed-process records matching Codex or its tools. Authentication/rate-limit/panic/connection/disk hints are clues, not proof of a cause. Raw stderr is intentionally omitted. An ordinary child failure leaves the Discord bot available; after diagnosing it, send a fresh message to retry explicitly. The bridge never automatically replays uncertain work. If the whole host or Gateway is down, Discord diagnostics cannot respond; use SSH/journal first. See [diagnostics and troubleshooting](troubleshooting.md).
