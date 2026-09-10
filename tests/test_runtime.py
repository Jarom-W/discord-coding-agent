import json
from pathlib import Path

import pytest

from discord_coding_agent import runtime

SHA = "a" * 40


@pytest.fixture
def install(tmp_path, monkeypatch):
    release = tmp_path / "releases" / SHA
    prefix = release / ".venv"
    package = prefix / "lib/python3.13/site-packages/discord_coding_agent"
    package.mkdir(parents=True)
    monkeypatch.setattr(runtime, "__file__", str(package / "runtime.py"))
    monkeypatch.setattr(runtime.sys, "prefix", str(prefix))
    monkeypatch.setattr(runtime.sys, "executable", str(prefix / "bin/python"))
    marker = release / "prepared.json"
    marker.write_text(json.dumps({"sha": SHA, "repository": "owner/project"}))
    runtime.current.cache_clear()
    yield marker
    runtime.current.cache_clear()


def test_release_identity_is_from_installed_package_and_captured_once(install):
    identity = runtime.current()
    assert identity.revision == SHA and identity.repository == "owner/project"
    assert identity.replies == "inline-text"
    assert str(install.parent / ".venv") in identity.python
    install.unlink()
    assert runtime.current() is identity  # Reconnects/checkout updates cannot change provenance.


@pytest.mark.parametrize("marker", ["{bad", "[]", "x" * 5000, "{}"])
def test_invalid_provenance_does_not_claim_a_running_revision(install, marker):
    install.write_text(marker)
    assert runtime.current().revision is None


def test_editable_package_does_not_claim_release_from_venv_or_environment(install, monkeypatch):
    monkeypatch.setattr(runtime, "__file__", str(install.parent / "source/runtime.py"))
    monkeypatch.setenv("DCA_REVISION", SHA)
    assert runtime.current().revision is None


def test_manual_install_does_not_claim_cwd_git_commit(install, monkeypatch):
    monkeypatch.setattr(runtime.sys, "prefix", str(Path("/manual/.venv")))
    monkeypatch.chdir(install.parent)
    assert runtime.current().revision is None
