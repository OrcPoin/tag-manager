"""Фоновый preview одной картинки без записи результата в dataset."""

from __future__ import annotations

import threading
from typing import Any

from core.pipeline import PipelineMode, PipelineOrchestrator


class PreviewRunner:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._result: Any = None
        self._error = ""
        self._image = ""

    def start(self, image_path: str, params: dict, backend, tagger_manager=None) -> bool:
        if self.is_alive():
            return False
        taggers = []
        for tagger_id in params.get("pipeline_tagger_ids", []):
            try:
                if tagger_manager:
                    taggers.append(tagger_manager.create(tagger_id))
            except Exception:
                continue
        with self._lock:
            self._result = None
            self._error = ""
            self._image = image_path
        self._thread = threading.Thread(
            target=self._run, args=(image_path, params, backend, taggers), daemon=True
        )
        self._thread.start()
        return True

    def _run(self, image_path, params, backend, taggers) -> None:
        try:
            mode = PipelineMode(params.get("pipeline_mode", "description_only"))
            result = PipelineOrchestrator(backend, taggers).run(
                image_path,
                mode=mode,
                system_prompt=params.get("system_prompt", ""),
                user_prompt=params.get("user_prompt", ""),
                generation=params,
                tagger_options={"top_k": int(params.get("pipeline_top_k", 128))},
            )
            with self._lock:
                self._result = result
        except Exception as exc:
            with self._lock:
                self._error = str(exc)

    def is_alive(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def snapshot(self) -> dict:
        with self._lock:
            result = self._result
            return {
                "running": self.is_alive(),
                "image": self._image,
                "result": result,
                "error": self._error,
            }
