"""Bounded delivery with retries and history reconciliation, never coding-task retries."""

import asyncio
import logging
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from .engine import Pending
from .errors import BridgeError, LimitError, log_error

log = logging.getLogger(__name__)
ATTACHMENT_BYTES = 512 * 1024


def payloads(text: str) -> list[tuple[str, bytes | None]]:
    # Use attachments for long/complex replies; preserve all UTF-8, including code fences.
    if len(text.encode("utf-16-le")) // 2 <= 1800:
        return [(text or "(empty result)", None)]
    chunks: list[tuple[str, bytes | None]] = []
    chunk = bytearray()
    for char in text:
        encoded = char.encode()
        if len(chunk) + len(encoded) > ATTACHMENT_BYTES:
            chunks.append(("Full text attached.", bytes(chunk)))
            chunk.clear()
        chunk.extend(encoded)
    if chunk:
        chunks.append(("Full text attached.", bytes(chunk)))
    return chunks


class Transport(Protocol):
    async def send(
        self, text: str, data: bytes | None, marker: str, pending: Pending | None
    ) -> int: ...
    async def find(self, marker: str) -> int | None: ...
    async def disable(self, message_id: int) -> None: ...
    async def typing(self) -> None: ...


@dataclass
class Job:
    text: str
    key: str
    result_id: str | None = None
    pending: Pending | None = None
    disable_id: int | None = None


class Delivery:
    def __init__(
        self,
        transport: Transport,
        timeout: float,
        valid: Callable[[str], bool],
        bind: Callable[[str, int], bool],
        delivered: Callable[[str], None],
    ) -> None:
        self.transport, self.timeout = transport, timeout
        self.valid, self.bind, self.delivered = valid, bind, delivered
        self.queue: asyncio.PriorityQueue[tuple[int, int, Job]] = asyncio.PriorityQueue(32)
        self.sequence = 0
        self.worker: asyncio.Task[None] | None = None
        self.keys: set[str] = set()
        self.failures = 0

    def start(self) -> None:
        self.worker = asyncio.create_task(self.run())

    @property
    def status(self) -> str:
        return f"Outbound queued: {self.queue.qsize()}; delivery failures: {self.failures}. Use !last to recover saved output."

    def put(self, job: Job, priority: int) -> None:
        if job.key in self.keys:
            return
        self.sequence += 1
        try:
            self.queue.put_nowait((priority, self.sequence, job))
            self.keys.add(job.key)
        except asyncio.QueueFull:
            self.failures += 1
            log.error(
                "delivery=queue_full result=%s; saved result available via !last", job.result_id
            )

    def text(self, content: str, *, result_id: str | None = None) -> None:
        self.put(
            Job(content, result_id or uuid.uuid4().hex, result_id=result_id), 1 if result_id else 3
        )

    def request(self, pending: Pending) -> None:
        heading = f"Request {pending.id}\n{pending.method}\n"
        self.put(Job(heading + pending.details, pending.id, pending=pending), 2)

    def invalidate(self, pending: Pending) -> None:
        if pending.message_id:
            self.disable(pending.message_id)

    def disable(self, message_id: int) -> None:
        self.put(Job("", f"disable-{message_id}", disable_id=message_id), 0)

    async def cosmetic(self) -> None:
        try:
            await asyncio.wait_for(self.transport.typing(), min(self.timeout, 5))
        except Exception as exc:
            log_error(log, "cosmetic-typing", exc)

    async def send_part(
        self, text: str, data: bytes | None, marker: str, pending: Pending | None
    ) -> int:
        for attempt in range(3):
            started = time.monotonic()
            if pending and not self.valid(pending.id):
                raise BridgeError("Request expired during delivery.")
            # Check before every send, including retry/restart, for an ambiguous previous success.
            found = await asyncio.wait_for(self.transport.find(marker), self.timeout)
            if found:
                return found
            try:
                return await asyncio.wait_for(
                    self.transport.send(text, data, marker, pending), self.timeout
                )
            except (TimeoutError, OSError) as exc:
                reported: BaseException = exc
                if isinstance(exc, TimeoutError):
                    reported = LimitError("Discord send", time.monotonic() - started, self.timeout)
                log_error(log, f"delivery-attempt-{attempt + 1}-limit-{self.timeout:g}s", reported)
                if attempt == 2:
                    # One last reconciliation: an HTTP timeout may have created the message.
                    found = await asyncio.wait_for(self.transport.find(marker), self.timeout)
                    if found:
                        return found
                    raise
                await asyncio.sleep(2**attempt)
        raise AssertionError("unreachable")

    async def run(self) -> None:
        while True:
            _, _, job = await self.queue.get()
            try:
                if job.disable_id:
                    await asyncio.wait_for(self.transport.disable(job.disable_id), self.timeout)
                    continue
                parts = payloads(job.text)
                for index, (text, data) in enumerate(parts):
                    marker = f"[dca:{job.key}:{index + 1}/{len(parts)}]"
                    await self.send_part(
                        text, data, marker, None if not job.pending else job.pending
                    )
                if job.pending:
                    pending = job.pending
                    if not self.valid(pending.id):
                        continue
                    instruction = (
                        f"Answer with !answer {pending.id} text (one question) or JSON mapping question IDs to lists of answers (multiple questions)."
                        if pending.method.endswith("requestUserInput")
                        else f"Review ALL details above, then Approve or Deny. Text fallback: !approve {pending.id} / !deny {pending.id}."
                    )
                    # Controls are a separate message, after complete details were delivered.
                    message_id = await self.send_part(
                        instruction, None, f"[dca:{job.key}:control]", pending
                    )
                    if not self.bind(pending.id, message_id):
                        await asyncio.wait_for(self.transport.disable(message_id), self.timeout)
                if job.result_id:
                    self.delivered(job.result_id)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.failures += 1
                log_error(log, f"delivery-{job.key}", exc)
            finally:
                self.keys.discard(job.key)
                self.queue.task_done()

    async def close(self) -> None:
        if self.worker:
            self.worker.cancel()
            await asyncio.gather(self.worker, return_exceptions=True)
