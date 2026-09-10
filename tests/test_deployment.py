import json
import shutil
import sys
from dataclasses import replace
from pathlib import Path

import pytest

from discord_coding_agent import deployment, service
from discord_coding_agent.errors import BridgeError, LimitError
from discord_coding_agent.locks import Lease
from discord_coding_agent.state import atomic_write

FIRST = "a" * 40
SECOND = "b" * 40


def workflow(**overrides):
    data = {
        "head_sha": FIRST,
        "head_branch": "main",
        "event": "push",
        "status": "completed",
        "conclusion": "success",
        "path": ".github/workflows/ci.yml",
        "repository": {"full_name": "owner/project"},
        "head_repository": {"full_name": "owner/project"},
    }
    return data | overrides


@pytest.mark.parametrize(
    "change",
    [
        {"event": "pull_request"},
        {"head_branch": "feature"},
        {"head_sha": SECOND},
        {"status": "in_progress"},
        {"conclusion": "failure"},
        {"conclusion": "cancelled"},
        {"path": ".github/workflows/unrelated.yml"},
        {"head_repository": {"full_name": "attacker/fork"}},
        {"repository": None},
    ],
)
def test_only_successful_matching_main_ci_is_eligible(monkeypatch, change):
    github = deployment.GitHub("owner/project", 1)
    monkeypatch.setattr(
        github, "get", lambda path: {"workflow_runs": [workflow(**change), workflow()]}
    )
    assert not github.passed(FIRST)


def test_ci_success_and_empty_runs(monkeypatch):
    github = deployment.GitHub("owner/project", 1)
    paths = []

    def get(path):
        paths.append(path)
        return {"workflow_runs": [workflow()]}

    monkeypatch.setattr(github, "get", get)
    assert github.passed(FIRST)
    assert "head_sha=" + FIRST in paths[0] and "event=push" in paths[0]
    monkeypatch.setattr(github, "get", lambda _: {"workflow_runs": []})
    assert not github.passed(FIRST)


@pytest.fixture
def updater(config, tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "units"))
    monkeypatch.setattr(service, "discover_codex", lambda *_: Path("/usr/bin/true"))
    config.path.parent.mkdir()
    atomic_write(config.path, "# fake test config, no credentials\n")
    settings = deployment.Settings("owner/project", tmp_path / "releases-root", health=0.01)
    original = service.render(config)
    atomic_write(service.location(settings.unit), original)
    u = deployment.Updater(config, settings)
    controls = []
    monkeypatch.setattr(service, "control", lambda *args: controls.append(args))
    monkeypatch.setattr(service, "verify", lambda *_: None)
    monkeypatch.setattr(u.github, "head", lambda: FIRST)
    monkeypatch.setattr(u.github, "passed", lambda _: True)
    monkeypatch.setattr(u, "healthy", lambda: True)
    prefix = settings.directory / "releases" / FIRST / ".venv"
    monkeypatch.setattr(
        u,
        "running",
        lambda: {
            "runtime": {
                "version": "test-version",
                "revision": FIRST,
                "repository": settings.repository,
                "python": str(prefix / "bin/python"),
                "package": str(prefix / "lib/python3.13/site-packages/discord_coding_agent"),
                "replies": "inline-text",
            }
        },
    )
    monkeypatch.setattr(u, "wait_healthy", lambda: None)
    monkeypatch.setattr(u, "prepare", lambda _: Path("/usr/bin/python3"))
    return u, controls, original


def test_deploy_and_manual_rollback(updater):
    u, controls, old = updater
    assert "Deployed" in u.check()
    assert u.data["current"] == FIRST and u.data["previous_unit"] == old
    assert controls[0][0] == "stop" and any(action == "start" for action, _ in controls)
    assert list(u.settings.directory.glob("backups/*/deployment-backup.json"))
    controls.clear()
    assert "Already deployed" in u.check() and not controls
    assert "Previous bot restored" in u.check(rollback=True)
    assert service.location(u.settings.unit).read_text() == old
    assert u.data["failed"] == FIRST and u.data["current"] is None
    controls.clear()
    assert "previously failed" in u.check() and not controls


def test_busy_task_defers_restart(updater):
    u, controls, _ = updater
    lease = Lease(u.config.state_dir / "activity.lock")
    assert lease.acquire()
    try:
        assert "coding work" in u.check()
        assert not controls
    finally:
        lease.close()
    assert "Deployed" in u.check()


