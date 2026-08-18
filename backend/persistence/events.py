from __future__ import annotations

import json
import os
from pathlib import Path

from backend.domain.common import to_primitive
from backend.domain.events import Event


class EventStore:
    """Append-only JSONL store with bounded size segments."""

    def __init__(self, directory: str | Path, run_id: str, max_segment_bytes: int = 25 * 1024 * 1024):
        if max_segment_bytes < 1:
            raise ValueError("max_segment_bytes must be positive")
        self.directory = Path(directory)
        self.run_id = run_id
        self.max_segment_bytes = max_segment_bytes
        self.directory.mkdir(parents=True, exist_ok=True)

    def append(self, event: Event) -> Path:
        encoded = (json.dumps(to_primitive(event), ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
        path = self._active_path(len(encoded))
        with path.open("ab") as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        return path

    def read_all(self) -> list[dict]:
        events = []
        for path in self._segments():
            with path.open(encoding="utf-8") as stream:
                for line in stream:
                    if line.strip():
                        try:
                            value = json.loads(line)
                        except json.JSONDecodeError:
                            # A crash may leave only the final append incomplete.
                            # Earlier durable events remain usable for recovery.
                            continue
                        if isinstance(value, dict):
                            events.append(value)
        return events

    def storage_bytes(self) -> int:
        return sum(path.stat().st_size for path in self._segments())

    def _segments(self) -> list[Path]:
        return sorted(self.directory.glob(f"run-{self.run_id}.events.*.jsonl"))

    def _active_path(self, incoming_size: int) -> Path:
        segments = self._segments()
        if segments and segments[-1].stat().st_size + incoming_size <= self.max_segment_bytes:
            return segments[-1]
        number = len(segments) + 1
        return self.directory / f"run-{self.run_id}.events.{number:04d}.jsonl"
