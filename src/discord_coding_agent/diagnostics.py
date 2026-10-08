"""Bounded operational logs and read-only host measurements; no raw RPC payloads."""

import contextlib
import logging
import os
import platform
import re
import shutil
import time
from collections import deque
from io import TextIOWrapper
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import cast

from .config import private_file
from .errors import BridgeError
from .state import private_dir

MAX_LOG_BYTES = 256 * 1024


def sanitize(text: str, token: str = "") -> str:
    if token:
        text = text.replace(token, "[redacted]")
    text = re.sub(
        r"(?i)(bearer\s+|(?:token|api[_-]?key|password|secret)=)\S+", r"\1[redacted]", text
    )
    text = re.sub(r"sk-[A-Za-z0-9_-]+", "[redacted]", text)
    return " ".join(text.replace("`", "'").split())[:1000]


class LogFile(RotatingFileHandler):
    def _open(self) -> TextIOWrapper:
        def opener(path: str, flags: int) -> int:
            return os.open(path, flags | os.O_NOFOLLOW, 0o600)

        return cast(
            TextIOWrapper, open(self.baseFilename, self.mode, encoding=self.encoding, opener=opener)
        )

    def handleError(self, record: logging.LogRecord) -> None:
        # Never let logging print the original record or break a coding task.
        self.failed = True

    failed = False


class Diagnostics(logging.Handler):
    def __init__(self, directory: Path, token: str) -> None:
        super().__init__(logging.INFO)
        self.token = token
        self.rows: deque[tuple[int, str]] = deque(maxlen=500)
        self.sequence = 0
        self.started = time.monotonic()
        self.file: LogFile | None = None
        self.storage_failed = False
        self.path = directory / "logs" / "bridge.log"
        try:
            self.open_file()
        except (OSError, BridgeError):
            # Diagnostics must remain available even if disk logging cannot start.
            self.storage_failed = True
        self.logger = logging.getLogger("discord_coding_agent")
        self.old_level = self.logger.level
        self.logger.setLevel(logging.INFO)
        self.logger.addHandler(self)

    def open_file(self) -> None:
        private_dir(self.path.parent)
        for path in [self.path, *(self.path.with_name(f"bridge.log.{i}") for i in range(1, 4))]:
            if path.exists() or path.is_symlink():
                private_file(path)
        fd = os.open(self.path, os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        os.close(fd)
        # Restore only a bounded tail, even if a locally edited log grew unexpectedly.
        with self.path.open("rb") as stream:
            size = stream.seek(0, 2)
            stream.seek(max(0, size - MAX_LOG_BYTES))
            lines = stream.read(MAX_LOG_BYTES).decode("utf-8", errors="replace").splitlines()
        for line in lines[1:] if size > MAX_LOG_BYTES else lines:
            self.append(sanitize(line, self.token))
        self.file = LogFile(self.path, maxBytes=MAX_LOG_BYTES, backupCount=3, encoding="utf-8")

    def append(self, text: str) -> None:
        self.sequence += 1
        self.rows.append((self.sequence, text))

    def emit(self, record: logging.LogRecord) -> None:
        # Only our own operational messages; no exception strings, source lines,
        # locals, third-party logs, tool output, prompts or protocol envelopes.
        message = sanitize(record.getMessage(), self.token)
        stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(record.created))
        text = f"{stamp} {record.levelname} {record.name.rsplit('.', 1)[-1]} {message}"
        self.append(text)
        if self.file:
            safe = logging.LogRecord(record.name, record.levelno, "", 0, text, (), None)
            self.file.handle(safe)

    def tail(self, count: int = 20, *, after: int = 0) -> str:
        rows = [row for sequence, row in self.rows if sequence > after][-count:]
        return "\n".join(rows)

    def report(self, count: int = 20) -> str:
        warning = (
            "\nDisk log writes are failing; showing memory history."
            if self.storage_failed or (self.file and self.file.failed)
            else ""
        )
        return f"**Bridge logs** (UTC, all channels){warning}\n```text\n{self.tail(count) or 'No events yet.'}\n```"

    def close(self) -> None:
        if hasattr(self, "logger"):
            self.logger.removeHandler(self)
            self.logger.setLevel(self.old_level)
            del self.logger
        if self.file:
            with contextlib.suppress(OSError):
                self.file.close()
            self.file = None
        super().close()


def host_report(directory: Path, *, proc: Path = Path("/proc")) -> str:
    """No shell or privileged probes; unavailable Linux metrics are explicit."""
    rows = [
        f"Host: {platform.system()} {platform.machine()}; Python {platform.python_version()}; CPUs: {os.cpu_count() or 'unknown'}"
    ]
    try:
        rows.append("Load average (1/5/15m): " + "/".join(f"{n:.2f}" for n in os.getloadavg()))
    except OSError:
        rows.append("Load average: unavailable")
    try:
        memory = {
            parts[0].rstrip(":"): int(parts[1])
            for line in (proc / "meminfo").read_text().splitlines()
            if len(parts := line.split()) >= 2
        }
        rows.append(
            f"RAM available/total: {memory['MemAvailable'] / 1024:.0f}/{memory['MemTotal'] / 1024:.0f} MiB; swap free/total: {memory['SwapFree'] / 1024:.0f}/{memory['SwapTotal'] / 1024:.0f} MiB"
        )
    except (OSError, ValueError, KeyError):
        rows.append("RAM/swap: unavailable")
    try:
        disk = shutil.disk_usage(directory)
        rows.append(f"State disk free/total: {disk.free / 2**30:.1f}/{disk.total / 2**30:.1f} GiB")
    except OSError:
        rows.append("State disk: unavailable")
    rows.append(
        "Snapshot only; SIGKILL or low RAM alone does not prove an OOM kill. Check the host kernel journal."
    )
    return "\n".join(rows)