@pytest.mark.parametrize(
    "change",
    [
        {"revision": SECOND},
        {"repository": "different/project"},
        {"python": "/bootstrap/.venv/bin/python"},
        {"package": "/bootstrap/src/discord_coding_agent"},
        {"package": None},
    ],
)
def test_saved_current_cannot_hide_a_different_running_install(updater, monkeypatch, change):
    u, controls, _ = updater
    u.data["current"] = FIRST
    u.save()
    record = u.running()
    record["runtime"].update(change)
    monkeypatch.setattr(u, "running", lambda: record)
    result = u.check()
    assert "Already deployed" not in result
    assert "unverified or different" in result and "No service changed" in result
    assert not controls and u.data["current"] == FIRST


@pytest.mark.parametrize("record", [None, {}, {"runtime": []}])
def test_saved_current_is_not_live_evidence_for_stopped_or_legacy_process(
    updater, monkeypatch, record
):
    u, controls, _ = updater
    u.data["current"] = FIRST
    u.save()
    monkeypatch.setattr(u, "running", lambda: record)
    result = u.check()
    assert "Already deployed" not in result and "unverified" in result
    assert not controls


def test_deploy_status_separates_recorded_and_running_revision(updater, monkeypatch, capsys):
    from argparse import Namespace

    from discord_coding_agent.cli import deploy_command

    u, controls, _ = updater
    u.data["current"] = SECOND
    u.save()
    monkeypatch.setattr(deployment.Settings, "load", lambda *_: u.settings)
    monkeypatch.setattr(deployment, "Updater", lambda *_: u)
    deploy_command(
        u.config,
        Namespace(action="status", retry=False, repository=None, directory=None, unit_name=None),
    )
    output = capsys.readouterr().out
    assert f"Recorded current: {SECOND}" in output and f"revision: {FIRST}" in output
    assert "Running interpreter:" in output and "release match: unverified" in output
    assert not controls and u.config.token not in output


def test_failed_preparation_keeps_old_bot_and_blocks_retry(updater, monkeypatch):
    u, controls, old = updater

    def fail(_):
        raise BridgeError("build failed")

    monkeypatch.setattr(u, "prepare", fail)
    with pytest.raises(BridgeError, match="build failed"):
        u.check()
    assert not controls and service.location(u.settings.unit).read_text() == old
    assert u.data["failed"] == FIRST
    assert "previously failed" in u.check()


def test_failed_candidate_rolls_back_without_replaying_work(updater, monkeypatch):
    u, _, old = updater
    calls = []

    def health():
        calls.append(1)
        if len(calls) == 1:
            raise BridgeError("candidate failed")

    monkeypatch.setattr(u, "wait_healthy", health)
    with pytest.raises(BridgeError, match="previous bot restored"):
        u.check()
    assert len(calls) == 2 and service.location(u.settings.unit).read_text() == old
    assert u.data["failed"] == FIRST and u.data["transaction"] is None


def test_killed_switch_recovers_durable_previous_unit(updater, monkeypatch):
    u, _, old = updater

    def killed():
        raise KeyboardInterrupt()

    monkeypatch.setattr(u, "wait_healthy", killed)
    with pytest.raises(KeyboardInterrupt):
        u.check()
    assert json.loads(u.path.read_text())["transaction"]["old_unit"] == old
    monkeypatch.setattr(u, "wait_healthy", lambda: None)
    assert "Recovered previous bot" in u.check()
    assert service.location(u.settings.unit).read_text() == old and u.data["failed"] == FIRST


def test_new_main_or_failed_ci_never_stops_bot(updater, monkeypatch):
    u, controls, _ = updater
    heads = iter([FIRST, SECOND])
    monkeypatch.setattr(u.github, "head", lambda: next(heads))
    assert "changed during preparation" in u.check() and not controls
    monkeypatch.setattr(u.github, "head", lambda: FIRST)
    monkeypatch.setattr(u.github, "passed", lambda _: False)
    assert "Waiting for successful CI" in u.check() and not controls


def test_stopped_or_legacy_bot_is_not_adopted(updater, monkeypatch):
    u, controls, _ = updater
    monkeypatch.setattr(u, "healthy", lambda: False)
    assert "Manually install/start" in u.check() and not controls


