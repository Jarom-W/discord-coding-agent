"""Crash-safe state and a kernel-owned lock (never unlink a live lock inode)."""

import fcntl
import json
import os
import shutil
import tempfile
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from .config import private_file
from .errors import BridgeError

MAX_RESULT = 4 * 1024 * 1024


def private_dir(path: Path) -> None:
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    if path.is_symlink() or path.stat().st_uid != os.getuid():
        raise BridgeError(f"{path}: directory must be owned by this user and not a symlink.")
    path.chmod(0o700)


def atomic_write(path: Path, content: str) -> None:
    private_dir(path.parent)
    fd, temporary = tempfile.mkstemp(prefix=".write-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        parent = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(parent)
        finally:
            os.close(parent)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def backup(path: Path) -> Path:
    target = path.with_name(f"{path.name}.backup-{time.time_ns()}")
    shutil.copyfile(path, target)
    target.chmod(0o600)
    return target


@dataclass
class State:
    repo_identity: str
    mode: str = "manual"
    schema: int = 1
    thread_id: str | None = None
    interrupted: bool = False
    active: dict[str, Any] | None = None
    last_interruption: dict[str, Any] | None = None
    last_result: str | None = None
    result_id: str | None = None
    delivered: bool = True
    seen_messages: list[int] = field(default_factory=list)
    controls: list[int] = field(default_factory=list)


class StateStore:
    def __init__(self, directory: Path, identity: str, mode: str) -> None:
        self.directory, self.identity, self.mode = directory, identity, mode
        self.path = directory / "state.json"
        self.fd: int | None = None

    def acquire(self) -> None:
        private_dir(self.directory)
        path = self.directory / "process.lock"
        fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            os.close(fd)
            raise BridgeError(
                "Another bridge owns the state lock. Inspect service status; do not delete the lock file."
            ) from exc
        self.fd = fd
        os.ftruncate(fd, 0)
        os.write(fd, str(os.getpid()).encode())

    def close(self) -> None:
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None

    def load(self, *, recover: bool = True) -> State:
        if not self.path.exists():
            return State(self.identity, self.mode)
        private_file(self.path)
        if self.path.stat().st_size > 32 * 1024 * 1024:
            raise BridgeError(
                "State exceeds 32 MiB. Preserve it and see state recovery documentation."
            )
        try:
            raw = json.loads(self.path.read_text())
            if not isinstance(raw, dict):
                raise ValueError()
            if raw.get("schema") != 1:
                # No published predecessor exists. Never guess a migration or discard old data.
                saved = backup(self.path) if recover else self.path
                raise BridgeError(
                    f"Unsupported state schema; preserved {saved}. Use the matching release or documented recovery."
                )
            state = State(**raw)
            if state.mode not in {"auto", "manual"} or not isinstance(state.repo_identity, str):
                raise ValueError()
            if state.thread_id is not None and (
                not isinstance(state.thread_id, str) or not state.thread_id
            ):
                raise ValueError()
            for record in [state.active, state.last_interruption]:
                if record is not None and not isinstance(record, dict):
                    raise ValueError()
            for value in [state.interrupted, state.delivered]:
                if not isinstance(value, bool):
                    raise ValueError()
            for text_value in [state.last_result, state.result_id]:
                if text_value is not None and not isinstance(text_value, str):
                    raise ValueError()
            for numbers, limit in [(state.seen_messages, 512), (state.controls, 32)]:
                if (
                    not isinstance(numbers, list)
                    or len(numbers) > limit
                    or any(type(n) is not int or n <= 0 for n in numbers)
                ):
                    raise ValueError()
        except (ValueError, TypeError, OSError) as exc:
            raise BridgeError(
                "State is corrupt; it was not overwritten. Stop the service, back it up and see docs/troubleshooting.md."
            ) from exc
        if state.repo_identity != self.identity:
            raise BridgeError(
                "State repository identity mismatch. Restore the original repository or choose a new STATE_DIR."
            )
        if state.active and recover:
            state.interrupted = True
            state.last_interruption = state.active
            state.active = None
            self.save(state)
        return state

    def save(self, state: State) -> None:
        atomic_write(self.path, json.dumps(asdict(state), ensure_ascii=False) + "\n")
