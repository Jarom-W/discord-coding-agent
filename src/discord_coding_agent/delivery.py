"""Bounded delivery with retries and history reconciliation, never coding-task retries."""

import asyncio
import hashlib
import logging
import re
import time
import uuid
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from typing import Protocol

from .engine import Pending
from .errors import BridgeError, LimitError, log_error

log = logging.getLogger(__name__)
INLINE_UNITS = 1800  # Reserve room for markers within Discord's 2000-character limit.
FENCE_UNITS = 100  # Bound repeated Markdown wrappers, including language labels.
SOURCE_UNITS = INLINE_UNITS - 2 * (FENCE_UNITS + 1)
FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})([^\r\n]*)[\r\n]*$")


@dataclass(frozen=True)
class Page:
    text: str
    prefix: str = ""
    suffix: str = ""

    @property
    def content(self) -> str:
        return self.prefix + self.text + self.suffix


def payloads(text: str) -> Iterator[Page]:
    """Yield complete Unicode text as chat pages, reopening ordinary fenced code blocks.

    Source text stays verbatim; prefix/suffix are presentation-only fence wrappers.
    Work is linear in input size and only one page is built at a time.
    """
    if not text:
        yield Page("(empty result)")
        return
    start = 0
    fence: tuple[str, str] | None = None
    while start < len(text):
        units, end = 0, start
        while end < len(text):
            width = 2 if ord(text[end]) > 0xFFFF else 1
            if units + width > SOURCE_UNITS:
                break
            units += width
            end += 1
        if end < len(text):
            end = text.rfind("\n", start, end) + 1 or text.rfind(" ", start, end) + 1 or end
        raw = text[start:end]
        prefix = fence[1] + "\n" if fence else ""
        line_start = start == 0 or text[start - 1] == "\n"
        for line in raw.splitlines(keepends=True):
            match = FENCE.fullmatch(line) if line_start else None
            if match:
                marker, info = match.groups()
                if fence:
                    if (
                        marker[0] == fence[0][0]
                        and len(marker) >= len(fence[0])
                        and not info.strip()
                    ):
                        fence = None
                elif (marker[0] != "`" or "`" not in info) and len(
                    line.rstrip("\r\n").encode("utf-16-le")
                ) // 2 <= FENCE_UNITS:
                    fence = marker, line.rstrip("\r\n")
            line_start = line.endswith("\n")
        suffix = (("" if raw.endswith("\n") else "\n") + fence[0]) if fence else ""
        yield Page(raw, prefix, suffix)
        start = end


class Transport(Protocol):
    async def send(self, text: str, marker: str, pending: Pending | None) -> int: ...
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
    parts: Iterator[Page] | None = field(default=None, repr=False)
    page: int = 0
    fingerprint: str = ""


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
        # Include the in-flight job so it always has room to requeue its next page.
        if len(self.keys) >= self.queue.maxsize:
            self.failures += 1
            log.error(
                "delivery=queue_full result=%s; saved result available via !last", job.result_id
            )
            return
        self.keys.add(job.key)
        self.enqueue(job, priority)

    def enqueue(self, job: Job, priority: int) -> None:
        self.sequence += 1
        self.queue.put_nowait((priority, self.sequence, job))

    def text(self, content: str, *, result_id: str | None = None) -> None:
        self.put(
            Job(content, result_id or uuid.uuid4().hex, result_id=result_id),
            3 if result_id else 2,
        )

    def request(self, pending: Pending) -> None:
        kind = "Question" if pending.method.endswith("requestUserInput") else "Approval needed"
        heading = f"## {kind}\nRequest: `{pending.id}`\n{pending.method}\n\n"
        self.put(Job(heading + pending.details, pending.id, pending=pending), 1)

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

    async def send_part(self, text: str, marker: str, pending: Pending | None) -> int:
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
                    self.transport.send(text, marker, pending), self.timeout
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
            priority, _, job = await self.queue.get()
            finished = True
            try:
                if job.disable_id:
                    await asyncio.wait_for(self.transport.disable(job.disable_id), self.timeout)
                    continue
                if job.parts is None:
                    job.parts = payloads(job.text)
                    # A changed session label/layout shifts page boundaries. Never
                    # reconcile against pages generated from different source text.
                    job.fingerprint = hashlib.sha256(
                        b"inline-layout-2\n" + job.text.encode("utf-8")
                    ).hexdigest()[:16]
                page = next(job.parts, None)
                if page is not None:
                    job.page += 1
                    marker = f"[dca:{job.key}:inline:{job.fingerprint}:{job.page}]"
                    await self.send_part(page.content, marker, job.pending)
                    # Yield between pages: controls and short replies must stay responsive.
                    self.enqueue(job, priority)
                    finished = False
                    continue
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
                        instruction, f"[dca:{job.key}:control]", pending
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
                if finished:
                    self.keys.discard(job.key)
                self.queue.task_done()

    async def close(self) -> None:
        if self.worker:
            self.worker.cancel()
            await asyncio.gather(self.worker, return_exceptions=True)
