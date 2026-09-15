import asyncio
import json
from dataclasses import replace

import pytest
from conftest import approval, begin, complete

from discord_coding_agent.engine import Engine, Origin
from discord_coding_agent.errors import BridgeError


@pytest.mark.parametrize(
    "origin",
    [Origin(99, 22, 33, 1), Origin(11, 99, 33, 1), Origin(11, 22, 99, 1), Origin(11, None, 33, 1)],
)
async def test_authorization(engine, origin):
    e, sink, rpc = engine
    await e.message(origin, "!new auto")
    await e.message(origin, "edit files")
    assert not e.busy and e.state.mode == "manual" and not sink.texts


async def test_atomic_exclusivity_and_dedup(engine, owner):
    e, sink, rpc = engine
    await asyncio.gather(
        e.message(owner, "first"),
        e.message(owner, "duplicate"),
        e.message(replace(owner, message=101), "second"),
    )
    await e.followups.worker
    assert len([c for c in rpc.calls if c[0] == "turn/start"]) == 1
    assert len([c for c in rpc.calls if c[0] == "turn/steer"]) == 1
    assert e.followups.accepted == 1
    assert len(e.state.seen_messages) == 2
    complete(e)
    await e.worker


async def test_attachment_rejection(engine, owner):
    e, sink, _ = engine
    await e.message(owner, "read it", attachments=True)
    assert not e.busy and "not read" in sink.texts[-1][0]


async def test_mode_switch_active_rejected_and_idle_persisted(engine, owner):
    e, sink, _ = engine
    await begin(e, owner)
    await e.message(replace(owner, message=101), "!new auto")
    assert e.state.mode == "manual"
    assert "Cannot switch" in sink.texts[-1][0]
    complete(e)
    await e.worker
    await e.message(replace(owner, message=102), "!new auto")
    resumed = e.store.load()
    assert resumed.mode == "auto" and resumed.thread_id is None
    assert resumed.last_result == "Done"


async def test_thread_persistence_result_before_delivery(engine, owner):
    e, sink, _ = engine
    await begin(e, owner)
    complete(e, "a complete result")
    await e.worker
    restored = Engine(e.config, e.store, sink)
    assert restored.state.thread_id == "thread-1"
    assert restored.state.last_result == "a complete result"
    assert not restored.state.delivered
    assert restored.state.seen_messages == [owner.message]


async def test_concurrent_button_and_text_one_decision(engine, owner):
    e, _, rpc = engine
    await begin(e, owner)
    p = approval(e)

    async def decide(button):
        try:
            return e.decide(owner, p.id, True, button_message=900 if button else None)
        except BridgeError as exc:
            return str(exc)

    answers = await asyncio.gather(decide(True), decide(False))
    assert len(rpc.replies) == 1
    assert sum("expired" in a for a in answers) == 1


@pytest.mark.parametrize("change", ["owner", "guild", "channel", "message", "turn", "task"])
async def test_stale_and_foreign_buttons(engine, owner, change):
    e, _, rpc = engine
    await begin(e, owner)
    p = approval(e)
    origin = replace(owner, **{change: 99}) if change in {"owner", "guild", "channel"} else owner
    if change == "turn":
        e.turn_id = "other-turn"
    if change == "task":
        e.task_id = "other-task"
    with pytest.raises(BridgeError):
        e.decide(origin, p.id, True, button_message=99 if change == "message" else 900)
    assert not rpc.replies


async def test_completion_invalidates_request(engine, owner):
    e, sink, rpc = engine
    await begin(e, owner)
    p = approval(e)
    complete(e)
    with pytest.raises(BridgeError):
        e.decide(owner, p.id, True)
    await e.worker
    assert p.id in sink.invalidated and not rpc.replies


async def test_resolved_event_and_expiry(engine, owner):
    e, sink, rpc = engine
    await begin(e, owner)
    p = approval(e)
    e.event("serverRequest/resolved", {"threadId": "thread-1", "requestId": p.rpc_id})
    assert not e.pending
    e.config = replace(e.config, timeouts=replace(e.config.timeouts, user_wait=0.01))
    p = approval(e, rpc_id=501)
    await asyncio.sleep(0.03)
    assert p.id not in e.pending and rpc.replies[-1][2]
    assert "configured limit 0.01s" in sink.texts[-1][0]


async def test_questions_multiple_and_secret(engine, owner):
    e, _, rpc = engine
    await begin(e, owner)
    params = {
        "threadId": "thread-1",
        "turnId": "turn-1",
        "itemId": "q",
        "isBlocking": True,
        "questions": [
            {"id": "a", "header": "A", "question": "Which?"},
            {"id": "b", "header": "B", "question": "Why?"},
        ],
    }
    e.request(600, "item/tool/requestUserInput", params)
    p = next(iter(e.pending.values()))
    e.bind(p.id, 900)
    with pytest.raises(BridgeError):
        e.decide(owner, p.id, False, answer="one")
    e.decide(owner, p.id, False, answer=json.dumps({"a": ["first"], "b": ["second"]}))
    assert rpc.replies[-1][1] == {
        "answers": {"a": {"answers": ["first"]}, "b": {"answers": ["second"]}}
    }
    params["questions"][0]["isSecret"] = True
    e.request(601, "item/tool/requestUserInput", params)
    assert rpc.replies[-1][2] and not e.pending


