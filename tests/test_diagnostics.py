import asyncio
import logging
import signal
import sys

import pytest

from discord_coding_agent.config import Timeouts
from discord_coding_agent.diagnostics import Diagnostics, host_report
from discord_coding_agent.errors import BridgeError
from discord_coding_agent.rpc import Rpc


def test_private_bounded_logs_redaction_rotation_restart(tmp_path):
    logs = Diagnostics(tmp_path, "discord-secret")
    logger = logging.getLogger("discord_coding_agent.test")
    try:
        for _ in range(600):
            logger.info(
                "operation=test token=another-secret discord-secret sk-private-key %s", "x" * 900
            )
        assert len(logs.rows) == 500
        assert logs.path.stat().st_size <= 256 * 1024
        assert list(logs.path.parent.glob("bridge.log.*"))
        for path in logs.path.parent.iterdir():
            assert path.stat().st_mode & 0o777 == 0o600
            text = path.read_text()
            assert not any(
                secret in text for secret in ["discord-secret", "another-secret", "sk-private-key"]
            )
        assert logs.path.parent.stat().st_mode & 0o777 == 0o700
        before = logs.sequence
        logging.getLogger("discord").critical("third-party-secret")
        assert logs.sequence == before
    finally:
        logs.close()
    restored = Diagnostics(tmp_path, "discord-secret")
    try:
        assert "operation=test" in restored.report()
        assert "third-party-secret" not in restored.report()
    finally:
        restored.close()


def test_log_disk_failure_keeps_memory_without_raw_traceback(tmp_path, monkeypatch, capsys):
    logs = Diagnostics(tmp_path, "secret")
    try:

        def fail(*_):
            raise OSError("secret")

        monkeypatch.setattr(logs.file, "shouldRollover", fail)
        logging.getLogger("discord_coding_agent.test").warning("safe event")
        assert "writes are failing" in logs.report()
        assert "safe event" in logs.report()
        assert "secret" not in capsys.readouterr().err
    finally:
        logs.close()


def test_host_report_missing_proc_and_known_metrics(tmp_path):
    assert "RAM/swap: unavailable" in host_report(tmp_path, proc=tmp_path)
    (tmp_path / "meminfo").write_text(
        "MemTotal: 8192000 kB\nMemAvailable: 4096000 kB\nSwapTotal: 1024000 kB\nSwapFree: 512000 kB\n"
    )
    assert "4000/8000 MiB; swap free/total: 500/1000 MiB" in host_report(tmp_path, proc=tmp_path)


@pytest.mark.parametrize("exit_code", [0, 7, -signal.SIGKILL])
async def test_eof_reports_exit_and_safe_stderr_hints(tmp_path, exit_code, caplog):
    errors = []
    rpc = Rpc(Timeouts(request=2, shutdown=0.1), lambda *_: None, lambda *_: None, errors.append)
    ending = (
        f"os.kill(os.getpid(), {signal.SIGKILL})" if exit_code < 0 else f"sys.exit({exit_code})"
    )
    script = (
        "import sys, os; sys.stdin.readline(); sys.stderr.write('panicked at sk-do-not-disclose token=hidden\\n'); sys.stderr.flush(); "
        + ending
    )
    with caplog.at_level(logging.INFO):
        await rpc.start([sys.executable, "-c", script], tmp_path)
        try:
            with pytest.raises(BridgeError, match="stdout disconnected") as caught:
                await rpc.call("initialize", {})
            text = str(caught.value)
            assert ("SIGKILL" if exit_code < 0 else f"exit code {exit_code}") in text
            assert "panic" in text and rpc.stderr_bytes > 0
            assert "sk-do-not-disclose" not in text + caplog.text
            assert "hidden" not in text + caplog.text
            assert len(errors) == 1 and not rpc.pending
        finally:
            await rpc.close()


async def test_eof_while_process_alive_is_bounded(tmp_path):
    rpc = Rpc(Timeouts(request=2, shutdown=0.05), lambda *_: None, lambda *_: None, lambda *_: None)
    await rpc.start([sys.executable, "-c", "import os,time; os.close(1); time.sleep(30)"], tmp_path)
    try:
        with pytest.raises(BridgeError, match="exit not yet observed"):
            await asyncio.wait_for(rpc.call("initialize", {}), 1)
        assert rpc.process.returncode is None
    finally:
        await rpc.close()


def test_log_symlink_rejected(tmp_path):
    directory = tmp_path / "logs"
    directory.mkdir()
    target = tmp_path / "keep"
    target.write_text("do not alter")
    (directory / "bridge.log").symlink_to(target)
    logs = Diagnostics(tmp_path, "token")
    try:
        assert "writes are failing" in logs.report() and logs.file is None
        logging.getLogger("discord_coding_agent.test").info("still available")
        assert "still available" in logs.report()
    finally:
        logs.close()
    assert target.read_text() == "do not alter"
