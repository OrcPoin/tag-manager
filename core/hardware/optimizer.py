"""Build reproducible llama.cpp profiles from hardware and GGUF metadata."""

from __future__ import annotations

from dataclasses import dataclass

from core.hardware.detector import HardwareInfo
from core.models.gguf import GGUFMetadata


@dataclass(frozen=True)
class OptimizationProfile:
    name: str
    context_size: int
    max_output_tokens: int
    args: tuple[str, ...]
    explanation: tuple[str, ...]


def optimize_llama_profile(
    hardware: HardwareInfo,
    model: GGUFMetadata,
    *,
    reasoning: bool,
    mmproj_size_bytes: int = 0,
    reasoning_budget: int | None = None,
    mode: str = "auto",
) -> OptimizationProfile:
    gpu = hardware.primary_gpu
    vram_gib = (gpu.total_bytes / (1 << 30)) if gpu else 0
    context = 16384 if reasoning else 8192
    if model.context_length:
        context = min(context, model.context_length)
    max_output = 6144 if reasoning else 4096
    max_output = min(max_output, max(512, context - 2048))

    if vram_gib >= 20:
        fit_target, batch, ubatch, kv = 1024, 2048, 2048, "f16"
    elif vram_gib >= 8:
        fit_target, batch, ubatch = 512, 2048, 2048
        kv = "q8_0" if reasoning else "f16"
    elif vram_gib >= 6:
        fit_target, batch, ubatch, kv = 768, 1024, 1024, "q8_0"
    elif gpu:
        fit_target, batch, ubatch, kv = 1024, 512, 512, "q4_0"
    else:
        fit_target, batch, ubatch, kv = 0, 512, 256, "q8_0"

    if mode == "prefer_vram" and gpu:
        fit_target = max(256, fit_target // 2)
        kv = "q8_0" if reasoning else "f16"
    elif mode == "prefer_ram" and gpu:
        fit_target = max(1536, fit_target)
        batch = min(batch, 1024)
        ubatch = min(ubatch, 512)
        kv = "q8_0"
    elif mode == "balanced":
        fit_target = max(768, fit_target)

    args = [
        "-ngl", "auto" if gpu else "0",
        "-c", str(context), "-b", str(batch), "-ub", str(ubatch),
        "-np", "1", "-t", str(hardware.physical_cores),
        "-tb", str(hardware.physical_cores), "-fa", "auto",
        "-ctk", kv, "-ctv", kv,
        "-rea", "on" if reasoning else "off",
        "--reasoning-budget", str(
            reasoning_budget if reasoning_budget is not None else (5120 if reasoning else 0)
        ),
    ]
    if gpu:
        args.extend(["-fit", "on", "-fitt", str(fit_target)])
    if mode == "prefer_ram" and model.is_moe:
        args.append("-cmoe")

    combined_fast_memory = (gpu.free_bytes if gpu else 0) + hardware.ram_available_bytes
    required = model.size_bytes + mmproj_size_bytes + 2 * (1 << 30)
    if combined_fast_memory >= required and mode != "prefer_ram":
        args.append("--no-mmap")

    explanation = [
        f"context {context}: {'reasoning' if reasoning else 'no-thinking'} profile",
        "one slot: caption worker sends one image at a time",
        f"KV cache {kv}: balances context memory and quality",
        f"batch/ubatch {batch}/{ubatch}",
        f"model quantization: {model.quantization}",
    ]
    if model.is_moe:
        explanation.append(
            f"MoE {model.expert_used_count or '?'} of {model.expert_count}: auto-fit tensor placement"
        )
    if gpu:
        explanation.append(f"{gpu.name}: {vram_gib:.1f} GiB VRAM, {fit_target} MiB reserve")
    else:
        explanation.append("CPU-only fallback")
    explanation.append(f"optimization mode: {mode}")
    return OptimizationProfile(
        f"{mode}-{'reasoning' if reasoning else 'fast'}", context, max_output,
        tuple(args), tuple(explanation),
    )


def apply_manual_overrides(
    profile: OptimizationProfile,
    *,
    context_size: int,
    max_output_tokens: int,
    gpu_layers: str,
    fit_target: int,
    slots: int,
    threads: int,
    batch: int,
    ubatch: int,
    flash_attention: str,
    load_mode: str,
    cache_k: str,
    cache_v: str,
    reasoning: bool,
    reasoning_budget: int,
) -> OptimizationProfile:
    """Replace tunable CLI flags while retaining the profile explanation."""
    values = {
        "-c": str(context_size), "-b": str(batch), "-ub": str(ubatch),
        "-np": str(slots), "-t": str(threads), "-tb": str(threads),
        "-fa": flash_attention, "-ctk": cache_k, "-ctv": cache_v,
        "-ngl": gpu_layers, "-fitt": str(fit_target),
        "-rea": "on" if reasoning else "off",
        "--reasoning-budget": str(reasoning_budget if reasoning else 0),
    }
    args = list(profile.args)
    i = 0
    while i < len(args):
        if args[i] in values:
            args[i + 1] = values[args[i]]
            i += 2
        else:
            i += 1
    args = [arg for arg in args if arg != "--no-mmap"]
    if "-lm" in args:
        index = args.index("-lm")
        del args[index:index + 2]
    if load_mode == "no-mmap":
        args.append("--no-mmap")
    elif load_mode != "mmap":
        args.extend(["-lm", load_mode])
    return OptimizationProfile(
        "manual", context_size, max_output_tokens, tuple(args),
        profile.explanation + ("manual overrides",),
    )