async def test_unsupported_request_returns_error(engine, owner):
    e, sink, rpc = engine
    await begin(e, owner)
    e.request(900, "unknown/method", {})
    assert rpc.replies[-1][2] and "Unsupported" in sink.texts[-1][0]


async def test_file_diff_preserved(engine, owner):
    e, sink, _ = engine
    await begin(e, owner)
    diff = "--- old\n+++ new\n" + "+code\n" * 1000
    e.event(
        "item/started",
        {
            "threadId": "thread-1",
            "turnId": "turn-1",
            "item": {
                "id": "item-1",
                "type": "fileChange",
                "changes": [{"path": "a.py", "diff": diff}],
            },
        },
    )
    p = approval(e, "item/fileChange/requestApproval")
    assert json.loads(p.details)["item"]["changes"][0]["diff"] == diff


async def test_stop_initializing_remains_responsive(engine, owner):
    e, sink, _ = engine

    async def slow():
        await asyncio.sleep(60)

    e._prepare = slow
    await e.message(owner, "do work")
    await asyncio.sleep(0)
    await asyncio.wait_for(e.command(owner, "!stop"), 0.1)
    await asyncio.wait_for(e.stop_worker, 0.5)
    assert e.state.interrupted and not e.busy
    assert any("does not undo" in text for text, _ in sink.texts)


async def test_task_and_init_timeout_classification(engine, owner):
    e, sink, _ = engine
    e.config = replace(e.config, timeouts=replace(e.config.timeouts, task=0.02))
    await begin(e, owner)
    await e.worker
    assert e.state.interrupted
    assert any("full task deadline" in text and "0.02s" in text for text, _ in sink.texts)
    e.config = replace(e.config, timeouts=replace(e.config.timeouts, task=1, initialization=0.01))

    async def slow():
        await asyncio.sleep(60)

    e._prepare = slow
    await e.message(replace(owner, message=101), "next explicit request")
    await e.worker
    assert any("initialization timed out" in text for text, _ in sink.texts)


async def test_persistence_failure_prevents_submission(engine, owner, monkeypatch):
    e, _, rpc = engine

    def fail():
        raise OSError("disk full")

    monkeypatch.setattr(e, "save", fail)
    with pytest.raises(OSError):
        await e.message(owner, "do work")
    assert not e.busy and not rpc.calls


async def test_no_automatic_accept_in_auto_mode(engine, owner):
    e, sink, rpc = engine
    e.state.mode = "auto"
    await begin(e, owner)
    p = approval(e)
    assert p.id in e.pending and not rpc.replies and sink.requests


async def test_stop_before_task_coroutine_starts(engine, owner):
    e, _, _ = engine
    await e.message(owner, "work")
    await e.stop()
    assert not e.busy and e.state.interrupted and e.state.active is None


async def test_last_explicitly_redelivers_and_prose_does_not_switch(engine, owner):
    e, sink, _ = engine
    await begin(e, owner)
    complete(e, "saved")
    await e.worker
    await e.command(owner, "!last")
    assert sink.texts[-1] == ("saved", None)
    await e.message(replace(owner, message=101), "open a new chat")
    await asyncio.sleep(0)
    assert e.state.thread_id == "thread-1"


async def test_automatic_review_rejection_action_and_rationale(engine, owner):
    e, sink, _ = engine
    await begin(e, owner)
    e.event(
        "item/autoApprovalReview/completed",
        {
            "threadId": "thread-1",
            "turnId": "turn-1",
            "action": {"command": "id", "cwd": str(e.config.repo)},
            "review": {"status": "denied", "rationale": "Example policy reason"},
        },
    )
    assert "automatic approval review: denied" in sink.texts[-1][0]
    assert "Example policy reason" in sink.texts[-1][0] and '"command": "id"' in sink.texts[-1][0]


async def test_decision_before_complete_delivery_refused(engine, owner):
    e, _, rpc = engine
    await begin(e, owner)
    p = approval(e)
    p.message_id = None
    with pytest.raises(BridgeError, match="complete request"):
        e.decide(owner, p.id, True)
    assert not rpc.replies


async def test_turn_failure_with_pending_stays_failed(engine, owner):
    e, _, _ = engine
    await begin(e, owner)
    approval(e)
    e.disconnected(BridgeError("fixture disconnect"))
    await e.worker
    assert e.phase == "failed" and not e.pending and e.state.interrupted


async def test_malformed_known_request_gets_immediate_error(engine, owner):
    e, sink, rpc = engine
    await begin(e, owner)
    e.request(
        999,
        "item/permissions/requestApproval",
        {"threadId": "thread-1", "turnId": "turn-1", "itemId": "p", "startedAtMs": 1},
    )
    assert rpc.replies[-1][2] and not e.pending
    assert "rejected" in sink.texts[-1][0]
