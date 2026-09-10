# Channel workspaces and named sessions

One owner account controls the bot in one configured server. Up to eight text channels can hold selections, with 64 saved sessions across the bot. There is one coding task at a time, including initialization and approval/question waits. This protects shared checkouts even when two channels select the same repository.

## One-time host setup — on the Pi

Follow the README quickstart, or [update the existing managed bridge](service.md#update-without-losing-credentials-or-conversation) while idle. `DISCORD_CHANNEL_ID` and `CODEX_REPO` are the initial channel/repository, retained for bootstrap and recovery; use Discord commands to switch projects and sessions. Keep the initial repository accessible. Keep the bridge installation, config and state outside coding repositories.

By default, repositories under the initial `CODEX_REPO`'s parent may be selected. For example, `/home/me/work/project-a` permits browsing/selecting under `/home/me/work`. To allow additional locations, edit your **private** `~/.config/discord-coding-agent/config.toml` (substitute your actual config path) and add a top-level setting **before `[timeouts]`**:

```toml
WORKSPACE_ROOTS = ["/home/YOUR-USER/work", "/srv/projects"]
```

Replace those placeholders with existing directories. Include the initial repository in these roots. Paths are resolved, including symlinks; a symlink cannot make `!dirs`/`!repo` escape the roots. The environment override `WORKSPACE_ROOTS` uses a JSON array of strings, but local TOML is easier. Restart while idle after changing roots. `doctor` prints effective roots without the bot token.

Roots govern bridge directory browsing/selection. They are **not a confidential-read boundary for Codex**: tools still run as the Linux account and can access other files that account can read. Choose a dedicated account/host for stronger isolation.

## Browse and select — in Discord

```text
!dirs
!dirs ~/work
!repo ~/work/project-a
!status
```

`!dirs` with no path lists roots. With a path it lists immediate subdirectories, including hidden directories, but never file contents. It scans at most 10,000 entries and returns at most 500 directories, with a notice when that bound is reached. Long listings appear as multiple inline chat messages. Absolute paths and `~` refer to the **Pi's service user**, not your laptop or Discord device; `$HOME` is not expanded in Discord. Relative `!dirs` paths start from the selected repository (or the first root in an unbound channel). Relative `!repo` paths start from the first root. Spaces need no quotes: `!repo ~/work/My Project` works.

Selection requires an existing Git working-tree root. A subdirectory inside a repository or an ordinary non-Git folder is rejected clearly. To start a repository, create/clone it through your normal host tools first; for example **on the Pi**:

```bash
mkdir -p "$HOME/work/new-project"
git -C "$HOME/work/new-project" init
```

Then send `!repo ~/work/new-project`. Selecting a repository previously used in this channel returns to its most recently selected saved session. Selecting a new repository creates a named session based on its directory name, adding a suffix for name collisions. No coding/model request is started by directory browsing or selection.

Use `!repo --fresh PATH` to explicitly create a separate saved session even when this channel already has history for that path, including after a deliberate repository replacement. It does not delete or rebind earlier history.

## Name, create and return to conversations — in Discord

```text
!name backend fixes
!new auto release planning
!sessions
!session backend fixes
!last
```

`!name` renames the current session without changing its thread. `!new [auto|manual] [NAME]` creates a new saved conversation in the selected repository; omitted mode uses the current session's mode and omitted name gets a unique generated name. `!sessions` lists this channel's names, repositories, modes and thread IDs, marking the selected one. `!session NAME` returns to that conversation, repository and mode; future ordinary messages resume its thread. `!last` refers to the selected session's saved result.

Names are case-insensitive within a channel and must have 1–48 printable characters, excluding `/` and `\`. Names can contain spaces. A name can be reused in another channel because channels have separate session lists. Nothing imports unrelated Codex CLI conversations automatically; this list contains conversations created or retained by this bridge.

Prose such as “open a new chat” is passed to Codex and does not change bridge routing. Use `!new`, `!session` or `!repo` explicitly. All selection/name/mode changes are rejected while **any channel** is running work or maintenance holds the shared lock. Busy coding messages are not queued and report that they were not submitted. `!stop` can interrupt the active task from another bound channel; approval buttons/text answers remain restricted to the request's own channel and active turn.

## Add a channel — in Discord

1. In the same server, create a private **normal text channel** such as `#agent-project-b`.
2. Grant your owner account access. Grant the existing bot **View Channels, Send Messages, Read Message History**. Check category overrides too; [the Discord guide](discord.md) shows these steps.
3. Send `!ping`, then `!repo ~/work/project-b` as the configured owner. The channel acquires its own selected workspace and sessions. You do not copy a token, install a second application, or restart the Pi service.
4. Send a read-only repository question, wait for completion, then `!name project-b planning`.

Other users, other servers, DMs, Discord threads, forum posts and announcement channels cannot start work. Ordinary text in a previously unbound channel is ignored; an explicit command begins setup. Limit bot access to the channels you intend to use. Anyone who can read a channel can read its answers, so channel privacy still matters even with owner-only controls.

## Persistence and recovery

The private `STATE_DIR/workspaces.json` catalog stores channel selections, session names, repository identities and recent owner message IDs. The initial channel’s `main` session uses `STATE_DIR/state.json`. New sessions use `STATE_DIR/sessions/SESSION_ID/state.json`; their IDs are opaque and are not filesystem paths supplied by Discord users. Codex still owns its separate thread/history storage. No state-schema rewrite discards the original thread or result.

After restart, select a session with `!session NAME` to continue it. An interrupted task is never replayed. Check its repository with `git status`/`git diff` before a new prompt. Repository moves/recreation can invalidate the saved identity; old thread history is not rebound to a different repository silently. Restore the original directory/Git identity or deliberately use `!repo --fresh PATH` for new history. `!repo` without `--fresh` and `!session` refuse an old identity; a newly created session gets the current identity.

For damaged catalog/state, stop the service and privately back up the **whole** state directory. Corruption fails clearly; unknown catalog schemas receive a backup. Do not delete a live process/activity lock. Restore a known-good matching catalog and session files, or deliberately choose a new `STATE_DIR` to start clean. This does not undo edits or delete Codex history. See [general recovery](troubleshooting.md).

Current bounds are eight channel entries (including channels in setup) and 64 sessions, with 32 queued outbound jobs per channel. No session/channel deletion or automatic history pruning is implemented. If you reach the limit, preserve the existing state and choose a new state directory deliberately; the old catalog remains recoverable. Config/state are private, and the product has no multi-owner/organization administration system.

For rollback, preserve the full catalog and session directories and check the target release’s configuration/schema compatibility in the [release notes](../CHANGELOG.md). Follow the [rollback procedure](service.md#rollback); never use rollback to replay interrupted coding work.
