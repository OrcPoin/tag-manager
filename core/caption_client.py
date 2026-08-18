"""Клиент к oobabooga / text-generation-webui через OpenAI-совместимый API.

Кодирует изображения в base64, отправляет multimodal-запрос в /v1/chat/completions,
делает retry с экспоненциальной задержкой и повторную генерацию при плохом капшене.
"""

from __future__ import annotations

import base64
import mimetypes
import os
import time
from dataclasses import dataclass

import httpx
from openai import (
    APIConnectionError,
    APITimeoutError,
    APIStatusError,
    InternalServerError,
    OpenAI,
    RateLimitError,
)

from config import (
    BACKOFF_BASE,
    MAX_API_RETRIES,
    MAX_CAPTION_RETRIES,
    MODEL_LOAD_MAX_WAIT_RETRIES,
    MODEL_LOAD_WAIT_SECONDS,
    RETRY_REINFORCEMENT,
)
from core.quality import evaluate_caption
from core.inference.interfaces import BackendHealth, BackendStatus
from core.inference.policies import (
    CaptionQualityPolicy,
    RetryStopped,
    TransportRetryPolicy,
)

# Ошибки транспорта, которые ИМЕЕТ смысл повторять с backoff.
_RETRYABLE = (APIConnectionError, APITimeoutError, RateLimitError, InternalServerError)


class EmptyContentError(RuntimeError):
    """Модель вернула пустой content (обычно thinking съел весь лимит токенов).

    Повторять такой запрос бессмысленно — нужно менять параметры (max_tokens),
    поэтому эта ошибка НЕ участвует в retry-циклах.
    """


class StopRequested(RetryStopped):
    """Пользователь запросил остановку — прерываем цикл попыток немедленно."""


@dataclass
class CaptionResult:
    success: bool
    caption: str = ""
    error: str = ""
    attempts: int = 0
    quality_reason: str = ""
    stopped: bool = False  # True — прервано пользователем (это не ошибка файла)
    prompt_tokens: int | None = None
    generated_tokens: int | None = None
    elapsed_seconds: float | None = None
    finish_reason: str | None = None


def _is_model_loading(exc: Exception) -> bool:
    """True, если ошибка = «модель ещё грузится» (oobabooga отвечает 503).

    В этом случае файл не виноват — надо подождать и повторить, а не падать.
    """
    status = getattr(exc, "status_code", None)
    if status == 503:
        return True
    text = str(getattr(exc, "message", "") or exc).lower()
    return "loading model" in text or "unavailable" in text


def _encode_image(image_path: str) -> str:
    """Прочитать изображение и вернуть data-URL с base64."""
    mime, _ = mimetypes.guess_type(image_path)
    if mime is None:
        ext = os.path.splitext(image_path)[1].lower().lstrip(".")
        mime = f"image/{'jpeg' if ext == 'jpg' else ext}"
    with open(image_path, "rb") as f:
        b64 = base64.b64encode(f.read()).decode("utf-8")
    return f"data:{mime};base64,{b64}"


