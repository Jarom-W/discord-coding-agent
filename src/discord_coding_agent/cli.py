"""Small stdlib CLI: setup, run, doctor and service management."""

import argparse
import asyncio
import getpass
import importlib.metadata
import json
import logging
import os
import platform
import signal
import sys
from dataclasses import replace
from pathlib import Path

import discord

from . import __version__, deployment, protocol, service
from .config import Config, config_path, discover_codex, repository_identity
from .discord_client import BridgeClient
from .errors import BridgeError, log_error
from .rpc import Rpc
from .state import StateStore, atomic_write, backup


def setup(path: Path) -> None:
    if path.exists():
        print(f"Existing configuration preserved in {backup(path)}")
    print(
        "Enter values locally. The bot token is hidden. No Codex/Discord request is sent by setup."
    )
    values = {
        "DISCORD_TOKEN": getpass.getpass(
            "Bot token (Developer Portal > Bot > Reset Token): "
        ).strip(),
        "DISCORD_OWNER_ID": input("Owner numeric user ID: ").strip(),
        "DISCORD_GUILD_ID": input("Server numeric ID: ").strip(),
        "DISCORD_CHANNEL_ID": input("Initial private text channel numeric ID: ").strip(),
        "CODEX_REPO": str(
            Path(input("Initial Git repository absolute path (separate from bridge): ").strip())
            .expanduser()
            .resolve()
        ),
        "CODEX_EXECUTABLE": input("Codex executable [codex]: ").strip() or "codex",
        "DISPLAY_NAME": input("Display label [Coding Agent; e.g. TARS]: ").strip()
        or "Coding Agent",
        "APPROVAL_MODE": "manual",
    }
    if path.resolve().is_relative_to(Path(values["CODEX_REPO"])):
        raise BridgeError("Configuration cannot be stored inside the coding repository.")
    atomic_write(
        path,
        "# Private configuration; do not commit or share.\n"
        + "\n".join(f"{key} = {json.dumps(value)}" for key, value in values.items())
        + "\n",
    )
    Config.load(path)
    print(
        f"Saved and validated {path} (mode 600). Run: discord-coding-agent run --connection-only; then !ping in Discord."
    )


async def doctor(config: Config, probe: bool, discord_check: bool) -> bool:
    print(
        f"Bridge {__version__}; Python {platform.python_version()}; {platform.system()} {platform.machine()}; discord.py {importlib.metadata.version('discord.py')}"
    )
    print(f"Config: {config.path}\nRepository: {config.repo}\nState: {config.state_dir}")
    print("Workspace roots: " + ", ".join(str(root) for root in config.roots))
    print(
        f"Workspace catalog: {config.state_dir / 'workspaces.json'}; updater settings present: {deployment.settings_path(config).exists()}"
    )
    print(
        f"Configured mode: {config.mode}; Codex baseline: {protocol.BASELINE}; token: present (hidden)"
    )
    executable = discover_codex(config.codex)
    print(
        f"Codex executable: {executable}; node on service PATH must be available for npm installations"
    )
    print(await protocol.version(executable, config.timeouts.initialization))
    # Atomic state replacement allows a read-only snapshot without taking the service lock.
    state = StateStore(config.state_dir, repository_identity(config.repo), config.mode).load(
        recover=False
    )
    print(
        f"Persisted selected mode: {state.mode}; thread present: {bool(state.thread_id)}; interrupted or active: {state.interrupted or bool(state.active)}"
    )
    if probe:
        rpc = Rpc(
            config.timeouts,
            lambda *_: None,
            lambda rid, *_: rpc.reply(rid, error="Diagnostic client does not grant requests"),
            lambda _: None,
        )
        try:
            await rpc.start(
                protocol.argv(executable, state.mode), config.repo, protocol.child_environment()
            )
            await protocol.initialize(rpc)
            await protocol.verify_process(rpc, config.repo, state.mode)
            account = await rpc.call("account/read", {"refreshToken": False})
            authenticated = account.get("account") is not None
            print(
                f"Stdio initialize/config/read: OK; process reviewer verified. Account present: {authenticated} (identity omitted)."
            )
            print(
                "No thread or model task started; thread-level reviewer and actual runtime decisions are not verified by doctor."
            )
        finally:
            await rpc.close()
    if discord_check:
        client = discord.Client(intents=discord.Intents.none())
        try:
            await asyncio.wait_for(client.login(config.token), config.timeouts.initialization)
            channel = await asyncio.wait_for(
                client.fetch_channel(config.channel_id), config.timeouts.delivery
            )
            print(
                f"Discord REST login/channel lookup: OK; correct guild: {getattr(getattr(channel, 'guild', None), 'id', None) == config.guild_id}."
            )
            print("REST is not a Gateway/intent/send test. Run --connection-only and !ping next.")
        finally:
            await client.close()
    print(
        "Use journalctl --user -u discord-coding-agent.service -n 200 --no-pager -o short-iso for sanitized bridge logs."
    )
    return True


