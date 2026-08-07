"""Bounded throughput helpers shared by tagger/VLM pipeline stages."""

from __future__ import annotations

from dataclasses import dataclass
import time


@dataclass(frozen=True)
class ThroughputMeasurement:
    items: int
    elapsed_seconds: float
    items_per_second: float
    provider: str = ""


@dataclass(frozen=True)
class ConcurrencyPlan:
    workers: int
    batch_size: int
    reason: str


def measure(label: str, operation, items: int = 1) -> ThroughputMeasurement:
    started = time.monotonic()
    operation()
    elapsed = max(1e-9, time.monotonic() - started)
    return ThroughputMeasurement(items, elapsed, items / elapsed, label)


def choose_concurrency(*, available_bytes: int, per_item_bytes: int,
                       requested_workers: int = 1, requested_batch: int = 1,
                       reserve_fraction: float = .15) -> ConcurrencyPlan:
    """Return a conservative plan; never schedules independent pools blindly."""
    if per_item_bytes <= 0 or available_bytes <= 0:
        return ConcurrencyPlan(1, 1, "unknown memory budget")
    budget = int(available_bytes * max(0.1, min(.9, 1.0 - reserve_fraction)))
    capacity = max(1, budget // per_item_bytes)
    workers = max(1, min(int(requested_workers), capacity))
    batch = max(1, min(int(requested_batch), capacity // workers or 1))
    reason = "within memory budget" if workers == requested_workers and batch == requested_batch else "clamped to memory budget"
    return ConcurrencyPlan(workers, batch, reason)
