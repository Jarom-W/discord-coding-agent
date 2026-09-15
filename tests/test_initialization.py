"""Preparation uses its shared budget; real Rpc.call timers still bound later requests."""

import asyncio
import json
from dataclasses import replace
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from conftest import complete
from jsonschema import validate

from discord_coding_agent import protocol
from discord_coding_agent.config import Timeouts
from discord_coding_agent.engine import Engine
from discord_coding_agent.rpc import Rpc

FIXTURES = Path(__file__).parent / "fixtures/codex-0.153.4"


class GatedRpc(Rpc):
    """Keep actual correlation/timers, gate responses, validate versioned request schemas."""

    def __init__(self, *args):
        super().__init__(*args)
        self.calls = []
        self.arrived = {}
        self.gates = {}

    async def start(self, *_):
        self.tasks = [asyncio.create_task(self.respond())]

    async def respond(self):
        while True:
            message = await self.outbound.get()
            try:
                if "id" not in message:
                    assert message["method"] == "initialized"
                    continue
                method, params = message["method"], message["params"]
                self.calls.append((method, params))
                schemas = {
                    "config/read": "ConfigReadParams",
                    "thread/start": "ThreadStartParams",
                    "thread/resume": "ThreadResumeParams",
                    "turn/start": "TurnStartParams",
                }
                if method in schemas:
                    validate(params, json.loads((FIXTURES / f"{schemas[method]}.json").read_text()))
                if method == "initialize":
                    result = {}
                elif method == "config/read":
                    result = {
                        "config": {
                            "approval_policy": "on-request",
                            "approvals_reviewer": "user",
                            "sandbox_mode": "workspace-write",
                        }
                    }
                elif method in {"thread/start", "thread/resume"}:
                    assert params["approvalPolicy"] == "on-request"
                    assert params["sandbox"] == "workspace-write"
                    result = {
                        "thread": {"id": params.get("threadId", "thread-1")},
                        "cwd": params["cwd"],
                        "approvalPolicy": "on-request",
                        "approvalsReviewer": "user",
                        "sandbox": {"type": "workspaceWrite"},
                    }
                elif method == "turn/start":
                    result = {"turn": {"id": "turn-1", "status": "inProgress"}}
                else:
                    raise AssertionError(f"Unsupported fixture method: {method}")
                self.arrived.setdefault(method, asyncio.Event()).set()
                if gate := self.gates.get(method):
                    await gate.wait()
                self.pending[message["id"]].set_result(result)
            finally:
                self.outbound.task_done()

    async def wait_for_call(self, method):
        await asyncio.wait_for(self.arrived.setdefault(method, asyncio.Event()).wait(), 1)


@pytest.fixture
async def preparation(engine, monkeypatch):
    e, sink, _ = engine
    e.config = replace(e.config, timeouts=Timeouts(shutdown=0.05))
    e.state.thread_id = "thread-1"
    e.state.last_result = "previous saved result"
    e.save()
    monkeypatch.setattr(
        "discord_coding_agent.engine.repository_identity", lambda _: e.store.identity
    )
    monkeypatch.setattr(
        "discord_coding_agent.engine.discover_codex", lambda _: Path("/unused/codex")
    )
    monkeypatch.setattr(protocol, "version", AsyncMock(return_value="codex-cli 0.153.4"))
    rpc = GatedRpc(e.config.timeouts, e.event, e.request, e.disconnected)
    e.rpc_factory = lambda *_: rpc
    e._prepare = Engine._prepare.__get__(e)
    yield e, sink, rpc
    await e.close()
    await rpc.close()


@pytest.fixture
async def advance_clock(monkeypatch):
    loop = asyncio.get_running_loop()
    clock = loop.time
    offset = 0

    async def advance(seconds):
        nonlocal offset
        offset += seconds
        for _ in range(8):
            await asyncio.sleep(0)

    monkeypatch.setattr(loop, "time", lambda: clock() + offset)
    return advance


@pytest.mark.parametrize("method", ["config/read", "thread/start", "thread/resume"])
async def test_preparation_survives_ordinary_rpc_limit(preparation, owner, advance_clock, method):
    e, sink, rpc = preparation
    if method == "thread/start":
        e.state.thread_id = None
    rpc.gates[method] = asyncio.Event()
    await e.message(owner, "How is the repo looking?")
    await rpc.wait_for_call(method)
    # Defaults: request=45, initialization=120. This must not expire a startup RPC.
    await advance_clock(60)
    assert e.busy and e.phase == "initializing"
    assert not any(m == "turn/start" for m, _ in rpc.calls)
    assert not any(future.done() for future in rpc.pending.values())
    assert f"Preparation step: {method}" in e.status()
    assert "initialization budget: 120s; ordinary RPC limit: 45s" in e.status(detailed=True)
    rpc.gates[method].set()
    await rpc.wait_for_call("turn/start")
    for _ in range(8):
        await asyncio.sleep(0)
    complete(e, "Repository inspected")
    await asyncio.wait_for(e.worker, 1)
    assert e.state.thread_id == "thread-1" and not e.state.interrupted
    assert e.state.last_result == "Repository inspected"
    assert len([m for m, _ in rpc.calls if m == "turn/start"]) == 1
    assert not any("timed out" in text for text, _ in sink.texts)


