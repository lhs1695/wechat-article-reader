"""Cooperative cancellation for blocking tool work."""

import asyncio
from collections.abc import Callable
from threading import Event
from typing import Any


async def run_cancellable(function: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    signal = Event()
    task = asyncio.create_task(asyncio.to_thread(function, *args, cancel_event=signal, **kwargs))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        signal.set()

        # A synchronous request already in flight cannot be forcibly stopped.
        # Its cancellation checks prevent retries and commits after it returns.
        def finished(done: asyncio.Task[Any]) -> None:
            if not done.cancelled():
                done.exception()

        task.add_done_callback(finished)
        raise