async def run(config: Config, connection_only: bool) -> None:
    store = StateStore(config.state_dir, repository_identity(config.repo), config.mode)
    store.acquire()
    try:
        client = BridgeClient(config, store, connection_only)
        loop = asyncio.get_running_loop()
        shutdown = asyncio.Event()
        for sig in [signal.SIGINT, signal.SIGTERM]:
            loop.add_signal_handler(sig, shutdown.set)
        async with client:
            connection = asyncio.create_task(client.start(config.token, reconnect=True))
            stopped = asyncio.create_task(shutdown.wait())
            try:
                done, _ = await asyncio.wait(
                    [connection, stopped], return_when=asyncio.FIRST_COMPLETED
                )
                if connection in done:
                    await connection
            finally:
                await client.close()
                connection.cancel()
                stopped.cancel()
                await asyncio.gather(connection, stopped, return_exceptions=True)
    finally:
        store.close()


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(
        description="Independent owner-only Discord bridge to Codex app-server."
    )
    root.add_argument("--version", action="version", version=__version__)
    root.add_argument(
        "--config",
        type=Path,
        default=config_path(),
        help="Private TOML configuration path (before subcommand)",
    )
    sub = root.add_subparsers(dest="command", required=True)
    sub.add_parser(
        "setup", help="Interactively write private configuration, backing up existing config"
    )
    runner = sub.add_parser("run", help="Run the Discord Gateway bridge in the foreground")
    runner.add_argument(
        "--connection-only", action="store_true", help="Discord commands only; never start Codex"
    )
    diagnostic = sub.add_parser(
        "doctor", help="Sanitized versions/path diagnostics; opt-in connection probes"
    )
    diagnostic.add_argument(
        "--probe",
        action="store_true",
        help="Start owned Codex child for non-model initialize/config/account checks",
    )
    diagnostic.add_argument(
        "--discord",
        action="store_true",
        help="Check token and channel via Discord REST without sending messages",
    )
    manager = sub.add_parser("service", help="Manage only a bridge-owned systemd user unit")
    manager.add_argument(
        "action",
        choices=[
            "render",
            "validate",
            "install",
            "start",
            "stop",
            "restart",
            "enable",
            "disable",
            "status",
            "uninstall",
        ],
    )
    manager.add_argument("--unit-name", default=service.DEFAULT_UNIT)
    deploy = sub.add_parser("deploy", help="Opt-in pull deployment of CI-verified main commits")
    deploy.add_argument(
        "action",
        choices=["install", "enable", "disable", "check", "status", "rollback", "uninstall"],
    )
    deploy.add_argument(
        "--repository",
        help="Public GitHub OWNER/REPO trusted for updates; required on first install",
    )
    deploy.add_argument(
        "--directory", type=Path, help="Separate directory for prepared releases and backups"
    )
    deploy.add_argument(
        "--unit-name",
        help="Existing managed bot service (install only)",
    )
    deploy.add_argument(
        "--retry", action="store_true", help="Retry the currently failed revision (check only)"
    )
    return root


