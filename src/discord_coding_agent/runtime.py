"""Process-local release identity; never infer running code from a checkout's HEAD."""

import json
import re
import sys
from dataclasses import asdict, dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from . import __version__


@dataclass(frozen=True)
class Runtime:
    version: str
    python: str
    package: str
    revision: str | None = None
    repository: str | None = None
    replies: str = "inline-text"

    def record(self) -> dict[str, Any]:
        return asdict(self)

    def summary(self) -> str:
        revision = self.revision[:12] if self.revision else "unavailable"
        return f"Bridge: {self.version}; revision: {revision}; replies: {self.replies} (no file uploads)."


@lru_cache(maxsize=1)
def current() -> Runtime:
    # Preserve the venv executable path: resolving its symlink loses installation identity.
    python = str(Path(sys.executable).absolute())
    package = Path(__file__).resolve().parent
    prefix = Path(sys.prefix).resolve()
    release = prefix.parent
    revision, repository = None, None
    marker = release / "prepared.json"
    # An editable package is not the code in a release merely because its cwd,
    # Git HEAD or environment mentions that commit.
    if (
        prefix.name == ".venv"
        and re.fullmatch(r"[0-9a-f]{40}", release.name)
        and package.is_relative_to(prefix)
    ):
        try:
            with marker.open() as stream:
                data = json.loads(stream.read(4096))
            if (
                isinstance(data, dict)
                and data.get("sha") == release.name
                and isinstance(data.get("repository"), str)
                and re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", data["repository"])
            ):
                revision, repository = release.name, data["repository"]
        except (OSError, ValueError):
            pass  # Missing/invalid provenance means unknown, not a startup failure.
    return Runtime(__version__, python, str(package), revision, repository)
