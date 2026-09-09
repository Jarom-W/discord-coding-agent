import json
import shutil
import subprocess
from dataclasses import replace
from pathlib import Path

import pytest

from discord_coding_agent import service
from discord_coding_agent.config import Config, Timeouts
from discord_coding_agent.errors import BridgeError
from discord_coding_agent.state import atomic_write


def write_config(tmp_path):
    repo = tmp_path / "repo"
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    path = tmp_path / "config/config.toml"
    data = {
        "DISCORD_TOKEN": "fake-test-token",
        "DISCORD_OWNER_ID": "1",
        "DISCORD_GUILD_ID": "2",
        "DISCORD_CHANNEL_ID": "3",
        "CODEX_REPO": str(repo),
    }
    atomic_write(path, "\n".join(f"{k} = {json.dumps(v)}" for k, v in data.items()))
    return path, data


def test_configuration_private_and_env_override(tmp_path, monkeypatch):
    path, _ = write_config(tmp_path)
    monkeypatch.setenv("DISCORD_OWNER_ID", "42")
    config = Config.load(path)
    assert config.owner_id == 42 and "fake-test-token" not in repr(config)
    path.chmod(0o644)
    with pytest.raises(BridgeError, match="600"):
        Config.load(path)


@pytest.mark.parametrize(
    "field,value",
    [
        ("DISCORD_OWNER_ID", "name"),
        ("DISCORD_GUILD_ID", "0"),
        ("APPROVAL_MODE", "blind"),
        ("DISCORD_TOKEN", "bad token"),
    ],
)
def test_invalid_configuration(tmp_path, monkeypatch, field, value):
    path, _ = write_config(tmp_path)
    monkeypatch.setenv(field, value)
    with pytest.raises(BridgeError):
        Config.load(path)


@pytest.mark.parametrize("value", [0, -1, float("nan"), float("inf"), True])
def test_timeout_validation(value):
    with pytest.raises(BridgeError):
        Timeouts(task=value)


def test_service_idempotent_install_preserves_unmanaged(config, monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "user-config"))
    monkeypatch.setattr(service, "verify", lambda *_: None)
    monkeypatch.setattr(service, "discover_codex", lambda *_: Path("/usr/bin/codex"))
    calls = []
    monkeypatch.setattr(service, "control", lambda *args: calls.append(args))
    path = service.install(config, service.DEFAULT_UNIT)
    original = path.read_text()
    service.install(config, service.DEFAULT_UNIT)
    assert path.read_text() == original and not list(path.parent.glob("*.backup-*"))
    service.install(replace(config, path=tmp_path / "another/config.toml"), service.DEFAULT_UNIT)
    assert list(path.parent.glob("*.backup-*"))
    path.write_text("[Service]\nExecStart=/personal/tars\n")
    with pytest.raises(BridgeError, match="unmanaged"):
        service.install(config, service.DEFAULT_UNIT)
    assert "tars" in path.read_text()
    assert all(action == "daemon-reload" for action, _ in calls)


@pytest.mark.skipif(not shutil.which("systemd-analyze"), reason="systemd-analyze unavailable")
def test_service_unit_validation_and_escaping(config, tmp_path):
    special = tmp_path / 'space percent% dollar$ quote" slash\\'
    special.mkdir()
    unit = service.render(replace(config, path=special / "config.toml"), Path("/usr/bin/true"))
    assert f"WorkingDirectory={str(special).replace('%', '%%')}/\n" in unit
    assert "fake-test-token" not in unit and config.token not in unit
    service.verify(unit)
    assert '"' != unit.split("WorkingDirectory=", 1)[1][0]


def test_service_rejects_newline_injection(config):
    with pytest.raises(BridgeError):
        service.render(
            replace(config, path=Path("/tmp/evil\nExecStart=/bin/evil/config.toml")),
            Path("/usr/bin/true"),
        )
    with pytest.raises(BridgeError):
        service.unit_name("tars.service")
