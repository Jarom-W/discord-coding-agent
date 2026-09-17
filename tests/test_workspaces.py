import asyncio
import json
import subprocess
from collections import defaultdict
from dataclasses import replace
from unittest.mock import AsyncMock

import pytest
from conftest import MemorySink, StubRpc, approval, begin, complete

from discord_coding_agent.config import repository_identity
from discord_coding_agent.engine import Origin
from discord_coding_agent.errors import BridgeError
from discord_coding_agent.locks import Lease
from discord_coding_agent.models import Model
from discord_coding_agent.state import StateStore
from discord_coding_agent.workspaces import Workspaces


@pytest.fixture
def catalog(monkeypatch):
    lookup = AsyncMock(
        return_value=[Model("model-a", "Model A", True), Model("model-b", "Model B", False)]
    )
    monkeypatch.setattr("discord_coding_agent.workspaces.discover_models", lookup)
    return lookup


async def test_model_selection_keeps_history_and_isolated_across_sessions_restart(
    spaces, owner, catalog
):
    w, sinks, second = spaces
    first = w.selected(owner.channel)
    engine = w.engine(first)
    engine.state.thread_id = "original-thread"
    engine.state.last_result = "saved result"
    await w.message(owner, "!models")
    assert "model-a" in sinks[33].texts[-1][0] and "catalog default" in sinks[33].texts[-1][0]
    await w.message(replace(owner, message=101), "!model model-b")
    assert engine.store.load().model == "model-b"
    assert (
        engine.state.thread_id == "original-thread" and engine.state.last_result == "saved result"
    )
    assert "model-b" in engine.status()
    await w.message(replace(owner, message=102), "!model")
    assert "model-b" in sinks[33].texts[-1][0]
    assert catalog.await_count == 2
    await w.switch(33, "!new", "new session")
    assert w.engine(w.selected(33)).state.model is None
    await w.switch(44, "!repo", str(second))
    assert w.engine(w.selected(44)).state.model is None
    await w.switch(33, "!session", first.name)
    await w.close()
    restored = Workspaces(w.config, w.legacy, lambda channel: sinks[channel])
    assert restored.engine(restored.selected(33)).state.model == "model-b"
    assert restored.engine(restored.selected(44)).state.model is None
    await restored.close()


@pytest.mark.parametrize("argument", ["unknown", "two words", "`bad`", "x" * 201])
async def test_invalid_model_preserves_selection(spaces, owner, catalog, argument):
    w, _, _ = spaces
    engine = w.engine(w.selected(33))
    engine.state.model = "model-a"
    engine.save()
    with pytest.raises(BridgeError):
        await w.model_command(owner, "model", argument)
    assert engine.store.load().model == "model-a"


async def test_model_change_rejects_busy_other_channel_but_listing_works(spaces, owner, catalog):
    w, _, second = spaces
    await w.switch(44, "!repo", str(second))
    engine, _ = stub(w, w.selected(44))
    await begin(engine, replace(owner, channel=44))
    with pytest.raises(BridgeError, match="active work"):
        await w.model_command(owner, "model", "model-a")
    assert "model-b" in await w.model_command(owner, "models")
    complete(engine)
    await engine.worker


async def test_model_change_reserves_workspace_during_lookup(spaces, owner, monkeypatch):
    w, _, _ = spaces
    arrived, release = asyncio.Event(), asyncio.Event()

    async def lookup(*_):
        arrived.set()
        await release.wait()
        return [Model("model-a", "A", True)]

    monkeypatch.setattr("discord_coding_agent.workspaces.discover_models", lookup)
    task = asyncio.create_task(w.model_command(owner, "model", "model-a"))
    await arrived.wait()
    try:
        with pytest.raises(BridgeError, match="active work or maintenance"):
            await w.switch(33, "!new", "other")
        with pytest.raises(BridgeError, match="already in progress"):
            await w.model_command(owner, "models")
        engine, rpc = stub(w, w.selected(33))
        await w.message(owner, "start a task")
        assert not engine.busy and not rpc.calls
    finally:
        release.set()
        await task
    lease = Lease(w.activity_path)
    assert lease.acquire()
    lease.close()


