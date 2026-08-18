from __future__ import annotations

import hashlib
import os
from pathlib import Path

from backend.domain import ProjectScan


IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}
IGNORED_DIRECTORIES = {".tagmanager", ".thumbs", "_rejected"}


def scan_dataset(dataset_path: str | Path, recursive: bool = True) -> ProjectScan:
    root = Path(dataset_path).resolve()
    if not root.is_dir():
        raise NotADirectoryError(root)

    images = captions = root_images = nested_images = unsupported = 0
    signature = hashlib.sha256()
    for current, directories, files in os.walk(root):
        directories[:] = sorted(name for name in directories if name not in IGNORED_DIRECTORIES)
        current_path = Path(current)
        for name in sorted(files):
            image = current_path / name
            if image.suffix.lower() not in IMAGE_EXTENSIONS:
                if image.suffix.lower() not in {".txt", ".json", ".jsonl"} and not name.startswith("."):
                    unsupported += 1
                continue
            nested = current_path != root
            if nested:
                nested_images += 1
            else:
                root_images += 1
            if nested and not recursive:
                continue
            images += 1
            caption = image.with_suffix(".txt")
            if caption.is_file():
                captions += 1
            stat = image.stat()
            relative = image.relative_to(root).as_posix()
            signature.update(f"{relative}\0{stat.st_size}\0{stat.st_mtime_ns}\n".encode("utf-8"))

    return ProjectScan(
        images=images,
        root_images=root_images,
        nested_images=nested_images,
        captions=captions,
        missing_captions=images - captions,
        unsupported=unsupported,
        signature=signature.hexdigest(),
    )
