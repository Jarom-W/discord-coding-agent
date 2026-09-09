"""Owner-controlled channel bindings and named, persistent Codex conversations."""

import asyncio
import json
import math
import os
import re
import sys
import time
import uuid
from collections.abc import Callable
from dataclasses import asdict, dataclass, replace
from pathlib import Path

from .config import Config, outside, private_file, repository_identity
from .engine import HELP, Engine, Origin, Pending, Sink
from .errors import BridgeError
from .locks import Lease
from .state import StateStore, atomic_write, backup

MAX_CHANNELS = 8
MAX_SESSIONS = 64
WORKSPACE_HELP = """Channel workspaces (one owner/server; one coding task across all channels):
!dirs [PATH] — list directories under WORKSPACE_ROOTS; omit PATH to list the roots
!repo PATH — select an existing Git repository in this channel; spaces in paths are allowed
!sessions — list this channel's saved sessions, repositories and modes
!session NAME — return to a saved session and its repository
!name NAME — rename the selected session (names can contain spaces)
!new [auto|manual] [NAME] — create a saved session in the selected repository; old sessions remain available
!status — includes this channel's selection and any task running in another channel
!stop — interrupt the active coding task, including one in another channel
In another private text channel in the same server, grant this bot access and send !repo PATH. No new bot/token is needed.
Repository/session changes are idle-only. Attachments are unsupported. Names are local to a channel; saved Codex history is not deleted.
"""


def session_name(value: str) -> str:
    value = value.strip()
    if not 1 <= len(value) <= 48 or not value.isprintable() or any(c in value for c in "/\\"):
        raise BridgeError("Session names need 1–48 printable characters, without / or \\.")
    return value


@dataclass
class Session:
    id: str
    channel: int
    name: str
    repo: str
    identity: str
    mode: str
    used: float


class SessionSink:
    def __init__(self, record: Session, sink: Sink) -> None:
        self.record, self.sink = record, sink

    @property
    def status(self) -> str:
        return str(getattr(self.sink, "status", ""))

    def text(self, content: str, *, result_id: str | None = None) -> None:
        self.sink.text(f"Session: {self.record.name}\n{content}", result_id=result_id)

    def request(self, pending: Pending) -> None:
        self.sink.request(pending)

    def invalidate(self, pending: Pending) -> None:
        self.sink.invalidate(pending)


