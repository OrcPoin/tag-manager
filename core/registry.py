"""Реестр картинок, обработанных ИМЕННО этим приложением.

Позволяет докачивать датасет между запусками, не полагаясь на наличие .txt
(в датасете могут лежать чужие старые описания). Реестр — один скрытый файл
`.tagmanager_done.json` в корне папки датасета; ключи — пути картинок
относительно этой папки, значения — размер и дата картинки на момент обработки.

С Фазы 5 запись дополнена ПРОВЕНАНСОМ (чем и когда сделан капшен):
    prompt_hash — подпись system+user промпта на момент генерации;
    model       — какой моделью сгенерировано;
    output_hash — подпись ИМЕННО того текста, что приложение записало в .txt;
    ts          — когда (unix-время).
Провенанс — фундамент «умного обновления»: по нему понятно, устарел ли промпт,
сменилась ли модель и, главное, трогал ли .txt человек после нас (тогда
`output_hash` не совпадёт с хэшем текущего файла → его правки надо защитить).
Все поля опциональны: старые записи без них считаются «провенанс неизвестен»,
ничего не ломается (обратная совместимость).
"""

from __future__ import annotations

import hashlib
import json
import os

REGISTRY_FILENAME = ".tagmanager_done.json"


def _registry_path(folder: str) -> str:
    return os.path.join(folder, REGISTRY_FILENAME)


def _hash_text(text: str) -> str:
    """Стабильная короткая подпись текста (для промпта и содержимого .txt).

    Нормализуем перевод строк и хвостовые пробелы, чтобы CRLF/LF и лишний
    финальный '\\n' не считались «ручной правкой». Внутренний текст — как есть.
    """
    norm = (text or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    return hashlib.sha1(norm.encode("utf-8")).hexdigest()[:16]


def prompt_signature(system_prompt: str, user_prompt: str) -> str:
    """Подпись пары промптов — меняется при любой правке system/user промпта."""
    return _hash_text((system_prompt or "") + "\x00" + (user_prompt or ""))


def caption_signature(caption: str) -> str:
    """Подпись содержимого капшена (сравнима с тем, что лежит в .txt)."""
    return _hash_text(caption)


class DoneRegistry:
    """Загрузка/проверка/пометка обработанных этим приложением картинок."""

    def __init__(self, folder: str):
        self.folder = folder
        self.path = _registry_path(folder)
        self.entries: dict[str, dict] = {}
        self._load()

    def _key(self, image_path: str) -> str:
        try:
            return os.path.relpath(image_path, self.folder).replace("\\", "/")
        except ValueError:
            # Другой диск (Windows) — используем абсолютный путь как запасной ключ.
            return os.path.abspath(image_path).replace("\\", "/")

    def _load(self) -> None:
        if os.path.exists(self.path):
            try:
                with open(self.path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, dict):
                    self.entries = data.get("done", {}) if "done" in data else data
            except (OSError, json.JSONDecodeError):
                self.entries = {}

    def save(self) -> None:
        try:
            with open(self.path, "w", encoding="utf-8") as f:
                json.dump({"done": self.entries}, f, ensure_ascii=False, indent=2)
        except OSError:
            pass

    def is_done(self, image_path: str) -> bool:
        """True, если картинка обработана этим приложением и с тех пор не менялась."""
        entry = self.entries.get(self._key(image_path))
        if not entry:
            return False
        try:
            st = os.stat(image_path)
        except OSError:
            return False
        # Картинку могли заменить — тогда считаем её не обработанной.
        return (entry.get("size") == st.st_size
                and abs(entry.get("mtime", 0) - st.st_mtime) < 1.0)

    def mark_done(
        self,
        image_path: str,
        autosave: bool = True,
        *,
        prompt_hash: str | None = None,
        model: str | None = None,
        caption: str | None = None,
        ts: float | None = None,
    ) -> None:
        """Отметить картинку обработанной. Провенанс — опционально.

        prompt_hash / model — чем сделано; caption — записанный в .txt текст (из
        него считается output_hash для детекта позднейших ручных правок); ts —
        unix-время (по умолчанию текущее). Старые вызовы без kwargs работают.
        """
        try:
            st = os.stat(image_path)
        except OSError:
            return
        entry: dict = {"size": st.st_size, "mtime": st.st_mtime}
        if prompt_hash is not None:
            entry["prompt_hash"] = prompt_hash
        if model is not None:
            entry["model"] = model
        if caption is not None:
            entry["output_hash"] = caption_signature(caption)
        if ts is None:
            import time
            ts = time.time()
        entry["ts"] = ts
        self.entries[self._key(image_path)] = entry
        if autosave:
            self.save()

    # ------------------------------------------------------------------ #
    # Провенанс (Фаза 5) — запросы к записи
    # ------------------------------------------------------------------ #
    def entry(self, image_path: str) -> dict | None:
        """Сырая запись реестра для картинки (или None, если её нет)."""
        return self.entries.get(self._key(image_path))

    def is_known(self, image_path: str) -> bool:
        """True, если картинка вообще есть в реестре (сделана этим приложением)."""
        return self._key(image_path) in self.entries

    def timestamp(self, image_path: str) -> float | None:
        """Когда картинка была обработана (unix-время) или None."""
        e = self.entry(image_path)
        return e.get("ts") if e else None

    def prompt_changed(self, image_path: str, current_hash: str) -> bool:
        """True, если капшен сделан ДРУГИМ промптом (или провенанс неизвестен).

        Неизвестный провенанс трактуем как «мог быть старый промпт» → True, чтобы
        такие капшены попадали в обновление, а не молча пропускались.
        """
        e = self.entry(image_path)
        if not e or "prompt_hash" not in e:
            return True
        return e["prompt_hash"] != current_hash

    def model_changed(self, image_path: str, current_model: str) -> bool:
        """True, если капшен сделан другой моделью (или модель неизвестна)."""
        e = self.entry(image_path)
        if not e or "model" not in e:
            return True
        return e["model"] != current_model

    def was_edited_by_hand(self, image_path: str, txt_text: str) -> bool:
        """True, если текущий .txt отличается от того, что записало приложение.

        Значит, после нас файл трогал человек — его правки надо защитить. Если
        провенанс без output_hash (старая запись) — сказать нельзя, возвращаем
        False (не мешаем обновлению, но и не заявляем ложно о ручной правке).
        """
        e = self.entry(image_path)
        if not e or "output_hash" not in e:
            return False
        return e["output_hash"] != caption_signature(txt_text)

    def count(self) -> int:
        return len(self.entries)