class CaptionClient:
    def __init__(self, base_url: str, api_key: str, model: str, timeout: float):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = timeout
        self.client = OpenAI(base_url=base_url, api_key=api_key, timeout=timeout)
        self._last_usage: tuple[int | None, int | None] = (None, None)
        self._last_elapsed: float | None = None
        self._last_finish_reason: str | None = None

    def check_connection(self) -> tuple[bool, str]:
        """Проверить доступность API (список моделей)."""
        try:
            names = self.list_models()
            return True, f"OK. Доступные модели: {', '.join(names) if names else '—'}"
        except Exception as exc:  # noqa: BLE001
            return False, f"Ошибка соединения: {exc}"

    def list_models(self) -> list[str]:
        """Список моделей сервера. Понимает и OpenAI-формат (data[].id),
        и llama.cpp/ollama-формат (models[].name)."""
        try:
            models = self.client.models.list()
            names = [m.id for m in getattr(models, "data", []) if getattr(m, "id", None)]
            if names:
                return names
        except Exception:  # noqa: BLE001 — упадём на прямой запрос ниже
            pass
        # Прямой запрос: llama-server отдаёт {"models":[{"name":...}]} или {"data":[{"id":...}]}.
        r = httpx.get(f"{self.base_url}/models", timeout=10.0)
        r.raise_for_status()
        data = r.json()
        names = [m.get("id") for m in data.get("data", []) if m.get("id")]
        if not names:
            names = [m.get("name") for m in data.get("models", []) if m.get("name")]
        return [n for n in names if n]

    def active_model(self) -> str | None:
        """Имя загруженной модели (первое из списка). None, если сервер недоступен."""
        try:
            names = self.list_models()
            return names[0] if names else None
        except Exception:  # noqa: BLE001
            return None

    def health(self) -> BackendHealth:
        """Return a backend-neutral health snapshot for UI/diagnostics."""
        try:
            names = self.list_models()
        except Exception as exc:  # noqa: BLE001
            return BackendHealth(
                status=BackendStatus.ERROR,
                message=f"API недоступен: {exc}",
                model=self.model or None,
            )
        active = names[0] if names else (self.model or None)
        return BackendHealth(
            status=BackendStatus.READY,
            message="OpenAI-compatible API готов",
            model=active,
            details={"base_url": self.base_url, "models": names},
        )

    def stop_generation(self) -> bool:
        """Best-effort остановка для серверов с таким эндпоинтом (нативная oobabooga).

        У llama-server эндпоинта нет (вернёт 404) — там реальная остановка делается
        обрывом стрима в рабочем потоке (см. _single_call/should_stop). Оставлено на
        случай настоящего Python-API oobabooga; ошибки/404 глушим.
        """
        try:
            response = httpx.post(
                f"{self.base_url}/internal/stop-generation", timeout=5.0
            )
            response.raise_for_status()
            return True
        except Exception:  # noqa: BLE001
            return False

    # Маркеры отключения размышлений. У разных моделей своя конвенция, поэтому шлём
    # набор сразу — лишние безвредны (модель их просто не распознает):
    #   /no_think            — Qwen3 и производные;
    #   disable reasoning... — совет для Gemma в Oobabooga (llama.cpp discussion #21338);
    #   </thought off>       — там же, как явный маркер конца/отключения мыслей.
    _NO_THINK_MARKER = "/no_think\ndisable reasoning and thought.\n</thought off>"

    def _build_messages(self, system_prompt: str, user_prompt: str, data_url: str,
                        disable_thinking: bool = False):
        if disable_thinking:
            system_prompt = (system_prompt or "").rstrip() + "\n" + self._NO_THINK_MARKER
        return [
            {"role": "system", "content": system_prompt},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": user_prompt},
                    {"type": "image_url", "image_url": {"url": data_url}},
                ],
            },
        ]

    def _stream_once(
        self,
        messages,
        temperature: float,
        max_tokens: int,
        top_p: float,
        should_stop=None,
        disable_thinking: bool = False,
        on_stream=None,
        reasoning_budget: int | None = None,
    ) -> str:
        """Один стриминговый вызов.

        Стрим нужен для ОСТАНОВКИ: llama-server не имеет stop-эндпоинта, поэтому
        единственный способ прервать генерацию — разорвать HTTP-соединение. Мы читаем
        поток по токенам и между чанками проверяем should_stop(); при запросе рвём
        соединение (stream.close()) и поднимаем StopRequested — сервер видит обрыв
        клиента и освобождает слот почти мгновенно.

        disable_thinking=True → просим модель не «размышлять». Универсального поля в
        OpenAI API нет, поэтому шлём в extra_body сразу несколько известных серверу
        опций: chat_template_kwargs={"enable_thinking": false} и reasoning_budget=0
        (llama.cpp / Qwen3 / Gemma). Плюс на уровне промпта добавляются текстовые
        маркеры (см. _build_messages). Сервера, не знающие опций, их игнорируют.
        """
        extra_body = None
        if disable_thinking or reasoning_budget is not None:
            budget = 0 if disable_thinking else max(0, int(reasoning_budget or 0))
            extra_body = {"chat_template_kwargs": {"enable_thinking": budget > 0},
                          "reasoning_budget": budget}
        stream = self.client.chat.completions.create(
            model=self.model,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            top_p=top_p,
            stream=True,
            stream_options={"include_usage": True},
            extra_body=extra_body,
        )
        parts: list[str] = []
        finish_reason: str | None = None
        prompt_tokens = generated_tokens = None
        started = time.monotonic()
        try:
            for chunk in stream:
                if should_stop and should_stop():
                    raise StopRequested()
                choices = getattr(chunk, "choices", None)
                usage = getattr(chunk, "usage", None)
                if usage is not None:
                    prompt_tokens = getattr(usage, "prompt_tokens", prompt_tokens)
                    generated_tokens = getattr(usage, "completion_tokens", generated_tokens)
                if not choices:
                    continue
                delta = choices[0].delta
                piece = getattr(delta, "content", None)
                reasoning_piece = (
                    getattr(delta, "reasoning_content", None)
                    or getattr(delta, "reasoning", None)
                )
                if piece:
                    parts.append(piece)
                if on_stream and (piece or reasoning_piece):
                    on_stream(time.monotonic() - started, len(parts))
                if choices[0].finish_reason:
                    finish_reason = choices[0].finish_reason
        finally:
            self._last_usage = (prompt_tokens, generated_tokens)
            self._last_elapsed = time.monotonic() - started
            self._last_finish_reason = finish_reason
            # Рвём соединение в любом исходе (стоп/ошибка/конец) — освобождает слот.
            try:
                stream.close()
            except Exception:  # noqa: BLE001
                pass

        content = "".join(parts).strip()
        if not content:
            # Thinking-модель израсходовала лимит на размышления и не дошла до ответа.
            if finish_reason == "length":
                raise EmptyContentError(
                    "Пустой ответ: лимит Max tokens исчерпан на размышлениях "
                    "модели. Увеличьте Max tokens."
                )
            raise EmptyContentError("Пустой ответ от модели")
        return content

    def _single_call(
        self,
        messages,
        temperature: float,
        max_tokens: int,
        top_p: float,
        should_stop=None,
        on_attempt=None,
        disable_thinking: bool = False,
        on_stream=None,
        reasoning_budget: int | None = None,
    ) -> str:
        """Один API-вызов (стриминговый) с обработкой сбоев.

        Три класса поведения:
          * Сетевые/серверные сбои — повтор с экспоненциальной задержкой (коротко).
          * 503 «модель грузится» — терпеливое ожидание (MODEL_LOAD_* попыток).
          * Пустой content (thinking исчерпал лимит) — сразу наверх, не ретраим.
        Между попытками и между токенами проверяем should_stop().
        """
        policy = TransportRetryPolicy(
            max_retries=MAX_API_RETRIES,
            backoff_base=BACKOFF_BASE,
            model_load_max_wait_retries=MODEL_LOAD_MAX_WAIT_RETRIES,
            model_load_wait_seconds=MODEL_LOAD_WAIT_SECONDS,
        )

        def operation():
            return self._stream_once(
                messages, temperature, max_tokens, top_p,
                should_stop=should_stop,
                disable_thinking=disable_thinking,
                on_stream=on_stream,
                reasoning_budget=reasoning_budget,
            )

        def is_retryable(exc: Exception) -> bool:
            return isinstance(exc, _RETRYABLE) or (
                isinstance(exc, APIStatusError)
                and not (400 <= (getattr(exc, "status_code", 0) or 0) < 500)
            )

        try:
            return policy.execute(
                operation,
                is_model_loading=_is_model_loading,
                is_retryable=is_retryable,
                status_code=lambda exc: getattr(exc, "status_code", 0) or 0,
                should_stop=should_stop,
                on_wait=(
                    (lambda count, message: on_attempt(0, f"{message} ({count})"))
                    if on_attempt else None
                ),
            )
        except RetryStopped as exc:
            raise StopRequested() from exc

    def generate_caption(
        self,
        image_path: str,
        system_prompt: str,
        user_prompt: str,
        temperature: float,
        max_tokens: int,
        top_p: float,
        on_attempt=None,
        max_caption_retries: int | None = None,
        should_stop=None,
        disable_thinking: bool = False,
        on_stream=None,
        reasoning_budget: int | None = None,
    ) -> CaptionResult:
        """
        Сгенерировать капшен для одного изображения с проверкой качества.

        При «плохом» капшене повторяет генерацию с усиленным промптом
        (до max_caption_retries попыток). on_attempt(n, msg) — колбэк для лога.
        max_caption_retries=1 фактически отключает авто-ретрай (одна попытка,
        принимаем что получилось). None → значение из config.
        should_stop() — колбэк отмены: если вернёт True, генерация прерывается и
        возвращается CaptionResult(stopped=True).
        """
        retries = MAX_CAPTION_RETRIES if max_caption_retries is None else max(1, max_caption_retries)
        try:
            data_url = _encode_image(image_path)
        except OSError as exc:
            return CaptionResult(success=False, error=f"Не удалось прочитать изображение: {exc}")

        current_attempt = 0

        def generate(prompt: str, attempt: int) -> str:
            nonlocal current_attempt
            current_attempt = attempt
            messages = self._build_messages(system_prompt, prompt, data_url,
                                            disable_thinking=disable_thinking)
            return self._single_call(
                messages, temperature, max_tokens, top_p,
                should_stop=should_stop, on_attempt=on_attempt,
                disable_thinking=disable_thinking,
                on_stream=on_stream,
                reasoning_budget=reasoning_budget,
            )

        policy = CaptionQualityPolicy(retries, RETRY_REINFORCEMENT, evaluate_caption)
        try:
            outcome = policy.execute(
                user_prompt, generate,
                on_attempt=on_attempt,
                should_stop=should_stop,
            )
        except RetryStopped:
            return CaptionResult(success=False, error="Остановлено",
                                 attempts=current_attempt, stopped=True)
        except EmptyContentError as exc:
            return CaptionResult(
                success=False,
                error=f"{exc} (текущий Max tokens={max_tokens})",
                attempts=current_attempt,
            )
        except Exception as exc:  # noqa: BLE001
            return CaptionResult(success=False, error=str(exc), attempts=current_attempt)
        return CaptionResult(
            success=True,
            caption=outcome.caption,
            attempts=outcome.attempts,
            quality_reason=outcome.quality_reason,
            prompt_tokens=self._last_usage[0],
            generated_tokens=self._last_usage[1],
            elapsed_seconds=self._last_elapsed,
            finish_reason=self._last_finish_reason,
        )
