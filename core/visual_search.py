from __future__ import annotations

import hashlib
import json
import math
import re
from pathlib import Path

from PIL import Image, ImageFilter, ImageOps, ImageStat

from backend.persistence.atomic import atomic_write_json

INDEX_VERSION = 2
PIPELINE_VERSION = "hybrid-layout-semantic-v3"
IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp"}
TOKEN_RE = re.compile(r"[\w-]{2,}", re.UNICODE)
MODE_ALIASES = {
    "overall": "Общий контекст",
    "pose_action": "Поза и действие",
    "composition": "Композиция и ракурс",
    "theme": "Тема и объекты",
}
POSE_ACTION = {"standing", "sitting", "lying", "walking", "running", "holding", "hand", "hands", "grabbing", "握", "стоит", "сидит", "лежит", "идет", "идёт", "держит", "держится", "руль", "ручка", "поза", "pose"}
COMPOSITION = {"front", "side", "profile", "back", "close", "portrait", "full", "body", "wide", "low", "high", "angle", "вид", "сбоку", "спереди", "снизу", "сверху", "крупный", "план"}


def _normalize(values: list[float]) -> list[float]:
    norm = math.sqrt(sum(value * value for value in values)) or 1.0
    return [round(value / norm, 7) for value in values]


def _visual_features(path: Path) -> list[float]:
    """Layout-oriented descriptor: spatial luminance, color histograms and edges."""
    with Image.open(path) as source:
        image = ImageOps.exif_transpose(source).convert("RGB")
        layout = image.resize((8, 8), Image.Resampling.LANCZOS)
        values = [channel / 255.0 for pixel in layout.getdata() for channel in pixel]
        gray = image.convert("L").resize((16, 16), Image.Resampling.LANCZOS)
        edges = gray.filter(ImageFilter.FIND_EDGES).resize((8, 8), Image.Resampling.BILINEAR)
        values.extend(value / 255.0 for value in edges.getdata())
        for channel in image.resize((128, 128), Image.Resampling.BILINEAR).split():
            histogram = channel.histogram()
            values.extend(sum(histogram[offset:offset + 16]) / 16384.0 for offset in range(0, 256, 16))
        stats = ImageStat.Stat(image.resize((64, 64), Image.Resampling.BILINEAR))
        values.extend(value / 255.0 for value in stats.mean + stats.stddev)
        values.extend([min(3.0, image.width / max(1, image.height)) / 3.0,
                       min(3.0, image.height / max(1, image.width)) / 3.0])
    return _normalize(values)


def _semantic_features(caption: str, dimensions: int = 256) -> list[float]:
    values = [0.0] * dimensions
    for token in TOKEN_RE.findall(caption.casefold().replace("_", " ")):
        digest = hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest()
        bucket = int.from_bytes(digest[:4], "little") % dimensions
        values[bucket] += 1.0 if digest[4] & 1 else -1.0
    return _normalize(values) if any(values) else values


def _tokens(caption: str) -> set[str]:
    return set(TOKEN_RE.findall(caption.casefold().replace("_", " ")))


def _cosine(left: list[float], right: list[float]) -> float:
    return sum(a * b for a, b in zip(left, right))


