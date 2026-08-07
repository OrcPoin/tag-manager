"""Backend-neutral retry and caption quality orchestration policies."""

from __future__ import annotations

from dataclasses import dataclass
import time
from typing import Callable, TypeVar


T = TypeVar("T")


class RetryStopped(RuntimeError):
    """Raised when an interruptible policy wait is cancelled."""


def sleep_interruptible(
    seconds: float,
    should_stop: Callable[[], bool] | None,
    *,
    sleep: Callable[[float], None] = time.sleep,
) -> None:
    """Wait for ``seconds`` while checking cancellation at short intervals."""
    waited = 0.0
    step = 0.25
    while waited < seconds:
        if should_stop and should_stop():
            raise RetryStopped()
        interval = min(step, seconds - waited)
        sleep(interval)
        waited += interval


@dataclass(frozen=True)
class TransportRetryPolicy:
    """Retry transient calls without depending on an HTTP client library."""

    max_retries: int
    backoff_base: float
    model_load_max_wait_retries: int
    model_load_wait_seconds: float

    def execute(
        self,
        operation: Callable[[], T],
        *,
        is_model_loading: Callable[[Exception], bool],
        is_retryable: Callable[[Exception], bool],
        status_code: Callable[[Exception], int],
        should_stop: Callable[[], bool] | None = None,
        on_wait: Callable[[int, str], None] | None = None,
    ) -> T:
        last_error: Exception | None = None
        transport_attempts = 0
        load_waits = 0

        while True:
            if should_stop and should_stop():
                raise RetryStopped()
            try:
                return operation()
            except RetryStopped:
                raise
            except Exception as exc:  # classification belongs to the adapter
                if is_model_loading(exc):
                    last_error = exc
                    load_waits += 1
                    if load_waits > self.model_load_max_wait_retries:
                        raise RuntimeError(
                            "Модель так и не загрузилась за отведённое время "
                            f"({self.model_load_max_wait_retries} попыток). Проверьте сервер."
                        ) from exc
                    if on_wait:
                        on_wait(load_waits, "жду загрузки модели…")
                    sleep_interruptible(
                        self.model_load_wait_seconds, should_stop
                    )
                    continue

                status = status_code(exc)
                if 400 <= status < 500:
                    message = str(getattr(exc, "message", "") or exc)
                    raise RuntimeError(
                        f"Ошибка API ({status}): {message}. Проверьте настройки "
                        "(имя модели, параметры)."
                    ) from exc
                if not is_retryable(exc):
                    raise

                last_error = exc
                transport_attempts += 1
                if transport_attempts >= self.max_retries:
                    break
                sleep_interruptible(
                    self.backoff_base * (2 ** (transport_attempts - 1)),
                    should_stop,
                )

        raise RuntimeError(
            f"API-вызов не удался после {self.max_retries} попыток: {last_error}"
        ) from last_error


@dataclass(frozen=True)
class CaptionQualityOutcome:
    caption: str
    attempts: int
    quality_reason: str


@dataclass(frozen=True)
class CaptionQualityPolicy:
    """Repeat generation on low-quality output and retain the longest result."""

    max_retries: int
    reinforcement: str
    evaluate: Callable[[str], tuple[bool, str]]

    def execute(
        self,
        user_prompt: str,
        generate: Callable[[str, int], str],
        *,
        on_attempt: Callable[[int, str], None] | None = None,
        should_stop: Callable[[], bool] | None = None,
    ) -> CaptionQualityOutcome:
        retries = max(1, self.max_retries)
        best_caption = ""
        best_reason = ""

        for attempt in range(1, retries + 1):
            if should_stop and should_stop():
                raise RetryStopped()
            prompt = user_prompt if attempt == 1 else user_prompt + self.reinforcement
            if on_attempt:
                on_attempt(
                    attempt,
                    "исходный промпт" if attempt == 1
                    else "усиленный промпт (тот же формат)",
                )
            caption = generate(prompt, attempt)
            is_good, reason = self.evaluate(caption)
            if is_good:
                return CaptionQualityOutcome(caption, attempt, "ok")
            if len(caption) > len(best_caption):
                best_caption = caption
                best_reason = reason
            if on_attempt:
                on_attempt(attempt, f"плохой капшен: {reason}")

        return CaptionQualityOutcome(
            best_caption,
            retries,
            f"низкое качество ({best_reason})",
        )
