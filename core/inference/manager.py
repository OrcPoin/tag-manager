"""Managed backend selection and llama.cpp version storage."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
import threading

from core.inference.external_api import ExternalApiBackend
from core.inference.llama_server import LlamaServerConfig, LlamaServerProcess


class LlamaCppBackend(ExternalApiBackend):
    backend_name = "managed_llama_cpp"

    def __init__(self, process: LlamaServerProcess, api_key: str = "not-needed",
                 timeout: float = 600.0):
        self.process_manager = process
        super().__init__(
            process.config.base_url,
            api_key,
            os.path.basename(process.config.model),
            timeout,
        )

    def start(self, *, should_stop=None):
        return self.process_manager.start(should_stop=should_stop)

    def stop(self) -> bool:
        return self.process_manager.stop()

    def restart(self):
        return self.process_manager.restart()

    def health(self):
        managed = self.process_manager.health()
        if not managed.ready:
            return managed
        return super().health()


class BackendManager:
    """Own the single managed process used by the application."""

    def __init__(self):
        self._lock = threading.RLock()
        self._managed: LlamaCppBackend | None = None
        self._stop_timer: threading.Timer | None = None

    def managed(self, config: LlamaServerConfig, *, timeout: float) -> LlamaCppBackend:
        with self._lock:
            if self._stop_timer is not None:
                self._stop_timer.cancel()
                self._stop_timer = None
            if self._managed and self._managed.process_manager.config != config:
                if self._managed.process_manager.running:
                    # Keep ownership of the live process. UI edits become effective
                    # after Stop, never orphaning a server because its config changed.
                    return self._managed
                self._managed = None
            if self._managed is None:
                self._managed = LlamaCppBackend(
                    LlamaServerProcess(config), timeout=timeout
                )
            return self._managed

    def external(self, *, base_url: str, api_key: str, model: str, timeout: float):
        return ExternalApiBackend(base_url, api_key, model, timeout)

    def stop_managed(self) -> bool:
        with self._lock:
            if self._stop_timer is not None:
                self._stop_timer.cancel()
                self._stop_timer = None
            return not self._managed or self._managed.stop()

    def stop_managed_after(self, seconds: float) -> bool:
        """Keep the owned server warm briefly, without giving up lifecycle ownership."""
        with self._lock:
            if self._managed is None or not self._managed.process_manager.running:
                return False
            if self._stop_timer is not None:
                self._stop_timer.cancel()
            self._stop_timer = threading.Timer(max(0.0, seconds), self.stop_managed)
            self._stop_timer.daemon = True
            self._stop_timer.start()
            return True


class LlamaVersionStore:
    """Atomic, checksummed storage for side-by-side llama.cpp builds."""

    def __init__(self, root: str):
        self.root = os.path.abspath(root)
        self.versions_dir = os.path.join(self.root, "versions")
        self.current_file = os.path.join(self.root, "current")
        self.history_file = os.path.join(self.root, "history.json")
        os.makedirs(self.versions_dir, exist_ok=True)

    def install(self, version_id: str, source_dir: str) -> str:
        version_id = version_id.strip()
        if not version_id or version_id in {".", ".."} or os.path.basename(version_id) != version_id:
            raise ValueError("Некорректный идентификатор версии")
        target = os.path.join(self.versions_dir, version_id)
        if os.path.exists(target):
            raise FileExistsError(
                "Версия уже существует; активные и установленные версии не перезаписываются"
            )
        temp_dir = tempfile.mkdtemp(prefix=f".{version_id}-", dir=self.versions_dir)
        try:
            payload = os.path.join(temp_dir, "payload")
            shutil.copytree(source_dir, payload)
            checksums = self._checksums(payload)
            with open(os.path.join(temp_dir, "manifest.json"), "w", encoding="utf-8") as stream:
                json.dump({"version": version_id, "sha256": checksums}, stream,
                          ensure_ascii=False, indent=2)
            os.replace(temp_dir, target)
        except Exception:
            shutil.rmtree(temp_dir, ignore_errors=True)
            raise
        return target

    def activate(self, version_id: str) -> str:
        target = os.path.join(self.versions_dir, version_id)
        if not os.path.isdir(target):
            raise FileNotFoundError(f"Версия llama.cpp не установлена: {version_id}")
        previous = self.current()
        if previous and previous != version_id:
            history = self._history()
            history.append(previous)
            self._atomic_json(self.history_file, history[-20:])
        self._atomic_text(self.current_file, version_id)
        return target

    def current(self) -> str | None:
        try:
            with open(self.current_file, encoding="utf-8") as stream:
                value = stream.read().strip()
            return value or None
        except OSError:
            return None

    def rollback(self) -> str:
        history = self._history()
        if not history:
            raise RuntimeError("Нет предыдущей версии для rollback")
        version_id = history.pop()
        self._atomic_json(self.history_file, history)
        self._atomic_text(self.current_file, version_id)
        return version_id

    def verify(self, version_id: str) -> bool:
        folder = os.path.join(self.versions_dir, version_id)
        try:
            with open(os.path.join(folder, "manifest.json"), encoding="utf-8") as stream:
                expected = json.load(stream)["sha256"]
        except (OSError, KeyError, ValueError):
            return False
        return expected == self._checksums(os.path.join(folder, "payload"))

    @staticmethod
    def _checksums(folder: str) -> dict[str, str]:
        result = {}
        for root, dirs, files in os.walk(folder):
            dirs.sort()
            for name in sorted(files):
                path = os.path.join(root, name)
                digest = hashlib.sha256()
                with open(path, "rb") as stream:
                    for block in iter(lambda: stream.read(1024 * 1024), b""):
                        digest.update(block)
                result[os.path.relpath(path, folder).replace(os.sep, "/")] = digest.hexdigest()
        return result

    def _history(self) -> list[str]:
        try:
            with open(self.history_file, encoding="utf-8") as stream:
                data = json.load(stream)
            return [str(item) for item in data] if isinstance(data, list) else []
        except (OSError, ValueError):
            return []

    @staticmethod
    def _atomic_text(path: str, value: str) -> None:
        folder = os.path.dirname(path)
        fd, temp_path = tempfile.mkstemp(prefix=".tmp-", dir=folder, text=True)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                stream.write(value + "\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temp_path, path)
        finally:
            if os.path.exists(temp_path):
                os.unlink(temp_path)

    @classmethod
    def _atomic_json(cls, path: str, value) -> None:
        folder = os.path.dirname(path)
        fd, temp_path = tempfile.mkstemp(prefix=".tmp-", dir=folder, text=True)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump(value, stream, ensure_ascii=False, indent=2)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temp_path, path)
        finally:
            if os.path.exists(temp_path):
                os.unlink(temp_path)
