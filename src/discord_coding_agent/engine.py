"""Single-owner task/session controller independent of Discord HTTP and Gateway."""

import asyncio
import json
import logging
import math
import re
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from . import protocol
from .config import Config, discover_codex, repository_identity
from .errors import BridgeError, LimitError, log_error
from .locks import Lease
from .rpc import Json, Rpc
from .state import MAX_RESULT, StateStore

log = logging.getLogger(__name__)

HELP = """Ordinary text starts/continues the saved Codex conversation. One active task; busy messages are NOT submitted.
!help — commands and limitations
!ping — Discord connection test; no model
!status — operational state, thread, mode, elapsed, last observed activity, pending requests
!run 30m task text — submit one task with a time limit (s/m/h, e.g. 90s or 1.5h); !run unlimited task text removes the task limit for that task
!new [auto|manual] — fresh conversation; current mode if omitted; idle only
!approvals — selected mode and verification scope
!approve ID / !deny ID — decide a pending approval (same path as buttons)
!answer ID text — answer one question; for multiple: !answer ID {"question_id":["answer"],"other_id":["answer"]}
!stop — interrupt, then stop the owned child if necessary; does not undo edits/external effects
!last — retrieve the last saved result
Use !new, !session or !repo to switch through the bridge. Asking Codex to “open a new chat” in prose does not switch it.
Ordinary text uses the configured task limit (default: none). !run overrides it for one task, including initialization and human wait; it cannot change active work. Natural-language time rules are passed to Codex; use !run for an enforced timer. Approval and connection timeouts still apply.
Text and repository paths only; attachments are rejected. Internet and valid Codex authentication required. Manual mode reviews sandbox escalations, not every edit. The service waits for your messages; no autonomous schedule."""


def parse_task_limit(value: str) -> float:
    """Parse an explicit bridge timer; never guess time limits from prompt prose."""
    if value.lower() == "unlimited":
        return 0
    match = re.fullmatch(r"([0-9]+(?:\.[0-9]+)?)([smh])", value.lower())
    if match:
        seconds = float(match[1]) * {"s": 1, "m": 60, "h": 3600}[match[2]]
        if math.isfinite(seconds) and seconds > 0:
            return seconds
    raise BridgeError(
        "Use !run DURATION task text: a positive duration such as 90s, 30m or 1.5h, or unlimited. Nothing was submitted."
    )


def describe_task_limit(limit: float) -> str:
    return f"{limit:g}s including initialization and human wait" if limit else "none (unlimited)"


@dataclass(frozen=True)
class Origin:
    owner: int
    guild: int | None
    channel: int
    message: int


@dataclass
class Pending:
    id: str
    rpc_id: str | int
    method: str
    params: Json
    task_id: str
    turn_id: str
    created: float = field(default_factory=time.monotonic)
    message_id: int | None = None
    details: str = ""
    deciding: bool = False


class Sink(Protocol):
    def text(self, content: str, *, result_id: str | None = None, inline: bool = False) -> None: ...
    def request(self, pending: Pending) -> None: ...
    def invalidate(self, pending: Pending) -> None: ...


