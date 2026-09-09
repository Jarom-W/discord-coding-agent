"""Opt-in pull deployment of CI-verified main commits; no inbound server or runner."""

import json
import logging
import math
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import tomllib
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from . import service
from .config import Config, outside, private_file
from .errors import BridgeError, LimitError, log_error
from .locks import Lease
from .state import atomic_write, backup, private_dir

log = logging.getLogger(__name__)
MARKER = "# Managed by discord-coding-agent updater v1"
SHA = re.compile(r"[0-9a-f]{40}")


@dataclass(frozen=True)
class Settings:
    repository: str
    directory: Path
    unit: str = service.DEFAULT_UNIT
    interval: float = 300
    preparation: float = 1800
    network: float = 30
    health: float = 120

    def validate(self, config: Config) -> None:
        if not re.fullmatch(r"[A-Za-z0-9-]+/[A-Za-z0-9_.-]+", self.repository):
            raise BridgeError(
                "Deployment repository must be a public GitHub OWNER/REPO, without URL or credentials."
            )
        service.unit_name(self.unit)
        if not self.directory.is_absolute() or self.directory.is_symlink():
            raise BridgeError("Deployment directory must be absolute and not a symlink.")
        for name in ["interval", "preparation", "network", "health"]:
            value = getattr(self, name)
            if type(value) not in {int, float} or not math.isfinite(value) or value <= 0:
                raise BridgeError(f"Deployment {name} must be positive finite seconds.")
        if self.interval < 300:
            raise BridgeError(
                "Deployment interval must be at least 300 seconds to limit unauthenticated API traffic."
            )
        outside(self.directory, config.repo, "Deployment directory")
        for protected in [config.path, config.state_dir, Path(sys.executable).absolute()]:
            if protected.resolve().is_relative_to(self.directory.resolve()):
                raise BridgeError(
                    "Keep deployment releases separate from config, state and the bootstrap installation."
                )

    @classmethod
    def load(cls, config: Config) -> "Settings":
        path = settings_path(config)
        if not path.exists():
            raise BridgeError(
                "No deployment.toml found. Run deploy install --repository OWNER/REPO first."
            )
        private_file(path)
        try:
            data = tomllib.loads(path.read_text())
            data["directory"] = Path(data["directory"])
            settings = cls(**data)
            settings.validate(config)
            return settings
        except (ValueError, TypeError, KeyError) as exc:
            raise BridgeError("Invalid deployment.toml; compare the deployment guide.") from exc


def settings_path(config: Config) -> Path:
    return config.path.with_name("deployment.toml")