async def test_model_failures_keep_previous_choice_and_release_lease(
    spaces, owner, catalog, monkeypatch
):
    w, _, _ = spaces
    engine = w.engine(w.selected(33))
    engine.state.model = "model-a"
    engine.save()
    catalog.side_effect = BridgeError("lookup unavailable")
    with pytest.raises(BridgeError, match="lookup unavailable"):
        await w.model_command(owner, "model", "model-b")
    catalog.side_effect = None
    monkeypatch.setattr(engine, "save", lambda: (_ for _ in ()).throw(OSError("disk full")))
    with pytest.raises(OSError, match="disk full"):
        await w.model_command(owner, "model", "model-b")
    assert engine.state.model == engine.store.load().model == "model-a"
    lease = Lease(w.activity_path)
    assert lease.acquire()
    lease.close()


async def test_model_connection_only_and_unbound_channel_no_lookup(spaces, owner, catalog):
    w, _, _ = spaces
    with pytest.raises(BridgeError, match="repository"):
        await w.model_command(replace(owner, channel=44), "models")
    with pytest.raises(BridgeError, match="owner"):
        await w.model_command(replace(owner, owner=99), "models")
    w.connection_only = True
    with pytest.raises(BridgeError, match="Connection-only"):
        await w.model_command(owner, "models")
    assert "Codex default" in await w.model_command(owner, "model")
    catalog.assert_not_awaited()


@pytest.fixture
async def spaces(config):
    await asyncio.to_thread(subprocess.run, ["git", "init", "-q", str(config.repo)], check=True)
    second = config.repo.parent / "another repo"
    await asyncio.to_thread(subprocess.run, ["git", "init", "-q", str(second)], check=True)
    sinks = defaultdict(MemorySink)
    store = StateStore(config.state_dir, repository_identity(config.repo), "manual")
    store.acquire()
    workspaces = Workspaces(config, store, lambda channel: sinks[channel])
    workspaces.connected = True
    yield workspaces, sinks, second
    await workspaces.close()
    store.close()


def stub(w, record):
    e = w.engine(record)
    rpc = StubRpc(e)

    async def prepare():
        e.rpc = rpc
        e.state.thread_id = e.state.thread_id or "thread-1"
        e.verified = True
        e.save()

    e._prepare = prepare
    return e, rpc


async def test_legacy_thread_and_result_remain_available(config, tmp_path):
    store = StateStore(tmp_path / "legacy", "identity", "auto")
    store.acquire()
    state = store.load()
    state.thread_id, state.last_result = "original-thread", "original result"
    store.save(state)
    before = store.path.read_bytes()
    w = Workspaces(config, store, lambda _: MemorySink())
    assert store.path.read_bytes() == before
    e = w.engine(w.selected(config.channel_id))
    assert e.state.thread_id == "original-thread" and e.state.last_result == "original result"
    assert e.state.mode == "auto"
    await w.close()
    store.close()


async def test_names_repo_switch_and_restart(spaces, owner):
    w, sinks, second = spaces
    first = w.selected(owner.channel)
    e, _ = stub(w, first)
    await begin(e, owner)
    complete(e, "first result")
    await e.worker
    await w.message(replace(owner, message=101), "!name backend fixes")
    await w.message(replace(owner, message=102), f"!repo {second}")
    assert w.selected(owner.channel).repo == str(second)
    await w.message(replace(owner, message=103), "!new auto release planning")
    selected = w.selected(owner.channel)
    assert selected.name == "release planning" and w.engine(selected).state.mode == "auto"
    await w.message(replace(owner, message=104), "!sessions")
    assert (
        "backend fixes" in sinks[33].texts[-1][0] and "release planning" in sinks[33].texts[-1][0]
    )
    await w.message(replace(owner, message=105), "!session BACKEND FIXES")
    assert w.selected(owner.channel).id == first.id
    assert w.engine(first).state.thread_id == "thread-1"
    await w.close()
    restored = Workspaces(w.config, w.legacy, lambda channel: sinks[channel])
    assert restored.selected(owner.channel).name == "backend fixes"
    assert restored.engine(restored.selected(owner.channel)).state.last_result == "first result"
    await restored.message(replace(owner, message=106), "!session release planning")
    assert restored.engine(restored.selected(owner.channel)).state.mode == "auto"
    await restored.close()


