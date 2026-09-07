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
        self._cond = threading.Condition(threading.Lock())
        self._next_ticket = 0
        self._now_serving = 0
        self._cancelled_tickets: set[int] = set()

    def __enter__(self) -> TicketLock:
        with self._cond:
            ticket = self._next_ticket
            self._next_ticket += 1
            try:
                while self._now_serving != ticket:
                    self._cond.wait()
            except BaseException:
                if self._now_serving == ticket:
                    self._advance_now_serving()
                else:
                    self._cancelled_tickets.add(ticket)
                raise
        return self

    def _advance_now_serving(self) -> None:
        """Advance _now_serving, skipping any cancelled tickets (must hold _cond)."""
        self._now_serving += 1
        while self._now_serving in self._cancelled_tickets:
            self._cancelled_tickets.discard(self._now_serving)
            self._now_serving += 1
        self._cond.notify_all()

    def __exit__(self, _exc_type: object, _exc: object, _tb: object) -> None:
        with self._cond:
            self._advance_now_serving()
