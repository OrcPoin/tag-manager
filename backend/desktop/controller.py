from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Callable, Protocol


@dataclass(frozen=True)
class ActiveRun:
    run_id: str
    status: str
    done: int
    total: int
    errors: int = 0
    review_count: int = 0


class ServiceClient(Protocol):
    def active_runs(self) -> list[ActiveRun]: ...
    def command(self, run_id: str, action: str) -> dict: ...
    def run(self, run_id: str) -> dict: ...


class DesktopServiceClient:
    def __init__(self, base_url: str, timeout: float = 1.5):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def _request(self, path: str, method: str = "GET"):
        request = urllib.request.Request(self.base_url + path, method=method,
                                         headers={"Idempotency-Key": "desktop-" + path.strip("/").replace("/", "-")})
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            return json.loads(response.read().decode("utf-8"))

    def active_runs(self) -> list[ActiveRun]:
        try:
            rows = self._request("/api/runs/active")
        except (OSError, urllib.error.URLError, ValueError):
            return []
        return [ActiveRun(str(row["run_id"]), str(row["status"]),
                          int(row.get("progress", {}).get("done", 0)),
                          int(row.get("progress", {}).get("total", 0)),
                          int(row.get("progress", {}).get("errors", 0)),
                          int(row.get("progress", {}).get("review_count", 0))) for row in rows]

    def command(self, run_id: str, action: str) -> dict:
        return self._request(f"/api/runs/{run_id}/{action}", "POST")

    def run(self, run_id: str) -> dict:
        return self._request(f"/api/runs/{run_id}")


class Window(Protocol):
    def show(self) -> None: ...
    def restore(self) -> None: ...
    def hide(self) -> None: ...
    def destroy(self) -> None: ...


class DesktopController:
    def __init__(self, window: Window, client: ServiceClient, *, keep_background: Callable[[], bool] = lambda: False,
                 stop_service: Callable[[], None] = lambda: None, confirm_exit: Callable[[], bool] = lambda: True,
                 background_available: Callable[[], bool] = lambda: True):
        self.window, self.client = window, client
        self.keep_background = keep_background
        self.stop_service = stop_service
        self.confirm_exit = confirm_exit
        self.background_available = background_available
        self.background = False
        self._exit_lock = threading.Lock()

    def active(self) -> list[ActiveRun]:
        return self.client.active_runs()

    def close_requested(self) -> bool:
        """Handle the native close event; True permits destruction."""
        if self.background_available() and (self.active() or self.keep_background()):
            self.background = True
            self.window.hide()
            return False
        if not self.confirm_exit():
            return False
        self.stop_service()
        return True

    def open_window(self) -> None:
        self.background = False
        restore = getattr(self.window, "restore", None)
        if callable(restore):
            restore()
        self.window.show()

    def toggle_pause(self) -> bool:
        runs = self.active()
        if not runs:
            return False
        run = runs[0]
        action = "resume" if run.status == "paused" else "pause"
        self.client.command(run.run_id, action)
        return True

    def safe_stop(self) -> bool:
        runs = self.active()
        if not runs:
            return False
        self.client.command(runs[0].run_id, "stop")
        return True

    def progress_label(self) -> str:
        runs = self.active()
        if not runs:
            return "No active processing"
        run = runs[0]
        return f"Current processing: {run.done} / {run.total} ({run.status})"

    def full_exit(self) -> bool:
        with self._exit_lock:
            if not self.confirm_exit():
                return False
            self.stop_service()
            self.window.destroy()
            return True
