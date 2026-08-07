"""Lifecycle management for a local llama.cpp ``llama-server`` process."""

from __future__ import annotations

from dataclasses import dataclass
import atexit
import os
import socket
import subprocess
import threading
import time
from typing import Callable

import httpx

from core.inference.errors import (
    BackendOutOfMemoryError,
    BackendStartupTimeout,
    ExecutableNotFoundError,
    MmprojNotFoundError,
    ModelNotFoundError,
    PortUnavailableError,
)
from core.inference.interfaces import BackendHealth, BackendStatus


@dataclass(frozen=True)
class LlamaServerConfig:
    executable: str
    model: str
    mmproj: str = ""
    host: str = "127.0.0.1"
    port: int = 8080
    api_prefix: str = "/v1"
    startup_timeout: float = 180.0
    extra_args: tuple[str, ...] = ()
    log_path: str = "llama-server.log"

    @property
    def base_url(self) -> str:
        prefix = "/" + self.api_prefix.strip("/") if self.api_prefix else ""
        return f"http://{self.host}:{self.port}{prefix}"


def _port_is_available(host: str, port: int) -> bool:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind((host, port))
        return True
    except OSError:
        return False


class LlamaServerProcess:
    def __init__(self, config: LlamaServerConfig):
        self.config = config
        self._process: subprocess.Popen | None = None
        self._log_stream = None
        self._lock = threading.RLock()
        self._status = BackendStatus.STOPPED
        self._message = "llama-server остановлен"
        atexit.register(self.stop)

    @property
    def process(self) -> subprocess.Popen | None:
        return self._process

    @property
    def running(self) -> bool:
        return self._process is not None and self._process.poll() is None

    def command(self) -> list[str]:
        command = [
            self.config.executable,
            "-m", self.config.model,
            "--host", self.config.host,
            "--port", str(self.config.port),
        ]
        if self.config.mmproj:
            command.extend(["--mmproj", self.config.mmproj])
        command.extend(self.config.extra_args)
        return command

    def validate(self) -> None:
        if not os.path.isfile(self.config.executable):
            raise ExecutableNotFoundError(
                f"llama-server не найден: {self.config.executable}"
            )
        if not os.path.isfile(self.config.model):
            raise ModelNotFoundError(f"GGUF-модель не найдена: {self.config.model}")
        if self.config.mmproj and not os.path.isfile(self.config.mmproj):
            raise MmprojNotFoundError(f"mmproj не найден: {self.config.mmproj}")
        if not 1 <= self.config.port <= 65535:
            raise PortUnavailableError(f"Некорректный порт: {self.config.port}")

    def start(
        self,
        *,
        wait: bool = True,
        should_stop: Callable[[], bool] | None = None,
    ) -> BackendHealth:
        with self._lock:
            if self.running:
                return self.health()
            self.validate()
            if not _port_is_available(self.config.host, self.config.port):
                raise PortUnavailableError(
                    f"Порт {self.config.host}:{self.config.port} уже занят"
                )
            log_dir = os.path.dirname(os.path.abspath(self.config.log_path))
            os.makedirs(log_dir, exist_ok=True)
            self._log_stream = open(self.config.log_path, "a", encoding="utf-8")
            creationflags = 0
            if os.name == "nt":
                creationflags = (
                    getattr(subprocess, "CREATE_NO_WINDOW", 0)
                    | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
                )
            self._status = BackendStatus.STARTING
            self._message = "llama-server запускается"
            try:
                self._process = subprocess.Popen(
                    self.command(),
                    stdin=subprocess.DEVNULL,
                    stdout=self._log_stream,
                    stderr=subprocess.STDOUT,
                    creationflags=creationflags,
                )
            except Exception:
                self._close_log()
                self._status = BackendStatus.ERROR
                raise

        return self.wait_until_ready(should_stop=should_stop) if wait else self.health()

    def wait_until_ready(
        self, *, should_stop: Callable[[], bool] | None = None
    ) -> BackendHealth:
        deadline = time.monotonic() + self.config.startup_timeout
        last_error = ""
        while time.monotonic() < deadline:
            if should_stop and should_stop():
                self.stop()
                raise BackendStartupTimeout("Запуск llama-server отменён")
            process = self._process
            if process is None or process.poll() is not None:
                code = process.returncode if process else "?"
                tail = self._read_log_tail()
                self._status = BackendStatus.ERROR
                if "out of memory" in tail.lower() or "cuda error" in tail.lower():
                    raise BackendOutOfMemoryError(
                        "llama-server завершился из-за нехватки памяти. "
                        "Уменьшите GPU layers/context/batch."
                    )
                raise RuntimeError(
                    f"llama-server завершился при запуске (код {code}). {tail}"
                )
            try:
                response = httpx.get(
                    f"{self.config.base_url}/models", timeout=2.0
                )
                response.raise_for_status()
                self._status = BackendStatus.READY
                self._message = "llama-server готов"
                return self.health()
            except Exception as exc:  # noqa: BLE001
                last_error = str(exc)
                time.sleep(0.25)
        self.stop()
        self._status = BackendStatus.ERROR
        raise BackendStartupTimeout(
            f"llama-server не стал готов за {self.config.startup_timeout:g} сек. "
            f"Последняя ошибка: {last_error}"
        )

    def stop(self, graceful_timeout: float = 5.0) -> bool:
        with self._lock:
            process = self._process
            if process is None:
                self._status = BackendStatus.STOPPED
                self._close_log()
                return True
            self._status = BackendStatus.STOPPING
            if process.poll() is None:
                try:
                    process.terminate()
                    process.wait(timeout=graceful_timeout)
                except subprocess.TimeoutExpired:
                    self._kill_tree(process.pid)
                    try:
                        process.wait(timeout=5.0)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=5.0)
                except OSError:
                    pass
            self._process = None
            self._close_log()
            self._status = BackendStatus.STOPPED
            self._message = "llama-server остановлен"
            return True

    def restart(self, *, should_stop=None) -> BackendHealth:
        self.stop()
        return self.start(should_stop=should_stop)

    def health(self) -> BackendHealth:
        if self._process is not None and self._process.poll() is not None:
            self._status = BackendStatus.ERROR
            self._message = f"llama-server завершился (код {self._process.returncode})"
        return BackendHealth(
            self._status,
            self._message,
            model=os.path.basename(self.config.model) or None,
            details={
                "pid": self._process.pid if self.running else None,
                "base_url": self.config.base_url,
                "executable": self.config.executable,
                "model_path": self.config.model,
                "mmproj": self.config.mmproj or None,
                "extra_args": self.config.extra_args,
            },
        )

    def _kill_tree(self, pid: int) -> None:
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/PID", str(pid), "/T", "/F"],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        elif self._process is not None:
            self._process.kill()

    def _read_log_tail(self, limit: int = 2000) -> str:
        try:
            if self._log_stream:
                self._log_stream.flush()
            with open(self.config.log_path, encoding="utf-8", errors="replace") as stream:
                return stream.read()[-limit:].strip()
        except OSError:
            return ""

    def _close_log(self) -> None:
        if self._log_stream is not None:
            try:
                self._log_stream.close()
            finally:
                self._log_stream = None
