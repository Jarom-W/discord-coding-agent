# Prepare the Pi, Codex and repository

A Pi 4 with 4 GB RAM is the baseline target. Use **64-bit Raspberry Pi OS or Debian ARM64**; ordinary Linux x86-64 is also targeted. Large builds may exhaust Pi RAM/storage. Model inference happens remotely, while shell tools and builds run on the Pi as your regular Linux account.

## OS, SSH and Python

**On the laptop:** use Raspberry Pi Imager to install a current 64-bit OS, configure your own hostname/user, Wi-Fi or Ethernet, and SSH (prefer keys). Follow the official [Raspberry Pi remote-access guide](https://www.raspberrypi.com/documentation/computers/remote-access.html) for your OS. Do not expose SSH publicly merely for this bridge.

Connect using your actual user and hostname; `pi-user` and `pi-hostname.local` below are placeholders:

```bash
ssh pi-user@pi-hostname.local
```

**On the Pi:**

```bash
uname -m
getconf LONG_BIT
cat /etc/os-release
python3 --version
```

Expect `aarch64` and `64` on an ARM64 Pi, or `x86_64` and `64` on supported ordinary Linux. `armv7l` indicates a 32-bit OS; install a 64-bit OS first. Python must be 3.11 through 3.14; Python 3.13 is tested explicitly. This release does not support 3.10 or claim 3.15 support.

**On the Pi (Debian/Raspberry Pi OS; explicit administrator action):**

```bash
sudo apt update
sudo apt install git python3 python3-venv python3-pip ca-certificates
```

This installs OS prerequisites; the bridge never runs apt, edits sudo policy, changes the firewall, or installs global packages itself. Run the bridge and Codex as a regular user, not root. If `python3 -m venv` fails, install the venv package matching that interpreter. If pip must compile a dependency rather than finding a wheel, the OS may also need `build-essential python3-dev`; examine the build error first.

## Install the checked Codex version separately

First inspect an existing installation **on the Pi**:

```bash
command -v codex
codex --version
```

The checked baseline is **`codex-cli 0.153.4`**. No other CLI version is claimed compatible. This project does not upgrade the user's CLI. The current [Codex CLI installation documentation](https://developers.openai.com/codex/cli) and official [release assets](https://github.com/openai/codex/releases/tag/rust-v0.153.4) are the installation sources; match the Linux ARM64/x86-64 architecture and verify published release checksums where provided.

If you already use npm with a **user-owned** prefix or nvm and `node`/`npm` are on PATH, install a separate pinned copy without replacing the existing executable:

```bash
mkdir -p "$HOME/tools/codex-0.153.4"
npm install --prefix "$HOME/tools/codex-0.153.4" @openai/codex@0.153.4
"$HOME/tools/codex-0.153.4/node_modules/.bin/codex" --version
```

Use that last absolute executable path in `CODEX_EXECUTABLE` during setup. Use a Node.js version supported by the official package. If npm is unavailable, use the official binary release or install Node through your normal OS/user-managed method; do not blindly run a curl-to-shell installer or `sudo npm install -g`. The checked package supports Linux ARM64 and x86-64 distribution; actual Raspberry Pi hardware testing is tracked separately in [compatibility](compatibility.md).

For npm/nvm, both Codex and the required `node` must be in the service environment. Run the service installer from the shell where `codex --version` succeeds. It resolves the configured executable and records that shell's absolute PATH entries. After replacing a Node version, reinstall the generated unit so it does not refer to removed nvm paths.

In the following examples, `codex` means your checked executable. If it is not on PATH, substitute its full quoted path for every `codex` command. That includes login and doctor setup, not just running the bridge.

## Headless authentication

An existing valid Codex login for **the same Linux account and Codex home** can be used. Check **on the Pi**:

```bash
codex login status
```

If needed, use the official [headless authentication flow](https://developers.openai.com/codex/auth):

```bash
codex login --device-auth
```

**On the laptop or phone:** open the URL printed by that trusted CLI, sign in, and enter the one-time device code. Device login may need enabling in ChatGPT security settings or workspace administration; availability is account/policy dependent. If unavailable, follow the official headless-login alternatives using your secure SSH session. Never paste authentication caches, tokens or device codes into Discord, Git, an issue or a support chat.

ChatGPT login and API authentication are different. An eligible ChatGPT login may use plan allowances subject to its terms and limits; no plan or workload coverage is guaranteed here. API key usage is billed separately through the OpenAI Platform account. If you deliberately choose API authentication, obtain the key from your own Platform account, then **on the Pi** enter it without echoing or recording it in shell history:

```bash
read -rsp 'OpenAI API key: ' dca_api_key
printf '\n'
printf '%s' "$dca_api_key" | codex login --with-api-key
unset dca_api_key
codex login status
```

Do not print or share `~/.codex/auth.json`. Codex manages its own authentication; bridge config needs only the Discord bot token. Managed organizational restrictions remain in effect.

## Obtain the coding repository and install the bridge

**On the Pi:** clone your own target separately. Replace the example URL with a repository you may work on:

```bash
mkdir -p "$HOME/work"
git clone https://github.com/YOUR-ACCOUNT/YOUR-TARGET.git "$HOME/work/target"
git -C "$HOME/work/target" status
```

For private repositories, authenticate Git locally through your usual SSH/credential mechanism. Do not embed a token in the Git remote URL. `CODEX_REPO` must be the actual working-tree root, not a subdirectory. Linked worktrees are accepted and included in repository identity.

`CODEX_REPO` is the initial selection. Browse other host directories with `!dirs` and select existing repositories with `!repo PATH` in Discord. Keep projects under configured `WORKSPACE_ROOTS` (default: the initial repository's parent). Each Discord channel may select its own repository and named session; [workspace setup](workspaces.md) covers the commands. This does not create or clone a Git repository automatically.

Then follow the README's installation commands under `~/services/discord-coding-agent`. Keep config/state outside **both** repositories. The code rejects config/state within the selected coding repository and rejects using the bridge installation as the target.

For a safe first trial, create the [disposable demonstration repository](walkthrough.md) instead of selecting important work. A process running as your Linux account is not a confidential-read boundary around that repository: it may read other account-accessible files. A dedicated Linux account/host gives stronger separation.