class Workspaces:
    def __init__(
        self,
        config: Config,
        legacy: StateStore,
        sink: Callable[[int], Sink],
        *,
        connection_only: bool = False,
    ) -> None:
        self.config, self.legacy, self.sink = config, legacy, sink
        self.connection_only = connection_only
        self.path = legacy.directory / "workspaces.json"
        self.activity_path = legacy.directory / "activity.lock"
        self.sessions: dict[str, Session] = {}
        self.channels: dict[int, str | None] = {}
        self.engines: dict[str, Engine] = {}
        self.seen: list[int] = []
        self.connected = False
        self.load()

    def load(self) -> None:
        if not self.path.exists():
            state = self.legacy.load()
            record = Session(
                "default",
                self.config.channel_id,
                "main",
                str(self.config.repo),
                self.legacy.identity,
                state.mode,
                time.time(),
            )
            self.sessions[record.id] = record
            self.channels[record.channel] = record.id
            self.seen = state.seen_messages.copy()
            # Original state.json remains in place; the catalog only indexes it.
            self.save()
            return
        private_file(self.path)
        try:
            if self.path.stat().st_size > 512 * 1024:
                raise ValueError()
            raw = json.loads(self.path.read_text())
            if not isinstance(raw, dict):
                raise ValueError()
            if raw.get("schema") != 1:
                saved = backup(self.path)
                raise BridgeError(
                    f"Unknown workspace catalog schema; preserved {saved}. Use the matching bridge release."
                )
            if raw["guild"] != self.config.guild_id:
                raise BridgeError(
                    "Workspace catalog belongs to another server. Restore DISCORD_GUILD_ID or use a new STATE_DIR."
                )
            self.sessions = {key: Session(**value) for key, value in raw["sessions"].items()}
            self.channels = {int(key): value for key, value in raw["channels"].items()}
            self.seen = raw["seen"]
            if "default" not in self.sessions:
                raise ValueError()
            if (
                not 1 <= len(self.channels) <= MAX_CHANNELS
                or not 1 <= len(self.sessions) <= MAX_SESSIONS
            ):
                raise ValueError()
            if any(
                type(channel) is not int or not 0 < channel < 2**64 for channel in self.channels
            ):
                raise ValueError()
            if (
                not isinstance(self.seen, list)
                or len(self.seen) > 512
                or any(type(n) is not int or n <= 0 for n in self.seen)
            ):
                raise ValueError()
            names: set[tuple[int, str]] = set()
            for key, record in self.sessions.items():
                if (
                    key != record.id
                    or (key != "default" and not re.fullmatch(r"[0-9a-f]{32}", key))
                    or record.channel not in self.channels
                    or record.mode not in {"manual", "auto"}
                    or not Path(record.repo).is_absolute()
                    or not isinstance(record.identity, str)
                    or session_name(record.name) != record.name
                    or type(record.used) not in {int, float}
                    or not math.isfinite(record.used)
                ):
                    raise ValueError()
                name_key = record.channel, record.name.casefold()
                if name_key in names:
                    raise ValueError()
                names.add(name_key)
            for channel, selected in self.channels.items():
                if selected is not None and (
                    selected not in self.sessions or self.sessions[selected].channel != channel
                ):
                    raise ValueError()
        except (ValueError, TypeError, KeyError, AttributeError) as exc:
            raise BridgeError(
                "Workspace catalog is corrupt; it was not replaced. Back up workspaces.json and see recovery documentation."
            ) from exc

    def save(self) -> None:
        atomic_write(
            self.path,
            json.dumps(
                {
                    "schema": 1,
                    "guild": self.config.guild_id,
                    "channels": self.channels,
                    "sessions": {key: asdict(value) for key, value in self.sessions.items()},
                    "seen": self.seen,
                }
            )
            + "\n",
        )

    def directory(self, record: Session) -> Path:
        return (
            self.legacy.directory
            if record.id == "default"
            else self.legacy.directory / "sessions" / record.id
        )

    def engine(self, record: Session) -> Engine:
        if record.id not in self.engines:
            store = StateStore(self.directory(record), record.identity, record.mode)
            config = replace(
                self.config,
                repo=Path(record.repo),
                channel_id=record.channel,
                state_dir=store.directory,
                workspace_roots=self.config.roots,
            )
            engine = Engine(
                config,
                store,
                SessionSink(record, self.sink(record.channel)),
                connection_only=self.connection_only,
                activity_path=self.activity_path,
            )
            engine.connected = self.connected
            self.engines[record.id] = engine
        return self.engines[record.id]

    def selected(self, channel: int) -> Session | None:
        key = self.channels.get(channel)
        return self.sessions.get(key) if key else None

    def active(self) -> Engine | None:
        return next(
            (
                e
                for e in self.engines.values()
                if e.busy or (e.stop_worker and not e.stop_worker.done())
            ),
            None,
        )

    def permitted(self, origin: Origin) -> bool:
        return origin.owner == self.config.owner_id and origin.guild == self.config.guild_id

    def allowed_path(self, value: str, base: Path | None = None) -> Path:
        path = Path(value).expanduser()
        if not path.is_absolute():
            path = (base or self.config.roots[0]) / path
        path = path.resolve()
        if not any(path.is_relative_to(root) for root in self.config.roots):
            raise BridgeError(
                "Path is outside WORKSPACE_ROOTS. Use !dirs to list roots or change WORKSPACE_ROOTS locally."
            )
        if not path.is_dir():
            raise BridgeError("Directory does not exist or is inaccessible on the host.")
        return path

    def validate_repo(self, value: str) -> tuple[Path, str]:
        path = self.allowed_path(value)
        outside(self.config.path, path, "Configuration")
        outside(self.legacy.directory, path, "State")
        outside(Path(__file__).resolve(), path, "Bridge installation")
        outside(
            Path(os.environ.get("DCA_BOOTSTRAP_PYTHON", sys.executable)).absolute().parent,
            path,
            "Bootstrap installation",
        )
        return path, repository_identity(path)

    def list_dirs(self, value: str, channel: int) -> str:
        if not value:
            return (
                "Workspace roots on the host:\n"
                + "\n".join(str(root) for root in self.config.roots)
                + "\nUse !dirs PATH to browse; !repo PATH selects a Git working-tree root."
            )
        selected = self.selected(channel)
        path = self.allowed_path(value, Path(selected.repo) if selected else None)
        entries: list[str] = []
        with os.scandir(path) as children:
            for index, item in enumerate(children):
                child = Path(item.path)
                if item.is_dir() and any(
                    child.resolve().is_relative_to(root) for root in self.config.roots
                ):
                    entries.append(child.name + "/")
                if len(entries) >= 500 or index >= 9999:
                    entries.append("[listing limit reached; browse a narrower path]")
                    break
        return f"Directories in {path}:\n" + (
            "\n".join(sorted(entries, key=str.casefold)) or "(none)"
        )

    def named(self, channel: int, name: str) -> Session:
        match = next(
            (
                s
                for s in self.sessions.values()
                if s.channel == channel and s.name.casefold() == name.casefold()
            ),
            None,
        )
        if not match:
            raise BridgeError(
                "Unknown session in this channel. Use !sessions; names are local to each channel."
            )
        return match

    def unique_name(self, channel: int, base: str = "session") -> str:
        base = re.sub(r"[^\w .-]", "-", base)[:36].strip() or "session"
        names = {s.name.casefold() for s in self.sessions.values() if s.channel == channel}
        name, number = base, 1
        while name.casefold() in names:
            number += 1
            name = f"{base}-{number}"
        return name

    def add(self, channel: int, name: str, path: Path, identity: str, mode: str) -> Session:
        if len(self.sessions) >= MAX_SESSIONS:
            raise BridgeError(
                f"Maximum {MAX_SESSIONS} saved sessions reached; see workspace limits/recovery documentation."
            )
        name = session_name(name)
        if any(
            s.channel == channel and s.name.casefold() == name.casefold()
            for s in self.sessions.values()
        ):
            raise BridgeError(
                "That session name already exists in this channel. Use !session NAME or choose another name."
            )
        record = Session(uuid.uuid4().hex, channel, name, str(path), identity, mode, time.time())
        self.engine(record)  # Validate state before publishing the selection.
        self.sessions[record.id] = record
        return record

    async def switch(self, channel: int, cmd: str, argument: str) -> str:
        lease = Lease(self.activity_path)
        if self.active() or not lease.acquire():
            raise BridgeError(
                "Cannot change repositories/sessions/modes during active work or maintenance. Wait or use !stop; nothing was changed."
            )
        previous_channels, previous_sessions = self.channels.copy(), self.sessions.copy()
        previous_attributes = {
            key: (record.name, record.used) for key, record in self.sessions.items()
        }
        previous_engines = set(self.engines)
        try:
            selected = self.selected(channel)
            if cmd == "!repo":
                fresh = argument.startswith("--fresh ")
                if fresh:
                    argument = argument[len("--fresh ") :].strip()
                if not argument:
                    raise BridgeError(
                        "Use !repo PATH (an existing Git repository under !dirs roots)."
                    )
                path, identity = await asyncio.to_thread(self.validate_repo, argument)
                candidates = [
                    s
                    for s in self.sessions.values()
                    if s.channel == channel and s.repo == str(path) and not fresh
                ]
                if candidates:
                    target = max(candidates, key=lambda s: s.used)
                    if target.identity != identity:
                        raise BridgeError(
                            "Saved repository identity changed. Restore it or deliberately use !repo --fresh PATH for a new session; old history was not rebound."
                        )
                else:
                    target = self.add(
                        channel,
                        self.unique_name(channel, path.name),
                        path,
                        identity,
                        self.config.mode,
                    )
            elif cmd == "!session":
                target = self.named(channel, argument)
                path, identity = await asyncio.to_thread(self.validate_repo, target.repo)
                if identity != target.identity:
                    raise BridgeError(
                        "Saved repository identity changed; session was not switched. Restore the repository or see recovery documentation."
                    )
            elif cmd == "!new":
                if not selected:
                    raise BridgeError("Select a repository first with !repo PATH.")
                parts = argument.split(maxsplit=1)
                mode = self.engine(selected).state.mode
                if parts and parts[0] in {"auto", "manual"}:
                    mode = parts[0]
                    name = parts[1] if len(parts) == 2 else self.unique_name(channel)
                else:
                    name = argument or self.unique_name(channel)
                path, identity = await asyncio.to_thread(self.validate_repo, selected.repo)
                target = self.add(channel, name, path, identity, mode)
            else:  # !name
                if not selected:
                    raise BridgeError("Select a repository first with !repo PATH.")
                name = session_name(argument)
                if any(
                    s.channel == channel
                    and s.id != selected.id
                    and s.name.casefold() == name.casefold()
                    for s in self.sessions.values()
                ):
                    raise BridgeError("That name already exists in this channel.")
                selected.name = name
                target = selected
            self.engine(target)
            target.used = time.time()
            self.channels[channel] = target.id
            self.save()
            return f"Selected session: {target.name}\nRepository: {target.repo}\nMode: {self.engine(target).state.mode}; thread: {self.engine(target).state.thread_id or 'created on first task'}. Use !sessions to return to earlier work."
        except BaseException:
            self.channels, self.sessions = previous_channels, previous_sessions
            for key, (name, used) in previous_attributes.items():
                self.sessions[key].name, self.sessions[key].used = name, used
            for key in set(self.engines) - previous_engines:
                self.engines.pop(key).activity.close()
            raise
        finally:
            lease.close()

    async def message(self, origin: Origin, content: str, attachments: bool = False) -> None:
        if not self.permitted(origin) or origin.message in self.seen:
            return
        if origin.channel not in self.channels:
            if not content.strip().startswith("!"):
                return
            if len(self.channels) >= MAX_CHANNELS:
                self.sink(self.config.channel_id).text(
                    f"Channel limit ({MAX_CHANNELS}) reached; channel {origin.channel} was not bound. See workspace limits in the guide."
                )
                return  # Do not allocate unbounded delivery workers for arbitrary channels.
            self.channels[origin.channel] = None
        self.seen = (self.seen + [origin.message])[-512:]
        self.save()
        sink = self.sink(origin.channel)
        if attachments:
            sink.text(
                "Attachments are unsupported and were not read. Nothing was submitted. Send text or a repository path."
            )
            return
        parts = content.strip().split(maxsplit=1)
        if not parts:
            return
        cmd, argument = parts[0].lower(), parts[1] if len(parts) == 2 else ""
        try:
            if cmd in {"!help", "!sessions"} and argument:
                raise BridgeError(
                    f"{cmd} takes no arguments; use !session NAME to select a session."
                )
            if cmd == "!help":
                sink.text(WORKSPACE_HELP + "\n" + HELP)
            elif cmd == "!dirs":
                sink.text(
                    await asyncio.wait_for(
                        asyncio.to_thread(self.list_dirs, argument, origin.channel), 10
                    )
                )
            elif cmd in {"!repo", "!session", "!name", "!new"}:
                sink.text(await self.switch(origin.channel, cmd, argument))
            elif cmd == "!sessions":
                selected = self.selected(origin.channel)
                rows = [
                    f"{'*' if selected and s.id == selected.id else '-'} {s.name} | {s.repo} | {self.engine(s).state.mode} | thread {self.engine(s).state.thread_id or 'not created'}"
                    for s in self.sessions.values()
                    if s.channel == origin.channel
                ]
                sink.text(
                    "Saved sessions in this channel (* selected):\n"
                    + ("\n".join(rows) or "None; use !repo PATH.")
                )
            elif cmd == "!stop" and self.active():
                active = self.active()
                assert active
                await active.command(origin, content.strip())
                if origin.channel != active.config.channel_id:
                    sink.text(f"Stop requested for the task in channel {active.config.channel_id}.")
            else:
                selected = self.selected(origin.channel)
                if cmd == "!ping" and not argument:
                    sink.text(
                        f"{self.config.display_name}: pong — Discord receive/send works; no model invoked."
                    )
                elif not selected:
                    sink.text(
                        "This channel has no repository/session yet. Use !dirs, then !repo PATH. Only the configured owner can bind channels in this server."
                    )
                else:
                    engine = self.engine(selected)
                    if cmd == "!status":
                        active = self.active()
                        sink.text(
                            f"Channel session: {selected.name}\nRepository: {selected.repo}\nActive coding channel: {active.config.channel_id if active else 'none'}"
                        )
                    await engine.message(origin, content, attachments)
        except (BridgeError, OSError, TimeoutError) as exc:
            sink.text(
                str(exc)
                if isinstance(exc, BridgeError)
                else "Directory/workspace operation failed or exceeded 10 seconds. Check local filesystem access and !dirs roots; no coding request was submitted."
            )

    def channel_engines(self, channel: int) -> list[Engine]:
        return [e for e in self.engines.values() if e.config.channel_id == channel]

    async def close(self) -> None:
        await asyncio.gather(*(engine.close() for engine in self.engines.values()))
