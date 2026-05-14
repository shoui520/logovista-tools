"""Bounded scan helpers for lazy reader operations."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable


@dataclass
class ScanBudget:
    max_bytes: int | None = None
    cancel: Callable[[], bool] | None = None
    bytes_scanned: int = 0
    truncated: bool = False
    cancelled: bool = False

    def allow(self, byte_count: int) -> bool:
        if self.cancel is not None and self.cancel():
            self.cancelled = True
            self.truncated = True
            return False
        if self.max_bytes is not None and self.bytes_scanned + byte_count > self.max_bytes:
            self.truncated = True
            return False
        self.bytes_scanned += byte_count
        return True
