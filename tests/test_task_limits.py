import asyncio
from dataclasses import replace

import pytest
from conftest import approval, begin, complete

from discord_coding_agent.engine import Origin, parse_task_limit
from discord_coding_agent.errors import BridgeError


@pytest.mark.parametrize(
    "value,seconds", [("90s", 90), ("30m", 1800), ("1.5h", 5400), ("2H", 7200), ("unlimited", 0)]
)
def test_explicit_duration(value, seconds):
    assert parse_task_limit(value) == seconds


@pytest.mark.parametrize(
    "value", ["0", "0s", "-1m", "nan", "infh", "1e9s", "30", "30minutes", "1m30s", "9" * 400 + "h"]
)
def test_invalid_duration(value):
    with pytest.raises(BridgeError):
        parse_task_limit(value)


async def test_no_deadline_after_more_than_an_hour(engine, owner, monkeypatch):
    e, sink, rpc = engine
    e.config = replace(e.config, timeouts=replace(e.config.timeouts, task=0))
    await begin(e, owner)
    loop = asyncio.get_running_loop()
    clock = loop.time
    # Advance the real scheduler's clock instead of waiting an hour. A scheduled
    # 3600-second deadline would now fire; initialization has already finished.
    with monkeypatch.context() as patch:
        patch.setattr(loop, "time", lambda: clock() + 3601)
        for _ in range(5):
            await asyncio.sleep(0)
        assert e.busy and not e.done.done()
        assert "Active task limit: none (unlimited)" in e.status()
        complete(e, "Finished after the old deadline")
        await e.worker
    assert not e.state.interrupted
    assert e.state.last_result == "Finished after the old deadline"
    assert len([call for call in rpc.calls if call[0] == "turn/start"]) == 1
    assert not any("timed out" in text for text, _ in sink.texts)


async def test_explicit_limit_expires_during_human_wait(engine, owner):
    e, sink, rpc = engine
    e.config = replace(e.config, timeouts=replace(e.config.timeouts, task=0))
    await e.message(owner, "!run 0.03s inspect the repository")
    for _ in range(20):
        if e.turn_id:
            break
        await asyncio.sleep(0)
    p = approval(e)
    assert "Active task limit: 0.03s" in e.status()
    await asyncio.wait_for(e.worker, 1)
    assert e.state.interrupted and not e.pending
    assert p.id in sink.invalidated and not rpc.replies
    assert e.state.last_interruption["task_limit_seconds"] == 0.03
    assert any(method == "turn/interrupt" for method, _ in rpc.calls)
    assert any("full task deadline" in text and "0.03s" in text for text, _ in sink.texts)


async def test_unlimited_override_preserves_oversight_and_stop(engine, owner):
    e, sink, rpc = engine
    e.config = replace(e.config, timeouts=replace(e.config.timeouts, task=0.01))
    await e.message(owner, "!run unlimited fix tests")
    await asyncio.sleep(0.03)
    assert e.busy and not e.done.done()
    p = approval(e)
    assert e.phase == "awaiting approval" and not rpc.replies
    await e.message(replace(owner, message=101), "!stop")
    await asyncio.wait_for(e.stop_worker, 1)
    assert e.state.interrupted and not e.busy
    assert p.id in sink.invalidated
    assert not rpc.replies


async def test_run_override_is_single_task_and_preserves_prompt(engine, owner):
    e, _, rpc = engine
    await e.message(owner, "!run 2h Only work for 30 minutes.\nInspect the repository.")
    await asyncio.sleep(0)
    assert e.task_limit == 7200
    params = next(params for method, params in rpc.calls if method == "turn/start")
    assert params["input"][0]["text"] == "Only work for 30 minutes.\nInspect the repository."
    complete(e)
    await e.worker
    assert "Default task limit: 3s" in e.status()
    await begin(e, replace(owner, message=101))
    assert e.task_limit == 3 and e.state.thread_id == "thread-1"
    complete(e)
    await e.worker


async def test_run_exclusivity_dedup_and_busy_timer_unchanged(engine, owner):
    e, sink, rpc = engine
    await asyncio.gather(
        e.message(owner, "!run 2h first"),
        e.message(owner, "!run 2h duplicate"),
        e.message(replace(owner, message=101), "!run 1s second"),
        e.message(replace(owner, message=102), "ordinary busy message"),
    )
    await asyncio.sleep(0)
    assert e.task_limit == 7200
    assert len([call for call in rpc.calls if call[0] == "turn/start"]) == 1
    await e.followups.worker
    assert sum("NOT submitted" in text for text, _ in sink.texts) == 1
    assert e.followups.accepted == 1
    complete(e)
    await e.worker


@pytest.mark.parametrize("command", ["!run", "!run 30m", "!run bananas edit", "!run 0s edit"])
async def test_invalid_run_not_submitted(engine, owner, command):
    e, sink, rpc = engine
    await e.message(owner, command)
    assert not e.busy and not rpc.calls
    assert "!run DURATION task text" in sink.texts[-1][0]


@pytest.mark.parametrize(
    "origin",
    [Origin(99, 22, 33, 1), Origin(11, 99, 33, 1), Origin(11, 22, 99, 1), Origin(11, None, 33, 1)],
)
async def test_run_unauthorized(engine, origin):
    e, sink, rpc = engine
    await e.message(origin, "!run unlimited edit")
    assert not e.busy and not rpc.calls and not sink.texts


async def test_run_connection_only_and_attachments_rejected(engine, owner):
    e, sink, rpc = engine
    e.connection_only = True
    await e.message(owner, "!run unlimited edit")
    assert "Connection-only" in sink.texts[-1][0]
    e.connection_only = False
    await e.message(replace(owner, message=101), "!run 30m read attachment", attachments=True)
    assert "Attachments are unsupported" in sink.texts[-1][0]
    assert not e.busy and not rpc.calls


async def test_unlimited_retains_initialization_timeout(engine, owner):
    e, sink, _ = engine
    e.config = replace(e.config, timeouts=replace(e.config.timeouts, task=0, initialization=0.01))

    async def slow():
        await asyncio.sleep(60)

    e._prepare = slow
    await e.message(owner, "start work")
    await asyncio.wait_for(e.worker, 1)
    assert e.state.interrupted
    assert any("initialization timed out" in text for text, _ in sink.texts)
    assert not any("full task deadline" in text for text, _ in sink.texts)