def run(
    argv: list[str], operation: str, limit: float, *, cwd: Path | None = None, output: bool = False
) -> str:
    """Bound commands and owned descendants; never include raw installer output in logs."""
    started = time.monotonic()
    env = {
        k: v
        for k, v in os.environ.items()
        if not k.startswith(("DISCORD_", "OPENAI_", "GH_", "GITHUB_", "GIT_"))
        and k not in {"PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV"}
    }
    env.update(
        {
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
            "PIP_DISABLE_PIP_VERSION_CHECK": "1",
            "PIP_NO_INPUT": "1",
        }
    )
    log.info("deployment operation=%s start limit=%gs", operation, limit)
    with tempfile.TemporaryFile() as capture:
        process = subprocess.Popen(
            argv,
            cwd=cwd,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=capture if output else subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        try:
            code = process.wait(timeout=limit)
            if code:
                raise BridgeError(
                    f"Deployment {operation} failed (exit {code}); the candidate is not healthy. Inspect local dependencies, paths and the deployment guide. Installer output is omitted to avoid exposing credentials."
                )
        except subprocess.TimeoutExpired as exc:
            raise LimitError(f"deployment {operation}", time.monotonic() - started, limit) from exc
        finally:
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()
        capture.seek(0)
        value = capture.read(65537)
        if len(value) > 65536:
            raise BridgeError(f"Deployment {operation} output exceeded 64 KiB.")
    log.info(
        "deployment operation=%s complete elapsed=%.2fs", operation, time.monotonic() - started
    )
    return value.decode("utf-8").strip()


class GitHub:
    def __init__(self, repository: str, timeout: float) -> None:
        self.repository, self.timeout = repository, timeout

    def get(self, path: str) -> dict[str, Any]:
        request = urllib.request.Request(
            f"https://api.github.com/repos/{self.repository}/{path}",
            headers={
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2026-03-10",
                "User-Agent": "discord-coding-agent-updater",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                payload = response.read(2 * 1024 * 1024 + 1)
            if len(payload) > 2 * 1024 * 1024:
                raise ValueError()
            value = json.loads(payload)
            if not isinstance(value, dict):
                raise ValueError()
            return value
        except urllib.error.HTTPError as exc:
            raise BridgeError(
                f"GitHub deployment check returned HTTP {exc.code}; check public repository access, API rate limit and connectivity. Existing bot was not replaced."
            ) from None
        except (OSError, ValueError) as exc:
            raise BridgeError(
                f"GitHub deployment check failed (network read limit {self.timeout:g}s). Check connectivity/API availability; retry on the next timer tick."
            ) from exc

    def head(self) -> str:
        value = self.get("commits/main").get("sha", "")
        if not isinstance(value, str) or not SHA.fullmatch(value):
            raise BridgeError("GitHub returned an invalid main commit ID; no deployment.")
        return value

    def passed(self, sha: str) -> bool:
        query = urllib.parse.urlencode(
            {"branch": "main", "event": "push", "head_sha": sha, "per_page": 1}
        )
        runs = self.get(f"actions/workflows/ci.yml/runs?{query}").get("workflow_runs")
        if not isinstance(runs, list) or not runs or not isinstance(runs[0], dict):
            return False
        latest = runs[0]
        # Do not search only for success: a newer failed/in-progress rerun must block deployment.
        return (
            latest.get("head_sha") == sha
            and latest.get("head_branch") == "main"
            and latest.get("event") == "push"
            and latest.get("status") == "completed"
            and latest.get("conclusion") == "success"
            and latest.get("path", "").split("@", 1)[0] == ".github/workflows/ci.yml"
            and all(
                isinstance(latest.get(key), dict)
                and latest[key].get("full_name", "").casefold() == self.repository.casefold()
                for key in ["repository", "head_repository"]
            )
        )


def unit_names(settings: Settings) -> tuple[str, str]:
    stem = settings.unit.removesuffix(".service") + "-update"
    return stem + ".service", stem + ".timer"


def render_units(config: Config, settings: Settings) -> dict[str, str]:
    name, timer = unit_names(settings)
    python = service.argument(str(Path(sys.executable).absolute()), executable=True)
    stop_limit = settings.preparation + settings.health * 2 + settings.network * 4 + 600
    return {
        name: f"""{MARKER}
[Unit]
Description=Fetch CI-verified Discord coding agent updates when idle
[Service]
Type=oneshot
WorkingDirectory={service.clean(str(config.path.parent)).replace("%", "%%")}/
ExecStart={python} -m discord_coding_agent --config {service.argument(str(config.path))} deploy check
Environment="PATH={service.environment(service.service_path())}"
UMask=0077
Nice=10
KillMode=control-group
TimeoutStartSec={stop_limit:g}
""",
        timer: f"""{MARKER}
[Unit]
Description=Check for Discord coding agent updates every {settings.interval:g} seconds
[Timer]
OnBootSec=120
OnUnitInactiveSec={settings.interval:g}
RandomizedDelaySec=30
Unit={name}
[Install]
WantedBy=timers.target
""",
    }


def unit_location(name: str) -> Path:
    return service.location(service.DEFAULT_UNIT).parent / name


def manage(action: str, settings: Settings) -> None:
    _, timer = unit_names(settings)
    for name in unit_names(settings):
        path = unit_location(name)
        if path.is_symlink() or not path.exists() or not path.read_text().startswith(MARKER):
            raise BridgeError("Updater unit is absent or unmanaged; run deploy install first.")
    if action in {"enable", "disable"}:
        run(["systemctl", "--user", action, "--now", timer], f"timer-{action}", 60)
    elif action == "uninstall":
        manage("disable", settings)
        # Stop the updater too; its transaction journal makes any interrupted switch recoverable.
        run(["systemctl", "--user", "stop", unit_names(settings)[0]], "stop-updater", 60)
        for name in unit_names(settings):
            path = unit_location(name)
            backup(path)
            path.unlink()
        service.control("daemon-reload", settings.unit)


def install(config: Config, settings: Settings) -> None:
    settings.validate(config)
    path = service.location(settings.unit)
    service.ensure_managed(path)
    if (
        not path.exists()
        or f"--config {service.argument(str(config.path))} run" not in path.read_text()
    ):
        raise BridgeError(
            "Install this configuration's managed bot service first. The updater will not adopt a different personal TARS unit."
        )
    units = render_units(config, settings)
    service.verify_units(units)
    for name in units:
        target = unit_location(name)
        if target.is_symlink() or (target.exists() and not target.read_text().startswith(MARKER)):
            raise BridgeError("Refusing to replace an unmanaged updater unit.")
    data = asdict(settings)
    data["directory"] = str(settings.directory)
    content = "\n".join(f"{key} = {json.dumps(value)}" for key, value in data.items()) + "\n"
    for target, text in [
        (settings_path(config), content),
        *((unit_location(k), v) for k, v in units.items()),
    ]:
        if target.exists() and target.read_text() == text:
            continue
        if target.exists():
            backup(target)
        atomic_write(target, text)
    private_dir(settings.directory)
    service.control("daemon-reload", settings.unit)


class Updater:
    def __init__(self, config: Config, settings: Settings) -> None:
        settings.validate(config)
        self.config, self.settings = config, settings
        self.github = GitHub(settings.repository, settings.network)
        self.path = settings.directory / "deployment.json"
        self.load()

    def load(self) -> None:
        self.data: dict[str, Any] = {
            "current": None,
            "previous_unit": None,
            "previous": None,
            "failed": None,
            "transaction": None,
        }
        if self.path.exists():
            private_file(self.path)
            try:
                data = json.loads(self.path.read_text())
                if not isinstance(data, dict) or set(data) != set(self.data):
                    raise ValueError()
                for key in ["current", "previous", "failed"]:
                    if data[key] is not None and (
                        not isinstance(data[key], str) or not SHA.fullmatch(data[key])
                    ):
                        raise ValueError()
                if data["previous_unit"] is not None and not isinstance(data["previous_unit"], str):
                    raise ValueError()
                if data["transaction"] is not None:
                    tx = data["transaction"]
                    if (
                        not isinstance(tx, dict)
                        or not isinstance(tx.get("old_unit"), str)
                        or not isinstance(tx.get("sha"), str)
                        or not SHA.fullmatch(tx["sha"])
                    ):
                        raise ValueError()
                self.data = data
            except (ValueError, TypeError) as exc:
                raise BridgeError(
                    "Deployment state is corrupt; preserve deployment.json and recover manually. No service changed."
                ) from exc

    def save(self) -> None:
        atomic_write(self.path, json.dumps(self.data) + "\n")

    def owned_unit(self) -> Path:
        path = service.location(self.settings.unit)
        service.ensure_managed(path)
        if (
            not path.exists()
            or f"--config {service.argument(str(self.config.path))} run" not in path.read_text()
        ):
            raise BridgeError("Bot unit does not match this configuration; refusing deployment.")
        return path

    def healthy(self) -> bool:
        value = run(
            [
                "systemctl",
                "--user",
                "show",
                self.settings.unit,
                "--property=ActiveState,MainPID,InvocationID",
            ],
            "health-status",
            10,
            output=True,
        )
        fields = dict(line.split("=", 1) for line in value.splitlines() if "=" in line)
        ready_path = self.config.state_dir / "ready.json"
        if not ready_path.exists():
            return False
        private_file(ready_path)
        try:
            ready = json.loads(ready_path.read_text())
            return (
                fields.get("ActiveState") == "active"
                and fields.get("MainPID") not in {None, "0"}
                and bool(fields.get("InvocationID"))
                and ready.get("protocol") == 1
                and ready.get("ready") is True
                and str(ready.get("pid")) == fields.get("MainPID")
                and ready.get("invocation") == fields.get("InvocationID")
                and ready.get("config") == str(self.config.path)
            )
        except (ValueError, AttributeError):
            return False

    def wait_healthy(self) -> None:
        started = time.monotonic()
        while time.monotonic() - started < self.settings.health:
            if self.healthy():
                return
            time.sleep(min(2, self.settings.health))
        raise LimitError(
            "deployment Gateway readiness", time.monotonic() - started, self.settings.health
        )

    def prepare(self, sha: str) -> Path:
        release = self.settings.directory / "releases" / sha
        if release.is_symlink():
            raise BridgeError("Release directory must not be a symlink.")
        marker = release / "prepared.json"
        if marker.exists():
            private_file(marker)
            if json.loads(marker.read_text()) == {
                "repository": self.settings.repository,
                "sha": sha,
            }:
                return release / ".venv/bin/python"
            raise BridgeError("Prepared release identity mismatch.")
        if release.exists():
            # A failed preparation can be retried, but never delete arbitrary pre-existing files.
            raise BridgeError(
                f"Incomplete release at {release}; inspect/move it aside before deploy check --retry."
            )
        releases = self.settings.directory / "releases"
        if releases.exists() and len(list(releases.iterdir())) >= 10:
            raise BridgeError(
                "Deployment storage reached 10 releases. Inspect preserved/protected releases before removing unused copies; current bot is unchanged."
            )
        private_dir(release)
        atomic_write(
            release / "candidate.json",
            json.dumps({"repository": self.settings.repository, "sha": sha}) + "\n",
        )
        started = time.monotonic()

        def step(
            argv: list[str], name: str, *, cwd: Path | None = None, output: bool = False
        ) -> str:
            remaining = self.settings.preparation - (time.monotonic() - started)
            if remaining <= 0:
                raise LimitError(
                    "deployment preparation", time.monotonic() - started, self.settings.preparation
                )
            return run(argv, name, remaining, cwd=cwd, output=output)

        source = release / "source"
        step(["git", "-c", "init.templateDir=", "init", "-q", str(source)], "git-init")
        step(
            [
                "git",
                "-c",
                "core.hooksPath=/dev/null",
                "fetch",
                "--depth=1",
                f"https://github.com/{self.settings.repository}.git",
                sha,
            ],
            "git-fetch",
            cwd=source,
        )
        actual = step(["git", "rev-parse", "FETCH_HEAD"], "git-revision", cwd=source, output=True)
        if actual != sha:
            raise BridgeError("Fetched revision differs from the CI-verified commit.")
        step(
            ["git", "-c", "core.hooksPath=/dev/null", "checkout", "--detach", sha],
            "git-checkout",
            cwd=source,
        )
        python = release / ".venv/bin/python"
        step([sys.executable, "-m", "venv", str(release / ".venv")], "venv-create")
        step(
            [
                str(python),
                "-m",
                "pip",
                "install",
                "--only-binary=:all:",
                "--require-hashes",
                "-r",
                str(source / "requirements.lock"),
            ],
            "install-dependencies",
        )
        step([str(python), "-m", "pip", "install", "--no-deps", str(source)], "install-bridge")
        step([str(python), "-m", "pip", "check"], "dependency-check")
        step(
            [
                str(python),
                "-m",
                "discord_coding_agent",
                "--config",
                str(self.config.path),
                "doctor",
            ],
            "candidate-doctor",
        )
        atomic_write(
            marker, json.dumps({"repository": self.settings.repository, "sha": sha}) + "\n"
        )
        return python

    def replace_unit(self, text: str) -> None:
        service.verify(text, self.settings.unit)
        atomic_write(self.owned_unit(), text)
        service.control("daemon-reload", self.settings.unit)

    def restore(self, text: str) -> None:
        service.control("stop", self.settings.unit)
        self.replace_unit(text)
        service.control("start", self.settings.unit)
        self.wait_healthy()

    def prune(self) -> None:
        """Keep three deployment copies/backups; never remove a selected coding repo."""
        repositories = [self.config.repo.resolve()]
        catalog = self.config.state_dir / "workspaces.json"
        if catalog.exists():
            private_file(catalog)
            try:
                entries = json.loads(catalog.read_text())["sessions"].values()
                repositories.extend(Path(entry["repo"]).resolve() for entry in entries)
            except (ValueError, TypeError, KeyError, AttributeError) as exc:
                raise BridgeError(
                    "Cannot prune with an invalid workspace catalog; existing releases preserved."
                ) from exc
        for base, pattern, marker_name in [
            (self.settings.directory / "releases", r"[0-9a-f]{40}", "candidate.json"),
            (self.settings.directory / "backups", r"[0-9a-f]{40}-[0-9]+", "deployment-backup.json"),
        ]:
            if not base.exists() or base.is_symlink():
                continue
            candidates = sorted(
                (
                    p
                    for p in base.iterdir()
                    if re.fullmatch(pattern, p.name) and p.is_dir() and not p.is_symlink()
                ),
                key=lambda p: p.stat().st_mtime,
                reverse=True,
            )
            for path in candidates[3:]:
                if path.name in {self.data["current"], self.data["previous"]}:
                    continue
                if any(repo.is_relative_to(path.resolve()) for repo in repositories):
                    continue
                marker = path / marker_name
                if not marker.exists() or marker.is_symlink():
                    continue
                private_file(marker)
                value = json.loads(marker.read_text())
                if value != {"repository": self.settings.repository, "sha": path.name[:40]}:
                    continue
                shutil.rmtree(path)

    def check(self, *, retry: bool = False, rollback: bool = False) -> str:
        own = Lease(self.settings.directory / "updater.lock")
        if not own.acquire():
            return "Another deployment operation is running; skipped."
        activity = Lease(self.config.state_dir / "activity.lock")
        try:
            self.load()  # Another updater may have completed since this object was created.
            self.owned_unit()
            if self.data["transaction"]:
                if not activity.acquire():
                    return "Interrupted deployment needs recovery, but coding work is active; deferred."
                tx = self.data["transaction"]
                self.restore(tx["old_unit"])
                self.data["failed"], self.data["transaction"] = tx["sha"], None
                self.save()
                return "Recovered previous bot unit after an interrupted deployment. Candidate is blocked pending --retry or a new main commit."
            if rollback:
                if not self.data["previous_unit"]:
                    raise BridgeError("No previous deployment is available.")
                if not activity.acquire():
                    return "Coding work or workspace maintenance is active; rollback deferred."
                old = self.owned_unit().read_text()
                self.data["transaction"] = {
                    "old_unit": old,
                    "sha": self.data["current"] or self.data["previous"],
                }
                self.save()
                self.restore(self.data["previous_unit"])
                self.data.update(
                    failed=self.data["current"],
                    current=self.data["previous"],
                    previous_unit=old,
                    previous=self.data["current"],
                    transaction=None,
                )
                self.save()
                return "Previous bot restored. Disable the update timer to stay pinned."
            sha = self.github.head()
            if sha == self.data["current"]:
                return f"Already deployed {sha[:12]}."
            if sha == self.data["failed"] and not retry:
                return f"Revision {sha[:12]} previously failed; waiting for a new main commit or deploy check --retry."
            if not self.github.passed(sha):
                return f"Waiting for successful CI push run on main at {sha[:12]}; no bot changes."
            if not self.healthy():
                return "Bot is not ready with deployment protocol 1. Manually install/start this release first, or fix Gateway/service health. No bot changes."
            try:
                python = self.prepare(sha)
            except Exception:
                self.data["failed"] = sha
                self.save()
                raise
            candidate = service.render(self.config, python=python)
            service.verify(candidate, self.settings.unit)
            if self.github.head() != sha or not self.github.passed(sha):
                return "main/CI changed during preparation; deployment deferred."
            if settings_path(self.config).exists() and Settings.load(self.config) != self.settings:
                return "Deployment settings changed during preparation; deferred to the next check."
            if not activity.acquire():
                return f"Prepared {sha[:12]}; coding work or workspace maintenance is active. Will retry when idle."
            if not self.healthy():
                return "Bot disconnected during preparation; deployment deferred."
            previous = self.owned_unit().read_text()
            backup(self.owned_unit())
            self.data["transaction"] = {"old_unit": previous, "sha": sha}
            self.save()  # Durable rollback target before stopping or replacing anything.
            try:
                service.control("stop", self.settings.unit)
                if self.config.path.exists():
                    backup(self.config.path)
                # Back up durable sessions/catalog, but never runtime locks or Codex authentication.
                destination = self.settings.directory / "backups" / f"{sha}-{time.time_ns()}"
                private_dir(destination)
                atomic_write(
                    destination / "deployment-backup.json",
                    json.dumps({"repository": self.settings.repository, "sha": sha}) + "\n",
                )
                for path in self.config.state_dir.rglob("*.json"):
                    if path.name != "ready.json" and not path.is_symlink():
                        relative = path.relative_to(self.config.state_dir)
                        target = destination / relative
                        private_dir(target.parent)
                        shutil.copyfile(path, target)
                        target.chmod(0o600)
                self.replace_unit(candidate)
                service.control("start", self.settings.unit)
                self.wait_healthy()
            except Exception as exc:
                log_error(log, f"deployment-switch-{sha}", exc)
                self.data["failed"] = sha
                self.save()
                self.restore(previous)
                self.data["transaction"] = None
                self.save()
                raise BridgeError(
                    "Deployment switch failed; previous bot restored. "
                    + (
                        str(exc)
                        if isinstance(exc, BridgeError)
                        else "Inspect the updater journal for the failed operation."
                    )
                    + " Revision is blocked until --retry or a new main commit."
                ) from None
            self.data.update(
                previous=self.data["current"],
                previous_unit=previous,
                current=sha,
                failed=None,
                transaction=None,
            )
            self.save()
            try:
                self.prune()
            except (BridgeError, OSError, ValueError):
                log.warning(
                    "deployment prune failed; healthy release retained. Inspect deployment storage locally."
                )
            return f"Deployed {sha[:12]}; Discord Gateway/channel readiness checked. Codex/model behavior was not invoked by deployment."
        finally:
            activity.close()
            own.close()
