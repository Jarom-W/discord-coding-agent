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
    assert "initialization budget: 120s; ordinary RPC limit: 45s" in e.status()
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
