"""Backend-neutral multi-stage image analysis orchestrator."""

from __future__ import annotations

from dataclasses import asdict
import hashlib
import json
import os
import tempfile
import time
from typing import Callable

from core.pipeline.models import PipelineResult, RunProvenance, ScoredTag, TaggerResult, VLMResult
from core.pipeline.policies import FailureAction, PipelineMode, PipelinePolicy
from core.taggers.normalize import TagFilterPolicy, normalize_tagger_result


def merge_tagger_results(results: list[TaggerResult]) -> TaggerResult:
    """Merge providers deterministically, retaining the strongest confidence."""
    selected: dict[tuple[str, str], ScoredTag] = {}
    for result in results:
        if not result.success:
            continue
        for tag in result.tags:
            key = (tag.category, tag.name)
            if key not in selected or tag.confidence > selected[key].confidence:
                selected[key] = tag
    tags = sorted(selected.values(), key=lambda item: (-item.confidence, item.category, item.name))
    return TaggerResult(
        True,
        general=[tag for tag in tags if tag.category == "general"],
        characters=[tag for tag in tags if tag.category == "characters"],
        rating=[tag for tag in tags if tag.category == "rating"],
        metadata={"providers": len(results), "merged": True},
    )


def tags_to_text(result: TaggerResult) -> str:
    return ", ".join(tag.name for tag in result.tags)


class PipelineOrchestrator:
    def __init__(self, backend=None, taggers: list | None = None):
        self.backend = backend
        self.taggers = list(taggers or [])

    def run(
        self, image_path: str, *, mode: PipelineMode, system_prompt: str = "",
        user_prompt: str = "", generation: dict | None = None,
        tagger_options: dict | None = None, filter_policy: TagFilterPolicy | None = None,
        policy: PipelinePolicy | None = None, should_stop: Callable[[], bool] | None = None,
        run_id: str = "",
    ) -> PipelineResult:
        policy = policy or PipelinePolicy()
        generation = generation or {}
        raw_results: list[TaggerResult] = []
        warnings: list[str] = []
        needs_tags = mode != PipelineMode.DESCRIPTION_ONLY
        needs_vlm = mode not in (PipelineMode.TAGS_ONLY,)

        if needs_tags:
            for tagger in self.taggers:
                if should_stop and should_stop():
                    return PipelineResult(False, tagger_results=raw_results, warnings=warnings, error="cancelled")
                result = self._run_tagger(tagger, image_path, tagger_options or {}, policy, should_stop)
                raw_results.append(result)
                if not result.success:
                    warnings.append(f"{getattr(tagger, 'tagger_id', 'tagger')}: {result.error}")
                    if policy.tagger.failure_action == FailureAction.STOP:
                        return PipelineResult(False, tagger_results=raw_results, warnings=warnings, error=result.error)
            if filter_policy:
                raw_results = [normalize_tagger_result(item, filter_policy) for item in raw_results]

        merged = merge_tagger_results(raw_results)
        vlm_result = None
        if needs_vlm:
            from core.prompt_builder import build_user_prompt
            if self.backend is None:
                return PipelineResult(False, tagger_results=raw_results, error="VLM backend is not configured")
            prompt = build_user_prompt(user_prompt, [merged]) if mode in (
                PipelineMode.TAGS_TO_VLM_CONTEXT, PipelineMode.CUSTOM
            ) else user_prompt
            response = self.backend.generate_caption(
                image_path=image_path, system_prompt=system_prompt, user_prompt=prompt,
                temperature=float(generation.get("temperature", 0.2)),
                max_tokens=int(generation.get("max_tokens", 2048)),
                top_p=float(generation.get("top_p", 0.9)),
                max_caption_retries=max(1, policy.vlm.retries + 1), should_stop=should_stop,
                disable_thinking=bool(generation.get("disable_thinking", False)),
            )
            text = str(getattr(response, "caption", getattr(response, "text", "")))
            vlm_result = VLMResult(
                bool(response.success), text=text, error=str(getattr(response, "error", "")),
                attempts=int(getattr(response, "attempts", 0)),
                prompt_tokens=getattr(response, "prompt_tokens", None),
                generated_tokens=getattr(response, "generated_tokens", None),
                elapsed_seconds=getattr(response, "elapsed_seconds", None),
            )
            if not vlm_result.success and policy.vlm.failure_action == FailureAction.STOP:
                return PipelineResult(False, tagger_results=raw_results, vlm_result=vlm_result,
                                      warnings=warnings, error=vlm_result.error)

        if mode == PipelineMode.TAGS_ONLY:
            final = tags_to_text(merged)
        elif mode == PipelineMode.TAGS_AND_DESCRIPTION:
            tags = tags_to_text(merged)
            final = tags + ("\n\n" + vlm_result.text if vlm_result and vlm_result.text else "")
        else:
            final = vlm_result.text if vlm_result else tags_to_text(merged)
        provenance = RunProvenance(
            run_id=run_id, backend=self.backend.__class__.__name__ if self.backend else "",
            vlm_model=str(getattr(self.backend, "model", "")),
            taggers=[dict(result.metadata) for result in raw_results],
            prompt_hash=hashlib.sha256((system_prompt + "\0" + user_prompt).encode()).hexdigest(),
            generation_settings=dict(generation),
            preprocessing={"tagger_options": tagger_options or {}, "mode": mode.value},
        )
        return PipelineResult(True, final, raw_results, vlm_result, provenance, warnings)

    @staticmethod
    def _run_tagger(tagger, image_path, options, policy, should_stop):
        last = TaggerResult(False, error="tagger failed")
        for attempt in range(policy.tagger.retries + 1):
            if should_stop and should_stop():
                return TaggerResult(False, error="cancelled")
            started = time.monotonic()
            last = tagger.predict(image_path, **options)
            last.metadata = {**last.metadata, "attempts": attempt + 1,
                             "elapsed_seconds": time.monotonic() - started}
            if last.success:
                return last
        return last


def write_pipeline_sidecar(image_path: str, result: PipelineResult) -> str:
    folder = os.path.join(os.path.dirname(image_path), ".tagmanager")
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, os.path.basename(image_path) + ".pipeline.json")
    payload = {"schema_version": 1, "result": asdict(result)}
    fd, temporary = tempfile.mkstemp(prefix=".pipeline-", dir=folder, text=True)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, ensure_ascii=False, indent=2)
            stream.flush(); os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return path
