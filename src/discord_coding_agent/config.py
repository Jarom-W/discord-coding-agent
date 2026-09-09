"""Validated per-user TOML configuration; environment overrides retain familiar names."""

import math
import os
import shutil
import stat
import subprocess
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .errors import BridgeError

APP = "discord-coding-agent"


def config_path() -> Path:
    return (
        Path(os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config"))) / APP / "config.toml"
    )


def state_path() -> Path:
    return Path(os.environ.get("XDG_STATE_HOME", str(Path.home() / ".local/state"))) / APP


def outside(path: Path, repo: Path, label: str) -> None:
    if path.resolve().is_relative_to(repo):
        raise BridgeError(f"{label} must be outside CODEX_REPO.")


def private_file(path: Path) -> None:
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise BridgeError(f"{path}: expected an owned regular file with mode 600; use chmod 600.")


def discover_codex(value: str = "codex") -> Path:
    found = shutil.which(value)
    if found is None:
        raise BridgeError(
            "Codex executable not found. Activate npm/nvm PATH or set CODEX_EXECUTABLE."
        )
    # Keep npm's bin symlink location: its sibling node may be required by /usr/bin/env.
    return Path(found).absolute()


def repository_identity(path: Path) -> str:
    try:
        result = subprocess.run(
            ["git", "-C", str(path), "rev-parse", "--show-toplevel", "--absolute-git-dir"],
            capture_output=True,
            text=True,
            check=True,
            timeout=10,
        )
        root, git_dir = result.stdout.strip().splitlines()
    except (OSError, subprocess.SubprocessError, ValueError) as exc:
        raise BridgeError(
            "CODEX_REPO must be an accessible Git working tree; check git -C PATH status."
        ) from exc
    if Path(root).resolve() != path.resolve():
        raise BridgeError("CODEX_REPO must select the Git working-tree root, not a subdirectory.")
    info = Path(git_dir).stat()
    return f"{path.resolve()}|{Path(git_dir).resolve()}|{info.st_dev}:{info.st_ino}"


@dataclass(frozen=True)
class Timeouts:
    initialization: float = 120
    request: float = 45
    transport: float = 20
    task: float = 0  # Zero disables only the full-task deadline.
    user_wait: float = 900
    delivery: float = 30
    shutdown: float = 10

    def __post_init__(self) -> None:
        for name, value in vars(self).items():
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
                or value < 0
                or (value == 0 and name != "task")
            ):
                requirement = "nonnegative" if name == "task" else "positive"
                raise BridgeError(
                    f"timeouts.{name} must be a {requirement} finite number of seconds."
                )


@dataclass(frozen=True)
class Config:
    token: str = field(repr=False)
    owner_id: int
    guild_id: int
    channel_id: int
    repo: Path
    codex: str = "codex"
    display_name: str = "Coding Agent"
    mode: str = "manual"
    state_dir: Path = field(default_factory=state_path)
    timeouts: Timeouts = field(default_factory=Timeouts)
    path: Path = field(default_factory=config_path)

    @classmethod
    def load(cls, path: Path | None = None) -> "Config":
        path = (path or config_path()).expanduser().absolute()
        data: dict[str, Any] = {}
        if path.exists():
            private_file(path)
            try:
                data = tomllib.loads(path.read_text())
            except (ValueError, OSError) as exc:
                raise BridgeError(
                    "Configuration is invalid TOML; inspect it locally without sharing the token."
                ) from exc
        allowed = {
            "DISCORD_TOKEN",
            "DISCORD_OWNER_ID",
            "DISCORD_GUILD_ID",
            "DISCORD_CHANNEL_ID",
            "CODEX_REPO",
            "CODEX_EXECUTABLE",
            "DISPLAY_NAME",
            "APPROVAL_MODE",
            "STATE_DIR",
            "timeouts",
        }
        if set(data) - allowed:
            raise BridgeError("Unknown configuration keys; compare config.example.toml.")

        def get(key: str, default: Any = None) -> Any:
            return os.environ.get(key, data.get(key, default))

        try:
            config = cls(
                token=str(get("DISCORD_TOKEN", "")),
                owner_id=int(get("DISCORD_OWNER_ID", 0)),
                guild_id=int(get("DISCORD_GUILD_ID", 0)),
                channel_id=int(get("DISCORD_CHANNEL_ID", 0)),
                repo=Path(get("CODEX_REPO", "")).expanduser().resolve(),
                codex=str(get("CODEX_EXECUTABLE", "codex")),
                display_name=str(get("DISPLAY_NAME", "Coding Agent")),
                mode=str(get("APPROVAL_MODE", "manual")),
                state_dir=Path(get("STATE_DIR", str(state_path()))).expanduser().resolve(),
                timeouts=Timeouts(**data.get("timeouts", {})),
                path=path,
            )
        except (TypeError, ValueError) as exc:
            raise BridgeError(
                "Invalid configuration values; numeric IDs and repository path are required."
            ) from exc
        if not config.token or any(c.isspace() for c in config.token):
            raise BridgeError("DISCORD_TOKEN is missing or contains whitespace; use the Bot token.")
        if any(not 0 < n < 2**64 for n in [config.owner_id, config.guild_id, config.channel_id]):
            raise BridgeError("Owner, guild and channel must be positive numeric Discord IDs.")
        if not get("CODEX_REPO") or not config.repo.is_dir():
            raise BridgeError("CODEX_REPO must name an existing Git working tree.")
        if config.mode not in {"manual", "auto"}:
            raise BridgeError("APPROVAL_MODE must be manual or auto.")
        if not 1 <= len(config.display_name) <= 32 or any(ord(c) < 32 for c in config.display_name):
            raise BridgeError("DISPLAY_NAME must contain 1–32 printable characters.")
        outside(config.path, config.repo, "Configuration")
        outside(config.state_dir, config.repo, "State directory")
        outside(Path(__file__), config.repo, "Bridge installation")
        repository_identity(config.repo)
        return config
