# systemd user service, updates and removal

These commands run **on the Pi as the same regular user who installed/authenticated Codex**. Do not run the bridge with sudo. Complete foreground `!ping` and a Codex conversation first. The service installer only manages units bearing this project's marker and refuses unmanaged units, including an unrelated existing TARS service.

## Install and start

```bash
cd "$HOME/services/discord-coding-agent"
.venv/bin/discord-coding-agent service render
.venv/bin/discord-coding-agent service validate
.venv/bin/discord-coding-agent service install
.venv/bin/discord-coding-agent service enable
.venv/bin/discord-coding-agent service start
.venv/bin/discord-coding-agent service status
```

Expected validation: `systemd-analyze --user verify: OK` (offline verification falls back without `--user` if no user manager is available). Installation writes `~/.config/systemd/user/discord-coding-agent.service`, reloads the user manager, and reports its path. It never enables/starts implicitly. The installer is idempotent; changed managed units are backed up before replacement.

The unit records the exact installed Python interpreter and Codex executable, and captures absolute PATH entries from the installing shell, including active npm/nvm bin directories. It does not run `.bashrc` or rewrite Codex settings. `WorkingDirectory` is an unquoted path-valued directive; `ExecStart` arguments and Environment values have separate escaping. Percent specifiers are escaped. Unit tests validate spaces, quotes, percent, dollar and backslash paths. Control characters are rejected.

Use a distinct config/state/unit for another personal installation:

```bash
.venv/bin/discord-coding-agent --config "$HOME/.config/discord-coding-agent-demo/config.toml" service install --unit-name discord-coding-agent-demo.service
```

That config must also set a distinct `STATE_DIR`; do not run two instances against the same repository or token/channel. No multi-instance coordination across different state directories is implemented.

## Start at boot and after logout

`enable` starts the unit with the user manager; **linger** starts/keeps the user manager without an interactive login. **On the Pi**, request this explicit administrator action if your machine's policy requires it:

```bash
sudo loginctl enable-linger "$USER"
loginctl show-user "$USER" -p Linger
```

Expect `Linger=yes`. The installer does not invoke sudo or change linger for you. A user-bus failure often means you used `sudo`, `su`, cron, or an SSH environment without a user systemd session. Log in directly as the service user and check `loginctl user-status "$USER"`. On normal Debian/Pi OS the login should establish `/run/user/UID` and the user bus. Do not blindly hard-code someone else's UID or bus address.

## Daily operations and full logs

```bash
systemctl --user status discord-coding-agent.service --no-pager -l
systemctl --user restart discord-coding-agent.service
systemctl --user stop discord-coding-agent.service
systemctl --user start discord-coding-agent.service
journalctl --user -u discord-coding-agent.service -n 200 --no-pager -o short-iso
journalctl --user -u discord-coding-agent.service -f -o short-iso
systemctl --user cat discord-coding-agent.service
```

`status` is a summary; `journalctl` gives full bridge logs. Restart while idle whenever possible. During active work the service attempts interrupt, then terminates its own process group; cancellation cannot undo effects. After a crash/restart, previous uncertain work is marked interrupted and never automatically repeated. The last completed result is retained separately.

The unit restarts on process failure with bounded systemd start bursts. Ordinary Codex task errors are visible through Discord and `!status`; they do not require cycling the Gateway process. Fix persistent auth/config errors, then `systemctl --user reset-failed discord-coding-agent.service` and start again.

## Update without losing credentials or conversation

Wait for idle (`!status`) or use `!stop` first. **On the Pi:**

```bash
cd "$HOME/services/discord-coding-agent"
systemctl --user stop discord-coding-agent.service
umask 077
dca_backup="$HOME/bridge-backup-$(date +%Y%m%d-%H%M%S)"
mkdir -p "$dca_backup"
cp -a "$HOME/.config/discord-coding-agent" "$dca_backup/config"
cp -a "$HOME/.local/state/discord-coding-agent" "$dca_backup/state"
cp -a "$HOME/.config/systemd/user/discord-coding-agent.service" "$dca_backup/"
git rev-parse HEAD > "$dca_backup/previous-commit.txt"
git status --short
git fetch origin
git log --oneline HEAD..origin/main
```

These examples assume default paths; substitute configured XDG/custom paths. Keep the backup private: it includes your token and last result. Review release notes. If `git status` shows local changes, preserve/reconcile them before updating; never use a destructive reset. With a clean tree and a reviewed update:

```bash
git merge --ff-only origin/main
.venv/bin/python -m pip install --require-hashes -r requirements.lock
.venv/bin/python -m pip install --no-deps --force-reinstall .
.venv/bin/discord-coding-agent doctor --probe
.venv/bin/discord-coding-agent service install
systemctl --user start discord-coding-agent.service
```

The installed package is a non-editable copy: updating Git alone does not update running code. Run the pip reinstall. Do not rerun setup unless changing config. This procedure leaves config/state/auth untouched; it does not upgrade Codex. Review its compatibility separately.

## Rollback

Stop the bridge. Find your actual backup directory; below it is stored in `dca_backup`. Return to the recorded commit without discarding uncommitted work, then reinstall that release:

```bash
systemctl --user stop discord-coding-agent.service
cd "$HOME/services/discord-coding-agent"
git switch --detach "$(cat "$dca_backup/previous-commit.txt")"
.venv/bin/python -m pip install --require-hashes -r requirements.lock
.venv/bin/python -m pip install --no-deps --force-reinstall .
.venv/bin/discord-coding-agent service install
systemctl --user start discord-coding-agent.service
```

If a newer release changed state schema, consult its migration notes before restoring the saved state directory. Keep the current state as a second private backup. Never restore state while a service owns its lock, and never assume restoring state reverts repository edits or remote actions. This initial release has schema 1 and refuses unknown schemas, preserving a timestamped backup instead of guessing a migration.

## Uninstall

**On the Pi:**

```bash
cd "$HOME/services/discord-coding-agent"
.venv/bin/discord-coding-agent service uninstall
```

This stops/disables only the managed bridge unit, backs it up, removes it and reloads systemd. It leaves config, state, the bridge checkout, target coding repository and Codex authentication intact. You may later remove the bridge directory and private config/state after inspecting and backing them up. Never remove the target repository or `~/.codex` as part of uninstall. Disable linger with `sudo loginctl disable-linger "$USER"` only if no other user services need it; this project does not do that automatically.
