"""Safe operational errors: never embed server payloads or credentials."""

import logging
import traceback
from types import TracebackType


class BridgeError(Exception):
    pass


class RpcRejected(BridgeError):
    """A correlated server rejection, distinct from an uncertain transport outcome."""

    def __init__(self, request_id: str | int, code: object) -> None:
        self.code = code
        super().__init__(
            f"Codex RPC rejected request {request_id} (code {code}). Run doctor; inspect "
            "authentication, managed policy and CLI compatibility. Server payload omitted for privacy."
        )


class LimitError(BridgeError):
    def __init__(
        self, operation: str, elapsed: float, limit: float, *, diagnostic: str | None = None
    ) -> None:
        super().__init__(
            f"{operation} timed out after {elapsed:.1f}s (configured limit {limit:g}s). "
            + (diagnostic or "Run doctor and inspect journal timestamps; work was not replayed.")
        )
        self.operation = operation
        self.elapsed = elapsed
        self.limit = limit


def safe_trace(tb: TracebackType | None) -> str:
    # No source lines, exception messages, or locals: these may contain prompt/token text.
    return " -> ".join(f"{f.name}:{f.lineno}" for f in traceback.extract_tb(tb))


def log_error(logger: logging.Logger, operation: str, exc: BaseException) -> None:
    if isinstance(exc, LimitError):
        logger.error(
            "operation=%s elapsed=%.3fs limit=%gs next=doctor/journal",
            exc.operation,
            exc.elapsed,
            exc.limit,
        )
    logger.error(
        "operation=%s error=%s trace=%s",
        operation,
        type(exc).__name__,
        safe_trace(exc.__traceback__),
    )
