"""Read Codex's model picker without creating a thread or starting a turn."""

import asyncio
import re
from dataclasses import dataclass
from typing import TypeGuard

from . import protocol
from .config import Config, discover_codex
from .errors import BridgeError
from .rpc import Rpc


@dataclass(frozen=True)
class Model:
    model: str
    name: str
    default: bool
    reasoning_efforts: tuple[str, ...]
    default_reasoning_effort: str


def valid_reasoning(value: object) -> TypeGuard[str]:
    return isinstance(value, str) and re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", value) is not None


def valid_model(value: object) -> bool:
    return (
        isinstance(value, str)
        and 1 <= len(value) <= 200
        and value.isprintable()
        and not any(char.isspace() or char == "`" for char in value)
    )


async def list_models(rpc: Rpc) -> list[Model]:
    models: dict[str, Model] = {}
    cursor: str | None = None
    cursors: set[str] = set()
    for _ in range(20):
        response = await rpc.call(
            "model/list", {"limit": 100, "includeHidden": False, "cursor": cursor}
        )
        data = response.get("data")
        if not isinstance(data, list):
            raise BridgeError(
                "Codex returned an invalid model list. Check doctor and retry /models."
            )
        for item in data:
            if (
                not isinstance(item, dict)
                or not valid_model(item.get("model"))
                or not isinstance(item.get("displayName"), str)
                or type(item.get("hidden")) is not bool
                or type(item.get("isDefault")) is not bool
            ):
                raise BridgeError(
                    "Codex returned an invalid model entry. Check doctor and retry /models."
                )
            options = item.get("supportedReasoningEfforts")
            default_effort = item.get("defaultReasoningEffort")
            if (
                not isinstance(options, list)
                or not valid_reasoning(default_effort)
                or any(
                    not isinstance(option, dict)
                    or not valid_reasoning(option.get("reasoningEffort"))
                    for option in options
                )
            ):
                raise BridgeError(
                    "Codex returned invalid reasoning options. Retry /models; nothing was changed."
                )
            efforts = tuple(dict.fromkeys(option["reasoningEffort"] for option in options))
            if efforts and default_effort not in efforts:
                raise BridgeError(
                    "Codex returned an unsupported default reasoning level. Retry /models; nothing was changed."
                )
            if not item["hidden"]:
                models[item["model"]] = Model(
                    item["model"], item["displayName"], item["isDefault"], efforts, default_effort
                )
        cursor = response.get("nextCursor")
        if cursor is None:
            return list(models.values())
        if not isinstance(cursor, str) or not cursor or cursor in cursors:
            break
        cursors.add(cursor)
    raise BridgeError("Codex model pagination did not finish. Retry /models; nothing was changed.")


async def discover_models(config: Config, mode: str) -> list[Model]:
    rpc = Rpc(config.timeouts, lambda *_: None, lambda *_: None, lambda _: None)
    try:
        async with asyncio.timeout(config.timeouts.initialization):
            executable = discover_codex(config.codex)
            await protocol.version(executable, config.timeouts.initialization)
            await rpc.start(
                protocol.argv(executable, mode), config.repo, protocol.child_environment()
            )
            await protocol.initialize(rpc)
            return await list_models(rpc)
    except (OSError, TimeoutError) as exc:
        raise BridgeError(
            "Could not load Codex models. Check doctor and retry /models; nothing was changed."
        ) from exc
    finally:
        await rpc.close()
