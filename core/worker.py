"""Фоновый рабочий поток обработки.

Причина существования: Streamlit исполняет скрипт в одном потоке, поэтому долгая
(8–10 мин) генерация капшена внутри `generate_caption()` полностью блокирует UI —
клики по «Стоп»/«Пауза» не обрабатываются, пока запрос не вернётся сам. Вынеся
генерацию в отдельный поток, мы держим UI живым: главный поток опрашивает воркер
(`snapshot()`) и по «Стоп»/«Пауза» дёргает `client.stop_generation()`, обрывая
генерацию на сервере — висящий запрос тут же возвращается.

Воркер НЕ обращается к `st.session_state` (в фоновом потоке нет ScriptRunContext).
Всё общее состояние он держит у себя под `threading.Lock` и отдаёт UI через
`snapshot()` / `get_review()`.
"""

from __future__ import annotations

import os
import shutil
import threading
import time
from typing import Callable

from core.caption_client import CaptionClient
from core.dataset import apply_trigger, merge_captions
from core.image_scanner import ImageTask, UpdateTask
from core.logger import Logger
from core.registry import DoneRegistry, prompt_signature, caption_signature
from core.state import ProcessingState
from core.stoplist import apply_stoplist, load_stoplist


def _write_caption(txt_path: str, caption: str, trigger: str = "") -> None:
    # apply_trigger живёт в core.dataset (единый источник правды: та же логика
    # используется вкладкой «Теги» для ретрофита триггера по датасету).
    with open(txt_path, "w", encoding="utf-8") as f:
        f.write(apply_trigger(caption, trigger).strip() + "\n")