class Engine:
    def __init__(
        self,
        config: Config,
        store: StateStore,
        sink: Sink,
        *,
        connection_only: bool = False,
        rpc_factory: Callable[..., Rpc] = Rpc,
        activity_path: Path | None = None,
    ) -> None:
        self.config, self.store, self.sink = config, store, sink
        self.state = store.load()
        self.connection_only, self.rpc_factory = connection_only, rpc_factory
        self.phase = "idle"
        self.connected = False
        self.verified = False
        self.worker: asyncio.Task[None] | None = None
        self.stop_worker: asyncio.Task[None] | None = None
        self.rpc: Rpc | None = None
        self.turn_id: str | None = None
        self.task_id = ""
        self.started: float | None = None
        self.elapsed: float | None = None
        self.task_limit = config.timeouts.task
        self.last_event = "none"
        self.last_event_at: float | None = None
        self.pending: dict[str, Pending] = {}
        self.items: dict[str, Json] = {}
        self.messages: dict[str, str] = {}
        self.done: asyncio.Future[Json] | None = None
        self.timers: dict[str, asyncio.Task[None]] = {}
        self.seen = set(self.state.seen_messages)
        self.activity = Lease(activity_path or config.state_dir / "activity.lock")

    @property
    def busy(self) -> bool:
        return self.worker is not None and not self.worker.done()

    def authorized(self, origin: Origin) -> bool:
        return (origin.owner, origin.guild, origin.channel) == (
            self.config.owner_id,
            self.config.guild_id,
            self.config.channel_id,
        )

    def transition(self, phase: str) -> None:
        log.info(
            "task=%s transition=%s->%s thread=%s turn=%s",
            self.task_id,
            self.phase,
            phase,
            self.state.thread_id,
            self.turn_id,
        )
        self.phase = phase

    def save(self) -> None:
        self.store.save(self.state)

    def remember(self, message_id: int) -> bool:
        if message_id in self.seen:
            return False
        self.state.seen_messages = (self.state.seen_messages + [message_id])[-512:]
        self.seen = set(self.state.seen_messages)
        self.save()
        return True

    async def message(self, origin: Origin, content: str, attachments: bool = False) -> None:
        if not self.authorized(origin) or not self.remember(origin.message):
            return
        if attachments:
            self.sink.text(
                "Attachments are unsupported and were not read. Send text or a path inside the selected repository. Nothing was submitted."
            )
            return
        content = content.strip()
        if not content:
            return
        if content.startswith("!"):
            try:
                await self.command(origin, content)
            except BridgeError as exc:
                self.sink.text(str(exc))
            return
        self.submit(origin, content, self.config.timeouts.task)

    def submit(self, origin: Origin, content: str, task_limit: float) -> None:
        """Reserve a task atomically after message authorization and deduplication."""
        if self.connection_only:
            self.sink.text(
                "Connection-only mode: no Codex process or model invoked. Restart without --connection-only."
            )
            return
        if self.busy or (self.stop_worker and not self.stop_worker.done()):
            self.sink.text(
                "Busy: your message was NOT submitted. Wait for completion or use !stop."
            )
            return
        # No await between the busy check, durable reservation and task creation.
        if not self.activity.acquire():
            self.sink.text(
                "Busy: another channel is working or maintenance is in progress. Your message was NOT submitted. Use !status, wait, or !stop."
            )
            return
        self.task_id = uuid.uuid4().hex
        self.started = time.monotonic()
        self.elapsed = None
        self.task_limit = task_limit
        self.verified = False
        self.state.active = {
            "task_id": self.task_id,
            "message_id": origin.message,
            "started_at": time.time(),
            "turn_id": None,
            "task_limit_seconds": task_limit,
        }
        self.state.interrupted = False
        try:
            self.save()
        except BaseException:
            self.state.active = None
            self.activity.close()
            raise
        self.transition("initializing")
        log.info("task=%s task_limit_seconds=%g (0=unlimited)", self.task_id, task_limit)
        self.worker = asyncio.create_task(self._run(content), name=f"coding-{self.task_id}")
        self.sink.text(
            f"Accepted task {self.task_id[:12]}. Task limit: {describe_task_limit(task_limit)}. Use !status for last observed activity; !stop to interrupt."
        )

    async def command(self, origin: Origin, content: str) -> None:
        parts = content.split(maxsplit=2)
        cmd = parts[0].lower()
        if cmd in {"!help", "!ping", "!status", "!approvals", "!last", "!stop"} and len(parts) != 1:
            raise BridgeError(f"{cmd} takes no arguments; see !help.")
        if cmd == "!help":
            self.sink.text(HELP, inline=True)
        elif cmd == "!ping":
            self.sink.text(
                f"{self.config.display_name}: pong — Discord receive/send works; no model invoked."
            )
        elif cmd == "!status":
            self.sink.text(self.status())
        elif cmd == "!run":
            if len(parts) != 3:
                raise BridgeError(
                    "Use !run DURATION task text; e.g. !run 30m inspect and fix tests."
                )
            self.submit(origin, parts[2], parse_task_limit(parts[1]))
        elif cmd == "!approvals":
            self.sink.text(self.approvals())
        elif cmd == "!last":
            # An explicit retrieval must emit a new message, not reconcile away an old result.
            self.sink.text(self.state.last_result or "No completed result saved.")
        elif cmd == "!new":
            if len(parts) > 2 or (len(parts) == 2 and parts[1] not in {"auto", "manual"}):
                raise BridgeError("Use !new, !new auto or !new manual.")
            if self.busy or (self.stop_worker and not self.stop_worker.done()):
                raise BridgeError(
                    "Cannot switch sessions or modes during active work. Wait or use !stop; your task was not changed."
                )
            self.state.mode = parts[1] if len(parts) == 2 else self.state.mode
            self.state.thread_id = None
            self.state.interrupted = False
            self.verified = False
            self.save()
            self.transition("idle")
            self.sink.text(
                f"Fresh conversation selected; mode {self.state.mode}. Codex thread will be created and verified on the next ordinary message."
            )
        elif cmd in {"!approve", "!deny", "!answer"}:
            if (
                len(parts) < 2
                or (cmd == "!answer" and len(parts) != 3)
                or (cmd != "!answer" and len(parts) != 2)
            ):
                raise BridgeError("Use !approve ID, !deny ID or !answer ID text/JSON. See !help.")
            message = self.decide(
                origin, parts[1], cmd == "!approve", answer=parts[2] if cmd == "!answer" else None
            )
            self.sink.text(message)
        elif cmd == "!stop":
            if self.busy:
                if not self.stop_worker or self.stop_worker.done():
                    self.transition("stopping")
                    self.invalidate_all()
                    self.stop_worker = asyncio.create_task(self.stop())
                self.sink.text(
                    "Interrupt requested. Cancellation does not undo completed edits or external effects."
                )
            else:
                self.sink.text("No active task.")
        else:
            raise BridgeError("Unknown command; use !help. Nothing was submitted to Codex.")

    def approvals(self) -> str:
        detail = (
            "Codex auto_review reviews eligible escalations and can reject them; requests still requiring you are displayed."
            if self.state.mode == "auto"
            else "Codex routes approval requests to you. Approve/Deny applies once; sandbox-allowed actions need no prompt."
        )
        verification = (
            "Last task verified process config and the effective thread response; the child closes after each task. This is not proof of every tool's runtime review."
            if self.verified
            else "Not currently verified; checked before the next coding turn. Unsupported settings fail without silently switching modes."
        )
        return f"Selected mode: {self.state.mode}; policy on-request; sandbox workspace-write. {detail} {verification}"

    def status(self) -> str:
        now = time.monotonic()
        duration = (
            self.elapsed
            if self.elapsed is not None
            else now - self.started
            if self.started
            else None
        )
        elapsed = f"{duration:.1f}s" if duration is not None else "n/a"
        age = f"{now - self.last_event_at:.1f}s ago" if self.last_event_at else "n/a"
        return (
            f"State: {self.phase if self.connected else 'disconnected'}; task phase: {self.phase}; Gateway: {'connected' if self.connected else 'disconnected'}\n"
            f"Thread: {self.state.thread_id or 'not created'}; mode: {self.state.mode}; verified: {self.verified}\n"
            f"Elapsed: {elapsed}; last observed activity: {self.last_event} ({age})\n"
            f"Pending: {', '.join(self.pending) or 'none'}; interrupted: {self.state.interrupted}; last result delivered: {self.state.delivered}\n"
            f"{getattr(self.sink, 'status', '')}\n"
            f"{'Active' if self.busy else 'Default'} task limit: {describe_task_limit(self.task_limit if self.busy else self.config.timeouts.task)}. Quiet logs/typing are not proof a task is stuck."
        )

    async def _prepare(self) -> None:
        if not any(self.config.repo.is_relative_to(root) for root in self.config.roots):
            raise BridgeError(
                "This saved repository is outside WORKSPACE_ROOTS. Select an allowed repository with !repo or update the roots locally."
            )
        identity = await asyncio.to_thread(repository_identity, self.config.repo)
        if identity != self.store.identity:
            raise BridgeError(
                "Repository identity changed; this session was not resumed. Inspect the repository and workspace recovery guide."
            )
        executable = discover_codex(self.config.codex)
        await protocol.version(executable, self.config.timeouts.initialization)
        rpc = self.rpc_factory(self.config.timeouts, self.event, self.request, self.disconnected)
        self.rpc = rpc
        await rpc.start(
            protocol.argv(executable, self.state.mode),
            self.config.repo,
            protocol.child_environment(),
        )
        await protocol.initialize(rpc)
        await protocol.verify_process(rpc, self.config.repo, self.state.mode)
        method = "thread/resume" if self.state.thread_id else "thread/start"
        response = await rpc.call(
            method, protocol.thread_params(self.config.repo, self.state.mode, self.state.thread_id)
        )
        thread_id = protocol.verify_thread(response, self.config.repo, self.state.mode)
        if self.state.thread_id and thread_id != self.state.thread_id:
            raise BridgeError(
                "Resume returned a different thread ID; no turn submitted. Inspect Codex state."
            )
        self.state.thread_id = thread_id
        self.verified = True
        self.save()

    async def _run(self, prompt: str) -> None:
        try:
            await self._execute(prompt)
        finally:
            self.activity.close()

    async def _execute(self, prompt: str) -> None:
        self.done = asyncio.get_running_loop().create_future()
        self.messages.clear()
        self.items.clear()
        task_started = time.monotonic()
        try:
            try:
                async with asyncio.timeout(self.task_limit or None) as task_deadline:
                    init_started = time.monotonic()
                    try:
                        async with asyncio.timeout(self.config.timeouts.initialization):
                            await self._prepare()
                    except TimeoutError as exc:
                        raise LimitError(
                            "initialization",
                            time.monotonic() - init_started,
                            self.config.timeouts.initialization,
                        ) from exc
                    assert self.rpc
                    self.transition("running")
                    response = await self.rpc.call(
                        "turn/start",
                        {
                            "threadId": self.state.thread_id,
                            "input": [{"type": "text", "text": prompt, "text_elements": []}],
                        },
                    )
                    turn = response.get("turn", {})
                    self.set_turn(turn.get("id"))
                    if (
                        turn.get("status") in {"completed", "failed", "interrupted"}
                        and not self.done.done()
                    ):
                        self.done.set_result(turn)
                    completed = await self.done
                    status = completed.get("status")
                    if status != "completed":
                        raise BridgeError(
                            f"Codex turn ended with status {status if status in {'failed', 'interrupted'} else 'unknown'}. Review the repository and !last before continuing; no work was replayed. Check authentication/usage limits and doctor if it failed."
                        )
                    result = (
                        "\n\n".join(self.messages.values())
                        or "Codex completed with no assistant text. Inspect repository changes and !status."
                    )
                    self.state.last_result = result
                    self.state.result_id = self.task_id
                    self.state.delivered = False
                    self.state.active = None
                    self.state.interrupted = False
                    self.save()  # Durable before any outbound delivery.
                    self.sink.text(result, result_id=self.task_id)
                    self.transition("idle")
            except TimeoutError as exc:
                if not task_deadline.expired():
                    raise
                raise LimitError(
                    "full task deadline (including human wait)",
                    time.monotonic() - task_started,
                    self.task_limit,
                ) from exc
        except asyncio.CancelledError:
            self.state.interrupted = True
            self.transition("idle")
            self.sink.text(
                "Task interrupted. Inspect git status/diff before continuing. Nothing will be replayed automatically."
            )
        except Exception as exc:
            self.state.interrupted = True
            self.transition("failed")
            log_error(log, f"task-{self.task_id}", exc)
            self.sink.text(
                str(exc)
                if isinstance(exc, BridgeError)
                else "Bridge task failed. Inspect sanitized journal logs and run doctor; uncertain work was not replayed."
            )
        finally:
            if self.state.interrupted and self.state.active:
                self.state.last_interruption = self.state.active.copy()
            self.invalidate_all()
            if self.rpc:
                # A full deadline or protocol failure also tries interruption before escalation.
                if self.state.interrupted and self.turn_id and not self.rpc.failure:
                    try:
                        await self.rpc.call(
                            "turn/interrupt",
                            {"threadId": self.state.thread_id, "turnId": self.turn_id},
                            self.config.timeouts.shutdown,
                        )
                    except BridgeError:
                        pass
                await self.rpc.close()
            self.rpc = None
            self.elapsed = time.monotonic() - task_started
            self.turn_id = None
            self.state.active = None
            self.save()
            if self.done and not self.done.cancelled():
                # Retrieve an early disconnect exception even if initialization failed first.
                if self.done.done():
                    self.done.exception()
                else:
                    self.done.cancel()

    def set_turn(self, turn_id: Any) -> None:
        if not isinstance(turn_id, str) or not turn_id:
            raise BridgeError("Invalid turn ID from Codex; inspect protocol compatibility.")
        if self.turn_id and self.turn_id != turn_id:
            raise BridgeError("Unexpected overlapping Codex turn; work stopped.")
        self.turn_id = turn_id
        if self.state.active:
            self.state.active["turn_id"] = turn_id
            self.save()

    def event(self, method: str, params: Json) -> None:
        # Filter raw reasoning, deltas and unrelated threads before retaining anything.
        if (
            method.startswith("codex/event/")
            or "delta" in method.lower()
            or "reasoning" in method.lower()
        ):
            return
        if params.get("threadId") != self.state.thread_id or not self.busy:
            return
        if method == "turn/started":
            self.set_turn(params.get("turn", {}).get("id"))
        turn_id = params.get("turnId", params.get("turn", {}).get("id"))
        if turn_id is not None and self.turn_id and turn_id != self.turn_id:
            return
        self.last_event, self.last_event_at = method, time.monotonic()
        if method == "serverRequest/resolved":
            for pending in list(self.pending.values()):
                if pending.rpc_id == params.get("requestId"):
                    self.invalidate(pending.id)
        elif method in {"item/started", "item/completed"}:
            item = params.get("item", {})
            item_id = item.get("id")
            if not isinstance(item_id, str):
                raise BridgeError("Malformed Codex item; no approval can be safely associated.")
            if item.get("type") in {"fileChange", "commandExecution"}:
                details = {
                    k: v for k, v in item.items() if k not in {"aggregatedOutput", "output", "text"}
                }
                self.items[item_id] = details
                if len(self.items) > 64:
                    del self.items[next(iter(self.items))]
                if len(json.dumps(self.items).encode()) > MAX_RESULT:
                    raise BridgeError(
                        "Approval details exceed the 4 MiB safety bound. Work stopped; inspect locally."
                    )
            if method == "item/completed" and item.get("type") == "agentMessage":
                text = item.get("text")
                if not isinstance(text, str):
                    raise BridgeError("Malformed completed assistant message.")
                self.messages[item_id] = text
                if sum(len(s.encode()) for s in self.messages.values()) > MAX_RESULT:
                    raise BridgeError(
                        "Assistant result exceeds the 4 MiB bound; work stopped. Inspect the Codex thread locally."
                    )
            if item.get("status") == "declined":
                self.sink.text(
                    "Codex reports a declined action. In auto mode this may be an automatic-review rejection. Review the final explanation before retrying; restrictions remain in effect."
                )
        elif method == "turn/completed" and self.done and not self.done.done():
            self.invalidate_all()
            self.done.set_result(params["turn"])
        elif method == "error":
            self.sink.text(
                "Codex reported an error; it may be retrying. Use !status for observed activity and inspect authentication/usage limits. Error payload omitted from logs for privacy."
            )
        elif method == "item/autoApprovalReview/completed":
            review = params.get("review", {})
            status = review.get("status")
            if status in {"denied", "timedOut", "aborted"}:
                self.sink.text(
                    f"Codex automatic approval review: {status}. Restrictions remain in effect. "
                    "Review the action and rationale below; use !stop if needed.\n"
                    + json.dumps(
                        {"action": params.get("action"), "review": review},
                        ensure_ascii=False,
                        indent=2,
                    )
                )
                log.warning("auto_review=%s task=%s turn=%s", status, self.task_id, self.turn_id)
        elif method == "guardianWarning":
            self.sink.text(
                "Codex automatic-review warning: " + str(params.get("message", "unavailable"))
            )

    def disconnected(self, error: BridgeError) -> None:
        if self.done and not self.done.done():
            self.done.set_exception(error)

    def request(self, rpc_id: str | int, method: str, params: Json) -> None:
        assert self.rpc
        if method == "currentTime/read":
            self.rpc.reply(rpc_id, {"currentTimeAt": int(time.time())})
            return
        if method not in protocol.APPROVALS | {protocol.QUESTION}:
            self.rpc.reply(
                rpc_id,
                error="Unsupported bridge request; use a supported CLI workflow locally. No approval granted.",
            )
            self.sink.text(
                f"Unsupported Codex request ({method[:100]}): returned an RPC error. Use !stop and handle this capability locally; run doctor to check compatibility."
            )
            return
        try:
            protocol.validate_request(method, params)
        except BridgeError as exc:
            self.rpc.reply(rpc_id, error=str(exc))
            self.sink.text(
                f"Codex request rejected: {exc} Run doctor and inspect protocol compatibility; no approval granted."
            )
            return
        if (
            not self.busy
            or self.phase == "stopping"
            or params.get("threadId") != self.state.thread_id
            or params.get("turnId") != self.turn_id
        ):
            self.rpc.reply(
                rpc_id,
                error="Request does not belong to the active bridge turn; no approval granted.",
            )
            return
        if len(self.pending) >= 16 or any(p.rpc_id == rpc_id for p in self.pending.values()):
            self.rpc.reply(
                rpc_id,
                error="Duplicate request or pending request limit reached; no approval granted.",
            )
            return
        if method == protocol.QUESTION:
            questions = params.get("questions")
            if (
                not isinstance(questions, list)
                or not questions
                or any(
                    not isinstance(q, dict) or not isinstance(q.get("id"), str) or q.get("isSecret")
                    for q in questions
                )
            ):
                self.rpc.reply(
                    rpc_id,
                    error="Secret or malformed questions are unsupported over Discord. Ask the owner to configure secrets locally.",
                )
                self.sink.text(
                    "Codex requested secret or malformed input. It was rejected; configure secrets locally, never in Discord."
                )
                return
        pending = Pending(
            uuid.uuid4().hex[:16], rpc_id, method, params, self.task_id, self.turn_id or ""
        )
        pending.details = json.dumps(
            {
                "request": params,
                "item": self.items.get(params.get("itemId", ""), {}),
                "repository": str(self.config.repo),
            },
            ensure_ascii=False,
            indent=2,
        )
        if len(pending.details.encode()) > MAX_RESULT:
            self.rpc.reply(
                rpc_id, error="Approval details too large to safely display; no approval granted."
            )
            return
        self.pending[pending.id] = pending
        self.timers[pending.id] = asyncio.create_task(self.expire(pending.id))
        self.last_event, self.last_event_at = method, time.monotonic()
        self.wait_phase()
        log.info(
            "approval=open task=%s request=%s rpc=%s turn=%s",
            self.task_id,
            pending.id,
            rpc_id,
            self.turn_id,
        )
        self.sink.request(pending)

    def wait_phase(self) -> None:
        if self.phase == "stopping":
            return
        self.transition(
            "awaiting approval"
            if any(p.method in protocol.APPROVALS for p in self.pending.values())
            else "awaiting answer"
            if self.pending
            else "running"
        )

    def bind(self, request_id: str, message_id: int) -> bool:
        pending = self.pending.get(request_id)
        if not pending or pending.deciding:
            return False
        pending.message_id = message_id
        self.state.controls = (self.state.controls + [message_id])[-32:]
        self.save()
        return True

    def decide(
        self,
        origin: Origin,
        request_id: str,
        approved: bool,
        *,
        answer: str | None = None,
        button_message: int | None = None,
    ) -> str:
        pending = self.pending.get(request_id)
        if not self.authorized(origin):
            raise BridgeError(
                "This control is restricted to the configured owner, server and channel."
            )
        if (
            not pending
            or pending.deciding
            or not self.busy
            or self.phase == "stopping"
            or pending.task_id != self.task_id
            or pending.turn_id != self.turn_id
            or time.monotonic() - pending.created >= self.config.timeouts.user_wait
        ):
            raise BridgeError(
                "Request expired or already decided. Use !status; old controls cannot approve new work."
            )
        if pending.message_id is None or (
            button_message is not None and button_message != pending.message_id
        ):
            raise BridgeError(
                "The complete request has not been delivered, or this is the wrong control message. Wait for the request or use !stop."
            )
        if pending.method == protocol.QUESTION:
            if answer is None:
                raise BridgeError("This request needs !answer ID text/JSON, not Approve/Deny.")
            result = self.answer(pending, answer)
        else:
            if answer is not None:
                raise BridgeError("Use !approve ID or !deny ID for approval requests.")
            result = protocol.approval_result(pending.method, pending.params, approved)
        # Atomic claim shared by text and buttons; no await until after invalidation.
        pending.deciding = True
        assert self.rpc
        self.rpc.reply(pending.rpc_id, result)
        log.info(
            "approval=decided task=%s request=%s decision=%s",
            self.task_id,
            request_id,
            "answer" if answer is not None else "approve" if approved else "deny",
        )
        self.invalidate(request_id)
        return f"Decision submitted for {request_id}."

    @staticmethod
    def answer(pending: Pending, text: str) -> Json:
        ids = [q["id"] for q in pending.params["questions"]]
        if len(ids) == 1 and not text.lstrip().startswith("{"):
            answers = {ids[0]: [text]}
        else:
            try:
                answers = json.loads(text)
            except ValueError as exc:
                raise BridgeError(
                    'Use JSON: {"question_id":["answer"],"other_id":["answer"]}.'
                ) from exc
        if (
            not isinstance(answers, dict)
            or set(answers) != set(ids)
            or any(
                not isinstance(v, list)
                or not v
                or any(not isinstance(a, str) or not a.strip() for a in v)
                for v in answers.values()
            )
        ):
            raise BridgeError(
                "Answer every question ID exactly once with a nonempty list of answer strings."
            )
        return {"answers": {key: {"answers": value} for key, value in answers.items()}}

    async def expire(self, request_id: str) -> None:
        await asyncio.sleep(self.config.timeouts.user_wait)
        pending = self.pending.get(request_id)
        if pending and self.rpc:
            log.warning(
                "approval=expired task=%s request=%s limit=%s",
                self.task_id,
                request_id,
                self.config.timeouts.user_wait,
            )
            self.rpc.reply(
                pending.rpc_id, error="Human input deadline expired; no approval granted."
            )
            self.invalidate(request_id)
            self.sink.text(
                str(
                    LimitError(
                        f"user wait for {request_id}",
                        time.monotonic() - pending.created,
                        self.config.timeouts.user_wait,
                    )
                )
            )

    def invalidate(self, request_id: str) -> None:
        pending = self.pending.pop(request_id, None)
        timer = self.timers.pop(request_id, None)
        if timer and timer is not asyncio.current_task():
            timer.cancel()
        if pending:
            self.sink.invalidate(pending)
            log.info("approval=invalidated task=%s request=%s", self.task_id, request_id)
        if self.busy and self.phase != "stopping":
            self.wait_phase()

    def invalidate_all(self) -> None:
        phase = self.phase
        for request_id in list(self.pending):
            self.invalidate(request_id)
        self.phase = phase

    async def stop(self) -> None:
        worker = self.worker
        if not worker or worker.done():
            return
        if self.rpc and self.turn_id:
            try:
                await self.rpc.call(
                    "turn/interrupt",
                    {"threadId": self.state.thread_id, "turnId": self.turn_id},
                    self.config.timeouts.shutdown,
                )
                await asyncio.wait_for(asyncio.shield(worker), self.config.timeouts.shutdown)
                return
            except (BridgeError, TimeoutError):
                pass
        worker.cancel()
        try:
            await worker
        except asyncio.CancelledError:
            # Cancellation can happen before _run has entered its try/finally.
            self.activity.close()
            self.state.interrupted = True
            self.state.last_interruption = self.state.active
            self.state.active = None
            self.save()
            self.transition("idle")

    async def close(self) -> None:
        if self.stop_worker and not self.stop_worker.done():
            await self.stop_worker
        else:
            await self.stop()
        self.invalidate_all()
