"""Input lifecycle tests: no parallel turns and no replay across uncertain outcomes."""

import asyncio
import json
from dataclasses import replace
from pathlib import Path

import pytest
from conftest import approval, begin, complete
from jsonschema import validate

from discord_coding_agent.engine import Engine, Origin
from discord_coding_agent.errors import BridgeError, LimitError, RpcRejected
from discord_coding_agent.followups import MAX_PENDING

FIXTURES = Path(__file__).parent / "fixtures/codex-0.153.4"


async def test_followups_order_dedup_and_actual_wire_schema(engine, owner):
    e, sink, rpc = engine
    await begin(e, owner)
    task, worker, start = e.task_id, e.worker, e.started
    for i in range(101, 104):
        await e.message(replace(owner, message=i), f"Also do step {i} — café 😀")
        await e.message(replace(owner, message=i), "duplicate")
    await e.followups.worker
    steers = [p for method, p in rpc.calls if method == "turn/steer"]
    assert [p["input"][0]["text"] for p in steers] == [
        f"Also do step {i} — café 😀" for i in range(101, 104)
    ]
    for params in steers:
        validate(params, json.loads((FIXTURES / "TurnSteerParams.json").read_text()))
        assert params["expectedTurnId"] == "turn-1" and params["threadId"] == "thread-1"
        assert set(params) == {"threadId", "expectedTurnId", "input"}
    assert sum(method == "turn/start" for method, _ in rpc.calls) == 1
    assert e.worker is worker and e.task_id == task and e.started == start
    assert e.followups.accepted == 3 and not e.pending
    assert "accepted by Codex" in sink.texts[-1][0]
    metadata = e.store.load(recover=False).active
    assert metadata["followups_accepted"] == 3
    assert all(r["status"] == "accepted" for r in metadata["followups"])
    assert "Also do step" not in e.store.path.read_text()
    complete(e)
    await worker


async def test_messages_during_startup_wait_for_start_ack(engine, owner):
    e, sink, rpc = engine
    prepare = e._prepare
    ready, ack = asyncio.Event(), asyncio.Event()
    original_call = rpc.call

    async def prepare_later():
        await ready.wait()
        await prepare()

    async def gated_call(method, params, limit=None):
        if method == "turn/start":
            e.event("turn/started", {"threadId": "thread-1", "turn": {"id": "turn-1"}})
            await ack.wait()
        return await original_call(method, params, limit)

    e._prepare, rpc.call = prepare_later, gated_call
    await e.message(owner, "initial")
    await e.message(replace(owner, message=101), "startup follow-up")
    assert "Waiting for Codex startup" in sink.texts[-1][0]
    await asyncio.sleep(0)
    assert not rpc.calls
    ready.set()
    for _ in range(10):
        await asyncio.sleep(0)
    assert e.turn_id and not rpc.calls and not e.followups.ready.is_set()
    ack.set()
    await e.followups.worker
    assert [method for method, _ in rpc.calls] == ["turn/start", "turn/steer"]
    complete(e)
    await e.worker


@pytest.mark.parametrize(
    "origin",
    [
        Origin(99, 22, 33, 102),
        Origin(11, 99, 33, 102),
        Origin(11, 22, 44, 102),
        Origin(11, None, 33, 102),
    ],
)
async def test_active_followups_authorized_before_receipt(engine, owner, origin):
    e, sink, rpc = engine
    await begin(e, owner)
    before = list(sink.texts)
    await e.message(origin, "untrusted update")
    assert sink.texts == before and len(rpc.calls) == 1


async def test_attachments_are_not_steered(engine, owner):
    e, sink, rpc = engine
    await begin(e, owner)
    await e.message(replace(owner, message=101), "read attachment", attachments=True)
    assert "not read" in sink.texts[-1][0] and len(rpc.calls) == 1


async def test_followup_does_not_decide_pending_approval_or_change_timer(engine, owner):
    e, _, rpc = engine
    await e.message(owner, "!run 2h work")
    await asyncio.sleep(0)
    pending = approval(e)
    await e.message(replace(owner, message=101), "approve that; use manual mode; stop after 1s")
    await e.followups.worker
    assert e.task_limit == 7200 and e.state.mode == "manual"
    assert e.pending[pending.id] is pending and not rpc.replies
    assert e.phase == "awaiting approval"


