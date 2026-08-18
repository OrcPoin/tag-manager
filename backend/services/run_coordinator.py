from __future__ import annotations

import threading
from typing import Callable, Protocol

from backend.domain import RunStatus

from .run_service import RunService


class RunExecutor(Protocol):
    def __call__(self, run_id: str, progress: Callable[[int, int, str | None], None],
                 should_pause: Callable[[], bool], should_stop: Callable[[], bool]) -> dict: ...


class RunExecutorUnavailable(RuntimeError):
    pass


class RunCoordinator:
    def __init__(self, runs: RunService, executor: RunExecutor | None = None):
        self.runs = runs
        self.executor = executor
        self._threads: dict[str, threading.Thread] = {}
        self._lock = threading.Lock()

    def start(self, run_id: str, idempotency_key: str):
        if self.executor is None:
            raise RunExecutorUnavailable("Inference executor не подключён")
        with self._lock:
            active_ids = [active_id for active_id, thread in self._threads.items() if thread.is_alive()]
            if active_ids and run_id not in active_ids:
                raise RunExecutorUnavailable(
                    "Другой запуск уже использует inference backend. Дождитесь его завершения или остановите его безопасно."
                )
        run = self.runs.execute_command(run_id, "start", idempotency_key)
        with self._lock:
            thread = self._threads.get(run_id)
            if thread is None or not thread.is_alive():
                thread = threading.Thread(target=self._execute, args=(run_id,), daemon=True, name=f"tag-manager-{run_id[:8]}")
                self._threads[run_id] = thread
                thread.start()
        return run

    def _execute(self, run_id: str) -> None:
        assert self.executor is not None
        try:
            summary = self.executor(
                run_id,
                lambda done, total, image=None: self.runs.record_progress(run_id, done, total, image),
                lambda: self.runs.get(run_id).status is RunStatus.PAUSED,
                lambda: self.runs.get(run_id).status is RunStatus.STOP_REQUESTED,
            )
            self.runs.complete(run_id, summary)
        except Exception as error:
            try:
                self.runs.fail(run_id, str(error))
            except Exception:
                # The project may have been externally removed during shutdown.
                # There is no remaining durable target for a secondary error.
                pass
        finally:
            with self._lock:
                self._threads.pop(run_id, None)

    def wait(self, run_id: str, timeout: float | None = None) -> bool:
        with self._lock:
            thread = self._threads.get(run_id)
        if thread is None:
            return True
        thread.join(timeout)
        return not thread.is_alive()

    def shutdown(self, timeout_per_run: float = 5.0) -> list[str]:
        with self._lock:
            run_ids = list(self._threads)
        unfinished = []
        for run_id in run_ids:
            try:
                run = self.runs.get(run_id)
                if run.status in {RunStatus.RUNNING, RunStatus.PAUSED}:
                    self.runs.stop(run_id)
            except Exception:
                pass
            if not self.wait(run_id, timeout_per_run):
                unfinished.append(run_id)
        return unfinished
