"""
Cooperative Deadlines
---------------------
The council red-team flagged a blocker: `future.result(timeout=90)` returns to the
caller but CANNOT kill the underlying worker thread in Python. A "timed-out" agent
keeps running, and in ScoutAI it keeps holding the module-level shared curl_cffi
session (financial_analyst._STATE["session"]) — the next request then uses that
session concurrently, which is exactly the conflict the codebase is built to avoid.

Fix: agents accept a Deadline and check it cooperatively BETWEEN network steps and
retries (retry-stacking is what produces the ~5-minute worst case). The executor
timeout in graph.py is only a backstop; the real budgeting happens here, and the
session is rebuilt after a timeout so a zombie thread cannot corrupt it.
"""

import time
from typing import Optional


class Deadline:
    """A wall-clock budget. Agents check remaining() / expired() and bail early."""

    def __init__(self, seconds: float):
        self.seconds = seconds
        self.start = time.monotonic()

    def remaining(self) -> float:
        return max(0.0, self.seconds - (time.monotonic() - self.start))

    def expired(self) -> bool:
        return self.remaining() <= 0

    def clamp(self, requested_timeout: float) -> float:
        """
        Clamp a per-request network timeout to whatever budget is left, so a single
        slow socket cannot blow the whole deadline. Never returns <= 0 (callers that
        get a tiny value should check expired() first and skip the call).
        """
        return max(0.1, min(requested_timeout, self.remaining()))


def as_deadline(d: Optional[Deadline], default_seconds: float) -> Deadline:
    """Return d if it's a Deadline, else a fresh one — lets agents run standalone."""
    return d if isinstance(d, Deadline) else Deadline(default_seconds)
