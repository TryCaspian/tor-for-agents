"""In-enclave policy. Runs inside the attested code, so the operator cannot
lift it without changing the measurement (which peers pin and would detect).

This is what turns decision-coercion from "sign anything" into "sign only
what policy already permits." Kept small and pure so it is easy to audit and
test.
"""
import time
from collections import deque


class Policy:
    def __init__(self, allowed_ops=None, max_per_minute: int = 30,
                 max_value: float | None = None):
        # None = allow any op; otherwise an allowlist of op names.
        self.allowed_ops = set(allowed_ops) if allowed_ops is not None else None
        self.max_per_minute = max_per_minute
        self.max_value = max_value
        self._recent = deque()

    def check(self, request: dict, now: float | None = None) -> tuple[bool, str]:
        """Return (allowed, reason). Pure given `now`."""
        now = now if now is not None else time.time()

        op = request.get("op")
        if self.allowed_ops is not None and op not in self.allowed_ops:
            return False, f"op {op!r} not in policy allowlist"

        if self.max_value is not None:
            value = request.get("value")
            if isinstance(value, (int, float)) and value > self.max_value:
                return False, f"value {value} exceeds cap {self.max_value}"

        # sliding one-minute rate window
        while self._recent and now - self._recent[0] > 60:
            self._recent.popleft()
        if len(self._recent) >= self.max_per_minute:
            return False, f"rate limit: {self.max_per_minute}/min exceeded"

        return True, "ok"

    def record(self, now: float | None = None):
        self._recent.append(now if now is not None else time.time())