class CaptionWorker:
    """Управляет очередью обработки в фоновом потоке.

    Жизненный цикл: start() → [pause()/resume()]* → stop()/естественное завершение.
    Между запусками один и тот же экземпляр переиспользуется (лежит в session_state).
    """

    def __init__(self):
        self.state = ProcessingState()
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None

        # Управляющие события.
        self._stop_event = threading.Event()
        self._pause_event = threading.Event()
        self._review_event = threading.Event()  # выставляется, когда пришло решение ревью

        # Снапшот параметров генерации (фиксируется на момент start()).
        self._params: dict = {}
        self._client: CaptionClient | None = None
        self._logger: Logger | None = None
        self._registry: DoneRegistry | None = None

        # Наблюдаемое состояние (читается UI через snapshot()).
        self.running = False
        self.paused = False
        self.finished = False
        self.status_msg = "Готов к запуску."
        self.current_name = ""
        self.review_task: ImageTask | None = None
        self._review_decision: tuple[str, str] | None = None

        # Обновление (Фаза 5).
        self._update_tasks: list[UpdateTask] = []
        self._update_total = 0
        self._update_done = 0
        self._update_skipped = 0
        self._update_errors = 0
        self._update_deferred = 0  # отложено на ручной просмотр (MANUAL_DEFER)

        # Диффы изменений при обновлении (для UI).
        self._update_diffs: list[tuple[str, str, str]] = []

        # Таймстемп старта (для расчёта ETA).
        self._start_ts: float = 0.0

        # Стоп-лист тегов (загружается при start).
        self._stoplist: set[str] = set()

    # ------------------------------------------------------------------ #
    # Управление (вызывается из главного потока UI)
    # ------------------------------------------------------------------ #
    def start(
        self,
        tasks: list[ImageTask],
        folder: str,
        params: dict,
        logger: Logger,
        registry: DoneRegistry,
        client: CaptionClient,
    ) -> None:
        """Запустить обработку списка задач в фоновом потоке."""
        if self.is_alive():
            return  # уже работает
        self.state.set_tasks(tasks, folder)
        self._params = dict(params)
        self._logger = logger
        self._registry = registry
        self._client = client

        self._stop_event.clear()
        self._pause_event.clear()
        self._review_event.clear()
        self._stoplist = load_stoplist()
        with self._lock:
            self.running = True
            self.paused = False
            self.finished = False
            self.review_task = None
            self._review_decision = None
            self.status_msg = "Запуск…"
            self.current_name = ""
            self._start_ts = time.time()
            self._update_total = 0
            self._update_done = 0
            self._update_skipped = 0
            self._update_errors = 0
            self._update_deferred = 0
            self._update_diffs = []

        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def start_resumed(
        self,
        params: dict,
        logger: Logger,
        registry: DoneRegistry,
        client: CaptionClient,
    ) -> None:
        """Продолжить уже загруженный в state прогресс (folder/tasks/index заданы)."""
        if self.is_alive():
            return
        self._params = dict(params)
        self._logger = logger
        self._registry = registry
        self._client = client

        self._stop_event.clear()
        self._pause_event.clear()
        self._review_event.clear()
        self._stoplist = load_stoplist()
        with self._lock:
            self.running = True
            self.paused = False
            self.finished = False
            self.review_task = None
            self._review_decision = None
            self.status_msg = "Продолжаю…"
            self.current_name = ""
            self._start_ts = time.time()
            self._update_total = 0
            self._update_done = 0
            self._update_skipped = 0
            self._update_errors = 0
            self._update_deferred = 0
            self._update_diffs = []

        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def pause(self) -> None:
        """Пауза = мгновенный обрыв текущей генерации + остановка цикла до resume()."""
        self._pause_event.set()
        with self._lock:
            self.paused = True
            self.status_msg = "⏸️ Пауза — генерация прервана"
        if self._client:
            self._client.stop_generation()
        if self._logger:
            self._logger.info("Пауза (генерация прервана)")

    def resume(self) -> None:
        self._pause_event.clear()
        with self._lock:
            self.paused = False
            self.status_msg = "Возобновление…"
        if self._logger:
            self._logger.info("Возобновление")

    def stop(self) -> None:
        """Остановить обработку: обрыв генерации + завершение потока + сохранение прогресса."""
        self._stop_event.set()
        self._pause_event.clear()      # чтобы поток не завис в паузной ветке
        self._review_event.set()       # разбудить ожидание ревью, если оно есть
        if self._client:
            self._client.stop_generation()
        with self._lock:
            self.running = False
            self.paused = False
            self.review_task = None
            self.status_msg = "⏹️ Остановлено"
        self.state.save_progress()
        if self._logger:
            self._logger.info("Остановлено пользователем")

    def submit_review(self, decision: str, edited_caption: str = "") -> None:
        """Решение ручного ревью: 'accept' | 'edit' | 'skip' | 'regenerate'."""
        self._review_decision = (decision, edited_caption)
        self._review_event.set()

    # ------------------------------------------------------------------ #
    # Наблюдение (вызывается из UI)
    # ------------------------------------------------------------------ #
    def is_alive(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def get_review(self) -> ImageTask | None:
        with self._lock:
            return self.review_task

    def snapshot(self) -> dict:
        with self._lock:
            total = self.state.total
            done = self.state.done_count
            return {
                "running": self.running,
                "paused": self.paused,
                "finished": self.finished,
                "status_msg": self.status_msg,
                "current_name": self.current_name,
                "has_review": self.review_task is not None,
                "total": total,
                "done": done,
                "processed": self.state.processed,
                "skipped": self.state.skipped,
                "errors": self.state.errors,
                "update_total": self._update_total,
                "update_done": self._update_done,
                "update_skipped": self._update_skipped,
                "update_errors": self._update_errors,
                "update_deferred": self._update_deferred,
                "update_diffs": list(self._update_diffs),
                "start_ts": self._start_ts,
            }

    # ------------------------------------------------------------------ #
    # Внутренний цикл (фоновый поток)
    # ------------------------------------------------------------------ #
    def _set_status(self, msg: str, current_name: str | None = None) -> None:
        with self._lock:
            self.status_msg = msg
            if current_name is not None:
                self.current_name = current_name

    def _log(self, msg: str, level: str = "info") -> None:
        if self._logger:
            getattr(self._logger, level, self._logger.info)(msg)

    def _run(self) -> None:
        p = self._params
        try:
            while not self._stop_event.is_set() and not self.state.is_finished():
                # Пауза: ждём resume/stop, ничего не генерируя.
                if self._pause_event.is_set():
                    if self._wait_while_paused():
                        break  # пришёл stop
                    continue

                task = self.state.current_task()
                if task is None:
                    break

                self._set_status(
                    f"Обрабатывается файл {self.state.done_count + 1} из "
                    f"{self.state.total}: {task.name}",
                    current_name=task.name,
                )

                def on_attempt(n, msg, _name=task.name):
                    self._log(f"[{_name}] попытка {n}: {msg}")

                result = self._client.generate_caption(
                    image_path=task.image_path,
                    system_prompt=p["system_prompt"],
                    user_prompt=p["user_prompt"],
                    temperature=p["temperature"],
                    max_tokens=p["max_tokens"],
                    top_p=p["top_p"],
                    on_attempt=on_attempt,
                    max_caption_retries=None if p["auto_retry"] else 1,
                    should_stop=lambda: self._stop_event.is_set() or self._pause_event.is_set(),
                    disable_thinking=p.get("disable_thinking", False),
                )

                # Прервано пользователем (стоп/пауза) — НЕ трогаем индекс, файл
                # переgenерируется заново на resume / докачается в след. запуске.
                if result.stopped:
                    if self._stop_event.is_set():
                        break
                    continue  # пауза — вернёмся в начало цикла и подождём

                if not result.success:
                    task.status = "error"
                    task.error = result.error
                    self.state.errors += 1
                    self._log(f"[{task.name}] ошибка: {result.error}", "error")
                    self.state.advance()
                    self.state.save_progress()
                    continue

                task.caption = result.caption

                # Ручное ревью: показать файл и ждать решения пользователя.
                if p["manual_review"]:
                    if self._handle_review(task):
                        break  # stop во время ревью
                    continue

                # Автоматический режим: записать и идти дальше.
                self._commit(task, result.caption, result)

            # Конец цикла.
            if self.state.is_finished() and not self._stop_event.is_set():
                self._set_status("✅ Обработка завершена")
                self._log("Обработка завершена")
                with self._lock:
                    self.finished = True
            self.state.save_progress()
        except Exception as exc:  # noqa: BLE001 — поток не должен молча умирать
            self._log(f"Сбой воркера: {exc}", "error")
            self._set_status(f"Сбой: {exc}")
        finally:
            with self._lock:
                self.running = False
                self.paused = False

    def _commit(self, task: ImageTask, caption: str, result) -> None:
        """Записать капшен, отметить в реестре, продвинуть очередь."""
        if self._stoplist:
            caption = apply_stoplist(caption, self._stoplist)
        final = apply_trigger(caption, self._params.get("trigger_word", ""))
        _write_caption(task.txt_path, caption, self._params.get("trigger_word", ""))
        p = self._params
        self._registry.mark_done(
            task.image_path,
            prompt_hash=prompt_signature(p.get("system_prompt", ""), p.get("user_prompt", "")),
            model=self._client.model if self._client else None,
            caption=final.strip(),
        )
        task.caption = caption
        task.status = "done"
        self.state.processed += 1
        note = "" if result.quality_reason == "ok" else f" ({result.quality_reason})"
        self._log(f"[{task.name}] готово за {result.attempts} попыт.{note}")
        self.state.advance()
        self.state.save_progress()

    def _wait_while_paused(self) -> bool:
        """Спать, пока стоит пауза. True — пришёл stop (надо выходить)."""
        while self._pause_event.is_set() and not self._stop_event.is_set():
            self._stop_event.wait(0.15)
        return self._stop_event.is_set()

    def _handle_review(self, task: ImageTask) -> bool:
        """Выставить задачу на ручное ревью и обработать решение.

        Возвращает True, если во время ревью пришёл stop (нужно выйти из цикла).
        """
        self._review_event.clear()
        self._review_decision = None
        with self._lock:
            self.review_task = task
            self.status_msg = f"Ожидает проверки: {task.name}"

        # Ждём решения, но не спим намертво — реагируем на stop.
        while not self._review_event.wait(0.2):
            if self._stop_event.is_set():
                with self._lock:
                    self.review_task = None
                return True

        decision, edited = self._review_decision or ("skip", "")
        with self._lock:
            self.review_task = None

        if self._stop_event.is_set():
            return True

        if decision == "regenerate":
            task.caption = ""       # заставит сгенерировать заново (индекс не двигаем)
            self._log(f"[{task.name}] перегенерация по запросу")
            return False

        if decision == "skip":
            task.status = "skipped"
            self.state.skipped += 1
            self._log(f"Пропущено вручную: {task.name}")
            self.state.advance()
            self.state.save_progress()
            return False

        # accept | edit — записываем (возможно отредактированный) капшен.
        # Триггер подставляется идемпотентно: если пользователь уже вписал его в
        # правке вручную — повторно не добавится.
        caption = edited if edited.strip() else task.caption
        if self._stoplist:
            caption = apply_stoplist(caption, self._stoplist)
        final = apply_trigger(caption, self._params.get("trigger_word", ""))
        _write_caption(task.txt_path, caption, self._params.get("trigger_word", ""))
        p = self._params
        self._registry.mark_done(
            task.image_path,
            prompt_hash=prompt_signature(p.get("system_prompt", ""), p.get("user_prompt", "")),
            model=self._client.model if self._client else None,
            caption=final.strip(),
        )
        task.caption = caption
        task.status = "done"
        self.state.processed += 1
        self._log(f"{'Правка' if decision == 'edit' else 'Принято'} вручную: {task.name}")
        self.state.advance()
        self.state.save_progress()
        return False

    # ================================================================== #
    # Режим обновления (Фаза 5)
    # ================================================================== #
    def start_update(
        self,
        tasks: list[UpdateTask],
        folder: str,
        params: dict,
        logger: Logger,
        registry: DoneRegistry,
        client: CaptionClient,
    ) -> None:
        """Запустить обновление существующих капшенов в фоновом потоке."""
        if self.is_alive():
            return
        self._update_tasks = tasks
        self._params = dict(params)
        self._logger = logger
        self._registry = registry
        self._client = client

        self._stop_event.clear()
        self._pause_event.clear()
        self._review_event.clear()
        self._stoplist = load_stoplist()
        with self._lock:
            self.running = True
            self.paused = False
            self.finished = False
            self.review_task = None
            self._review_decision = None
            self.status_msg = "Обновление…"
            self.current_name = ""
            self._update_total = len(tasks)
            self._update_done = 0
            self._update_skipped = 0
            self._update_errors = 0
            self._update_deferred = 0
            self._update_diffs = []
            self._start_ts = time.time()

        self._thread = threading.Thread(target=self._run_update, daemon=True)
        self._thread.start()

    def _run_update(self) -> None:
        import json
        from config import (
            AUGMENT_INSTRUCTION,
            DEFERRED_REVIEW_FILE,
            MANUAL_DEFER,
            MANUAL_OVERWRITE,
            MANUAL_PROTECT,
            MANUAL_TAGS_ONLY,
            TAG_STRATEGY_KEEP,
            TAG_STRATEGY_REPLACE,
            TAG_STRATEGY_UNION,
            PROSE_STRATEGY_REPLACE,
            UPDATE_MECH_AUGMENT,
            UPDATE_MECH_FULL,
        )
        from core.dataset import caption_fingerprint

        p = self._params
        mechanism = p.get("update_mechanism", UPDATE_MECH_AUGMENT)
        tag_strategy_cfg = p.get("tag_strategy", TAG_STRATEGY_UNION)
        prose_strategy_cfg = p.get("prose_strategy", "keep_old")
        manual_policy = p.get("manual_policy", MANUAL_PROTECT)
        manual_review = p.get("manual_review", False)
        trigger = p.get("trigger_word", "")
        deferred: list[str] = []

        tag_map = {
            TAG_STRATEGY_UNION: "union",
            TAG_STRATEGY_REPLACE: "replace",
            TAG_STRATEGY_KEEP: "keep",
        }
        prose_map = {
            PROSE_STRATEGY_REPLACE: "replace",
        }
        tag_strat = tag_map.get(tag_strategy_cfg, "union")
        prose_strat = prose_map.get(prose_strategy_cfg, "keep_old")

        cur_prompt_hash = prompt_signature(p.get("system_prompt", ""), p.get("user_prompt", ""))

        try:
            for i, task in enumerate(self._update_tasks):
                if self._stop_event.is_set():
                    break
                if self._pause_event.is_set():
                    if self._wait_while_paused():
                        break
                    continue

                self._set_status(
                    f"Обновление {i + 1} из {self._update_total}: {task.name}",
                    current_name=task.name,
                )

                # Политика ручных правок
                if task.manually_edited:
                    if manual_policy == MANUAL_PROTECT:
                        task.status = "skipped"
                        self._update_skipped += 1
                        self._log(f"[{task.name}] пропущен (ручные правки, защита)")
                        with self._lock:
                            self._update_done += 1
                        continue
                    elif manual_policy == MANUAL_DEFER:
                        deferred.append(task.txt_path)
                        task.status = "skipped"
                        self._update_skipped += 1
                        self._update_deferred += 1
                        self._log(f"[{task.name}] отложен на ручной просмотр")
                        with self._lock:
                            self._update_done += 1
                        continue
                    elif manual_policy == MANUAL_TAGS_ONLY:
                        tag_strat_eff = "union"
                        prose_strat_eff = "keep_old"
                    else:  # MANUAL_OVERWRITE
                        tag_strat_eff = tag_strat
                        prose_strat_eff = prose_strat
                else:
                    tag_strat_eff = tag_strat
                    prose_strat_eff = prose_strat

                # tags_only-защита прозы требует мёржа даже в augment-механизме
                # (иначе свежая проза перезаписала бы ручную). В остальных случаях
                # augment пишем как есть — см. ниже.
                protect_prose = (task.manually_edited
                                 and manual_policy == MANUAL_TAGS_ONLY)

                # augment-промпт (модель видит старый капшен). Считаем один раз:
                # при «перегенерировать» из ревью гоняем тот же промпт заново.
                if mechanism == UPDATE_MECH_AUGMENT:
                    user_prompt = p["user_prompt"] + AUGMENT_INSTRUCTION.format(
                        existing=task.existing_caption.strip()
                    )
                else:
                    user_prompt = p["user_prompt"]

                def on_attempt(n, msg, _name=task.name):
                    self._log(f"[{_name}] попытка {n}: {msg}")

                # Внутренний цикл: «перегенерировать» из ревью возвращает нас сюда.
                stop_all = False
                while True:
                    result = self._client.generate_caption(
                        image_path=task.image_path,
                        system_prompt=p["system_prompt"],
                        user_prompt=user_prompt,
                        temperature=p["temperature"],
                        max_tokens=p["max_tokens"],
                        top_p=p["top_p"],
                        on_attempt=on_attempt,
                        max_caption_retries=None if p.get("auto_retry") else 1,
                        should_stop=lambda: self._stop_event.is_set() or self._pause_event.is_set(),
                        disable_thinking=p.get("disable_thinking", False),
                    )

                    if result.stopped:
                        stop_all = self._stop_event.is_set()
                        break  # пауза/стоп — выходим из внутреннего цикла

                    if not result.success:
                        task.status = "error"
                        task.error = result.error
                        self._update_errors += 1
                        self._log(f"[{task.name}] ошибка: {result.error}", "error")
                        with self._lock:
                            self._update_done += 1
                        break

                    new_raw = result.caption

                    # Сборка результата:
                    #  * FULL — генерация с нуля, мёржим старое+новое по стратегиям;
                    #  * AUGMENT — модель уже вернула улучшённый капшен (видела старый),
                    #    это и есть результат, пишем как есть (как в режиме перезаписи).
                    #    merge_captions с keep_old прозой выбросил бы свежую прозу —
                    #    именно из-за этого augment раньше «терял» описания.
                    #  * tags_only-защита ручной прозы — единственное исключение для
                    #    augment: там прозу надо сохранить, поэтому мёржим.
                    if mechanism == UPDATE_MECH_FULL or protect_prose:
                        body = merge_captions(task.existing_caption, new_raw,
                                              tag_strategy=tag_strat_eff,
                                              prose_strategy=prose_strat_eff)
                    else:
                        body = new_raw

                    if self._stoplist:
                        body = apply_stoplist(body, self._stoplist)

                    final = apply_trigger(body, trigger).strip()

                    # Идемпотентность: не трогать файл, если ничего не изменилось.
                    if caption_fingerprint(final) == caption_fingerprint(task.existing_caption.strip()):
                        task.status = "done"
                        self._log(f"[{task.name}] без изменений (идемпотентно)")
                        with self._lock:
                            self._update_done += 1
                        break

                    # Ручное ревью: показать предложенный капшен и ждать решения.
                    if manual_review:
                        outcome = self._handle_update_review(task, final, cur_prompt_hash)
                        if outcome == "stop":
                            stop_all = True
                            break
                        if outcome == "regenerate":
                            continue  # заново генерируем этот же файл
                        # written | skip — задача завершена
                        break

                    # Автоматический режим: записать сразу.
                    self._write_update(task, final, cur_prompt_hash)
                    self._log(f"[{task.name}] обновлён ({task.reason})")
                    break

                if stop_all:
                    break

            # Сохраняем отложенные на ревью
            if deferred:
                folder = self._registry.folder if self._registry else ""
                review_path = os.path.join(folder, DEFERRED_REVIEW_FILE) if folder else ""
                if review_path:
                    try:
                        existing_deferred = []
                        if os.path.isfile(review_path):
                            with open(review_path, encoding="utf-8") as f:
                                existing_deferred = json.load(f)
                        merged_deferred = list(dict.fromkeys(existing_deferred + deferred))
                        with open(review_path, "w", encoding="utf-8") as f:
                            json.dump(merged_deferred, f, ensure_ascii=False, indent=2)
                    except OSError:
                        pass

            if not self._stop_event.is_set():
                self._set_status("✅ Обновление завершено")
                self._log(f"Обновление завершено: {self._update_done} файлов, "
                          f"пропущено {self._update_skipped}, ошибок {self._update_errors}")
                with self._lock:
                    self.finished = True

        except Exception as exc:
            self._log(f"Сбой воркера (обновление): {exc}", "error")
            self._set_status(f"Сбой: {exc}")
        finally:
            with self._lock:
                self.running = False
                self.paused = False

    def _write_update(self, task: UpdateTask, final: str, prompt_hash: str) -> None:
        """Записать обновлённый капшен: .bak, запись, реестр, дифф, счётчик."""
        try:
            shutil.copy2(task.txt_path, task.txt_path + ".bak")
        except OSError:
            pass

        with open(task.txt_path, "w", encoding="utf-8") as f:
            f.write(final.strip() + "\n")

        self._registry.mark_done(
            task.image_path,
            prompt_hash=prompt_hash,
            model=self._client.model if self._client else None,
            caption=final.strip(),
        )

        task.caption = final.strip()
        task.status = "done"
        with self._lock:
            self._update_done += 1
            if len(self._update_diffs) < 50:
                self._update_diffs.append(
                    (task.name, task.existing_caption.strip(), final.strip()))

    def _handle_update_review(self, task: UpdateTask, proposed: str,
                              prompt_hash: str) -> str:
        """Ручное ревью в режиме обновления. Возвращает исход:
        'stop' | 'regenerate' | 'skip' | 'written'.

        Переиспользует ту же UI-плумбинг, что и обычный режим (review_task /
        submit_review): показываем ПРЕДЛОЖЕННЫЙ капшен (уже с триггером/мёржем),
        пользователь принимает / правит / перегенерирует / пропускает.
        """
        # Показываем предложенный текст через поле task.caption (его читает
        # render_review). Оригинал не теряем — он в task.existing_caption.
        task.caption = proposed.strip()
        self._review_event.clear()
        self._review_decision = None
        with self._lock:
            self.review_task = task
            self.status_msg = f"Ожидает проверки: {task.name}"

        while not self._review_event.wait(0.2):
            if self._stop_event.is_set():
                with self._lock:
                    self.review_task = None
                return "stop"

        decision, edited = self._review_decision or ("skip", "")
        with self._lock:
            self.review_task = None

        if self._stop_event.is_set():
            return "stop"

        if decision == "regenerate":
            self._log(f"[{task.name}] перегенерация по запросу")
            return "regenerate"

        if decision == "skip":
            task.status = "skipped"
            self._update_skipped += 1
            self._log(f"Пропущено вручную: {task.name}")
            with self._lock:
                self._update_done += 1
            return "skip"

        # accept | edit — записываем (возможно отредактированный) капшен.
        # Триггер идемпотентен: если пользователь уже вписал его в правку — не дублируем.
        caption = edited if edited.strip() else task.caption
        if self._stoplist:
            caption = apply_stoplist(caption, self._stoplist)
        final = apply_trigger(caption, self._params.get("trigger_word", "")).strip()
        self._write_update(task, final, prompt_hash)
        self._log(f"{'Правка' if decision == 'edit' else 'Принято'} вручную: {task.name}")
        return "written"
