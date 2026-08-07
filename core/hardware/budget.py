"""Memory budget calculations shared by optimizer and compatibility checks."""

from __future__ import annotations

from dataclasses import dataclass

from core.hardware.detector import HardwareInfo
from core.models.gguf import GGUFMetadata


@dataclass(frozen=True)
class MemoryBudget:
    required_bytes: int
    available_bytes: int
    reserve_bytes: int
    fits: bool
    reasons: tuple[str, ...] = ()

    @property
    def utilization(self) -> float:
        return self.required_bytes / max(1, self.available_bytes)


def estimate_memory_budget(
    hardware: HardwareInfo,
    model: GGUFMetadata,
    *,
    context_size: int,
    mmproj_size_bytes: int = 0,
    slots: int = 1,
    kv_bytes_per_token: int = 4096,
) -> MemoryBudget:
    reserve = max(512 << 20, int((hardware.ram_total_bytes or 0) * 0.08))
    weights = model.size_bytes
    kv = context_size * max(1, slots) * kv_bytes_per_token
    required = weights + mmproj_size_bytes + kv + reserve
    gpu = hardware.primary_gpu
    available = (gpu.free_bytes if gpu else 0) + hardware.ram_available_bytes
    reasons = []
    if model.is_moe:
        reasons.append(
            f"MoE weights are total file size; active experts {model.expert_used_count or '?'} "
            "reduce compute, not necessarily storage"
        )
    reasons.append(f"weights={weights >> 20} MiB, KV estimate={kv >> 20} MiB")
    return MemoryBudget(required, available, reserve, required <= available, tuple(reasons))
