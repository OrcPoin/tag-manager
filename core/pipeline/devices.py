"""Small device abstraction; keeps future multi-GPU scheduling bounded."""
from __future__ import annotations
from dataclasses import dataclass

@dataclass(frozen=True)
class DeviceSlot:
    device_id: str
    kind: str = "cpu"
    memory_bytes: int = 0
    reserved_bytes: int = 0

    @property
    def available_bytes(self) -> int:
        return max(0, self.memory_bytes - self.reserved_bytes)

def select_device(devices: list[DeviceSlot], required_bytes: int = 0) -> DeviceSlot:
    candidates = [d for d in devices if d.available_bytes >= required_bytes]
    return max(candidates or devices, key=lambda item: item.available_bytes)
