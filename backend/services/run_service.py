from __future__ import annotations

from pathlib import Path
from uuid import uuid4
import hashlib
import json

from backend.domain import Event, Run, RunStage, RunStatus
from backend.domain.common import utc_now
from backend.persistence import EventStore, ProjectRepository
from core.image_scanner import find_images

from .project_service import ProjectNotFoundError, ProjectService


class RunNotFoundError(LookupError):
    pass


class InvalidRunTransitionError(ValueError):
    pass


class RunService:
    def __init__(self, projects: ProjectService):
        self.projects = projects

    def create(self, project_id: str, scope_plan: dict, recipe_snapshot: dict, idempotency_key: str,
               model_snapshot: dict | None = None, effective_resource_configuration: dict | None = None) -> Run:
        project = self.projects.get_project(project_id)
        proposed_id = uuid4().hex
        run_id = self.projects.index.remember_command(idempotency_key, "run.create", proposed_id)
        repository = ProjectRepository(project.dataset_path)
        try:
            return repository.load_run(run_id)
        except FileNotFoundError:
            pass
        run = Run(run_id=run_id, project_id=project_id, scope_plan=scope_plan, recipe_snapshot=recipe_snapshot,
                  model_snapshot=model_snapshot or {},
                  effective_resource_configuration=effective_resource_configuration or {})
        run.summary["provenance"] = self._provenance(run)
        repository.save_run(run)
        if run_id not in project.run_refs:
            project.run_refs.append(run_id)
            self.projects.update_project(project_id, {"run_refs": project.run_refs})
        self._event(repository, run, "run.created", "План обработки сохранён", {"status": run.status.value})
        return run

    def remaining_plan(self, run_id: str, idempotency_key: str) -> Run:
        """Create a continuation run from durable snapshots, limited to missing captions."""
        source = self.get(run_id)
        if source.status not in {RunStatus.FAILED, RunStatus.STOPPED}:
            raise InvalidRunTransitionError("Продолжить можно только прерванный или остановленный запуск")
        project = self.projects.get_project(source.project_id)
        recursive = bool(project.settings.get("include_subfolders", False))
        root = Path(project.dataset_path)
        all_images = [Path(path) for path in find_images(str(root), recursive)]
        explicit = source.scope_plan.get("images")
        if isinstance(explicit, list):
            allowed = {str(value).replace("\\", "/") for value in explicit}
            original_images = [path for path in all_images if path.relative_to(root).as_posix() in allowed]
        else:
            # Older runs did not persist a manifest. Their durable progress index
            # is still the authoritative boundary, including when Tagger has
            # already created .txt files for every image before the VLM stage.
            original_total = max(0, int(source.progress.total or len(all_images)))
            original_images = all_images[:original_total]
        images = original_images[max(0, source.progress.done):]
        relative = [path.relative_to(root).as_posix() for path in images]
        scope = {"mode": "missing", "images": relative, "create": len(relative),
                 "overwrite": 0, "preserve": max(0, source.progress.done),
                 "total": len(relative), "include_subfolders": recursive}
        return self.create(source.project_id, scope, dict(source.recipe_snapshot), idempotency_key,
                           dict(source.model_snapshot), dict(source.effective_resource_configuration))

    def get(self, run_id: str) -> Run:
        for project in self.projects.list_projects():
            if run_id in project.run_refs:
                try:
                    return ProjectRepository(project.dataset_path).load_run(run_id)
                except OSError as error:
                    raise RunNotFoundError(run_id) from error
        raise RunNotFoundError(run_id)

    def list_project(self, project_id: str, *, status: str | None = None, recipe_hash: str | None = None) -> list[Run]:
        project = self.projects.get_project(project_id)
        repository = ProjectRepository(project.dataset_path)
        runs = []
        for run_id in project.run_refs:
            try:
                runs.append(repository.load_run(run_id))
            except OSError:
                continue
        if status:
            runs = [run for run in runs if run.status.value == status]
        if recipe_hash:
            runs = [run for run in runs if run.summary.get("provenance", {}).get("recipe_hash") == recipe_hash]
        return sorted(runs, key=lambda run: run.created_at, reverse=True)

    def active_runs(self) -> list[Run]:
        """Return durable work that should keep the desktop service alive."""
        active = []
        for project in self.projects.list_projects():
            repository = ProjectRepository(project.dataset_path)
            for run_id in project.run_refs:
                try:
                    run = repository.load_run(run_id)
                except OSError:
                    continue
                if run.status in {RunStatus.RUNNING, RunStatus.PAUSED, RunStatus.STOP_REQUESTED}:
                    active.append(run)
        return sorted(active, key=lambda run: run.created_at, reverse=True)

    def repeat_configuration(self, run_id: str, idempotency_key: str, scope_plan: dict | None = None) -> Run:
        source = self.get(run_id)
        repeated = self.create(source.project_id, dict(scope_plan or source.scope_plan), dict(source.recipe_snapshot),
                               idempotency_key, dict(source.model_snapshot), dict(source.effective_resource_configuration))
        repeated.summary["repeated_from_run_id"] = source.run_id
        self._save(repeated)
        return repeated

    def compare(self, left_run_id: str, right_run_id: str) -> dict:
        left, right = self.get(left_run_id), self.get(right_run_id)
        if left.project_id != right.project_id:
            raise ValueError("Runs from different projects cannot be compared")
        sections = {
            "recipe": self._mapping_diff(left.recipe_snapshot, right.recipe_snapshot),
            "model": self._mapping_diff(left.model_snapshot, right.model_snapshot),
            "runtime": self._mapping_diff(left.effective_resource_configuration, right.effective_resource_configuration),
            "result": self._mapping_diff(self._result_snapshot(left), self._result_snapshot(right)),
        }
        return {"left_run_id": left_run_id, "right_run_id": right_run_id,
                "identical_configuration": not any(sections[name] for name in ("recipe", "model", "runtime")),
                "sections": sections}

    @classmethod
    def _mapping_diff(cls, left: dict, right: dict, prefix: str = "") -> list[dict]:
        differences = []
        for key in sorted(set(left) | set(right)):
            path = f"{prefix}.{key}" if prefix else str(key)
            a, b = left.get(key), right.get(key)
            if isinstance(a, dict) and isinstance(b, dict):
                differences.extend(cls._mapping_diff(a, b, path))
            elif a != b:
                differences.append({"path": path, "left": a, "right": b})
        return differences

    @staticmethod
    def _result_snapshot(run: Run) -> dict:
        return {"status": run.status.value, "done": run.progress.done, "total": run.progress.total,
                "errors": run.progress.errors, "review_count": run.progress.review_count,
                "retries": run.progress.retries, "elapsed_seconds": run.inference_metrics.elapsed_seconds}

    def reconcile_interrupted(self) -> int:
        reconciled = 0
        for project in self.projects.list_projects():
            repository = ProjectRepository(project.dataset_path)
            for run_id in project.run_refs:
                try:
                    run = repository.load_run(run_id)
                except OSError:
                    continue
                if run.status in {RunStatus.RUNNING, RunStatus.PAUSED}:
                    run.status = RunStatus.PAUSED
                    run.pause_requested = True
                    run.summary["recovery_required"] = True
                    run.summary["recovery_reason"] = "service_restarted"
                    run.last_heartbeat = utc_now()
                    repository.save_run(run)
                    self._event(repository, run, "run.recovered", "Запуск восстановлен после перезапуска и поставлен на паузу", {"status": "paused"})
                    reconciled += 1
                elif run.status is RunStatus.STOP_REQUESTED:
                    run.status = RunStatus.STOPPED
                    run.finished_at = utc_now()
                    run.summary["recovery_reason"] = "stop_completed_after_restart"
                    repository.save_run(run)
                    self._event(repository, run, "run.stopped", "Безопасная остановка завершена после перезапуска", {"status": "stopped"})
                    reconciled += 1
        return reconciled

    def summary(self, run_id: str) -> dict:
        run = self.get(run_id)
        return {
            "run_id": run.run_id,
            "project_id": run.project_id,
            "status": run.status.value,
            "stage": run.stage.value,
            "created_at": run.created_at,
            "started_at": run.started_at,
            "finished_at": run.finished_at,
            "progress": {
                "done": run.progress.done,
                "total": run.progress.total,
                "errors": run.progress.errors,
                "review_count": run.progress.review_count,
            },
            "inference_metrics": {
                key: getattr(run.inference_metrics, key)
                for key in run.inference_metrics.__dataclass_fields__
            },
            **run.summary,
        }

    @staticmethod
    def _stable_hash(value: object) -> str:
        encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    @classmethod
    def _provenance(cls, run: Run) -> dict:
        recipe = run.recipe_snapshot
        model = run.model_snapshot
        prompts = {"system_prompt": recipe.get("system_prompt", ""), "user_prompt": recipe.get("user_prompt", "")}
        return {
            "recipe_hash": cls._stable_hash(recipe),
            "prompt_hash": cls._stable_hash(prompts),
            "model": {"backend_type": model.get("backend_type"),
                      "model": model.get("model_path") or model.get("model"),
                      "mmproj": model.get("mmproj_path")},
            "taggers": list(recipe.get("pipeline_tagger_ids", [])),
            "runtime_profile": run.effective_resource_configuration.get("profile"),
            "runtime": dict(run.effective_resource_configuration.get("runtime", {})),
        }

    def record_progress(self, run_id: str, done: int, total: int, current_image: str | None = None) -> Run:
        run = self.get(run_id)
        if run.status is not RunStatus.RUNNING:
            return run
        run.progress.done = max(0, min(done, total))
        run.progress.total = max(0, total)
        run.progress.current_image = current_image
        run.last_heartbeat = utc_now()
        repository = self._save(run)
        self._event(repository, run, "run.progress", f"Готово {run.progress.done} из {run.progress.total} изображений", {
            "done": run.progress.done, "total": run.progress.total, "current_image": current_image,
        })
        return run

    def set_stage(self, run_id: str, stage: RunStage) -> Run:
        run = self.get(run_id)
        run.stage = stage
        run.last_heartbeat = utc_now()
        repository = self._save(run)
        self._event(repository, run, "run.stage", f"Этап: {stage.value}", {"stage": stage.value})
        return run

    def record_item_counts(self, run_id: str, *, errors: int, review_count: int, retries: int) -> Run:
        run = self.get(run_id)
        run.progress.errors = errors
        run.progress.review_count = review_count
        run.progress.retries = retries
        self._save(run)
        return run

    def complete(self, run_id: str, summary: dict | None = None) -> Run:
        run = self.get(run_id)
        if run.status is RunStatus.STOP_REQUESTED:
            run.status = RunStatus.STOPPED
        else:
            run.status = RunStatus.COMPLETED
        run.stage = RunStage.FINISHED
        run.finished_at = utc_now()
        run.summary.update(summary or {})
        repository = self._save(run)
        self._event(repository, run, f"run.{run.status.value}", "Обработка завершена" if run.status is RunStatus.COMPLETED else "Обработка безопасно остановлена", {"status": run.status.value})
        return run

    def record_inference_metrics(self, run_id: str, metrics: dict) -> Run:
        run = self.get(run_id)
        allowed = set(run.inference_metrics.__dataclass_fields__)
        accepted = {key: value for key, value in metrics.items() if key in allowed}
        for key, value in accepted.items():
            setattr(run.inference_metrics, key, value)
        run.last_heartbeat = utc_now()
        repository = self._save(run)
        self._event(repository, run, "run.inference_metrics", "Обновлены метрики inference", accepted)
        return run

    def record_model_state(self, run_id: str, state: str, message: str, details: dict | None = None) -> Run:
        run = self.get(run_id)
        repository = self._save(run)
        self._event(repository, run, f"model.{state}", message, {"state": state, **(details or {})})
        return run

    def set_review_wait(self, run_id: str, item_id: str | None) -> Run:
        run = self.get(run_id)
        if item_id:
            run.summary["awaiting_review_item_id"] = item_id
        else:
            run.summary.pop("awaiting_review_item_id", None)
        self._save(run)
        return run

    def disable_review_each(self, run_id: str) -> Run:
        run = self.get(run_id)
        run.summary["review_each_disabled"] = True
        run.summary.pop("awaiting_review_item_id", None)
        self._save(run)
        return run

    def fail(self, run_id: str, message: str) -> Run:
        run = self.get(run_id)
        run.status = RunStatus.FAILED
        run.finished_at = utc_now()
        run.summary["error"] = message
        repository = self._save(run)
        self._event(repository, run, "run.failed", "Обработка завершилась с ошибкой", {"error": message})
        return run

    def pause(self, run_id: str) -> Run:
        return self._transition(run_id, {RunStatus.RUNNING}, RunStatus.PAUSED, "run.paused", "Обработка приостановлена")

    def resume(self, run_id: str) -> Run:
        return self._transition(run_id, {RunStatus.PAUSED}, RunStatus.RUNNING, "run.resumed", "Обработка продолжена")

    def stop(self, run_id: str) -> Run:
        return self._transition(run_id, {RunStatus.PENDING, RunStatus.RUNNING, RunStatus.PAUSED}, RunStatus.STOP_REQUESTED, "run.stop_requested", "Запрошена безопасная остановка")

    def start(self, run_id: str) -> Run:
        run = self._transition(run_id, {RunStatus.PENDING}, RunStatus.RUNNING, "run.started", "Обработка началась")
        run.started_at = utc_now()
        self._save(run)
        return run

    def execute_command(self, run_id: str, action: str, idempotency_key: str) -> Run:
        command_type = f"run.{action}"
        existing = self.projects.index.find_command(idempotency_key)
        if existing is not None:
            if existing != (command_type, run_id):
                raise ValueError("Idempotency key was used for another command")
            return self.get(run_id)
        commands = {
            "start": self.start,
            "pause": self.pause,
            "resume": self.resume,
            "stop": self.stop,
        }
        if action not in commands:
            raise ValueError(f"Unknown run command: {action}")
        run = commands[action](run_id)
        self.projects.index.remember_command(idempotency_key, command_type, run_id)
        return run

    def _transition(self, run_id: str, allowed: set[RunStatus], target: RunStatus, event_type: str, message: str) -> Run:
        run = self.get(run_id)
        if run.status not in allowed:
            raise InvalidRunTransitionError(f"Cannot change run from {run.status.value} to {target.value}")
        run.status = target
        run.pause_requested = target is RunStatus.PAUSED
        run.stop_requested = target is RunStatus.STOP_REQUESTED
        run.last_heartbeat = utc_now()
        repository = self._save(run)
        self._event(repository, run, event_type, message, {"status": target.value})
        return run

    def _save(self, run: Run) -> ProjectRepository:
        project = self.projects.get_project(run.project_id)
        repository = ProjectRepository(project.dataset_path)
        repository.save_run(run)
        return repository

    @staticmethod
    def _event(repository: ProjectRepository, run: Run, event_type: str, message: str, payload: dict) -> None:
        EventStore(repository.sidecar_path / "runs", run.run_id).append(
            Event(event_type, run.project_id, run.run_id, message, payload)
        )