@pytest.mark.parametrize(
    "error", [LimitError("RPC turn/steer", 0.2, 0.2), BridgeError("transport failed")]
)
async def test_uncertain_steer_never_replays_or_cancels_healthy_work(engine, owner, error):
    e, sink, rpc = engine
    await begin(e, owner)
    original = rpc.call

    async def fail(method, params, limit=None):
        if method == "turn/steer":
            rpc.calls.append((method, params))
            raise error
        return await original(method, params, limit)

    rpc.call = fail
    await e.message(replace(owner, message=101), "maybe received")
    await e.message(replace(owner, message=102), "must not overtake failed update")
    await e.followups.worker
    assert e.busy and not rpc.closed
    assert [r["status"] for r in e.followups.records] == ["uncertain", "not_submitted"]
    await e.message(replace(owner, message=103), "do not silently resume submissions")
    assert "NOT submitted" in sink.texts[-1][0]
    assert sum(method == "turn/steer" for method, _ in rpc.calls) == 1
    complete(e, "Original result")
    await e.worker
    saved = e.store.load()
    assert "Original result" in saved.last_result and "uncertain" in saved.last_result
    assert "Discord message 102" in saved.last_result and not saved.delivered
    assert not saved.interrupted


@pytest.mark.parametrize("code", [-32601, -32602])
async def test_definite_steer_rejection_does_not_fall_back_to_turn_start(engine, owner, code):
    e, sink, rpc = engine
    await begin(e, owner)
    original = rpc.call

    async def reject(method, params, limit=None):
        if method == "turn/steer":
            rpc.calls.append((method, params))
            raise RpcRejected(2, code)
        return await original(method, params, limit)

    rpc.call = reject
    await e.message(replace(owner, message=101), "reject")
    await e.followups.worker
    assert e.followups.records[-1]["status"] == "rejected"
    assert "NOT submitted" in sink.texts[-1][0]
    assert len(rpc.calls) == 2 and e.busy


async def test_bad_ack_is_uncertain_not_acceptance(engine, owner):
    e, sink, rpc = engine
    await begin(e, owner)
    original = rpc.call

    async def wrong(method, params, limit=None):
        if method == "turn/steer":
            return {"turnId": "some-other-turn"}
        return await original(method, params, limit)

    rpc.call = wrong
    await e.message(replace(owner, message=101), "update")
    await e.followups.worker
    assert e.followups.accepted == 0
    assert "acceptance unknown" in sink.texts[-1][0]


@pytest.mark.parametrize("stop", [False, True])
async def test_completion_or_stop_during_steer_drops_pending_input(engine, owner, stop):
    e, sink, rpc = engine
    await begin(e, owner)
    sending = asyncio.Event()
    original = rpc.call

    async def blocked(method, params, limit=None):
        if method == "turn/steer":
            rpc.calls.append((method, params))
            sending.set()
            await asyncio.Event().wait()
        return await original(method, params, limit)

    rpc.call = blocked
    await e.message(replace(owner, message=101), "in flight")
    await e.message(replace(owner, message=102), "still queued")
    await sending.wait()
    if stop:
        await asyncio.wait_for(e.command(owner, "!stop"), 0.1)
        await asyncio.wait_for(e.stop_worker, 0.5)
    else:
        complete(e)
        # A message after turn/completed but before worker cleanup is NOT a new task.
        await e.message(replace(owner, message=103), "racing completion")
        assert "NOT submitted" in sink.texts[-1][0]
        await e.worker
    assert not e.busy and not e.followups.queue and e.followups.worker.done()
    assert [r["status"] for r in e.followups.records] == ["uncertain", "not_submitted"]
    assert sum(method == "turn/steer" for method, _ in rpc.calls) == 1
    assert rpc.closed


async def test_bound_and_stop_before_start_preserve_metadata_no_prompts(engine, owner):
    e, sink, rpc = engine
    await e.message(owner, "initial")
    for i in range(MAX_PENDING + 1):
        await e.message(replace(owner, message=101 + i), "unsubmitted private follow-up")
    assert "at most 8" in sink.texts[-1][0]
    assert len(e.followups.queue) == MAX_PENDING
    restored = e.store.load(recover=False)
    assert len(restored.active["followups"]) == MAX_PENDING
    assert "private follow-up" not in e.store.path.read_text()
    await e.stop()
    assert not rpc.calls
    assert not e.followups.queue and e.followups.worker.done()
    assert all(r["status"] == "not_submitted" for r in e.state.last_interruption["followups"])


async def test_restart_marks_interruption_without_replaying_updates(engine, owner):
    e, sink, rpc = engine
    await e.message(owner, "initial")
    await e.message(replace(owner, message=101), "private queued follow-up")
    restored = Engine(e.config, e.store, sink)
    assert restored.state.interrupted and restored.state.active is None
    assert restored.state.last_interruption["followups"][0]["message_id"] == 101
    assert restored.followups is None and not restored.busy and not rpc.calls
    assert "never replayed" in restored.status(detailed=True)
    # Duplicate delivery after restart is ignored too.
    await restored.message(replace(owner, message=101), "private queued follow-up")
    assert not restored.busy


