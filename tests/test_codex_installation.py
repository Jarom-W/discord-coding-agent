from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from discord_coding_agent import protocol
from discord_coding_agent.errors import BridgeError


def executable(path, data=b"\x7fELFfixture"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    path.chmod(0o755)
    return path


def test_cli_only_archive_missing_helper_is_actionable(tmp_path):
    cli = executable(tmp_path / "codex-x86_64-unknown-linux-musl")
    with pytest.raises(BridgeError, match="codex-code-mode-host") as caught:
        protocol.check_runtime_files(cli)
    assert "codex-package" in str(caught.value) and "No task prompt was submitted" in str(
        caught.value
    )
    helper = executable(tmp_path / "codex-code-mode-host")
    assert protocol.check_runtime_files(cli) == helper
    helper.chmod(0o600)
    with pytest.raises(BridgeError, match="non-executable"):
        protocol.check_runtime_files(cli)


def test_complete_package_and_symlink_check_real_native_location(tmp_path):
    root = tmp_path / "package"
    cli = executable(root / "bin/codex")
    helper = executable(root / "bin/codex-code-mode-host")
    (root / "codex-package.json").write_text('{"layoutVersion": 1}')
    link = tmp_path / "user-bin/codex"
    link.parent.mkdir()
    link.symlink_to(cli)
    with pytest.raises(BridgeError, match="codex-resources/bwrap"):
        protocol.check_runtime_files(link)
    for path in ["codex-path/rg", "codex-resources/bwrap", "codex-resources/zsh/bin/zsh"]:
        executable(root / path)
    assert protocol.check_runtime_files(link) == helper


def test_launcher_layout_is_not_guessed(tmp_path):
    cli = executable(tmp_path / "codex", b"#!/usr/bin/env node\n")
    assert protocol.check_runtime_files(cli) is None
    with pytest.raises(BridgeError, match="Cannot inspect"):
        protocol.check_runtime_files(tmp_path / "missing")


async def test_correct_cli_version_alone_no_longer_passes_preflight(tmp_path, monkeypatch):
    cli = executable(tmp_path / "codex")
    process = SimpleNamespace(
        returncode=0, communicate=AsyncMock(return_value=(b"codex-cli 0.153.4\n", b""))
    )
    monkeypatch.setattr(protocol.asyncio, "create_subprocess_exec", AsyncMock(return_value=process))
    with pytest.raises(BridgeError, match="Incomplete Codex runtime"):
        await protocol.version(cli, 1)
    executable(tmp_path / "codex-code-mode-host")
    assert await protocol.version(cli, 1) == "codex-cli 0.153.4"
