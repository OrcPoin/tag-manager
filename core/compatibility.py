"""Preflight compatibility and fallback decisions before starting inference."""

from __future__ import annotations

from dataclasses import dataclass

from core.hardware.budget import MemoryBudget, estimate_memory_budget
from core.hardware.detector import HardwareInfo
from core.models.gguf import GGUFMetadata


@dataclass(frozen=True)
class CompatibilityReport:
    compatible: bool
    severity: str
    summary: str
    budget: MemoryBudget
    recommendations: tuple[str, ...] = ()


def check_compatibility(
    hardware: HardwareInfo,
    model: GGUFMetadata,
    *,
    context_size: int,
    mmproj_size_bytes: int = 0,
    slots: int = 1,
) -> CompatibilityReport:
    budget = estimate_memory_budget(
        hardware, model, context_size=context_size,
        mmproj_size_bytes=mmproj_size_bytes, slots=slots,
    )
    recommendations: list[str] = []
    if not budget.fits:
        recommendations.extend([
            "Уменьшите context size или число slots",
            "Включите KV cache q8_0/q4_0 и auto-fit",
            "Отключите mmproj только для текстовой модели",
        ])
        return CompatibilityReport(
            False, "error", "Оценка памяти превышает доступный бюджет",
            budget, tuple(recommendations),
        )
    if budget.utilization > 0.85:
        recommendations.append("Оставлен небольшой запас памяти; избегайте параллельных прогонов")
        return CompatibilityReport(
            True, "warning", "Модель запускаема, но близка к memory budget",
            budget, tuple(recommendations),
        )
    return CompatibilityReport(True, "ok", "Модель совместима с текущим hardware budget", budget)