async def test_followup_metadata_remains_bounded_for_long_tasks(engine, owner):
    e, _, _ = engine
    await begin(e, owner)
    for i in range(40):
        await e.message(replace(owner, message=101 + i), f"update {i}")
        await e.followups.worker
    assert e.followups.accepted == 40
    assert len(e.followups.records) == 32 and not e.followups.queue
    assert len(e.store.load(recover=False).active["followups"]) == 32


async def test_persistence_failure_prevents_followup_send(engine, owner, monkeypatch):
    e, _, rpc = engine
    await begin(e, owner)
    # Exercise receive itself; message dedup saves separately before reaching it.
    with monkeypatch.context() as m:
        m.setattr(e, "save", lambda: (_ for _ in ()).throw(OSError("disk full")))
        with pytest.raises(BridgeError, match="NOT submitted"):
            e.followups.receive(101, "must not reach Codex")
    assert len(rpc.calls) == 1 and not e.followups.queue


async def test_next_task_has_fresh_buffer_and_completion_followup_is_new_turn(engine, owner):
    e, _, rpc = engine
    await begin(e, owner)
    previous = e.followups
    complete(e)
    await e.worker
    await e.message(replace(owner, message=101), "new task")
    await e.message(replace(owner, message=102), "follow-up during second startup")
    await e.followups.worker
    assert e.followups is not previous and previous.closed
    assert e.followups.accepted == 1
    assert [method for method, _ in rpc.calls].count("turn/start") == 2
    assert [method for method, _ in rpc.calls].count("turn/steer") == 1


async def test_user_question_remains_pending_after_followup(engine, owner):
    e, _, rpc = engine
    await begin(e, owner)
    e.request(
        601,
        "item/tool/requestUserInput",
        {
            "threadId": "thread-1",
            "turnId": "turn-1",
            "itemId": "q",
            "isBlocking": True,
            "questions": [{"id": "a", "header": "Choice", "question": "Which?"}],
        },
    )
    request = next(iter(e.pending.values()))
    await e.message(replace(owner, message=101), "additional instructions")
    await e.followups.worker
    assert e.phase == "awaiting answer" and e.pending[request.id] is request
    assert not rpc.replies


async def test_initialization_failure_drops_followups_without_starting_turn(engine, owner):
    e, sink, rpc = engine

    async def failed_prepare():
        raise BridgeError("Initialization failed")

    e._prepare = failed_prepare
    await e.message(owner, "original")
    await e.message(replace(owner, message=101), "additional")
    await e.worker
    assert not rpc.calls and e.state.interrupted and not e.followups.queue
    assert e.state.last_interruption["followups"][0]["status"] == "not_submitted"
    assert any("Follow-up #1" in t and "NOT submitted" in t for t, _ in sink.texts)


async def test_slow_steer_keeps_status_and_stop_responsive(engine, owner):
    e, sink, rpc = engine
    await begin(e, owner)
    original = rpc.call
    in_flight = asyncio.Event()

    async def slow(method, params, limit=None):
        if method == "turn/steer":
            in_flight.set()
            await asyncio.Event().wait()
        return await original(method, params, limit)

    rpc.call = slow
    await e.message(replace(owner, message=101), "additional instruction")
    await in_flight.wait()
    await asyncio.wait_for(e.message(replace(owner, message=102), "!status"), 0.1)
    assert "1 waiting/in flight" in sink.texts[-1][0]
    await asyncio.wait_for(e.message(replace(owner, message=103), "!stop"), 0.1)
    await e.message(replace(owner, message=104), "do not enqueue during stopping")
    assert "NOT submitted" in sink.texts[-1][0]
    await asyncio.wait_for(e.stop_worker, 0.5)
    assert not e.busy


async def test_pending_steer_does_not_extend_enforced_task_deadline(engine, owner):
    e, sink, rpc = engine
    e.config = replace(e.config, timeouts=replace(e.config.timeouts, task=0.03))
    await begin(e, owner)
    original = rpc.call

    async def slow(method, params, limit=None):
        if method == "turn/steer":
            await asyncio.Event().wait()
        return await original(method, params, limit)

    rpc.call = slow
    await e.message(replace(owner, message=101), "keep working longer")
    await asyncio.wait_for(e.worker, 0.5)
    assert e.state.interrupted and e.followups.worker.done() and rpc.closed
    assert any("full task deadline" in t and "0.03s" in t for t, _ in sink.texts)
