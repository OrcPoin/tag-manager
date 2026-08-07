"""Small dependency-free GGUF metadata reader used before loading a model."""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
import os
import re
import struct


_FIXED_TYPES = {
    0: 1, 1: 1, 2: 2, 3: 2, 4: 4, 5: 4, 6: 4, 7: 1,
    10: 8, 11: 8, 12: 8,
}


@dataclass(frozen=True)
class GGUFMetadata:
    path: str
    size_bytes: int
    version: int
    architecture: str = ""
    name: str = ""
    file_type: int | None = None
    block_count: int | None = None
    context_length: int | None = None
    embedding_length: int | None = None
    expert_count: int | None = None
    expert_used_count: int | None = None

    @property
    def is_moe(self) -> bool:
        return bool(self.expert_count and self.expert_count > 1)

    @property
    def quantization(self) -> str:
        """Best-effort quant label for UI; filename fallback covers older GGUF metadata."""
        if self.file_type is not None:
            labels = {
                0: "F32", 1: "F16", 2: "Q4_0", 3: "Q4_1", 6: "Q5_0",
                7: "Q5_1", 8: "Q8_0", 15: "Q2_K", 16: "Q3_K_S",
                17: "Q3_K_M", 18: "Q3_K_L", 19: "Q4_K_S", 20: "Q4_K_M",
                21: "Q5_K_S", 22: "Q5_K_M", 23: "Q6_K",
            }
            if self.file_type in labels:
                return labels[self.file_type]
        match = re.search(r"(IQ\d+_[A-Z]|Q\d+_[A-Z0-9_]+|F16|F32)", os.path.basename(self.path).upper())
        return match.group(1) if match else "unknown"


def _read_exact(stream, size: int) -> bytes:
    data = stream.read(size)
    if len(data) != size:
        raise ValueError("Неожиданный конец GGUF metadata")
    return data


def _u32(stream) -> int:
    return struct.unpack("<I", _read_exact(stream, 4))[0]


def _u64(stream) -> int:
    return struct.unpack("<Q", _read_exact(stream, 8))[0]


def _string(stream) -> str:
    size = _u64(stream)
    if size > 16 * 1024 * 1024:
        raise ValueError("Некорректная длина GGUF string")
    return _read_exact(stream, size).decode("utf-8", errors="replace")


def _scalar(stream, value_type: int):
    formats = {
        0: "<B", 1: "<b", 2: "<H", 3: "<h", 4: "<I", 5: "<i",
        6: "<f", 7: "<?", 10: "<Q", 11: "<q", 12: "<d",
    }
    if value_type == 8:
        return _string(stream)
    fmt = formats.get(value_type)
    if fmt is None:
        raise ValueError(f"Неизвестный GGUF value type: {value_type}")
    return struct.unpack(fmt, _read_exact(stream, struct.calcsize(fmt)))[0]


def _skip_value(stream, value_type: int) -> None:
    if value_type in _FIXED_TYPES:
        stream.seek(_FIXED_TYPES[value_type], os.SEEK_CUR)
    elif value_type == 8:
        stream.seek(_u64(stream), os.SEEK_CUR)
    elif value_type == 9:
        element_type = _u32(stream)
        count = _u64(stream)
        fixed = _FIXED_TYPES.get(element_type)
        if fixed is not None:
            stream.seek(fixed * count, os.SEEK_CUR)
        elif element_type == 8:
            for _ in range(count):
                stream.seek(_u64(stream), os.SEEK_CUR)
        else:
            raise ValueError(f"Неподдерживаемый GGUF array type: {element_type}")
    else:
        raise ValueError(f"Неизвестный GGUF value type: {value_type}")


@lru_cache(maxsize=32)
def _read_cached(path: str, size: int, mtime_ns: int) -> GGUFMetadata:
    del mtime_ns
    values: dict[str, object] = {}
    with open(path, "rb") as stream:
        if _read_exact(stream, 4) != b"GGUF":
            raise ValueError("Файл не является GGUF")
        version = _u32(stream)
        _u64(stream)  # tensor count
        metadata_count = _u64(stream)
        for _ in range(metadata_count):
            key = _string(stream)
            value_type = _u32(stream)
            if key.startswith("tokenizer."):
                break
            wanted = (
                key in {"general.architecture", "general.name", "general.file_type"}
                or key.endswith((
                    ".block_count", ".context_length", ".embedding_length",
                    ".expert_count", ".expert_used_count",
                ))
            )
            if wanted and value_type != 9:
                values[key] = _scalar(stream, value_type)
            else:
                _skip_value(stream, value_type)

    architecture = str(values.get("general.architecture", ""))
    prefix = architecture + "." if architecture else ""
    return GGUFMetadata(
        path=path,
        size_bytes=size,
        version=version,
        architecture=architecture,
        name=str(values.get("general.name", "")),
        file_type=_optional_int(values.get("general.file_type")),
        block_count=_optional_int(values.get(prefix + "block_count")),
        context_length=_optional_int(values.get(prefix + "context_length")),
        embedding_length=_optional_int(values.get(prefix + "embedding_length")),
        expert_count=_optional_int(values.get(prefix + "expert_count")),
        expert_used_count=_optional_int(values.get(prefix + "expert_used_count")),
    )


def _optional_int(value) -> int | None:
    return int(value) if isinstance(value, (int, float)) else None


def read_gguf_metadata(path: str) -> GGUFMetadata:
    stat = os.stat(path)
    return _read_cached(os.path.abspath(path), stat.st_size, stat.st_mtime_ns)
