"""Actual installed Codex checks. Opt in; no production auth is loaded by this test."""

import os
import shutil

import pytest

from discord_coding_agent import protocol
from discord_coding_agent.config import Timeouts, discover_codex
from discord_coding_agent.errors import RpcRejected
from discord_coding_agent.rpc import Rpc


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
            response = await rpc.call(
                "thread/resume" if restart else "thread/start",
                protocol.thread_params(repo, mode, saved),
            )
            thread = protocol.verify_thread(response, repo, mode)
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
