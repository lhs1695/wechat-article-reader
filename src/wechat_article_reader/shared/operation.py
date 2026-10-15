"""Operation deadlines and bounded, cancellable request coalescing."""

from __future__ import annotations

import time
from collections import OrderedDict
from collections.abc import Hashable
from threading import Event, Lock
from typing import Protocol

from .exceptions import OperationCancelledError, OperationDeadlineExceededError


class CancellationSignal(Protocol):
    def is_set(self) -> bool: ...


class OperationSignal:
    """Cancellation signal carrying one wall-clock deadline across sync work."""

    def __init__(self, timeout: float, *, event: Event | None = None) -> None:
        self.deadline = time.monotonic() + timeout
        self._event = event or Event()

    def set(self) -> None:
        self._event.set()

    def is_set(self) -> bool:
        return self._event.is_set()

    def remaining(self) -> float:
        if self._event.is_set():
            raise OperationCancelledError()
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise OperationDeadlineExceededError()
        return remaining

    def check(self) -> None:
        self.remaining()

    def wait(self, timeout: float | None = None) -> bool:
        remaining = max(0.0, self.deadline - time.monotonic())
        if timeout is not None:
            remaining = min(remaining, timeout)
        return self._event.wait(remaining)


def check_operation_deadline(signal: object | None) -> None:
    remaining = getattr(signal, "remaining", None)
    if callable(remaining):
        remaining()


class BoundedKeyedLockPool:
    """Coalesce work per key without serializing unrelated keys."""

    def __init__(self, max_keys: int = 256) -> None:
        self._max_keys = max_keys
        self._guard = Lock()
        self._locks: OrderedDict[Hashable, tuple[Lock, int]] = OrderedDict()

    def acquire(self, key: Hashable, signal: CancellationSignal | None = None) -> KeyedLockLease:
        with self._guard:
            entry = self._locks.get(key)
            if entry is None:
                self._evict_idle()
                if len(self._locks) >= self._max_keys:
                    raise RuntimeError("并发工作队列已满，请稍后重试")
                lock, users = Lock(), 0
            else:
                lock, users = entry
            self._locks[key] = (lock, users + 1)
            self._locks.move_to_end(key)

        try:
            while True:
                if signal is not None:
                    remaining = getattr(signal, "remaining", None)
                    wait_for = min(0.05, remaining()) if callable(remaining) else 0.05
                    if signal.is_set():
                        raise OperationCancelledError()
                else:
                    wait_for = 0.05
                if lock.acquire(timeout=wait_for):
                    if signal is not None:
                        check = getattr(signal, "check", None)
                        if callable(check):
                            check()
                        if signal.is_set():
                            lock.release()
                            raise OperationCancelledError()
                    return KeyedLockLease(self, key, lock)
        except BaseException:
            self._release_user(key, lock)
            raise

    def _release_user(self, key: Hashable, lock: Lock) -> None:
        with self._guard:
            entry = self._locks.get(key)
            if entry is None or entry[0] is not lock:
                return
            users = entry[1] - 1
            if users <= 0 and not lock.locked():
                del self._locks[key]
            else:
                self._locks[key] = (lock, users)
                self._locks.move_to_end(key)

    def _release(self, key: Hashable, lock: Lock) -> None:
        lock.release()
        self._release_user(key, lock)

    def _evict_idle(self) -> None:
        for key, (lock, users) in list(self._locks.items()):
            if users == 0 and not lock.locked():
                del self._locks[key]
                if len(self._locks) < self._max_keys:
                    return


class KeyedLockLease:
    def __init__(self, pool: BoundedKeyedLockPool, key: Hashable, lock: Lock) -> None:
        self._pool, self._key, self._lock = pool, key, lock

    def __enter__(self) -> KeyedLockLease:
        return self

    def __exit__(self, *_: object) -> None:
        self._pool._release(self._key, self._lock)
