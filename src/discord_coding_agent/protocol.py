"""Explicit Codex 0.153.4 adapter, grounded in its generated experimental schema."""

import asyncio
import os
import time
from pathlib import Path
from typing import Any

from . import __version__
from .errors import BridgeError, LimitError
from .rpc import Rpc

BASELINE = "0.153.4"
REVIEWERS = {"manual": "user", "auto": "auto_review"}
APPROVALS = {
    "item/commandExecution/requestApproval",
    "item/fileChange/requestApproval",
    "item/permissions/requestApproval",
}
QUESTION = "item/tool/requestUserInput"
OPT_OUT = [
    "item/agentMessage/delta",
    "item/reasoning/textDelta",
    "item/reasoning/summaryTextDelta",
    "item/reasoning/summaryPartAdded",
    "item/commandExecution/outputDelta",
    "item/fileChange/outputDelta",
    "codex/event/agent_message_delta",
    "codex/event/agent_reasoning_delta",
    "codex/event/exec_command_output_delta",
]


async def version(executable: Path, limit: float) -> str:
    started = time.monotonic()
    process = await asyncio.create_subprocess_exec(
        str(executable),
        "--version",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )
    try:
        output, _ = await asyncio.wait_for(process.communicate(), limit)
    except TimeoutError as exc:
        raise LimitError("Codex version check", time.monotonic() - started, limit) from exc
    finally:
        if process.returncode is None:
            process.kill()
            await process.wait()
    found = output.decode().strip()
    if process.returncode or found != f"codex-cli {BASELINE}":
        raise BridgeError(
            f"Unsupported Codex version; this release requires codex-cli {BASELINE}. Install it separately or contribute schema fixtures; no automatic upgrade or mode fallback."
        )
    return found


def child_environment() -> dict[str, str]:
    # Do not give the coding child the Discord bot token through its environment.
    return {k: v for k, v in os.environ.items() if not k.startswith("DISCORD_")}


def argv(executable: Path, mode: str) -> list[str]:
    return [
        str(executable),
        "app-server",
        "--listen",
        "stdio://",
        "-c",
        'approval_policy="on-request"',
        "-c",
        f'approvals_reviewer="{REVIEWERS[mode]}"',
        "-c",
        'sandbox_mode="workspace-write"',
    ]


async def initialize(rpc: Rpc) -> None:
    await rpc.call(
        "initialize",
        {
            "clientInfo": {
                "name": "discord_coding_agent",
                "title": "Discord Coding Agent (community)",
                "version": __version__,
            },
            "capabilities": {"experimentalApi": True, "optOutNotificationMethods": OPT_OUT},
        },
        rpc.timeouts.initialization,
    )
    rpc.enqueue({"method": "initialized", "params": {}})


async def verify_process(rpc: Rpc, repo: Path, mode: str) -> None:
    response = await rpc.call(
        "config/read", {"cwd": str(repo), "includeLayers": False}, rpc.timeouts.initialization
    )
    config = response.get("config", {})
    expected = {
        "approval_policy": "on-request",
        "approvals_reviewer": REVIEWERS[mode],
        "sandbox_mode": "workspace-write",
    }
    if any(config.get(k) != v for k, v in expected.items()):
        raise BridgeError(
            "Effective process approval/reviewer/sandbox configuration could not be verified. Managed restrictions may apply. Run doctor; select !new manual deliberately if auto is unavailable."
        )


def thread_params(repo: Path, mode: str, thread_id: str | None = None) -> dict[str, Any]:
    params: dict[str, Any] = {
        "cwd": str(repo),
        "approvalPolicy": "on-request",
        "approvalsReviewer": REVIEWERS[mode],
        "sandbox": "workspace-write",
    }
    if thread_id:
        params.update(threadId=thread_id, excludeTurns=True)
    return params


def verify_thread(response: dict[str, Any], repo: Path, mode: str) -> str:
    if (
        response.get("approvalsReviewer") != REVIEWERS[mode]
        or response.get("approvalPolicy") != "on-request"
        or response.get("sandbox", {}).get("type") != "workspaceWrite"
        or response.get("cwd") != str(repo)
    ):
        raise BridgeError(
            "Effective thread reviewer, policy, sandbox or repository did not match. No turn submitted. Run doctor; !new manual is a deliberate alternative when auto is unavailable."
        )
    thread_id = response.get("thread", {}).get("id")
    if not isinstance(thread_id, str) or not thread_id:
        raise BridgeError("Codex returned no valid thread ID; check protocol compatibility.")
    return thread_id


def approval_result(method: str, params: dict[str, Any], approved: bool) -> dict[str, Any]:
    if method == "item/permissions/requestApproval":
        return {"permissions": params["permissions"] if approved else {}, "scope": "turn"}
    decision = "accept" if approved else "decline"
    available = params.get("availableDecisions")
    if available is not None and decision not in available:
        if not approved and "cancel" in available:
            decision = "cancel"
        else:
            raise BridgeError(
                "This request does not offer that decision. Use !stop; session-wide policy amendments are not supported."
            )
    return {"decision": decision}


def validate_request(method: str, params: dict[str, Any]) -> None:
    """Validate routing and response-critical fields before exposing a decision."""
    if any(
        not isinstance(params.get(key), str) or not params[key]
        for key in ("threadId", "turnId", "itemId")
    ):
        raise BridgeError("Missing request thread, turn or item identity.")
    if method == QUESTION:
        if type(params.get("isBlocking")) is not bool:
            raise BridgeError("Unsupported question blocking variant.")
    elif type(params.get("startedAtMs")) is not int:
        raise BridgeError("Missing approval timestamp; unsupported protocol variant.")
    if method == "item/permissions/requestApproval" and not isinstance(
        params.get("permissions"), dict
    ):
        raise BridgeError("Missing structured permission request.")
    if params.get("availableDecisions") is not None and not isinstance(
        params["availableDecisions"], list
    ):
        raise BridgeError("Malformed available approval decisions.")