async def test_channels_share_one_task_and_switches_reject(spaces, owner):
    w, sinks, second = spaces
    other = replace(owner, channel=44, message=101)
    await w.message(other, f"!repo {second}")
    a, rpc_a = stub(w, w.selected(33))
    b, rpc_b = stub(w, w.selected(44))
    await w.message(owner, "first task")
    await asyncio.sleep(0)
    await w.message(replace(other, message=102), "overlapping task")
    assert a.busy and not b.busy and not rpc_b.calls
    assert "NOT submitted" in sinks[44].texts[-1][0]
    for i, command in enumerate(
        ["!new auto", f"!repo {w.config.repo}", "!name rename", "!session unknown"], 200
    ):
        await w.message(replace(other, message=i), command)
        assert "Cannot change" in sinks[44].texts[-1][0]
    await w.message(replace(other, message=300), "!stop")
    await a.stop_worker
    assert a.state.interrupted and not a.busy
    assert any(method == "turn/interrupt" for method, _ in rpc_a.calls)


async def test_maintenance_and_reservation_are_atomic(spaces, owner):
    w, sinks, _ = spaces
    e, rpc = stub(w, w.selected(33))
    lease = Lease(w.activity_path)
    assert lease.acquire()
    try:
        await w.message(owner, "start")
        assert not e.busy and not rpc.calls and "NOT submitted" in sinks[33].texts[-1][0]
        await w.message(replace(owner, message=101), "!new manual name")
        assert "Cannot change" in sinks[33].texts[-1][0]
    finally:
        lease.close()
    await begin(e, replace(owner, message=102))
    assert not lease.acquire()
    complete(e)
    await e.worker
    assert lease.acquire()
    lease.close()


async def test_old_buttons_do_not_follow_session_switch(spaces, owner):
    w, sinks, _ = spaces
    old, rpc = stub(w, w.selected(33))
    await begin(old, owner)
    pending = approval(old)
    complete(old)
    await old.worker
    await w.message(replace(owner, message=101), "!new manual next")
    new, _ = stub(w, w.selected(33))
    await begin(new, replace(owner, message=102))
    with pytest.raises(BridgeError):
        new.decide(owner, pending.id, True, button_message=900)
    assert not rpc.replies


@pytest.mark.parametrize(
    "origin", [Origin(99, 22, 33, 1), Origin(11, 99, 33, 1), Origin(11, None, 33, 1)]
)
async def test_workspace_authorization(spaces, origin):
    w, sinks, second = spaces
    await w.message(origin, f"!repo {second}")
    await w.message(origin, "!dirs /")
    assert not sinks and len(w.sessions) == 1 and not w.seen


async def test_dedup_new_and_connection_only_binding(spaces, owner):
    w, sinks, second = spaces
    w.connection_only = True
    await w.message(owner, f"!repo {second}")
    await w.message(replace(owner, message=101), "!new auto hello")
    await w.message(replace(owner, message=101), "!new auto duplicate")
    assert len(w.sessions) == 3
    await w.message(replace(owner, message=102), "ordinary prompt")
    assert not w.active() and "Connection-only" in sinks[33].texts[-1][0]


