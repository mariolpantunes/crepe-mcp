"""Shared locking primitives for the CREPE MCP in-memory stores.

Provides TicketLock — a mutex that grants entry in strict FIFO arrival order,
used by both the presentation store and the document store.
"""
from __future__ import annotations

import threading


class TicketLock:
    """A mutex that grants entry in strict arrival (FIFO) order.

    threading.Lock makes no fairness guarantee — under contention the OS
    scheduler picks which waiter wakes next, which can and does reorder
    logically-sequential operations. This hands out a ticket number on
    entry and only proceeds once it's that ticket's turn, so N threads
    calling __enter__ in some order T0, T1, …, Tn-1 (wall-clock arrival)
    are guaranteed to execute in that same order.

    This lock is interrupt-safe. If a waiting thread receives an exception
    (like a KeyboardInterrupt or timeout), the ticket counter advances correctly
    to avoid deadlocking the remaining queue.

    Note on notify_all(): this wakes all waiters on every release, which
    causes a thundering herd under a no-GIL build (PEP 703). If that
    ever matters, change to notify(1). Under the current GIL it is
    irrelevant — only one thread runs Python bytecode at a time.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._cond = threading.Condition(self._lock)
        self._next_ticket = 0
        self._now_serving = 0

    def __enter__(self) -> TicketLock:
        self._lock.acquire()
        ticket = self._next_ticket
        self._next_ticket += 1
        try:
            while self._now_serving != ticket:
                self._cond.wait()
        except BaseException:
            # The reserved slot will never run; advance the counter so that
            # every waiter behind us is not permanently blocked.
            self._now_serving += 1
            self._cond.notify_all()
            self._lock.release()
            raise
        return self

    def __exit__(self, _exc_type: object, _exc: object, _tb: object) -> None:
        self._now_serving += 1
        self._cond.notify_all()
        self._lock.release()
