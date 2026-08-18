from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class QualityAssessment:
    clean: bool
    codes: tuple[str, ...]
    findings: tuple[dict[str, str], ...] = ()


SEVERITY_BY_CODE = {
    "empty_output": "blocking",
    "output_truncated_by_token_limit": "blocking",
    "output_appears_incomplete": "quality",
    "prose_sentence_missing": "quality",
    "tag_list_structure_missing": "quality",
}


def assess_caption(caption: str, *, result_type: str = "hybrid_caption",
                   finish_reason: str | None = None, model_reason: str = "ok") -> QualityAssessment:
    """Conservative structural rules; semantic style stays with the versioned prompt."""
    value = caption.strip()
    codes: list[str] = []
    if not value:
        codes.append("empty_output")
    if finish_reason == "length":
        codes.append("output_truncated_by_token_limit")
    if value and value[-1] in {",", ":", ";", "("}:
        codes.append("output_appears_incomplete")
    if model_reason and model_reason != "ok":
        codes.append(model_reason)
    if result_type == "prose" and value and not any(mark in value for mark in ".!?"):
        codes.append("prose_sentence_missing")
    if result_type == "tags" and value and "," not in value:
        codes.append("tag_list_structure_missing")
    unique = tuple(dict.fromkeys(codes))
    findings = tuple({"code": code, "severity": SEVERITY_BY_CODE.get(code, "informational")}
                     for code in unique)
    return QualityAssessment(not unique, unique, findings)
