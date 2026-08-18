from __future__ import annotations

import time
import inspect
from pathlib import Path
from typing import Callable

from backend.domain import ReviewStatus, RunStage, RunStatus
from backend.services.project_service import ProjectService
from backend.services.review_service import ReviewService
from backend.services.run_service import RunService
from backend.persistence import atomic_write_json, atomic_write_text
from core.image_scanner import find_images
from core.prompt_builder import build_user_prompt, build_vlm_augment_prompt
from core.registry import DoneRegistry, prompt_signature
from core.worker import _write_caption
from core.taggers.manager import TaggerManager
from core.taggers.normalize import TagFilterPolicy, normalize_tagger_result
from backend.domain.common import to_primitive
from backend.quality import assess_caption


class VlmPipelineExecutor:
    """One VLM execution path shared by full runs and three-image Test Drive."""

    def __init__(self, runs: RunService, projects: ProjectService,
                 reviews: ReviewService, backend_factory: Callable,
                 tagger_manager_factory: Callable = TaggerManager):
        self.runs = runs
        self.projects = projects
        self.reviews = reviews
        self.backend_factory = backend_factory
        self.tagger_manager_factory = tagger_manager_factory

    def __call__(self, run_id, progress, should_pause, should_stop) -> dict:
        run = self.runs.get(run_id)
        project = self.projects.get_project(run.project_id)
        root = Path(project.dataset_path)
        recipe = run.recipe_snapshot
        scope = dict(run.scope_plan)
        scope.setdefault("include_subfolders", bool(recipe.get("include_subfolders", False)))
        images = self._scope(root, scope)
        first_image = images[0].relative_to(root).as_posix() if images else None
        # Publish the known scope before Tagger/model loading so Cockpit never
        # spends that potentially long phase at a misleading 0 / 0.
        progress(0, len(images), first_image)
        registry = DoneRegistry(str(root))
        ready = errors = review_count = retries = 0
        created = overwritten = 0
        item_speeds: list[float] = []
        test_results: list[dict[str, str]] = []
        started = time.monotonic()
        if recipe.get("pipeline_mode") == "tagger_only":
            return self._run_tagger_only(
                run_id, root, images, recipe, progress, should_pause, should_stop,
            )
        tagger_results = self._tagger_stage(run_id, root, images, recipe, should_pause, should_stop)
        backend = self.backend_factory(run)
        self.runs.set_stage(run_id, RunStage.MODEL_PREPARATION)
        try:
            self.runs.record_model_state(run_id, "loading", "Модель загружается")
            model_started = time.monotonic()
            last_model_update = [0.0]
            self.runs.record_inference_metrics(run_id, {
                "activity": "model_loading", "item_elapsed_seconds": 0.0,
                "elapsed_seconds": 0.0,
                "backend": getattr(backend, "backend_name", type(backend).__name__),
                "model": str(getattr(backend, "model", "")),
            })
            def model_should_stop() -> bool:
                now = time.monotonic()
                if now - last_model_update[0] >= 1.0:
                    last_model_update[0] = now
                    elapsed = now - model_started
                    self.runs.record_inference_metrics(run_id, {
                        "activity": "model_loading", "item_elapsed_seconds": elapsed,
                        "elapsed_seconds": elapsed,
                    })
                return should_stop()
            try:
                health = backend.start(should_stop=model_should_stop)
            except TypeError:
                health = backend.start()
            if not getattr(health, "ready", False):
                raise RuntimeError(getattr(health, "message", "Модель не готова"))
            self.runs.record_model_state(run_id, "ready", "Модель готова", {
                "model": getattr(health, "model", None),
                "pid": (getattr(health, "details", None) or {}).get("pid"),
                "backend": getattr(backend, "backend_name", type(backend).__name__),
            })
            self.runs.set_stage(run_id, RunStage.VLM)
            for index, image in enumerate(images, start=1):
                while should_pause() and not should_stop():
                    time.sleep(0.05)
                if should_stop():
                    break
                item_started = time.monotonic()
                relative = image.relative_to(root).as_posix()
                progress(index - 1, len(images), relative)
                user_prompt = str(recipe.get("user_prompt", "Describe this image."))
                caption_path = image.with_suffix(".txt")
                augment_vlm = str(recipe.get("caption_write_mode", "")) == "vlm_augment"
                existing_caption = ""
                if augment_vlm:
                    existing_caption = caption_path.read_text(encoding="utf-8-sig").strip()
                    user_prompt = build_vlm_augment_prompt(user_prompt, existing_caption)
                if relative in tagger_results:
                    user_prompt = build_user_prompt(user_prompt, tagger_results[relative])
                self.runs.record_inference_metrics(run_id, {
                    "activity": "waiting_first_token", "item_elapsed_seconds": 0.0,
                    "elapsed_seconds": time.monotonic() - started,
                    "backend": getattr(backend, "backend_name", type(backend).__name__),
                    "model": str(getattr(backend, "model", "")),
                })
                last_stream_update = [0.0]
                def on_stream(elapsed: float, chunks: int) -> None:
                    now = time.monotonic()
                    if now - last_stream_update[0] < 0.5:
                        return
                    last_stream_update[0] = now
                    self.runs.record_inference_metrics(run_id, {
                        "activity": "streaming", "item_elapsed_seconds": elapsed,
                        "elapsed_seconds": now - started,
                    })
                result = backend.generate_caption(
                    str(image), str(recipe.get("system_prompt", "")),
                    user_prompt,
                    float(recipe.get("temperature", 0.7)),
                    int(recipe.get("max_tokens", 4096)),
                    float(recipe.get("top_p", 0.9)),
                    max_caption_retries=(None if recipe.get("auto_retry", True) else 1),
                    should_stop=lambda: should_stop() or should_pause(),
                    disable_thinking=bool(recipe.get("disable_thinking", False)),
                    on_stream=on_stream,
                    reasoning_budget=int(recipe.get("reasoning_budget", 0)),
                )
                if getattr(result, "stopped", False) and should_pause() and not should_stop():
                    while should_pause() and not should_stop():
                        time.sleep(0.05)
                    if should_stop():
                        break
                    result = backend.generate_caption(
                        str(image), str(recipe.get("system_prompt", "")),
                        user_prompt,
                        float(recipe.get("temperature", 0.7)), int(recipe.get("max_tokens", 4096)),
                        float(recipe.get("top_p", 0.9)), should_stop=should_stop,
                        disable_thinking=bool(recipe.get("disable_thinking", False)),
                        on_stream=on_stream,
                        reasoning_budget=int(recipe.get("reasoning_budget", 0)),
                    )
                retries += max(0, int(getattr(result, "attempts", 1)) - 1)
                if not getattr(result, "success", False):
                    if should_stop():
                        break
                    errors += 1
                    self.runs.record_item_counts(run_id, errors=errors, review_count=review_count, retries=retries)
                    progress(index, len(images), image.relative_to(root).as_posix())
                    continue
                caption = str(result.caption).strip()
                assessment = assess_caption(caption, result_type=str(recipe.get("result_type", "hybrid_caption")),
                    finish_reason=getattr(result, "finish_reason", None),
                    model_reason=str(getattr(result, "quality_reason", "ok") or "ok"))
                quality_reason = "ok" if assessment.clean else assessment.codes[0]
                atomic_write_json(root / ".tagmanager" / "quality-cache" / f"{relative}.json", {
                    "schema_version": 1, "image": relative, "clean": assessment.clean,
                    "reason_codes": list(assessment.codes), "findings": list(assessment.findings), "run_id": run_id,
                })
                if bool(run.scope_plan.get("test_drive", False)):
                    test_results.append({"image": relative, "caption": caption,
                                         "quality": quality_reason})
                review_each = recipe.get("review_policy") == "stop_on_review" and not self.runs.get(run_id).summary.get("review_each_disabled")
                if not assessment.clean or review_each:
                    item = self.reviews.enqueue(
                        run.project_id, run_id, relative, caption,
                        (["manual_review"] if review_each else list(assessment.codes)),
                        (["Проверьте описание перед продолжением"] if review_each else list(assessment.codes)))
                    review_count += 1
                    self.runs.record_item_counts(run_id, errors=errors, review_count=review_count, retries=retries)
                    if review_each:
                        self.runs.set_review_wait(run_id, item.id)
                        while not should_stop():
                            current_run = self.runs.get(run_id)
                            if current_run.summary.get("review_each_disabled"):
                                break
                            decision = self.reviews.get(item.id)
                            if decision.status is not ReviewStatus.PENDING:
                                if decision.status in {ReviewStatus.ACCEPTED, ReviewStatus.EDITED}:
                                    caption = decision.proposed_caption
                                elif decision.status is ReviewStatus.SKIPPED:
                                    caption = ""
                                elif decision.status is ReviewStatus.REGENERATE_REQUESTED:
                                    regenerated = backend.generate_caption(
                                        str(image), str(recipe.get("system_prompt", "")), user_prompt,
                                        float(recipe.get("temperature", 0.7)), int(recipe.get("max_tokens", 4096)),
                                        float(recipe.get("top_p", 0.9)), should_stop=should_stop,
                                        disable_thinking=bool(recipe.get("disable_thinking", False)), on_stream=on_stream,
                                        reasoning_budget=int(recipe.get("reasoning_budget", 0)))
                                    if getattr(regenerated, "success", False):
                                        caption = str(regenerated.caption).strip()
                                break
                            time.sleep(0.15)
                        self.runs.set_review_wait(run_id, None)
                        if not caption:
                            progress(index, len(images), relative)
                            continue
                if not bool(run.scope_plan.get("test_drive", False)):
                    self.runs.set_stage(run_id, RunStage.WRITING)
                    existed = caption_path.exists()
                    written_caption = (
                        existing_caption + "\n\n" + caption
                        if augment_vlm and existing_caption else caption
                    )
                    if augment_vlm:
                        atomic_write_text(caption_path, written_caption)
                    else:
                        _write_caption(str(caption_path), caption, str(recipe.get("trigger_word", "")))
                    if existed:
                        overwritten += 1
                    else:
                        created += 1
                    registry.mark_done(str(image), prompt_hash=prompt_signature(
                        str(recipe.get("system_prompt", "")), str(recipe.get("user_prompt", ""))),
                        model=str(getattr(backend, "model", "")), caption=written_caption)
                    self.runs.set_stage(run_id, RunStage.VLM)
                ready += 1
                elapsed = time.monotonic() - item_started
                generated_tokens = getattr(result, "generated_tokens", None)
                prompt_tokens = getattr(result, "prompt_tokens", None)
                measured_elapsed = getattr(result, "elapsed_seconds", None) or elapsed
                if generated_tokens is not None and measured_elapsed > 0:
                    item_speeds.append(generated_tokens / measured_elapsed)
                metrics = {"item_elapsed_seconds": measured_elapsed, "activity": "item_complete",
                    "elapsed_seconds": time.monotonic() - started, "backend": getattr(backend, "backend_name", type(backend).__name__),
                    "model": str(getattr(backend, "model", "")),
                    "input_tokens": prompt_tokens, "output_tokens": generated_tokens,
                    "total_tokens": ((prompt_tokens or 0) + (generated_tokens or 0)) if prompt_tokens is not None or generated_tokens is not None else None,
                    "tokens_per_second": (generated_tokens / measured_elapsed if generated_tokens is not None and measured_elapsed > 0 else None)}
                self.runs.record_inference_metrics(run_id, metrics)
                self.runs.record_item_counts(run_id, errors=errors, review_count=review_count, retries=retries)
                progress(index, len(images), image.relative_to(root).as_posix())
            return {"ready": ready, "created": created, "overwritten": overwritten,
                    "preserved": int(scope.get("preserve", 0) or 0),
                    "errors": errors, "review_count": review_count,
                    "retries": retries, "duration_seconds": time.monotonic() - started,
                    "average_tokens_per_second": (sum(item_speeds) / len(item_speeds) if item_speeds else None),
                    "min_tokens_per_second": (min(item_speeds) if item_speeds else None),
                    "max_tokens_per_second": (max(item_speeds) if item_speeds else None),
                    "model_release": ("keep_warm" if float(run.effective_resource_configuration.get("keep_alive_seconds", 0) or 0) > 0 else "unloaded"),
                    "test_drive": bool(run.scope_plan.get("test_drive", False)),
                    "test_results": test_results}
        finally:
            self.runs.set_stage(run_id, RunStage.UNLOADING)
            keep_alive = float(run.effective_resource_configuration.get("keep_alive_seconds", 0) or 0)
            release = getattr(self.backend_factory, "release", None)
            if keep_alive > 0 and callable(release):
                release(backend, keep_alive)
                self.runs.record_model_state(run_id, "warm", "Модель временно оставлена в памяти",
                                             {"keep_alive_seconds": keep_alive})
            else:
                self.runs.record_model_state(run_id, "unloading", "Модель выгружается")
                backend.stop()
                self.runs.record_model_state(run_id, "stopped", "Модель выгружена")

    def _tagger_stage(self, run_id: str, root: Path, images: list[Path], recipe: dict,
                      should_pause, should_stop, on_result=None) -> dict[str, list]:
        tagger_ids = list(recipe.get("pipeline_tagger_ids", []))
        if not tagger_ids:
            return {}
        self.runs.set_stage(run_id, RunStage.TAGGER)
        manager = self.tagger_manager_factory(str(recipe.get("tagger_root", "taggers")))
        loaded = []
        results: dict[str, list] = {}
        cache_root = root / ".tagmanager" / "tagger-cache"
        try:
            loaded = [manager.create(str(tagger_id)) for tagger_id in tagger_ids]
            for index, image in enumerate(images, start=1):
                while should_pause() and not should_stop():
                    time.sleep(0.05)
                if should_stop():
                    break
                relative = image.relative_to(root).as_posix()
                policy_data = dict(recipe.get("tagger_policy", {}))
                policy = TagFilterPolicy(
                    general_threshold=float(policy_data.get("general_threshold", 0.35)),
                    character_threshold=float(policy_data.get("character_threshold", 0.75)),
                    rating_threshold=float(policy_data.get("rating_threshold", 0.0)),
                    blacklist=frozenset(self._normalized_names(policy_data.get("blacklist", []))),
                    aliases={self._normalized_name(key): self._normalized_name(value)
                             for key, value in dict(policy_data.get("aliases", {})).items()},
                    include_characters=bool(policy_data.get("include_characters", True)),
                    include_rating=bool(policy_data.get("include_rating", False)),
                    top_k=int(policy_data.get("top_k", 128)),
                )
                predict_options = {
                    "general_threshold": policy.general_threshold,
                    "character_threshold": policy.character_threshold,
                    "top_k": policy.top_k,
                }
                item_results = [normalize_tagger_result(self._predict_tagger(tagger, image, predict_options), policy)
                                for tagger in loaded]
                results[relative] = item_results
                cache_path = cache_root / (relative + ".json")
                atomic_write_json(cache_path, {"schema_version": 1, "image": relative,
                                               "results": to_primitive(item_results)})
                if on_result is not None:
                    on_result(index, image, relative, item_results)
        finally:
            for tagger_id in tagger_ids:
                manager.unload(str(tagger_id))
        return results

    def _run_tagger_only(self, run_id: str, root: Path, images: list[Path],
                         recipe: dict, progress, should_pause, should_stop) -> dict:
        created = overwritten = augmented = errors = 0
        mode = str(recipe.get("tagger_write_mode", "missing"))
        test_drive = bool(self.runs.get(run_id).scope_plan.get("test_drive", False))
        test_results = []

        def persist_result(index: int, image: Path, relative: str, item_results: list) -> None:
            nonlocal created, overwritten, augmented, errors
            caption_path = image.with_suffix(".txt")
            tags = self._tag_text(item_results)
            if not tags:
                errors += 1
                self.runs.record_item_counts(
                    run_id, errors=errors, review_count=0, retries=0,
                )
                progress(index, len(images), relative)
                return
            if test_drive:
                test_results.append({"image": relative, "caption": tags, "quality": "tagger"})
                progress(index, len(images), relative)
                return
            existed = caption_path.is_file() and bool(caption_path.read_text(encoding="utf-8-sig").strip())
            if mode == "augment" and existed:
                old = caption_path.read_text(encoding="utf-8-sig").strip()
                merged = self._merge_tags(old, tags)
                atomic_write_text(caption_path, merged)
                augmented += 1
            else:
                atomic_write_text(caption_path, tags)
                if existed:
                    overwritten += 1
                else:
                    created += 1
            progress(index, len(images), relative)

        self._tagger_stage(
            run_id, root, images, recipe, should_pause, should_stop,
            on_result=persist_result,
        )
        self.runs.set_stage(run_id, RunStage.FINISHED)
        return {"created": created, "overwritten": overwritten, "augmented": augmented,
                "errors": errors, "pipeline_mode": "tagger_only", "model_release": "not_loaded",
                "test_results": test_results}

    @staticmethod
    def _tag_text(results: list) -> str:
        best: dict[str, float] = {}
        for result in results:
            if not getattr(result, "success", False):
                continue
            for tag in getattr(result, "tags", []):
                name = str(tag.name).strip().replace("_", " ")
                if name:
                    best[name] = max(best.get(name, 0.0), float(tag.confidence))
        return ", ".join(name for name, _ in sorted(best.items(), key=lambda item: (-item[1], item[0])))

    @staticmethod
    def _normalized_name(value: object) -> str:
        return str(value).strip().lower().replace(" ", "_")

    @classmethod
    def _normalized_names(cls, values: object) -> list[str]:
        return [cls._normalized_name(value) for value in values] if isinstance(values, list) else []

    @staticmethod
    def _predict_tagger(tagger, image: Path, options: dict):
        parameters = inspect.signature(tagger.predict).parameters.values()
        accepts_options = any(parameter.kind is inspect.Parameter.VAR_KEYWORD for parameter in parameters)
        return tagger.predict(str(image), **options) if accepts_options else tagger.predict(str(image))

    @staticmethod
    def _merge_tags(existing: str, generated: str) -> str:
        lines = existing.strip().splitlines()
        first_line = lines[0].strip() if lines else ""
        if "," not in first_line:
            return generated + ("\n\n" + existing.strip() if existing.strip() else "")
        old = [value.strip() for value in first_line.split(",") if value.strip()]
        seen = {value.casefold().replace("_", " ") for value in old}
        additions = [value.strip() for value in generated.split(",")
                     if value.strip() and value.strip().casefold().replace("_", " ") not in seen]
        merged = ", ".join([*old, *additions])
        suffix = "\n".join(lines[1:]).strip()
        return merged + ("\n\n" + suffix if suffix else "")

    @staticmethod
    def _scope(root: Path, scope: dict) -> list[Path]:
        all_images = [Path(path) for path in find_images(
            str(root), bool(scope.get("include_subfolders", False)))]
        explicit = scope.get("images")
        if isinstance(explicit, list):
            allowed = {str(value).replace("\\", "/") for value in explicit}
            return [path for path in all_images if path.relative_to(root).as_posix() in allowed]
        if scope.get("mode") == "vlm_augment":
            return [path for path in all_images if path.with_suffix(".txt").is_file()
                    and bool(path.with_suffix(".txt").read_text(encoding="utf-8-sig").strip())]
        if scope.get("overwrite", 0):
            return all_images
        return [path for path in all_images if not path.with_suffix(".txt").is_file()]
