import json
import os

import pytest

from discord_coding_agent.errors import BridgeError
from discord_coding_agent.state import State, StateStore, atomic_write


def test_atomic_state_permissions_lock_and_restart(tmp_path):
    store = StateStore(tmp_path / "state", "repo", "manual")
    store.acquire()
    other = StateStore(store.directory, "repo", "manual")
    with pytest.raises(BridgeError, match="lock"):
        other.acquire()
    state = State("repo", "auto", thread_id="thread-1", active={"task_id": "abc"})
    store.save(state)
    assert store.path.stat().st_mode & 0o777 == 0o600
    assert store.directory.stat().st_mode & 0o777 == 0o700
    restored = store.load()
    assert restored.interrupted and restored.active is None and restored.thread_id == "thread-1"
    store.close()
    other.acquire()  # The file remains; the kernel releases a stale lock.
    other.close()


@pytest.mark.parametrize(
    "text",
    [
        "broken",
        "[]",
        '{"schema":1}',
        '{"schema":1,"repo_identity":"repo","mode":"bogus"}',
        '{"schema":1,"repo_identity":"repo","seen_messages":"wrong"}',
    ],
)
def test_corrupt_state_preserved(tmp_path, text):
    store = StateStore(tmp_path, "repo", "manual")
    atomic_write(store.path, text)
    with pytest.raises(BridgeError):
        store.load()
    assert store.path.read_text() == text


def test_repository_mismatch_and_unknown_schema_backup(tmp_path):
    store = StateStore(tmp_path, "repo", "manual")
    store.save(State("other"))
    with pytest.raises(BridgeError, match="mismatch"):
        store.load()
    raw = json.loads(store.path.read_text())
    raw["schema"] = 0
    atomic_write(store.path, json.dumps(raw))
    with pytest.raises(BridgeError, match="schema"):
        store.load()
    assert len(list(tmp_path.glob("state.json.backup-*"))) == 1


def test_symlink_state_rejected(tmp_path):
    target = tmp_path / "target"
    atomic_write(target, "{}")
    (tmp_path / "state.json").symlink_to(target)
    with pytest.raises(BridgeError):
        StateStore(tmp_path, "repo", "manual").load()


def test_failed_replace_preserves_previous_state(tmp_path, monkeypatch):
    path = tmp_path / "state.json"
    atomic_write(path, "old")

    def fail(*_):
        raise OSError("simulated crash")

    monkeypatch.setattr(os, "replace", fail)
    with pytest.raises(OSError):
        atomic_write(path, "new")
    assert path.read_text() == "old"


@pytest.mark.parametrize(
    "metadata",
    [
        {"followups": "bad"},
        {"followups": [{}] * 33},
        {"followups_accepted": True},
        {"followups": [{"number": 1, "message_id": 99, "status": "replay-me"}]},
        {"followups": [{"number": 1, "message_id": True, "status": "waiting"}]},
        {
            "followups": [
                {"number": 1, "message_id": 99, "status": "waiting", "text": "unexpected body"}
            ]
        },
    ],
)
def test_corrupted_followup_metadata_is_preserved_and_refused(tmp_path, metadata):
    store = StateStore(tmp_path, "repo", "manual")
    store.save(State("repo", active={"task_id": "task", **metadata}))
    before = store.path.read_bytes()
    with pytest.raises(BridgeError, match="corrupt"):
        store.load()
    assert store.path.read_bytes() == before
