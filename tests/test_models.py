import asyncio
import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from jsonschema import validate

from discord_coding_agent import models
from discord_coding_agent.errors import BridgeError

FIXTURES = Path(__file__).parent / "fixtures/codex-0.153.4"


def entry(model="model-a", **kwargs):
    return {
        "id": "picker-" + model,
        "model": model,
        "displayName": model.upper(),
        "description": "A model",
        "hidden": False,
        "isDefault": False,
        "defaultReasoningEffort": "medium",
        "supportedReasoningEfforts": [{"reasoningEffort": "medium", "description": "Balanced"}],
        **kwargs,
    }


async def test_catalog_uses_wire_model_ids_paginates_and_filters_hidden():
    pages = [
        {"data": [entry(isDefault=True), entry("hidden", hidden=True)], "nextCursor": "next"},
        {"data": [entry("model-b")], "nextCursor": None},
    ]
    for page in pages:
        validate(page, json.loads((FIXTURES / "ModelListResponse.json").read_text()))
    rpc = SimpleNamespace(call=AsyncMock(side_effect=pages))
    assert await models.list_models(rpc) == [
        models.Model("model-a", "MODEL-A", True),
        models.Model("model-b", "MODEL-B", False),
    ]
    for index, call in enumerate(rpc.call.await_args_list):
        assert call.args[0] == "model/list"
        params = call.args[1]
        validate(params, json.loads((FIXTURES / "ModelListParams.json").read_text()))
        assert params == {"limit": 100, "includeHidden": False, "cursor": "next" if index else None}


@pytest.mark.parametrize(
    "response",
    [
        {},
        {"data": None},
        {"data": [{}]},
        {"data": [entry(model="bad model")]},
        {"data": [entry(hidden="false")]},
        {"data": [], "nextCursor": 3},
        {"data": [], "nextCursor": "repeating"},
    ],
)
async def test_bad_catalog_fails_without_partial_results(response):
    rpc = SimpleNamespace(call=AsyncMock(return_value=response))
    with pytest.raises(BridgeError):
        await models.list_models(rpc)


@pytest.mark.parametrize(
    "failure", [None, OSError("unavailable"), BridgeError("RPC failed"), "timeout", "cancel"]
)
async def test_discovery_closes_child_without_starting_a_thread(config, monkeypatch, failure):
    rpc = SimpleNamespace(
        start=AsyncMock(), close=AsyncMock(), enqueue=MagicMock(), timeouts=config.timeouts
    )
    arrived = asyncio.Event()

    async def call(method, params, *args):
        if method == "initialize":
            return {}
        assert method == "model/list"
        arrived.set()
        if failure in {"timeout", "cancel"}:
            await asyncio.Event().wait()
        if isinstance(failure, Exception):
            raise failure
        return {"data": [entry()]}

    rpc.call = AsyncMock(side_effect=call)
    monkeypatch.setattr(models, "Rpc", lambda *_: rpc)
    monkeypatch.setattr(models, "discover_codex", lambda _: Path("/codex"))
    monkeypatch.setattr(models.protocol, "version", AsyncMock())
    monkeypatch.setenv("DISCORD_TOKEN", "not-forwarded")
    config = replace(config, timeouts=replace(config.timeouts, initialization=0.05))
    task = asyncio.create_task(models.discover_models(config, "manual"))
    if failure == "cancel":
        await arrived.wait()
        task.cancel()
    if failure:
        with pytest.raises(asyncio.CancelledError if failure == "cancel" else BridgeError):
            await task
    else:
        assert (await task)[0].model == "model-a"
    rpc.close.assert_awaited_once()
    assert "DISCORD_TOKEN" not in rpc.start.call_args.args[2]
    assert [call.args[0] for call in rpc.call.await_args_list] == ["initialize", "model/list"]
