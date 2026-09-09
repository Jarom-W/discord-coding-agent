"""Explicit model test using the owner's existing auth and a disposable target only."""

import asyncio
import os
from pathlib import Path

import pytest
from conftest import MemorySink

from discord_coding_agent.config import Config, repository_identity
from discord_coding_agent.engine import Engine, Origin
from discord_coding_agent.state import StateStore


@pytest.mark.live
@pytest.mark.skipif(
    os.getenv("DCA_TEST_LIVE_MODEL") != "1", reason="Live model access is explicitly opt-in"
)
async def test_live_disposable_conversation(tmp_path):
    def target():
        return Path(os.environ["DCA_TEST_LIVE_REPO"]).expanduser().resolve()

    repo = await asyncio.to_thread(target)
    assert await asyncio.to_thread((repo / ".dca-disposable").is_file), (
        "Create .dca-disposable in a throwaway repository first"
    )
    config = Config("unused-no-discord", 1, 2, 3, repo, state_dir=tmp_path / "state")
    store = StateStore(config.state_dir, repository_identity(repo), "manual")
    store.acquire()
    sink = MemorySink()
    engine = Engine(config, store, sink)
    try:
        await engine.message(
            Origin(1, 2, 3, 1),
            "Read-only: report the repository name and remember the word compass. Do not change files or invoke external services.",
        )
        await engine.worker
        assert not engine.state.interrupted and engine.state.last_result
        thread = engine.state.thread_id
        await engine.message(
            Origin(1, 2, 3, 2),
            "Which word did I ask you to remember? Answer only that word; no tools needed.",
        )
        await engine.worker
        assert engine.state.thread_id == thread
        assert "compass" in engine.state.last_result.lower()
    finally:
        await engine.close()
        store.close()