def test_foreign_unit_and_corrupt_deployment_state_refused(updater):
    u, controls, _ = updater
    atomic_write(service.location(u.settings.unit), "[Service]\nExecStart=/personal/tars\n")
    with pytest.raises(BridgeError, match="unmanaged"):
        u.check()
    assert not controls
    atomic_write(u.path, "{bad")
    with pytest.raises(BridgeError, match="Deployment state is corrupt"):
        deployment.Updater(u.config, u.settings)
    assert u.path.read_text() == "{bad"


def test_stale_health_marker_not_accepted(config, tmp_path, monkeypatch):
    u = deployment.Updater(config, deployment.Settings("owner/project", tmp_path / "deploy"))
    monkeypatch.setattr(
        deployment, "run", lambda *a, **k: "ActiveState=active\nMainPID=123\nInvocationID=new"
    )
    record = {
        "protocol": 1,
        "pid": 123,
        "invocation": "old",
        "config": str(config.path),
        "ready": True,
    }
    atomic_write(config.state_dir / "ready.json", json.dumps(record))
    assert not u.healthy()
    record["invocation"] = "new"
    atomic_write(config.state_dir / "ready.json", json.dumps(record))
    assert u.healthy()
    record["ready"] = False
    atomic_write(config.state_dir / "ready.json", json.dumps(record))
    assert not u.healthy()


@pytest.mark.skipif(not shutil.which("systemd-analyze"), reason="systemd-analyze unavailable")
def test_updater_units_validate(config, tmp_path):
    folder = tmp_path / 'space % dollar$ quote" slash\\'
    folder.mkdir()
    config = replace(config, path=folder / "config.toml")
    settings = deployment.Settings("owner/project", tmp_path / "deploy")
    units = deployment.render_units(config, settings)
    service.verify_units(units)
    assert "OnUnitInactiveSec=300" in next(
        text for name, text in units.items() if name.endswith(".timer")
    )
    assert all(config.token not in text for text in units.values())


def test_installer_idempotent_and_does_not_start(updater, monkeypatch):
    u, controls, _ = updater
    monkeypatch.setattr(service, "verify_units", lambda _: None)
    deployment.install(u.config, u.settings)
    deployment.install(u.config, u.settings)
    assert all(action == "daemon-reload" for action, _ in controls)
    assert not list(deployment.settings_path(u.config).parent.glob("deployment.toml.backup-*"))
    assert deployment.Settings.load(u.config) == u.settings


def test_network_malformed_response_and_timeout_are_actionable(monkeypatch):
    github = deployment.GitHub("owner/project", 0.01)

    def fail(*args, **kwargs):
        raise TimeoutError()

    monkeypatch.setattr(deployment.urllib.request, "urlopen", fail)
    with pytest.raises(BridgeError, match="network read limit 0.01s"):
        github.head()


def test_command_timeout_kills_owned_process():
    with pytest.raises(LimitError, match="deployment test timed out"):
        deployment.run([sys.executable, "-c", "import time; time.sleep(60)"], "test", 0.02)


def test_installer_does_not_inherit_git_destination_or_credentials(monkeypatch):
    monkeypatch.setenv("GIT_DIR", "/an/unrelated/repository/.git")
    monkeypatch.setenv("GIT_WORK_TREE", "/an/unrelated/repository")
    monkeypatch.setenv("GH_TOKEN", "fake-test-credential")
    value = deployment.run(
        [
            sys.executable,
            "-c",
            "import os; print([os.getenv(k) for k in ('GIT_DIR', 'GIT_WORK_TREE', 'GH_TOKEN')]); print(os.getenv('GIT_CONFIG_NOSYSTEM'))",
        ],
        "environment-check",
        5,
        output=True,
    )
    assert value == "[None, None, None]\n1"


def test_pruning_preserves_registered_repository(updater):
    u, _, _ = updater
    releases = u.settings.directory / "releases"
    roots = []
    for i in range(5):
        sha = str(i) * 40
        root = releases / sha
        atomic_write(
            root / "candidate.json", json.dumps({"repository": u.settings.repository, "sha": sha})
        )
        roots.append(root)
    atomic_write(
        u.config.state_dir / "workspaces.json",
        json.dumps({"sessions": {"id": {"repo": str(roots[0] / "source")}}}),
    )
    u.prune()
    assert roots[0].exists() and not roots[1].exists()
    assert all(path.exists() for path in roots[2:])
