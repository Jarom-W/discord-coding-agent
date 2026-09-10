import asyncio

import pytest

from discord_coding_agent.config import Config, Timeouts
from discord_coding_agent.engine import Engine, Origin
from discord_coding_agent.state import StateStore


class MemorySink:
    def __init__(self):
        self.texts = []
        self.requests = []
        self.invalidated = []

    def text(self, content, *, result_id=None):
        self.texts.append((content, result_id))

    def request(self, pending):
        self.requests.append(pending)

    def invalidate(self, pending):
        self.invalidated.append(pending.id)


class StubRpc:
    def __init__(self, engine):
        self.engine = engine
        self.calls = []
        self.replies = []
        self.failure = None
        self.closed = False

    async def call(self, method, params, limit=None):
        self.calls.append((method, params))
        if method == "turn/start":
            self.engine.set_turn("turn-1")
            return {"turn": {"id": "turn-1", "status": "inProgress"}}
        if method == "turn/interrupt" and self.engine.done and not self.engine.done.done():
            self.engine.done.set_result({"id": "turn-1", "status": "interrupted"})
        return {}

    def reply(self, request_id, result=None, error=None):
        self.replies.append((request_id, result, error))

    async def close(self):
        self.closed = True


@pytest.fixture
def config(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    return Config(
        "not-a-real-token",
        11,
        22,
        33,
        repo,
        state_dir=tmp_path / "state",
        path=tmp_path / "config/config.toml",
        timeouts=Timeouts(
            initialization=1,
            request=0.2,
            transport=0.2,
            task=3,
            user_wait=1,
            delivery=0.05,
            shutdown=0.05,
        ),
    )


@pytest.fixture
def owner():
    return Origin(11, 22, 33, 100)


@pytest.fixture
async def engine(config):
    sink = MemorySink()
    store = StateStore(config.state_dir, "test-repo-identity", "manual")
    store.acquire()
    engine = Engine(config, store, sink)
    engine.connected = True
    rpc = StubRpc(engine)

    async def prepare():
        engine.rpc = rpc
        engine.state.thread_id = "thread-1"
        engine.verified = True
        engine.save()

    engine._prepare = prepare
    yield engine, sink, rpc
    await engine.close()
    store.close()


async def begin(engine, owner):
    await engine.message(owner, "Inspect the repository")
    for _ in range(20):
        if engine.turn_id:
            return
        await asyncio.sleep(0)
    raise AssertionError("Turn did not start")


def approval(engine, method="item/commandExecution/requestApproval", rpc_id=500):
    params = {
        "threadId": "thread-1",
        "turnId": "turn-1",
        "itemId": "item-1",
        "startedAtMs": 1750000000000,
        "cwd": str(engine.config.repo),
        "command": "printf demo",
        "availableDecisions": ["accept", "decline"],
    }
    engine.request(rpc_id, method, params)
    pending = list(engine.pending.values())[-1]
    engine.bind(pending.id, 900)
    return pending


def complete(engine, text="Done"):
    engine.event(
        "item/completed",
        {
            "threadId": "thread-1",
            "turnId": "turn-1",
            "item": {"id": "agent-1", "type": "agentMessage", "text": text},
        },
    )
    engine.event(
        "turn/completed", {"threadId": "thread-1", "turn": {"id": "turn-1", "status": "completed"}}
    )