async def test_initialization_budget_is_shared_across_steps(preparation, owner, advance_clock):
    e, sink, rpc = preparation
    rpc.gates = {method: asyncio.Event() for method in ["config/read", "thread/resume"]}
    await e.message(owner, "How is the repo looking?")
    await rpc.wait_for_call("config/read")
    await advance_clock(80)
    rpc.gates["config/read"].set()
    await rpc.wait_for_call("thread/resume")
    await advance_clock(50)  # 130 total; neither individual step took 120 seconds.
    await asyncio.wait_for(e.worker, 1)
    assert e.phase == "failed" and e.state.interrupted
    assert e.state.thread_id == "thread-1" and e.state.last_result == "previous saved result"
    errors = "\n".join(text for text, _ in sink.texts)
    assert "initialization timed out" in errors and "configured limit 120s" in errors
    assert "Preparation step: thread/resume" in errors and "prompt was not submitted" in errors
    assert "full task deadline" not in errors
    assert not any(m == "turn/start" for m, _ in rpc.calls)
    assert rpc.closing and not rpc.pending and not rpc.tasks


async def test_full_task_deadline_still_applies_while_resuming(preparation, owner, advance_clock):
    e, sink, rpc = preparation
    rpc.gates["thread/resume"] = asyncio.Event()
    await e.message(owner, "!run 30s How is the repo looking?")
    await rpc.wait_for_call("thread/resume")
    await advance_clock(31)
    await asyncio.wait_for(e.worker, 1)
    assert e.state.interrupted
    errors = "\n".join(text for text, _ in sink.texts)
    assert "full task deadline" in errors and "configured limit 30s" in errors
    assert "initialization timed out" not in errors
    assert not any(m == "turn/start" for m, _ in rpc.calls)


async def test_turn_start_retains_request_timeout_after_resume(preparation, owner, advance_clock):
    e, sink, rpc = preparation
    rpc.gates["turn/start"] = asyncio.Event()
    await e.message(owner, "How is the repo looking?")
    await rpc.wait_for_call("turn/start")
    await advance_clock(46)
    await asyncio.wait_for(e.worker, 1)
    assert e.state.interrupted
    errors = "\n".join(text for text, _ in sink.texts)
    assert "RPC turn/start timed out" in errors and "configured limit 45s" in errors
    assert "prompt was not submitted" not in errors  # This submission is uncertain.
    saved = e.store.load().last_interruption
    assert saved["turn_submission_attempted"] is True
    assert "acceptance or effects may be uncertain" in saved["failure"]
    assert len([m for m, _ in rpc.calls if m == "turn/start"]) == 1
    assert rpc.closing and not rpc.pending and not rpc.tasks


async def test_stop_and_session_change_during_resume(preparation, owner):
    e, sink, rpc = preparation
    rpc.gates["thread/resume"] = asyncio.Event()
    await e.message(owner, "How is the repo looking?")
    await rpc.wait_for_call("thread/resume")
    await e.message(replace(owner, message=101), "!new auto")
    assert e.state.thread_id == "thread-1" and e.state.mode == "manual"
    await asyncio.wait_for(e.command(owner, "!stop"), 0.1)
    await asyncio.wait_for(e.stop_worker, 0.5)
    assert not e.busy and e.state.interrupted and e.activity.fd is None
    assert not any(m == "turn/start" for m, _ in rpc.calls)
    assert rpc.closing and not rpc.pending and not rpc.tasks
    assert any("does not undo" in text for text, _ in sink.texts)


async def test_initialize_failure_is_durable_and_a_fresh_message_recovers(
    preparation, owner, advance_clock
):
    e, sink, rpc = preparation
    rpc.gates["initialize"] = asyncio.Event()
    await e.message(owner, "first request must not be replayed")
    await rpc.wait_for_call("initialize")
    await advance_clock(121)
    await asyncio.wait_for(e.worker, 1)
    assert e.phase == "failed" and not e.busy and e.activity.fd is None
    assert not any(m == "turn/start" for m, _ in rpc.calls)
    persisted = e.store.load()
    assert "initialization timed out" in persisted.last_interruption["failure"]
    assert "prompt was not submitted" in persisted.last_interruption["failure"]
    assert "initialization timed out" in e.status(detailed=True)
    assert persisted.last_result == "previous saved result"
    assert persisted.last_interruption["turn_submission_attempted"] is False
    restored = Engine(e.config, e.store, sink)
    assert persisted.last_interruption["failure"] in restored.status(detailed=True)
    assert restored.state.thread_id == "thread-1"
    assert not restored.busy and restored.rpc is None
    assert "input closed" not in e.status()
    sink.texts.clear()  # Simulate a notification the owner never received.
    recovered = GatedRpc(e.config.timeouts, e.event, e.request, e.disconnected)
    e.rpc_factory = lambda *_: recovered
    try:
        await e.message(replace(owner, message=101), "fresh explicit task")
        await recovered.wait_for_call("turn/start")
        for _ in range(4):
            await asyncio.sleep(0)
        complete(e, "Recovered task result")
        await asyncio.wait_for(e.worker, 1)
        submitted = [p["input"][0]["text"] for m, p in recovered.calls if m == "turn/start"]
        assert submitted == ["fresh explicit task"]
        assert e.phase == "idle" and not e.state.interrupted
    finally:
        await recovered.close()


async def test_unknown_startup_failure_is_sanitized_and_retained(engine, owner, caplog):
    e, sink, _ = engine
    private_detail = "secret token and private prompt body"

    async def fail():
        raise RuntimeError(private_detail)

    e._prepare = fail
    await e.message(owner, "prompt must not appear in diagnostics")
    await e.worker
    failure = e.store.load().last_interruption["failure"]
    assert "Bridge task failed" in failure and "prompt was not submitted" in failure
    assert private_detail not in failure + caplog.text + repr(sink.texts)
    assert "prompt must not appear" not in e.store.path.read_text() + caplog.text
    assert not e.busy and e.activity.fd is None
