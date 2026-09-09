"""Explicit user-service installation; no sudo, global settings or unrelated services."""

import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from .config import Config, discover_codex
from .errors import BridgeError
from .state import atomic_write, backup

MARKER = "# Managed by discord-coding-agent v1"
DEFAULT_UNIT = "discord-coding-agent.service"


def unit_name(value: str) -> str:
    if not re.fullmatch(r"discord-coding-agent(?:-[a-zA-Z0-9_-]+)?\.service", value):
        raise BridgeError("Unit name must be discord-coding-agent[-INSTANCE].service.")
    return value


def clean(value: str) -> str:
    if any(ord(c) < 32 or ord(c) == 127 for c in value) or value != value.strip():
        raise BridgeError(
            "Service paths must not contain control characters or surrounding whitespace."
        )
    return value


def argument(value: str, *, executable: bool = False) -> str:
    value = clean(value).replace("\\", "\\\\").replace('"', '\\"').replace("%", "%%")
    if not executable:
        value = value.replace("$", "$$")
    return f'"{value}"'


def environment(value: str) -> str:
    """Environment= has C quoting/specifiers, but no ExecStart dollar expansion."""
    return clean(value).replace("\\", "\\\\").replace('"', '\\"').replace("%", "%%")


def service_path() -> str:
    # Capture the invoking shell's npm/nvm node and codex paths, without executing shell startup files.
    entries = [str(Path(sys.executable).absolute().parent)]
    entries.extend(os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin").split(os.pathsep))
    return os.pathsep.join(dict.fromkeys(p for p in entries if p and Path(p).is_absolute()))


def render(config: Config, executable: Path | None = None, *, python: Path | None = None) -> str:
    codex = executable or discover_codex(config.codex)
    path = os.pathsep.join(dict.fromkeys([str(codex.parent), *service_path().split(os.pathsep)]))
    # WorkingDirectory is a path-valued directive, not an argv field: no surrounding quotes.
    working = clean(str(config.path.parent)).replace("%", "%%")
    interpreter = str(python or Path(sys.executable).absolute())
    path_environment = environment(path)
    codex_environment = environment(str(codex))
    bootstrap_environment = environment(str(Path(sys.executable).absolute()))
    return f"""{MARKER}
[Unit]
Description=Owner-only Discord bridge to local Codex
StartLimitIntervalSec=120
StartLimitBurst=5

[Service]
Type=simple
WorkingDirectory={working}/
ExecStart={argument(interpreter, executable=True)} -m discord_coding_agent --config {argument(str(config.path))} run
Environment="PATH={path_environment}"
Environment="CODEX_EXECUTABLE={codex_environment}"
Environment="DCA_BOOTSTRAP_PYTHON={bootstrap_environment}"
UMask=0077
Restart=on-failure
RestartSec=10
KillMode=control-group
TimeoutStopSec={config.timeouts.shutdown * 4 + 10:g}
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=default.target
"""


def verify(text: str, name: str = DEFAULT_UNIT) -> None:
    verify_units({unit_name(name): text})


def verify_units(units: dict[str, str]) -> None:
    if not shutil.which("systemd-analyze"):
        raise BridgeError(
            "systemd-analyze is required to validate the generated unit before installation."
        )
    with tempfile.TemporaryDirectory(prefix="dca-unit-") as directory:
        paths = []
        for name, text in units.items():
            if not re.fullmatch(
                r"discord-coding-agent(?:-[a-zA-Z0-9_-]+)?\.(?:service|timer)", name
            ):
                raise BridgeError("Invalid managed unit name.")
            path = Path(directory) / name
            path.write_text(text)
            paths.append(str(path))
        result = subprocess.run(
            ["systemd-analyze", "--user", "verify", *paths],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode and (
            "Failed to initialize manager" in result.stderr
            or "Failed to lookup RuntimeDirectory" in result.stderr
        ):
            # CI/containers may have no user manager. Offline syntax verification needs no bus.
            result = subprocess.run(
                ["systemd-analyze", "verify", *paths],
                capture_output=True,
                text=True,
                timeout=30,
            )
        if result.returncode:
            # Paths and unit syntax only, no token in generated unit.
            raise BridgeError(
                f"systemd unit validation failed: {result.stderr.strip()}. Check user-bus/runtime setup and docs/service.md."
            )


def control(action: str, name: str) -> None:
    command = ["systemctl", "--user", action]
    if action != "daemon-reload":
        command.append(unit_name(name))
    try:
        subprocess.run(command, check=True, timeout=60)
    except (OSError, subprocess.SubprocessError) as exc:
        raise BridgeError(
            "systemctl --user failed; inspect user-bus/login/linger setup in docs/service.md."
        ) from exc


def location(name: str) -> Path:
    return (
        Path(os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config")))
        / "systemd/user"
        / unit_name(name)
    )


def ensure_managed(path: Path) -> None:
    if path.is_symlink() or (path.exists() and not path.read_text().startswith(MARKER)):
        raise BridgeError(
            "Refusing to replace/control an unmanaged existing unit. Use a different --unit-name; existing TARS installations are separate."
        )


def install(config: Config, name: str) -> Path:
    text = render(config)
    verify(text, name)
    path = location(name)
    ensure_managed(path)
    if path.exists() and path.read_text() == text:
        control("daemon-reload", name)
        return path
    if path.exists():
        backup(path)
    atomic_write(path, text)
    control("daemon-reload", name)
    return path


def uninstall(name: str) -> None:
    path = location(name)
    ensure_managed(path)
    if not path.exists():
        return
    control("stop", name)
    control("disable", name)
    saved = backup(path)
    path.unlink()
    control("daemon-reload", name)
    print(
        f"Unit removed; backup: {saved}. Configuration, state, repository and Codex authentication retained."
    )
