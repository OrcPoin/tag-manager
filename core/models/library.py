"""Filesystem model library with deterministic GGUF/mmproj association."""

from __future__ import annotations

from dataclasses import dataclass, field
import os

from core.models.gguf import GGUFMetadata, read_gguf_metadata


@dataclass(frozen=True)
class ModelEntry:
    model_id: str
    path: str
    metadata: GGUFMetadata
    mmproj_path: str = ""
    mtp_paths: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()

    @property
    def multimodal(self) -> bool:
        return bool(self.mmproj_path)


@dataclass(frozen=True)
class ModelLibrary:
    root: str
    entries: tuple[ModelEntry, ...] = ()
    errors: tuple[str, ...] = ()

    def by_id(self, model_id: str) -> ModelEntry | None:
        return next((entry for entry in self.entries if entry.model_id == model_id), None)


def scan_model_library(
    root: str,
    *,
    mmproj_roots: tuple[str, ...] = (),
    recursive: bool = True,
) -> ModelLibrary:
    root = os.path.abspath(root)
    paths = _gguf_paths(root, recursive)
    projector_paths = _gguf_paths(root, recursive, projector_only=True)
    for projector_root in mmproj_roots:
        projector_paths.extend(_gguf_paths(projector_root, True, projector_only=True))
    projector_paths = sorted(set(projector_paths))
    entries: list[ModelEntry] = []
    errors: list[str] = []
    for path in paths:
        if _is_projector(path):
            continue
        try:
            metadata = read_gguf_metadata(path)
            mmproj = _associate_projector(path, projector_paths)
            mtp = tuple(
                candidate for candidate in _gguf_paths(os.path.dirname(path), False)
                if candidate != path and "mtp" in os.path.basename(candidate).lower()
            )
            warnings = []
            if metadata.is_moe and metadata.expert_used_count is None:
                warnings.append("MoE model без metadata active expert count")
            if not mmproj:
                warnings.append("mmproj не найден: vision input может быть недоступен")
            entries.append(ModelEntry(
                model_id=os.path.basename(path), path=path, metadata=metadata,
                mmproj_path=mmproj, mtp_paths=mtp, warnings=tuple(warnings),
            ))
        except (OSError, ValueError) as exc:
            errors.append(f"{path}: {exc}")
    return ModelLibrary(root, tuple(entries), tuple(errors))


def _gguf_paths(root: str, recursive: bool, projector_only: bool = False) -> list[str]:
    if not root or not os.path.isdir(root):
        return []
    result: list[str] = []
    iterator = os.walk(root) if recursive else [(root, [], os.listdir(root))]
    for folder, _, files in iterator:
        for name in files:
            if not name.lower().endswith(".gguf"):
                continue
            path = os.path.join(folder, name)
            if projector_only and not _is_projector(path):
                continue
            result.append(os.path.abspath(path))
    return result


def _is_projector(path: str) -> bool:
    name = os.path.basename(path).lower()
    return name.startswith("mmproj") or "mmproj" in name


def _associate_projector(model_path: str, projector_paths: list[str]) -> str:
    model_dir = os.path.dirname(model_path)
    model_stem = os.path.basename(model_path).lower()
    same_dir = [path for path in projector_paths if os.path.dirname(path) == model_dir]
    candidates = same_dir or projector_paths
    if not candidates:
        return ""
    # Prefer projector sharing a model stem, then deterministic first item.
    stem_tokens = [token for token in model_stem.replace("_", "-").split("-") if len(token) > 3]
    scored = sorted(
        candidates,
        key=lambda path: (
            -sum(token in os.path.basename(path).lower() for token in stem_tokens),
            os.path.basename(path).lower(),
        ),
    )
    return scored[0]