async def test_directory_roots_symlinks_and_non_git_rejected(spaces, owner, tmp_path):
    w, sinks, second = spaces
    w.config = replace(w.config, workspace_roots=(second,))
    escape = second / "escape"
    escape.symlink_to(tmp_path)
    await w.message(owner, "!dirs")
    assert str(second) in sinks[33].texts[-1][0]
    await w.message(replace(owner, message=101), f"!dirs {second}")
    assert "escape/" not in sinks[33].texts[-1][0]
    await w.message(replace(owner, message=102), f"!repo {escape}")
    assert "outside WORKSPACE_ROOTS" in sinks[33].texts[-1][0]
    plain = second / "not a repository"
    plain.mkdir()
    await w.message(replace(owner, message=103), f"!repo {plain}")
    assert "working-tree root" in sinks[33].texts[-1][0]
    assert w.selected(33).id == "default"


async def test_changed_repository_and_corrupt_catalog_fail_closed(spaces, owner):
    w, sinks, _ = spaces
    w.sessions["default"].identity = "old identity"
    await w.message(owner, "!session main")
    assert "identity changed" in sinks[33].texts[-1][0]
    w.path.write_text("{broken")
    with pytest.raises(BridgeError, match="catalog is corrupt"):
        Workspaces(w.config, w.legacy, lambda _: MemorySink())
    assert w.path.read_text() == "{broken"


async def test_catalog_unknown_schema_preserved(spaces):
    w, _, _ = spaces
    data = json.loads(w.path.read_text())
    data["schema"] = 999
    w.path.write_text(json.dumps(data))
    with pytest.raises(BridgeError, match="Unknown workspace catalog schema"):
        Workspaces(w.config, w.legacy, lambda _: MemorySink())
    assert list(w.path.parent.glob("workspaces.json.backup-*"))


async def test_invalid_name_and_attachment_do_not_switch(spaces, owner):
    w, sinks, _ = spaces
    for i, command in enumerate(["!new manual a/b", "!name main/new", "!session missing"], 100):
        await w.message(replace(owner, message=i), command)
        assert w.selected(33).name == "main"
    await w.message(replace(owner, message=200), "!new manual next", attachments=True)
    assert len(w.sessions) == 1 and "unsupported" in sinks[33].texts[-1][0]


async def test_fresh_repo_recovers_changed_identity(spaces, owner):
    w, sinks, _ = spaces
    original = w.selected(33)
    original.identity = "old identity"
    await w.message(owner, f"!repo {original.repo}")
    assert "identity changed" in sinks[33].texts[-1][0]
    await w.message(replace(owner, message=101), f"!repo --fresh {original.repo}")
    assert w.selected(33).id != original.id
    assert w.selected(33).identity == repository_identity(w.config.repo)
    assert original.id in w.sessions


async def test_failed_catalog_write_keeps_selection(spaces, monkeypatch):
    w, _, _ = spaces
    original = w.selected(33)

    def fail():
        raise OSError("disk full")

    monkeypatch.setattr(w, "save", fail)
    with pytest.raises(OSError):
        await w.switch(33, "!name", "unsaved name")
    assert w.selected(33) is original and original.name == "main"
    with pytest.raises(OSError):
        await w.switch(33, "!new", "manual unsaved session")
    assert len(w.sessions) == 1 and w.selected(33).id == "default"
    lease = Lease(w.activity_path)
    assert lease.acquire()
    lease.close()


async def test_unbound_ordinary_text_is_ignored(spaces, owner):
    w, sinks, _ = spaces
    await w.message(replace(owner, channel=999), "normal server conversation")
    assert 999 not in w.channels and not sinks


async def test_active_channel_followup_is_routed_without_cross_channel_steering(spaces, owner):
    w, sinks, second = spaces
    await w.message(replace(owner, message=90, channel=44), f"!repo {second}")
    e, rpc = stub(w, w.selected(33))
    other, other_rpc = stub(w, w.selected(44))
    await w.message(owner, "work here")
    await w.message(replace(owner, message=101), "add this here")
    await w.message(replace(owner, message=101), "duplicate")
    await w.message(replace(owner, message=102, channel=44), "must not reach active repo")
    await e.followups.worker
    assert e.followups.accepted == 1
    assert not other.busy and not other_rpc.calls
    assert "NOT submitted" in sinks[44].texts[-1][0]
    assert [m for m, _ in rpc.calls] == ["turn/start", "turn/steer"]