def deploy_command(config: Config, args: argparse.Namespace) -> None:
    if args.retry and args.action != "check":
        raise BridgeError("--retry is only supported with deploy check.")
    if args.action != "install" and (args.repository or args.directory or args.unit_name):
        raise BridgeError(
            "Repository/directory/unit options are only supported with deploy install."
        )
    if args.action == "install":
        existing = deployment.settings_path(config).exists()
        if existing:
            settings = deployment.Settings.load(config)
            settings = replace(
                settings,
                repository=args.repository or settings.repository,
                directory=args.directory.expanduser().absolute()
                if args.directory
                else settings.directory,
                unit=args.unit_name or settings.unit,
            )
        else:
            if not args.repository:
                raise BridgeError(
                    "Use deploy install --repository OWNER/REPO; public GitHub repositories only."
                )
            directory = args.directory or (
                Path(os.environ.get("XDG_DATA_HOME", str(Path.home() / ".local/share")))
                / "discord-coding-agent"
                / (args.unit_name or service.DEFAULT_UNIT).removesuffix(".service")
            )
            settings = deployment.Settings(
                args.repository,
                directory.expanduser().absolute(),
                args.unit_name or service.DEFAULT_UNIT,
            )
        deployment.install(config, settings)
        print(
            f"Updater installed for {settings.repository}; timer is not enabled/started automatically. Run deploy check, then deploy enable. Settings: {deployment.settings_path(config)}"
        )
        return
    settings = deployment.Settings.load(config)
    if args.action in {"enable", "disable", "uninstall"}:
        deployment.manage(args.action, settings)
        print(
            f"Updater {args.action} complete. Bot config, sessions, repositories, releases and Codex authentication retained."
        )
    else:
        updater = deployment.Updater(config, settings)
        if args.action == "status":
            print(
                f"Repository: {settings.repository}; branch: main; CI: ci.yml push runs\nReleases/backups: {settings.directory}\nTimer: {deployment.unit_names(settings)[1]}; interval {settings.interval:g}s plus jitter"
            )
            print(
                f"Current: {updater.data['current']}; previous: {updater.data['previous']}; failed: {updater.data['failed']}; interrupted deployment: {bool(updater.data['transaction'])}"
            )
        else:
            print(updater.check(retry=args.retry, rollback=args.action == "rollback"))


def main() -> None:
    args = parser().parse_args()
    os.umask(0o077)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)sZ %(levelname)s %(name)s %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )
    import time

    logging.Formatter.converter = time.gmtime
    logging.getLogger("discord").setLevel(logging.CRITICAL)
    logging.getLogger("aiohttp").setLevel(logging.CRITICAL)
    try:
        if args.command == "setup":
            setup(args.config.expanduser().absolute())
        elif args.command == "service" and args.action not in {"render", "validate", "install"}:
            if args.action == "uninstall":
                service.uninstall(args.unit_name)
            else:
                service.ensure_managed(service.location(args.unit_name))
                service.control(args.action, args.unit_name)
        else:
            config = Config.load(args.config)
            if args.command == "run":
                asyncio.run(run(config, args.connection_only))
            elif args.command == "doctor":
                asyncio.run(doctor(config, args.probe, args.discord))
            elif args.command == "deploy":
                deploy_command(config, args)
            elif args.command == "service":
                if args.action == "render":
                    print(service.render(config))
                elif args.action == "validate":
                    service.verify(service.render(config), args.unit_name)
                    print("systemd-analyze --user verify: OK")
                elif args.action == "install":
                    print(
                        f"Installed {service.install(config, args.unit_name)}; use service enable and service start explicitly."
                    )
    except discord.PrivilegedIntentsRequired:
        print(
            "Message Content Intent is required. Enable and SAVE it under Bot in the SAME Developer Portal application as this token, then restart.",
            file=sys.stderr,
        )
        raise SystemExit(1) from None
    except discord.LoginFailure:
        print(
            "Invalid bot token. Reset Token in Developer Portal > Bot, update private config, restart; never share the token.",
            file=sys.stderr,
        )
        raise SystemExit(1) from None
    except BridgeError as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(1) from None
    except KeyboardInterrupt:
        pass
    except Exception as exc:
        log_error(logging.getLogger(__name__), "cli", exc)
        print(
            "Operation failed; inspect sanitized logs and run doctor. No credentials are printed.",
            file=sys.stderr,
        )
        raise SystemExit(1) from None
