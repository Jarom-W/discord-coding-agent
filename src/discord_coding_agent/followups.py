"""Bounded, ordered input to one active Codex turn; never replay uncertain input."""

from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from typing import TYPE_CHECKING

from .errors import BridgeError, RpcRejected, log_error
from .rpc import Json, Rpc

if TYPE_CHECKING:
    from .engine import Engine

log = logging.getLogger(__name__)
MAX_PENDING = 8
MAX_INPUT_BYTES = 64 * 1024


class Followups:
    def __init__(self, engine: Engine) -> None:
        self.engine = engine
        self.task_id = engine.task_id
        self.queue: deque[tuple[Json, str]] = deque()
        self.records: deque[Json] = deque(maxlen=32)
        self.ready = asyncio.Event()
        self.worker: asyncio.Task[None] | None = None
        self.rpc: Rpc | None = None
        self.thread_id = ""
        self.turn_id = ""
        self.closed = False
        self.blocked = False
        self.count = 0
        self.accepted = 0

    def persist(self) -> None:
        # Metadata only. Prompts remain in memory until sent, never in bridge state/logs.
        active = self.engine.state.active
        if active and active["task_id"] == self.task_id:
            active["followups"] = list(self.records)
            active["followups_accepted"] = self.accepted
            self.engine.save()

    def receive(self, message_id: int, text: str) -> None:
        if (
            self.closed
            or self.blocked
            or self.engine.phase == "stopping"
            or (self.engine.done and self.engine.done.done())
        ):
            self.engine.sink.text(
                "Follow-up NOT submitted: this task is finishing, stopping, or has an unresolved input failure. "
                "Check !status and the last follow-up receipt; wait or use !stop before continuing."
            )
            return
        if len(self.queue) >= MAX_PENDING or len(text.encode()) > MAX_INPUT_BYTES:
            self.engine.sink.text(
                f"Follow-up NOT submitted: at most {MAX_PENDING} messages can await Codex, "
                "each up to 64 KiB. Wait for a receipt or shorten your message."
            )
            return
        self.count += 1
        record: Json = {"number": self.count, "message_id": message_id, "status": "waiting"}
        self.records.append(record)
        try:
            self.persist()  # No await between durable reservation and scheduling.
        except OSError as exc:
            record["status"] = "not_submitted"
            raise BridgeError(
                "Follow-up NOT submitted: could not save its receipt. Check disk access and journal logs."
            ) from exc
        self.queue.append((record, text))
        if self.worker is None or self.worker.done():
            self.worker = asyncio.create_task(self.run(), name=f"followups-{self.task_id}")
        detail = (
            "Waiting for Codex startup."
            if not self.ready.is_set()
            else "Sending to Codex in order."
        )
        self.engine.sink.text(
            f"**Follow-up received · #{self.count}**\n{detail} I’ll confirm when Codex accepts it. "
            "The active task’s time limit stays unchanged."
        )

    def activate(self, rpc: Rpc, thread_id: str, turn_id: str) -> None:
        # Called only after turn/start is acknowledged, not merely turn/started.
        self.rpc, self.thread_id, self.turn_id = rpc, thread_id, turn_id
        self.ready.set()

    def update(self, record: Json, status: str) -> None:
        record["status"] = status
        self.persist()
        log.info(
            "followup=%s task=%s turn=%s message=%s number=%s",
            status,
            self.task_id,
            self.turn_id,
            record["message_id"],
            record["number"],
        )

    async def run(self) -> None:
        try:
            await self.ready.wait()
            while self.queue and not self.closed and not self.blocked:
                record, text = self.queue[0]
                engine = self.engine
                if (
                    engine.task_id != self.task_id
                    or engine.turn_id != self.turn_id
                    or not engine.done
                    or engine.done.done()
                ):
                    break
                assert self.rpc
                self.update(record, "sending")
                try:
                    response = await self.rpc.call(
                        "turn/steer",
                        {
                            "threadId": self.thread_id,
                            "expectedTurnId": self.turn_id,
                            "input": [{"type": "text", "text": text, "text_elements": []}],
                        },
                    )
                    if response.get("turnId") != self.turn_id:
                        raise BridgeError(
                            "Codex returned an unexpected turn/steer acknowledgement."
                        )
                except RpcRejected as exc:
                    self.update(record, "rejected")
                    self.blocked = True
                    detail = (
                        "This Codex process does not support turn/steer; run doctor and check the supported CLI version."
                        if exc.code == -32601
                        else "Codex rejected turn/steer; the turn may have ended. Check !status and doctor."
                    )
                    self.engine.sink.text(
                        f"**Follow-up #{record['number']} NOT submitted**\n{detail} "
                        "It was not retried or started as another task."
                    )
                except BridgeError as exc:
                    self.update(record, "uncertain")
                    self.blocked = True
                    log_error(log, f"followup-{self.task_id}-{record['number']}", exc)
                    self.engine.sink.text(
                        f"**Follow-up #{record['number']}: acceptance unknown**\n{exc}\n"
                        "Codex may have received it. Check !status and its result before sending again. "
                        "This input will not be retried; healthy original work continues."
                    )
                else:
                    self.accepted += 1
                    self.update(record, "accepted")
                    self.engine.last_event = "turn/steer accepted"
                    self.engine.last_event_at = time.monotonic()
                    self.engine.sink.text(
                        f"**Follow-up #{record['number']} accepted by Codex**\n"
                        "Added to the active task. Pending approvals/questions still need their own response."
                    )
                self.queue.popleft()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            # Persistence/unexpected failures must not disappear in a detached worker.
            log_error(log, f"followups-{self.task_id}", exc)
            self.blocked = True
            self.engine.disconnected(
                BridgeError(
                    "Follow-up bookkeeping failed; work stopped. Inspect disk access and sanitized logs. "
                    "Sending input may already have reached Codex; nothing is replayed."
                )
            )
        finally:
            self.discard_remaining()

    def discard_remaining(self) -> None:
        while self.queue:
            record, _ = self.queue.popleft()
            uncertain = record["status"] == "sending"
            if record["status"] in {"accepted", "rejected", "uncertain"}:
                continue
            # Set all outcomes even if durable storage has become unavailable.
            record["status"] = "uncertain" if uncertain else "not_submitted"
            self.engine.sink.text(
                f"**Follow-up #{record['number']}: "
                + (
                    "acceptance unknown**\nIt may have reached Codex. Inspect the result before sending again."
                    if uncertain
                    else "NOT submitted**\nThe task ended or input delivery stopped before this message was sent."
                )
                + " Nothing will be replayed after completion or restart."
            )

    def seal(self) -> None:
        self.closed = True
        if self.worker and not self.worker.done():
            self.worker.cancel()

    async def finish(self) -> None:
        self.seal()
        if self.worker:
            await asyncio.gather(self.worker, return_exceptions=True)
        # Also handles cancellation before run() entered its try/finally.
        self.discard_remaining()
        self.rpc = None
        try:
            self.persist()
        except OSError as exc:
            log_error(log, f"followup-save-{self.task_id}", exc)
            self.engine.state.interrupted = True
            self.engine.sink.text(
                "Could not save follow-up outcomes. Inspect disk access and journal logs; uncertain input is never replayed."
            )

    def problems(self) -> str:
        rows = [
            f"- Follow-up #{r['number']} (Discord message {r['message_id']}): {r['status']}"
            for r in self.records
            if r["status"] in {"uncertain", "rejected", "not_submitted"}
        ]
        if not rows:
            return ""
        return (
            "**Follow-up delivery**\n"
            + "\n".join(rows)
            + "\nUncertain input may already have been applied. Review this result and repository before resending. No input was replayed."
        )

    def status(self) -> str:
        return f"Follow-ups: {self.accepted} accepted; {len(self.queue)} waiting/in flight; input {'closed' if self.closed or self.blocked else 'open'}."
