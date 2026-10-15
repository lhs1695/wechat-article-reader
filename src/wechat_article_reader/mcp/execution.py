"""Cooperative cancellation for blocking tool work."""

import asyncio
from collections.abc import Callable
from typing import Any

from ..shared.exceptions import OperationDeadlineExceededError
from ..shared.operation import OperationSignal

DEFAULT_OPERATION_TIMEOUT_SECONDS = 180.0


async def report_operation_phase(context: Any | None, progress: float, message: str) -> None:
    """Report a phase counter without implying a percentage or unknown total."""
    if context is not None:
        try:
            await context.report_progress(progress, None, message)
        except Exception:
            # Progress is optional telemetry; direct FastMCP calls may lack request context.
            return


def make_progress_callback(
    context: Any | None, loop: asyncio.AbstractEventLoop
) -> Callable[[str, int, int], None]:
    def report(phase: str, completed: int, total: int) -> None:
        if context is None:
            return
        future = asyncio.run_coroutine_threadsafe(
            context.report_progress(
                float(completed), float(total), f"{phase}: {completed}/{total}"
            ),
            loop,
        )
        try:
            future.result(timeout=3)
        except Exception:
            future.cancel()

    return report


async def run_cancellable(
    function: Callable[..., Any],
    *args: Any,
    operation_timeout: float = DEFAULT_OPERATION_TIMEOUT_SECONDS,
    **kwargs: Any,
) -> Any:
    signal = OperationSignal(operation_timeout)
    task = asyncio.create_task(asyncio.to_thread(function, *args, cancel_event=signal, **kwargs))
    try:
        done, _ = await asyncio.wait({task}, timeout=operation_timeout)
        if task in done:
            result = task.result()
            signal.remaining()
            return result
        signal.set()

        def deadline_finished(done_task: asyncio.Task[Any]) -> None:
            if not done_task.cancelled():
                done_task.exception()

        task.add_done_callback(deadline_finished)
        raise OperationDeadlineExceededError()
    except asyncio.CancelledError:
        signal.set()

        # A synchronous request already in flight cannot be forcibly stopped.
        # Its cancellation checks prevent retries and commits after it returns.
        def cancelled_finished(done_task: asyncio.Task[Any]) -> None:
            if not done_task.cancelled():
                done_task.exception()

        task.add_done_callback(cancelled_finished)
        raise
