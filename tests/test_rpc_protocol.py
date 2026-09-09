import asyncio
import hashlib
import json
import sys
from pathlib import Path

import pytest
from jsonschema import ValidationError, validate

from discord_coding_agent import protocol
from discord_coding_agent.config import Timeouts
from discord_coding_agent.errors import BridgeError, LimitError
from discord_coding_agent.rpc import Rpc

ROOT = Path(__file__).parent
FIX = ROOT / "fixtures/codex-0.153.4"


async def start(mode):
    errors = []
    rpc = Rpc(
        Timeouts(request=0.1, transport=0.05, shutdown=0.05),
        lambda *_: None,
        lambda *_: None,
        errors.append,
    )
    await rpc.start([sys.executable, str(ROOT / "fake_server.py"), mode], ROOT)
    return rpc, errors


async def test_rpc_out_of_order_correlation():
    rpc, _ = await start("reverse")
    try:
        one, two = await asyncio.gather(rpc.call("first", {}, 2), rpc.call("second", {}, 2))
        assert one["method"] == "first" and two["method"] == "second"
        assert not rpc.pending
    finally:
        await rpc.close()


@pytest.mark.parametrize("mode", ["malformed", "partial", "eof"])
async def test_broken_transport_fails_pending(mode):
    rpc, errors = await start(mode)
    try:
        with pytest.raises(BridgeError):
            await rpc.call("probe", {}, 2)
        assert errors and not rpc.pending
        if mode == "partial":
            assert (
                isinstance(errors[0], LimitError) and errors[0].operation == "transport frame read"
            )
    finally:
        await rpc.close()
    assert rpc.process.returncode is not None and not rpc.tasks


async def test_rpc_timeout_is_not_task_deadline():
    rpc, _ = await start("silent")
    try:
        with pytest.raises(LimitError, match="RPC turn/start") as error:
            await rpc.call("turn/start", {})
        assert error.value.limit == 0.1 and not rpc.pending
    finally:
        await rpc.close()


async def test_realistic_server_rejects_wrong_enums(tmp_path):
    rpc, _ = await start("validate")
    try:
        good = protocol.thread_params(tmp_path, "auto")
        assert await rpc.call("thread/start", good, 2) == {"accepted": True}
        for key, value in [("approvalPolicy", "onRequest"), ("sandbox", "workspaceWrite")]:
            with pytest.raises(BridgeError):
                await rpc.call("thread/start", {**good, key: value}, 2)
    finally:
        await rpc.close()


def test_schema_evidence_checksums_and_enum_distinction(tmp_path):
    manifest = json.loads((FIX / "manifest.json").read_text())
    for name, digest in manifest["sha256"].items():
        assert hashlib.sha256((FIX / name).read_bytes()).hexdigest() == digest
    schema = json.loads((FIX / "TurnStartParams.json").read_text())
    params = {
        "threadId": "t",
        "input": [{"type": "text", "text": "hello", "text_elements": []}],
        "sandboxPolicy": {"type": "workspaceWrite"},
    }
    validate(params, schema)
    with pytest.raises(ValidationError):
        validate({**params, "sandboxPolicy": {"type": "workspace-write"}}, schema)


@pytest.mark.parametrize("mode", ["manual", "auto"])
def test_effective_thread_verification(tmp_path, mode):
    good = {
        "thread": {"id": "t"},
        "cwd": str(tmp_path),
        "approvalPolicy": "on-request",
        "approvalsReviewer": protocol.REVIEWERS[mode],
        "sandbox": {"type": "workspaceWrite"},
    }
    assert protocol.verify_thread(good, tmp_path, mode) == "t"
    for key, value in [
        ("approvalsReviewer", "unsupported"),
        ("approvalPolicy", "never"),
        ("sandbox", {"type": "dangerFullAccess"}),
        ("cwd", "/other"),
    ]:
        with pytest.raises(BridgeError):
            protocol.verify_thread({**good, key: value}, tmp_path, mode)


async def test_process_reviewer_failure(tmp_path):
    class Stub:
        async def call(self, *_):
            return {"config": {"approval_policy": "on-request", "sandbox_mode": "workspace-write"}}

    with pytest.raises(BridgeError, match="verified"):
        await protocol.verify_process(Stub(), tmp_path, "auto")


def test_decisions_validate_against_generated_schemas():
    for prefix, method, params in [
        ("CommandExecution", "item/commandExecution/requestApproval", {}),
        ("FileChange", "item/fileChange/requestApproval", {}),
        (
            "Permissions",
            "item/permissions/requestApproval",
            {"permissions": {"network": {"enabled": True}}},
        ),
    ]:
        for approved in [True, False]:
            result = protocol.approval_result(method, params, approved)
            validate(
                result, json.loads((FIX / (prefix + "RequestApprovalResponse.json")).read_text())
            )