class VisualSearchIndex:
    def __init__(self, dataset_root: str | Path, include_subfolders: bool = True, embedder=None):
        self.root = Path(dataset_root).resolve()
        self.include_subfolders = include_subfolders
        self.embedder = embedder
        self.path = self.root / ".tagmanager" / "visual-search" / "index-v2.json"

    def _images(self) -> list[Path]:
        paths = self.root.rglob("*") if self.include_subfolders else self.root.iterdir()
        return sorted(path for path in paths if path.is_file() and path.suffix.lower() in IMAGE_SUFFIXES)

    def _load(self) -> dict:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if data.get("version") == INDEX_VERSION and data.get("pipeline") == PIPELINE_VERSION:
                return data
        except (OSError, ValueError, TypeError):
            pass
        return {"version": INDEX_VERSION, "pipeline": PIPELINE_VERSION, "items": {}}

    def build(self, force: bool = False) -> dict:
        old = self._load().get("items", {})
        items: dict[str, dict] = {}
        updated = cached = failed = 0
        for image in self._images():
            relative = image.relative_to(self.root).as_posix()
            caption_path = image.with_suffix(".txt")
            image_stat = image.stat()
            caption_mtime = caption_path.stat().st_mtime_ns if caption_path.is_file() else 0
            signature = [image_stat.st_size, image_stat.st_mtime_ns, caption_mtime]
            prior = old.get(relative)
            if not force and prior and prior.get("signature") == signature:
                items[relative] = prior
                cached += 1
                continue
            try:
                caption = caption_path.read_text(encoding="utf-8").strip() if caption_path.is_file() else ""
                item = {"signature": signature, "visual": _visual_features(image),
                        "semantic": _semantic_features(caption), "caption": caption}
                if self.embedder:
                    item["embedding"] = self.embedder(image)
                items[relative] = item
                updated += 1
            except (OSError, ValueError, UnicodeDecodeError):
                failed += 1
        payload = {"version": INDEX_VERSION, "pipeline": PIPELINE_VERSION, "items": items}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_json(self.path, payload)
        return {"total": len(items), "updated": updated, "cached": cached, "failed": failed,
                "version": INDEX_VERSION, "pipeline": PIPELINE_VERSION}

    def search(self, references: list[str], limit: int = 100, threshold: float = 0.0,
               mode: str = "overall", query: str = "") -> dict:
        status = self.build()
        data = self._load()["items"]
        if any(path not in data for path in references):
            raise FileNotFoundError(next(path for path in references if path not in data))
        reference_items = [data[path] for path in references]
        query_vector = _semantic_features(query) if query.strip() else None
        results = []
        for path, item in data.items():
            if path in references:
                continue
            matches = []
            for reference in reference_items:
                visual = max(0.0, _cosine(item["visual"], reference["visual"]))
                embedding_available = bool(item.get("embedding")) and bool(reference.get("embedding"))
                embedding = max(0.0, _cosine(item["embedding"], reference["embedding"])) if embedding_available else 0.0
                semantic_available = any(item["semantic"]) and any(reference["semantic"])
                semantic = max(0.0, _cosine(item["semantic"], reference["semantic"])) if semantic_available else 0.0
                query_semantic = max(0.0, _cosine(item["semantic"], query_vector)) if query_vector and any(item["semantic"]) else 0.0
                if mode == "pose_action":
                    score = (0.18 * visual + 0.35 * semantic + 0.47 * embedding) if embedding_available else (0.30 * visual + 0.45 * semantic + 0.25 * query_semantic)
                elif mode == "composition":
                    score = (0.30 * visual + 0.70 * embedding) if embedding_available else 0.78 * visual + 0.22 * semantic
                elif mode == "theme":
                    score = (0.18 * visual + 0.42 * semantic + 0.40 * embedding) if embedding_available else 0.25 * visual + 0.55 * semantic + 0.20 * query_semantic
                else:
                    score = (0.20 * visual + 0.30 * semantic + 0.50 * embedding) if embedding_available else ((0.58 * visual + 0.42 * semantic) if semantic_available else visual)
                matches.append((score, visual, semantic, semantic_available))
            matches.sort(reverse=True)
            best = matches[0]
            score = best[0] if len(matches) == 1 else 0.7 * best[0] + 0.3 * sum(value[0] for value in matches) / len(matches)
            if score < threshold:
                continue
            reasons = []
            if mode == "pose_action" and best[2] >= 0.12: reasons.append("позе и действию")
            elif mode == "composition" and best[1] >= 0.75: reasons.append("композиции и ракурсу")
            elif mode == "theme" and best[2] >= 0.12: reasons.append("теме и объектам")
            elif best[2] >= 0.18: reasons.append("сюжету и тегам")
            if best[1] >= 0.92 and "композиции и ракурсу" not in reasons: reasons.append("визуальной структуре")
            if not reasons: reasons.append("общей визуальной структуре")
            results.append({"path": path, "name": Path(path).name, "caption": item["caption"],
                            "has_caption": bool(item["caption"]), "score": round(score, 4),
                            "visual_score": round(best[1], 4), "semantic_score": round(best[2], 4),
                            "reason": "Похоже по " + " и ".join(reasons)})
        results.sort(key=lambda value: (-value["score"], value["path"]))
        return {"references": references, "items": results[:limit], "total": len(results),
                "mode": mode if mode in MODE_ALIASES else "overall", "mode_label": MODE_ALIASES.get(mode, MODE_ALIASES["overall"]),
                "query": query, "index": status}
