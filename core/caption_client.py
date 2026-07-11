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

# Ошибки транспорта, которые ИМЕЕТ смысл повторять с backoff.
_RETRYABLE = (APIConnectionError, APITimeoutError, RateLimitError, InternalServerError)


class EmptyContentError(RuntimeError):
    """Модель вернула пустой content (обычно thinking съел весь лимит токенов).

    Повторять такой запрос бессмысленно — нужно менять параметры (max_tokens),
    поэтому эта ошибка НЕ участвует в retry-циклах.
    """


class StopRequested(RuntimeError):
    """Пользователь запросил остановку — прерываем цикл попыток немедленно."""


@dataclass
class CaptionResult:
    success: bool
    caption: str = ""
    error: str = ""
    attempts: int = 0
    quality_reason: str = ""
    stopped: bool = False  # True — прервано пользователем (это не ошибка файла)


def _is_model_loading(exc: Exception) -> bool:
    """True, если ошибка = «модель ещё грузится» (oobabooga отвечает 503).

    В этом случае файл не виноват — надо подождать и повторить, а не падать.
    """
    status = getattr(exc, "status_code", None)
    if status == 503:
        return True
    text = str(getattr(exc, "message", "") or exc).lower()
    return "loading model" in text or "unavailable" in text


def _sleep_interruptible(seconds: float, should_stop) -> None:
    """Спать до `seconds`, но просыпаться раньше, если запросили остановку."""
    waited = 0.0
    step = 0.25
    while waited < seconds:
        if should_stop and should_stop():
            raise StopRequested()
        time.sleep(step)
        waited += step


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

    def stop_generation(self) -> bool:
        """Best-effort остановка для серверов с таким эндпоинтом (нативная oobabooga).

        У llama-server эндпоинта нет (вернёт 404) — там реальная остановка делается
        обрывом стрима в рабочем потоке (см. _single_call/should_stop). Оставлено на
        случай настоящего Python-API oobabooga; ошибки/404 глушим.
        """
        try:
            httpx.post(f"{self.base_url}/internal/stop-generation", timeout=5.0)
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
        if disable_thinking:
            extra_body = {
                "chat_template_kwargs": {"enable_thinking": False},
                "reasoning_budget": 0,
            }
        stream = self.client.chat.completions.create(
            model=self.model,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            top_p=top_p,
            stream=True,
            extra_body=extra_body,
        )
        parts: list[str] = []
        finish_reason: str | None = None
        try:
            for chunk in stream:
                if should_stop and should_stop():
                    raise StopRequested()
                choices = getattr(chunk, "choices", None)
                if not choices:
                    continue
                delta = choices[0].delta
                piece = getattr(delta, "content", None)
                if piece:
                    parts.append(piece)
                if choices[0].finish_reason:
                    finish_reason = choices[0].finish_reason
        finally:
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
    ) -> str:
        """Один API-вызов (стриминговый) с обработкой сбоев.

        Три класса поведения:
          * Сетевые/серверные сбои — повтор с экспоненциальной задержкой (коротко).
          * 503 «модель грузится» — терпеливое ожидание (MODEL_LOAD_* попыток).
          * Пустой content (thinking исчерпал лимит) — сразу наверх, не ретраим.
        Между попытками и между токенами проверяем should_stop().
        """
        last_error: Exception | None = None
        transport_attempts = 0
        load_waits = 0

        while True:
            if should_stop and should_stop():
                raise StopRequested()
            try:
                return self._stream_once(messages, temperature, max_tokens, top_p,
                                         should_stop=should_stop,
                                         disable_thinking=disable_thinking)
            except (StopRequested, EmptyContentError):
                raise  # мимо retry — сразу наверх
            except (InternalServerError, APIStatusError) as exc:
                # 503 «модель грузится» — ждём терпеливо и повторяем.
                if _is_model_loading(exc):
                    last_error = exc
                    load_waits += 1
                    if load_waits > MODEL_LOAD_MAX_WAIT_RETRIES:
                        raise RuntimeError(
                            "Модель так и не загрузилась за отведённое время "
                            f"({MODEL_LOAD_MAX_WAIT_RETRIES} попыток). Проверьте сервер."
                        )
                    if on_attempt:
                        on_attempt(0, f"жду загрузки модели… ({load_waits})")
                    _sleep_interruptible(MODEL_LOAD_WAIT_SECONDS, should_stop)
                    continue
                # Прочие 5xx — как обычный транспортный сбой.
                last_error = exc
                transport_attempts += 1
                if transport_attempts >= MAX_API_RETRIES:
                    break
                _sleep_interruptible(BACKOFF_BASE * (2 ** (transport_attempts - 1)),
                                     should_stop)
            except (APIConnectionError, APITimeoutError, RateLimitError) as exc:
                last_error = exc
                transport_attempts += 1
                if transport_attempts >= MAX_API_RETRIES:
                    break
                _sleep_interruptible(BACKOFF_BASE * (2 ** (transport_attempts - 1)),
                                     should_stop)

        raise RuntimeError(
            f"API-вызов не удался после {MAX_API_RETRIES} попыток: {last_error}"
        )

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

        best_caption = ""
        best_reason = ""
        for attempt in range(1, retries + 1):
            if should_stop and should_stop():
                return CaptionResult(success=False, error="Остановлено", stopped=True)
            # Со 2-й попытки усиливаем ТОТ ЖЕ промпт (не подменяем формат),
            # добавляя требование строже соблюсти структуру.
            prompt = user_prompt if attempt == 1 else user_prompt + RETRY_REINFORCEMENT
            messages = self._build_messages(system_prompt, prompt, data_url,
                                            disable_thinking=disable_thinking)

            if on_attempt:
                on_attempt(attempt, "исходный промпт" if attempt == 1
                           else "усиленный промпт (тот же формат)")

            try:
                caption = self._single_call(messages, temperature, max_tokens, top_p,
                                            should_stop=should_stop, on_attempt=on_attempt,
                                            disable_thinking=disable_thinking)
            except StopRequested:
                return CaptionResult(success=False, error="Остановлено",
                                     attempts=attempt, stopped=True)
            except EmptyContentError as exc:
                # Нехватка токенов на thinking: повторять бессмысленно —
                # прекращаем сразу с понятным советом.
                return CaptionResult(
                    success=False,
                    error=f"{exc} (текущий Max tokens={max_tokens})",
                    attempts=attempt,
                )
            except Exception as exc:  # noqa: BLE001
                return CaptionResult(
                    success=False, error=str(exc), attempts=attempt
                )

            is_good, reason = evaluate_caption(caption)
            if is_good:
                return CaptionResult(
                    success=True, caption=caption, attempts=attempt, quality_reason="ok"
                )

            # Запоминаем лучший вариант (самый длинный) на случай исчерпания попыток.
            if len(caption) > len(best_caption):
                best_caption = caption
                best_reason = reason
            if on_attempt:
                on_attempt(attempt, f"плохой капшен: {reason}")

        # Попытки исчерпаны — возвращаем лучший, но помечаем причину.
        return CaptionResult(
            success=True,
            caption=best_caption,
            attempts=retries,
            quality_reason=f"низкое качество ({best_reason})",
        )
