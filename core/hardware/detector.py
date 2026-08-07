"""Cross-platform, best-effort hardware inventory without heavy dependencies."""

from __future__ import annotations

from dataclasses import dataclass
import ctypes
import os
import subprocess


@dataclass(frozen=True)
class GPUInfo:
    name: str
    backend: str
    total_bytes: int
    free_bytes: int
    driver: str = ""


@dataclass(frozen=True)
class HardwareInfo:
    logical_cores: int
    physical_cores: int
    ram_total_bytes: int
    ram_available_bytes: int
    gpus: tuple[GPUInfo, ...] = ()

    @property
    def primary_gpu(self) -> GPUInfo | None:
        return max(self.gpus, key=lambda gpu: gpu.free_bytes, default=None)


class _MemoryStatus(ctypes.Structure):
    _fields_ = [
        ("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
        ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
        ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
        ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
        ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
    ]


def _memory() -> tuple[int, int]:
    if os.name == "nt":
        status = _MemoryStatus()
        status.dwLength = ctypes.sizeof(status)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
            return int(status.ullTotalPhys), int(status.ullAvailPhys)
    try:
        page = os.sysconf("SC_PAGE_SIZE")
        return page * os.sysconf("SC_PHYS_PAGES"), page * os.sysconf("SC_AVPHYS_PAGES")
    except (AttributeError, ValueError, OSError):
        return 0, 0


def _nvidia_gpus() -> tuple[GPUInfo, ...]:
    try:
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,memory.total,memory.free,driver_version",
             "--format=csv,noheader,nounits"],
            check=True, capture_output=True, text=True, timeout=10,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.SubprocessError):
        return ()
    gpus = []
    for line in result.stdout.splitlines():
        parts = [part.strip() for part in line.split(",")]
        if len(parts) >= 4:
            try:
                gpus.append(GPUInfo(
                    parts[0], "cuda", int(float(parts[1]) * (1 << 20)),
                    int(float(parts[2]) * (1 << 20)), parts[3],
                ))
            except ValueError:
                continue
    return tuple(gpus)


def detect_hardware() -> HardwareInfo:
    logical = os.cpu_count() or 1
    # Dependency-free approximation; benchmark tuner can refine it later.
    physical = max(1, logical // 2) if logical > 4 else logical
    total, available = _memory()
    return HardwareInfo(logical, physical, total, available, _nvidia_gpus())
