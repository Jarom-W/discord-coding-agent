# Create the Discord bot and verify the connection

These steps use labels checked against the official [Discord application setup guide](https://docs.discord.com/developers/quick-start/getting-started) on 2026-09-09. Discord may rearrange its interface. Its HTTP-interactions tutorial includes a public endpoint; **this project uses the outbound Gateway**, so do not follow the ngrok/server/Interactions Endpoint URL portions. Components and ordinary messages arrive on the Gateway.

## 1. Create an application and bot — in the Developer Portal

Open [Discord Developer Portal → Applications](https://discord.com/developers/applications), choose **New Application** / **Create App**, name it (for example TARS), and create it. Record which application you selected. Open **Bot** in its sidebar. New applications normally already have a bot user; if your application offers **Add Bot**, add it. Set the bot's name/avatar there. This project authenticates a bot account, never your personal Discord account.

`DISPLAY_NAME` configures the bridge's greeting/help label; the actual Discord bot username is controlled on this Bot page. A server nickname can also be set manually in Discord. The bridge does not require Manage Nicknames permission.

## 2. Obtain the bot token — in the Developer Portal

On **Bot → Token**, use **Reset Token** and copy the resulting token to a password manager or directly to the hidden setup prompt on the Pi. It may only be shown once. This is **not** the Application ID, Public Key, OAuth Client Secret, user account token, or a webhook URL. Only the bot token goes in `DISCORD_TOKEN`.

Treat the token as a password. Do not paste it into a channel, issue, screenshot, shell command, Git file or this project's example configuration. If exposed, reset it immediately, replace the private config value locally and restart the bridge. The old token stops working.

## 3. Enable Message Content Intent — on that same application

On **Bot → Privileged Gateway Intents**, enable **Message Content Intent**, then **Save Changes**. Leave **Server Members Intent** and **Presence Intent** off. This bot only asks the Gateway for guilds, guild messages and message content; the code sets `intents.message_content = True` to match the portal setting. See [discord.py's intent guide](https://discordpy.readthedocs.io/en/stable/intents.html).

`PrivilegedIntentsRequired` means the requested intent is not enabled/approved for the application identified by the token. Verify the toggle was saved **and** that this is the same application whose Bot token you entered. Enabling it on a different application will not fix the connection. Large verified bots have additional Discord approval requirements; this bridge is scoped to one personal server.

## 4. Configure server installation and permissions — in the Developer Portal

Under **Installation → Installation Contexts**, enable **Guild Install**. User Install is not needed. Under **Install Link**, select **Discord Provided Link**. In **Default Install Settings → Guild Install**, select the **bot** OAuth2 scope. This project uses prefix commands and component buttons; it does not register application/slash commands, so `applications.commands` is unnecessary (Discord may include it by default; it does not enable the Message Content Intent).

Select only these permissions:

- **View Channels** — see the selected channel.
- **Send Messages** — replies and controls.
- **Read Message History** — reconcile an HTTP timeout with a possibly already-sent result.
- **Attach Files** — complete long answers and approval details.

Do not grant Administrator. If using **OAuth2 → URL Generator** instead of Installation, select `bot` and the same permissions. See [OAuth2 scopes](https://docs.discord.com/developers/topics/oauth2) and [Discord permissions](https://docs.discord.com/developers/topics/permissions).

## 5. Install into your server — in a browser/Discord

Copy the installation link, open it, choose **Add to server**, choose a server you manage, and authorize. You need permission to manage/install applications in that server. If the server is absent, check that you are logged into the right Discord account and have **Manage Server** there. Confirm the bot appears in the server member list (offline is normal before the service runs).

## 6. Create and grant access to a private text channel — in Discord

Create a server text channel such as `#coding-agent`, mark it private, and grant your owner account and the bot access. Check **Edit Channel → Permissions**. Deny `@everyone` View Channel and explicitly allow the bot the four permissions above. Also check the parent category: a denied permission or synchronized category override can make the bot invisible or unable to send/attach files even when the invite requested those permissions.

Use a normal text channel, not a DM, thread, forum post, voice channel or announcement channel. Other accounts' messages are ignored even if they can see the channel. Channel privacy still matters because replies and code are visible to anyone with access, including server administrators.

## 7. Copy numeric IDs — in Discord

Enable **User Settings → Advanced → Developer Mode**. Right-click (or long-press / use the context menu on mobile) and choose **Copy User ID** on your own profile, **Copy Server ID** on the server, and **Copy Channel ID** on the text channel. See Discord's [ID instructions](https://support.discord.com/hc/en-us/articles/206346498).

Use those decimal numbers for `DISCORD_OWNER_ID`, `DISCORD_GUILD_ID`, and the initial `DISCORD_CHANNEL_ID`. Usernames, role IDs, channel names, application IDs and invite links are different values. The owner ID is your account, not the bot's account. Additional channels can be bound from Discord after first setup.

## 8. Enter configuration — on the Pi

After [installing the bridge and preparing a separate target repository](pi-codex.md):

```bash
cd "$HOME/services/discord-coding-agent"
.venv/bin/discord-coding-agent setup
```

Enter the values locally. The token input is hidden. Setup validates the repository and writes a mode-600 TOML file in the per-user config directory. Existing config is backed up first. The example file is documentation only. Do not publish the completed file. If using a custom location, put `--config /absolute/path/config.toml` **before** `setup`, `run` or `doctor`.

## 9. Run the connection-only test — on the Pi, then in Discord

**On the Pi:**

```bash
.venv/bin/discord-coding-agent run --connection-only
```

**In the selected Discord channel:** send `!ping`. Expected reply: `TARS: pong — Discord receive/send works; no model invoked` (your label may differ). Ordinary coding text in this mode explains that Codex was not invoked. No Codex executable or login is needed for this test; a valid separate Git repository is still required by configuration validation.

Use Ctrl+C on the Pi to stop the foreground test. Then follow the normal startup in the README and the [walkthrough](walkthrough.md).

## Add another project channel

Create another private normal text channel **in this same server** and repeat the channel/category permission grants in step 6 for the existing bot. As the configured owner, send `!ping`, `!dirs`, then `!repo ~/work/your-other-project`. No extra application, invite, token, channel-ID edit or process is needed. `!sessions`, `!new manual NAME` and `!session NAME` manage that channel's conversations. See [the full workspace guide](workspaces.md). One coding task runs at a time across all channels; `!status` identifies the active channel.

## If ping does not work

- **Invalid token:** obtain a fresh token under Bot, not General Information/OAuth2; update local config and restart. A successful `doctor --discord` checks REST authentication and channel lookup without sending messages.
- **Missing intent / wrong application:** revisit step 3. An empty message body is also a symptom of Message Content Intent problems.
- **Incorrect IDs:** verify your user/server and initial text-channel IDs. Other users/servers are ignored. In an additional private channel, use an explicit command such as `!repo PATH` first; ordinary unbound-channel text is ignored.
- **Invisible channel:** check bot membership and category/channel View Channel overrides.
- **No send or no long reply:** check Send Messages, Read Message History and Attach Files. The journal reports missing permissions; `!last` recovers the saved result after they are fixed.
- **Network:** the Pi needs outbound internet/WebSocket and HTTPS connectivity. No incoming ports, router forwarding or **Interactions Endpoint URL** is used; leave that endpoint blank for this app.
