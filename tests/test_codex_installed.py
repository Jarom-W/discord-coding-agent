"""Actual installed Codex checks. Opt in; no production auth is loaded by this test."""

import asyncio
import json
import os
import shutil

import pytest

from discord_coding_agent import protocol
from discord_coding_agent.config import Timeouts, discover_codex
from discord_coding_agent.errors import RpcRejected
from discord_coding_agent.models import list_models
from discord_coding_agent.rpc import Rpc


@pytest.mark.codex
@pytest.mark.skipif(
    os.getenv("DCA_TEST_CODEX") != "1" or not shutil.which("codex"),
    reason="Set DCA_TEST_CODEX=1 for installed non-model checks",
)
async def test_installed_code_mode_executes_javascript_without_model_or_auth(tmp_path):
    """V1 length-prefixed IPC from upstream code-mode-protocol/src/host/{codec,message,payload}.rs."""
    executable = discover_codex()
    await protocol.version(executable, 20)
    helper = protocol.check_runtime_files(executable)
    if helper is None:
        pytest.skip("Point PATH at the native package bin/ to test its helper directly")
    process = await asyncio.create_subprocess_exec(
        str(helper),
        "--listen",
        "stdio",
        cwd=tmp_path,
        env={"PATH": os.defpath},
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )

    async def send(message):
        payload = json.dumps(message).encode()
        process.stdin.write(len(payload).to_bytes(4, "little") + payload)
        await process.stdin.drain()

    async def receive():
        size = int.from_bytes(await process.stdout.readexactly(4), "little")
        assert 0 < size <= 1024 * 1024
        return json.loads(await process.stdout.readexactly(size))

    try:
        async with asyncio.timeout(10):
            await send(
                {
                    "type": "connection/hello",
                    "supportedVersions": [1],
                    "requiredCapabilities": [],
                    "optionalCapabilities": [],
                }
            )
            assert (await receive())["type"] == "connection/ready"
            await send(
                {
                    "type": "operation/request",
                    "id": 1,
                    "request": {"method": "session/open", "sessionId": "dca-runtime-test"},
                }
            )
            opened = await receive()
            assert opened["id"] == 1 and opened["result"]["status"] == "ok"
            await send(
                {
                    "type": "operation/request",
                    "id": 2,
                    "request": {
                        "method": "session/execute",
                        "sessionId": "dca-runtime-test",
                        "request": {
                            "tool_call_id": "test-call",
                            "enabled_tools": [],
                            "source": "text(1 + 1);",
                            "yield_time_ms": 1000,
                            "max_output_tokens": 100,
                        },
                    },
                }
            )
            while True:
                message = await receive()
                if message["type"] == "execute/initialResponse":
                    assert message["id"] == 2 and message["result"]["status"] == "ok"
                    result = message["result"]["value"]["Result"]
                    assert result["error_text"] is None
                    assert result["content_items"] == [{"type": "input_text", "text": "2"}]
                    break
    finally:
        if process.returncode is None:
            process.kill()
        await process.wait()


@pytest.mark.codex
@pytest.mark.skipif(
    os.getenv("DCA_TEST_CODEX") != "1" or not shutil.which("codex"),
    reason="Set DCA_TEST_CODEX=1 for installed non-model checks",
)
@pytest.mark.parametrize("mode", ["manual", "auto"])
async def test_installed_initialize_create_and_resume(tmp_path, mode):
    repo = tmp_path / "repo"
    repo.mkdir()
    home = tmp_path / "codex-home"
    home.mkdir(mode=0o700)
    env = protocol.child_environment()
    env["CODEX_HOME"] = str(home)  # Isolated auth/state. Never read or alter personal auth.
    for name in ["OPENAI_API_KEY", "CODEX_API_KEY", "CODEX_ACCESS_TOKEN"]:
        env.pop(name, None)
    executable = discover_codex()
    await protocol.version(executable, 20)
    saved = None
    for restart in [False, True]:
        rpc = Rpc(
            Timeouts(initialization=30, request=30, shutdown=2),
            lambda *_: None,
            lambda rid, *_: None,
            lambda _: None,
        )
        try:
            await rpc.start(protocol.argv(executable, mode), repo, env)
            await protocol.initialize(rpc)
            await protocol.verify_process(rpc, repo, mode)
            catalog = await list_models(rpc)
            assert catalog
            selected_model = catalog[-1 if restart else 0].model
            response = await rpc.call(
                "thread/resume" if restart else "thread/start",
                protocol.thread_params(repo, mode, saved, selected_model),
            )
            thread = protocol.verify_thread(response, repo, mode, selected_model)
            # A real idle thread must reject steering, never implicitly start model work.
            with pytest.raises(RpcRejected) as rejection:
                await rpc.call(
                    "turn/steer",
                    {
                        "threadId": thread,
                        "expectedTurnId": "no-active-turn",
                        "input": [
                            {
                                "type": "text",
                                "text": "Non-model steering probe.",
                                "text_elements": [],
                            }
                        ],
                    },
                )
            assert rejection.value.code != -32601  # Method exists in this CLI.
            assert not rpc.failure
            if restart:
                assert thread == saved
            else:
                saved = thread
                # Empty threads have no rollout. Append harmless history without starting a model turn.
                await rpc.call(
                    "thread/inject_items",
                    {
                        "threadId": thread,
                        "items": [
                            {
                                "type": "message",
                                "role": "user",
                                "content": [
                                    {
                                        "type": "input_text",
                                        "text": "Non-model protocol fixture only.",
                                    }
                                ],
                            }
                        ],
                    },
                )
        finally:
            await rpc.close()
