"""Bounded JSON-lines RPC. The reader never awaits a Discord operation."""

import asyncio
import contextlib
import json
import logging
import os
import signal
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .config import Timeouts
from .errors import BridgeError, LimitError, RpcRejected, log_error

log = logging.getLogger(__name__)
Json = dict[str, Any]
MAX_FRAME = 8 * 1024 * 1024


class Rpc:
    def __init__(
        self,
        timeouts: Timeouts,
        event: Callable[[str, Json], None],
        request: Callable[[str | int, str, Json], None],
        disconnected: Callable[[BridgeError], None],
    ) -> None:
        self.timeouts = timeouts
        self.event, self.on_request, self.disconnected = event, request, disconnected
        self.process: asyncio.subprocess.Process | None = None
        self.tasks: list[asyncio.Task[None]] = []
        self.pending: dict[int, asyncio.Future[Json]] = {}
        self.outbound: asyncio.Queue[Json] = asyncio.Queue(64)
        self.sequence = 0
        self.failure: BridgeError | None = None
        self.closing = False
        self.close_lock = asyncio.Lock()
        self.stderr_bytes = 0

    async def start(self, argv: list[str], cwd: Path, env: dict[str, str] | None = None) -> None:
        self.process = await asyncio.create_subprocess_exec(
            *argv,
            cwd=cwd,
            env=env,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            limit=MAX_FRAME,
            start_new_session=True,
        )
        self.tasks = [
            asyncio.create_task(coro) for coro in [self._read(), self._write(), self._stderr()]
        ]

    def enqueue(self, message: Json) -> None:
        if self.failure or self.closing:
            raise self.failure or BridgeError("Codex transport is closing.")
        try:
            self.outbound.put_nowait(message)
        except asyncio.QueueFull as exc:
            error = BridgeError(
                "Codex RPC output queue exhausted; inspect doctor and protocol compatibility."
            )
            self._fail(error)
            raise error from exc

    async def call(self, method: str, params: Json, limit: float | None = None) -> Json:
        self.sequence += 1
        request_id = self.sequence
        started = time.monotonic()
        timeout = limit if limit is not None else self.timeouts.request
        future: asyncio.Future[Json] = asyncio.get_running_loop().create_future()
        self.pending[request_id] = future
        log.info("rpc=start id=%s method=%s limit=%s", request_id, method, timeout)
        try:
            self.enqueue({"id": request_id, "method": method, "params": params})
            try:
                result = await asyncio.wait_for(future, timeout)
            except TimeoutError as exc:
                raise LimitError(f"RPC {method}", time.monotonic() - started, timeout) from exc
            log.info(
                "rpc=complete id=%s method=%s elapsed=%.3f",
                request_id,
                method,
                time.monotonic() - started,
            )
            return result
        finally:
            self.pending.pop(request_id, None)
            if future.done() and not future.cancelled():
                future.exception()
            future.cancel()

    def reply(
        self, request_id: str | int, result: Json | None = None, error: str | None = None
    ) -> None:
        if error:
            self.enqueue({"id": request_id, "error": {"code": -32601, "message": error}})
        else:
            self.enqueue({"id": request_id, "result": result or {}})

    def _fail(self, error: BridgeError) -> None:
        if self.failure or self.closing:
            return
        self.failure = error
        for future in self.pending.values():
            if not future.done():
                future.set_exception(error)
        self.disconnected(error)

    async def _read(self) -> None:
        assert self.process and self.process.stdout
        reader = self.process.stdout
        try:
            while True:
                # No idle-read timeout: quiet model/tool execution is legitimate.
                first = await reader.read(1)
                if not first:
                    raise BridgeError(
                        "Codex stdout disconnected. Inspect authentication and process logs; no task was replayed."
                    )
                started = time.monotonic()
                try:
                    tail = await asyncio.wait_for(reader.readline(), self.timeouts.transport)
                except TimeoutError as exc:
                    raise LimitError(
                        "transport frame read", time.monotonic() - started, self.timeouts.transport
                    ) from exc
                if not tail.endswith(b"\n") or len(first + tail) > MAX_FRAME:
                    raise BridgeError(
                        "Incomplete or oversized Codex protocol frame; check the supported CLI version."
                    )
                try:
                    message = json.loads(first + tail)
                except (ValueError, UnicodeError) as exc:
                    raise BridgeError(
                        "Malformed Codex JSON frame; check CLI version and executable wrappers."
                    ) from exc
                if not isinstance(message, dict):
                    raise BridgeError("Codex protocol frame must be an object.")
                if "method" in message:
                    method, params = message["method"], message.get("params", {})
                    if not isinstance(method, str) or not isinstance(params, dict):
                        raise BridgeError("Invalid Codex method or parameters.")
                    if "id" in message:
                        request_id = message["id"]
                        if type(request_id) not in (str, int):
                            raise BridgeError("Invalid Codex request ID.")
                        self.on_request(request_id, method, params)
                    else:
                        self.event(method, params)
                elif "id" in message:
                    if type(message["id"]) not in (int, str):
                        raise BridgeError("Invalid Codex response ID.")
                    future = self.pending.get(message["id"])
                    if future and not future.done():
                        if "error" in message:
                            error = message["error"]
                            if not isinstance(error, dict) or type(error.get("code")) is not int:
                                raise BridgeError(
                                    "Malformed Codex RPC error envelope; outcome unknown."
                                )
                            future.set_exception(RpcRejected(message["id"], error["code"]))
                        elif isinstance(message.get("result"), dict):
                            future.set_result(message["result"])
                        else:
                            raise BridgeError("Codex response has no object result or error.")
                else:
                    raise BridgeError("Unrecognized Codex RPC envelope.")
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log_error(log, "rpc-reader", exc)
            self._fail(
                exc
                if isinstance(exc, BridgeError)
                else BridgeError("Codex protocol reader failed; run doctor.")
            )

    async def _write(self) -> None:
        assert self.process and self.process.stdin
        try:
            while True:
                message = await self.outbound.get()
                data = json.dumps(message, ensure_ascii=False).encode() + b"\n"
                if len(data) > MAX_FRAME:
                    raise BridgeError("Outgoing Codex protocol frame exceeds 8 MiB.")
                self.process.stdin.write(data)
                started = time.monotonic()
                try:
                    await asyncio.wait_for(self.process.stdin.drain(), self.timeouts.transport)
                except TimeoutError as exc:
                    raise LimitError(
                        "transport write", time.monotonic() - started, self.timeouts.transport
                    ) from exc
                self.outbound.task_done()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log_error(log, "rpc-writer", exc)
            self._fail(
                exc
                if isinstance(exc, BridgeError)
                else BridgeError("Codex stdin disconnected; work was not replayed.")
            )

    async def _stderr(self) -> None:
        assert self.process and self.process.stderr
        # Drain without retaining raw diagnostics: Codex/MCP stderr can contain secrets.
        while data := await self.process.stderr.read(4096):
            self.stderr_bytes += len(data)

    async def close(self) -> None:
        async with self.close_lock:
            if self.closing:
                return
            self.closing = True
            for future in self.pending.values():
                if not future.done():
                    future.set_exception(
                        BridgeError("Owned Codex process stopped; uncertain work was not replayed.")
                    )
            process = self.process
            if process:
                # Only the process group created by this Rpc instance is signalled.
                with contextlib.suppress(ProcessLookupError):
                    os.killpg(process.pid, signal.SIGTERM)
                try:
                    await asyncio.wait_for(process.wait(), self.timeouts.shutdown)
                except TimeoutError:
                    log.warning(
                        "shutdown=escalate pid=%s limit=%s", process.pid, self.timeouts.shutdown
                    )
                finally:
                    with contextlib.suppress(ProcessLookupError):
                        os.killpg(process.pid, signal.SIGKILL)
                await process.wait()
            for task in self.tasks:
                task.cancel()
            await asyncio.gather(*self.tasks, return_exceptions=True)
            self.tasks.clear()
