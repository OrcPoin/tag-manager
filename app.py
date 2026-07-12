"""Tag Manager — Streamlit-приложение для генерации капшенов к изображениям
через локальный LLM (oobabooga / text-generation-webui, OpenAI-совместимый API).

Запуск:  streamlit run app.py
"""

from __future__ import annotations

import os
import time

import streamlit as st

import config
from core import app_settings
from core import dataset as ds
from core import health
from core import op_history
from core import presets as presets_mod
from core.caption_client import CaptionClient
from core.folder_dialog import pick_folder
from core.image_scanner import ImageTask, UpdateTask, build_task_list, build_update_plan, find_images, scan_summary
from core.logger import Logger
from core.registry import DoneRegistry, prompt_signature
from core.worker import CaptionWorker

st.set_page_config(page_title="Tag Manager", page_icon="🏷️", layout="wide")


# --------------------------------------------------------------------------- #
# Разделяемые между сессиями ресурсы
# --------------------------------------------------------------------------- #
# ВАЖНО: st.session_state привязан к сессии браузера и ОБНУЛЯЕТСЯ при перезагрузке
# страницы (F5). Если держать воркер там, после refresh создаётся НОВЫЙ воркер, а
# старый фоновый поток продолжает молча писать файлы — UI при этом показывает
# «готов к запуску», и по «Запустить» можно породить второй поток на ту же папку.
# @st.cache_resource возвращает singleton на весь процесс сервера, переживающий
# refresh и реконнекты, поэтому после перезагрузки UI подхватывает ТОТ ЖЕ живой
# воркер и его прогресс.
@st.cache_resource
def get_shared_worker() -> CaptionWorker:
    return CaptionWorker()


@st.cache_resource
def get_shared_logger() -> Logger:
    return Logger(config.LOG_FILE)


# --------------------------------------------------------------------------- #
# Инициализация session_state
# --------------------------------------------------------------------------- #
def init_state() -> None:
    ss = st.session_state
    if "initialized" in ss:
        return
    ss.initialized = True
    ss.worker = get_shared_worker()
    ss.logger = get_shared_logger()
    ss.presets = presets_mod.load_presets()

    # Настройки API (дефолты из config)
    ss.api_url = config.DEFAULT_API_URL
    ss.model = config.DEFAULT_MODEL
    ss.temperature = config.DEFAULT_TEMPERATURE
    ss.max_tokens = config.DEFAULT_MAX_TOKENS
    ss.top_p = config.DEFAULT_TOP_P
    ss.timeout = config.DEFAULT_TIMEOUT
    ss.auto_retry = True        # авто-перегенерация при «плохом» капшене (~10 мин/повтор)
    ss.manual_review = False    # проверять каждый капшен вручную перед записью
    ss.disable_thinking = config.DEFAULT_DISABLE_THINKING  # выключить размышления модели
    ss.trigger_word = config.DEFAULT_TRIGGER_WORD  # триггер стиля, подставляется первым тегом
    ss.notify_on_finish = True  # браузерное уведомление по завершении

    # Накладываем сохранённые «липкие» настройки поверх дефолтов, чтобы не
    # переставлять галки/слайдеры при каждом запуске (settings.json).
    for k, v in app_settings.load_settings().items():
        ss[k] = v

    # Подтягиваем активную модель с сервера ПОСЛЕ восстановления api_url (сервер
    # обычно уже поднят). Тихо игнорируем недоступность — останется сохранённое.
    _detected = CaptionClient(
        base_url=ss.api_url, api_key=config.DEFAULT_API_KEY,
        model=ss.model, timeout=10.0,
    ).active_model()
    if _detected:
        ss.model = _detected

    # Папка / режим
    ss.folder = ""
    ss.recursive = False
    ss.mode = config.PROCESSING_MODES[0]

    # Настройки обновления (Фаза 5)
    ss.update_mechanism = config.DEFAULT_UPDATE_MECHANISM
    ss.tag_strategy = config.DEFAULT_TAG_STRATEGY
    ss.prose_strategy = config.DEFAULT_PROSE_STRATEGY
    ss.manual_policy = config.DEFAULT_MANUAL_POLICY
    ss.upd_filter_prompt = True
    ss.upd_filter_model = False
    ss.upd_filter_quality = False
    ss.upd_filter_all = False

    # Промпты
    first_preset = next(iter(ss.presets))
    ss.preset_name = first_preset
    ss.system_prompt = ss.presets[first_preset]["system"]
    ss.user_prompt = ss.presets[first_preset]["user"]

    # Реестр текущей папки и служебное
    ss.scan_info = None
    ss.registry = None          # DoneRegistry текущей папки (реестр «сделано этим приложением»)


init_state()
ss = st.session_state
worker: CaptionWorker = ss.worker
proc = worker.state
logger: Logger = ss.logger

# После refresh (F5) session_state пуст, но воркер (singleton) может всё ещё
# обрабатывать папку. Восстанавливаем путь из живого состояния, чтобы UI не
# выглядел «сброшенным» и кнопки продолжения работали с правильной папкой.
if not ss.folder and proc.folder:
    ss.folder = proc.folder


def get_client() -> CaptionClient:
    return CaptionClient(
        base_url=ss.api_url,
        api_key=config.DEFAULT_API_KEY,
        model=ss.model,
        timeout=ss.timeout,
    )


def get_params() -> dict:
    """Снапшот параметров генерации для передачи в воркер."""
    params = {
        "system_prompt": ss.system_prompt,
        "user_prompt": ss.user_prompt,
        "temperature": ss.temperature,
        "max_tokens": ss.max_tokens,
        "top_p": ss.top_p,
        "auto_retry": ss.auto_retry,
        "manual_review": ss.manual_review,
        "disable_thinking": ss.disable_thinking,
        "trigger_word": ss.trigger_word,
    }
    if ss.get("mode") == config.MODE_UPDATE:
        params.update({
            "update_mechanism": ss.get("update_mechanism", config.DEFAULT_UPDATE_MECHANISM),
            "tag_strategy": ss.get("tag_strategy", config.DEFAULT_TAG_STRATEGY),
            "prose_strategy": ss.get("prose_strategy", config.DEFAULT_PROSE_STRATEGY),
            "manual_policy": ss.get("manual_policy", config.DEFAULT_MANUAL_POLICY),
        })
    return params


def get_registry() -> DoneRegistry:
    """Реестр «сделано этим приложением» для текущей папки (кэшируется в session_state)."""
    if ss.registry is None or ss.registry.folder != ss.folder:
        ss.registry = DoneRegistry(ss.folder)
    return ss.registry


# --------------------------------------------------------------------------- #
# Вкладка «Теги»: массовые правки готового датасета
# --------------------------------------------------------------------------- #
# Операция описывается примитивным дескриптором (кортеж), а не лямбдой в
# session_state — так предпросмотр и применение переживают rerun и строят один
# и тот же колбэк из одних данных.
def _tags_build_op(desc: tuple):
    kind = desc[0]
    if kind == "trigger_add":
        return lambda t: ds.apply_trigger(t, desc[1])
    if kind == "trigger_del":
        return lambda t: ds.remove_trigger(t, desc[1])
    if kind == "replace_tag":
        return lambda t: ds.replace_whole_tag(t, desc[1], desc[2])
    if kind == "replace_sub":
        return lambda t: ds.replace_substring(t, desc[1], desc[2])
    if kind == "sanitize":
        return lambda t: ds.sanitize_caption(
            t, dedupe=desc[1], collapse_spaces=desc[2], lowercase=desc[3]
        )
    if kind == "add_tag":
        return lambda t: ds.add_tag_to_caption(t, desc[1], desc[2])
    if kind == "del_tag":
        return lambda t: ds.remove_tag_from_caption(t, desc[1])
    if kind == "stoplist":
        from core.stoplist import apply_stoplist as _apply_sl
        return lambda t: _apply_sl(t, desc[1])
    return lambda t: t


def _tags_stage(desc: tuple, label: str, files: list) -> None:
    """Посчитать предпросмотр операции и положить в ss.tags_pending (без записи)."""
    prev = ds.preview_operation(files, _tags_build_op(desc))
    ss.tags_pending = {"desc": desc, "label": label, "preview": prev}


def _browse_into(input_key: str) -> None:
    """Открыть системный диалог и записать выбранную папку в ss[input_key].

    Единая логика кнопки «📁 Обзор» для всех вкладок. ВАЖНО: вызывается как
    on_click-колбэк, а не инлайн. Колбэк выполняется ДО инстанцирования виджетов
    в следующем прогоне, поэтому запись в ключ виджета ввода легальна (инлайн-
    запись после создания text_input Streamlit запрещает). Пустой результат
    (диалог недоступен/отменён) — мягкий тост, поле не трогаем.
    """
    picked = pick_folder(ss.get(input_key, ""))
    if picked:
        ss[input_key] = picked
    else:
        st.toast("Диалог недоступен — введите путь вручную")


def _folder_picker_row(input_key: str, rec_key: str, rec_default: bool,
                       default_folder: str) -> tuple[str, bool]:
    """Общий ряд выбора папки: текстовый путь + «📁 Обзор» + «Рекурсивно».

    Используется на вкладках «Теги» и «Здоровье» (у «Генерации»/«Галереи» свои
    компоновки). Ключи виджетов уникальны между вкладками. Путь из диалога пишем
    через on_click-колбэк (см. _browse_into). vertical_alignment="bottom"
    выравнивает кнопку/галку по нижней кромке поля (иначе они уезжают вверх под
    подпись поля). Возврат: (путь, рекурсивно).
    """
    ss.setdefault(input_key, default_folder)
    c1, c2, c3 = st.columns([5, 1, 1], vertical_alignment="bottom")
    with c1:
        folder = st.text_input("Папка датасета", key=input_key)
    with c2:
        st.button("📁 Обзор", key=f"{input_key}_browse", width="stretch",
                  on_click=_browse_into, args=(input_key,))
    with c3:
        recursive = st.checkbox("Рекурсивно", rec_default, key=rec_key)
    return folder, recursive


def render_tags_tab() -> None:
    ss.setdefault("tags_folder", "")
    ss.setdefault("tags_recursive", False)
    ss.setdefault("tags_files", [])
    ss.setdefault("tags_freq", None)
    ss.setdefault("tags_pending", None)
    ss.setdefault("tags_backup", True)

    st.subheader("Массовые правки тегов готового датасета")

    # Пока идёт генерация, воркер сам пишет .txt — параллельная массовая правка
    # затёрла бы результаты. Блокируем вкладку до остановки обработки.
    if worker.is_alive():
        st.warning("Идёт генерация капшенов. Массовые правки заблокированы, чтобы "
                   "не конфликтовать с записью файлов — остановите обработку.")
        return

    folder, recursive = _folder_picker_row(
        "tags_folder_input", "tags_rec", ss.tags_recursive,
        ss.tags_folder or ss.folder)

    if st.button("🔍 Сканировать датасет"):
        if os.path.isdir(folder):
            ss.tags_folder = folder
            ss.tags_recursive = recursive
            ss.tags_files = ds.find_caption_files(folder, recursive)
            ss.tags_freq = None
            ss.tags_pending = None
            st.toast(f"Капшенов найдено: {len(ss.tags_files)}")
        else:
            st.error("Папка не найдена")

    files = ss.tags_files
    if not files:
        st.info("Укажите папку датасета и нажмите «Сканировать датасет».")
        return

    # --- сводка по датасету ---
    summ = ds.dataset_summary(ss.tags_folder, ss.tags_recursive, ss.trigger_word)
    mc = st.columns(4)
    mc[0].metric("Капшенов", summ["captions"])
    mc[1].metric("Картинок без капшена", summ["images_no_caption"])
    if ss.trigger_word.strip():
        mc[2].metric("С триггером", summ["with_trigger"])
        mc[3].metric("Без триггера", summ["without_trigger"])
    else:
        mc[2].metric(".bak копий", summ["backups"])

    st.divider()
    op_tabs = st.tabs(["🎯 Триггер", "🔁 Найти/заменить", "➕➖ Тег",
                       "📊 Частоты", "📜 История"])

    with op_tabs[0]:
        trig = st.text_input("Триггер-слово", ss.trigger_word)
        tc = st.columns(2)
        if tc[0].button("Добавить во все", width="stretch",
                        disabled=not trig.strip()):
            _tags_stage(("trigger_add", trig), f"Добавить триггер «{trig}»", files)
        if tc[1].button("Убрать из всех", width="stretch",
                        disabled=not trig.strip()):
            _tags_stage(("trigger_del", trig), f"Убрать триггер «{trig}»", files)
        st.caption("Ретрофит триггера к уже готовым капшенам. Идемпотентно: "
                   "повторное добавление не дублирует, убирается только если стоит первым.")

    with op_tabs[1]:
        mode = st.radio(
            "Режим", ["Точный тег", "Подстрока"], horizontal=True,
            help="Точный тег — меняет тег целиком только в тег-строках, проза не "
                 "тронута. Подстрока — грубая замена по всему тексту (для опечаток).",
        )
        find = st.text_input("Найти")
        repl = st.text_input("Заменить на")
        if st.button("Предпросмотр замены", disabled=not find.strip()):
            if mode == "Точный тег":
                _tags_stage(("replace_tag", find, repl), f"Тег «{find}» → «{repl}»", files)
            else:
                _tags_stage(("replace_sub", find, repl),
                            f"Подстрока «{find}» → «{repl}»", files)

        st.divider()
        st.markdown("**Чистка тегов** — нормализация тег-строк (проза не тронута)")
        sc = st.columns(3)
        s_dedupe = sc[0].checkbox("Убрать дубли", True)
        s_ws = sc[1].checkbox("Схлопнуть пробелы", True)
        s_lower = sc[2].checkbox("В нижний регистр", False)
        if st.button("Предпросмотр чистки",
                     disabled=not (s_dedupe or s_ws or s_lower)):
            _tags_stage(("sanitize", s_dedupe, s_ws, s_lower), "Чистка тегов", files)
        st.caption("Дубли — по совпадению без учёта регистра, остаётся первый. "
                   "«Схлопнуть пробелы» убирает двойные пробелы внутри тега. "
                   "Пустые фрагменты и лишние запятые убираются всегда.")

        st.divider()
        st.markdown("**Стоп-лист** — удалить нежелательные теги из всего датасета")
        from core.stoplist import load_stoplist as _load_sl2
        _sl = _load_sl2()
        if _sl:
            st.caption(f"Тегов в стоп-листе: {len(_sl)} (редактировать в сайдбаре)")
            if st.button("Применить стоп-лист к датасету"):
                _tags_stage(("stoplist", frozenset(_sl)),
                            f"Стоп-лист ({len(_sl)} тегов)", files)
        else:
            st.caption("Стоп-лист пуст. Добавьте теги в сайдбаре → «Стоп-лист тегов».")

    with op_tabs[2]:
        ac = st.columns(2)
        with ac[0]:
            add_tag = st.text_input("Добавить тег")
            at_start = st.checkbox("В начало (первым тегом)")
            if st.button("Предпросмотр добавления", disabled=not add_tag.strip()):
                _tags_stage(("add_tag", add_tag, at_start),
                            f"Добавить тег «{add_tag}»", files)
        with ac[1]:
            del_tag = st.text_input("Удалить тег")
            if st.button("Предпросмотр удаления", disabled=not del_tag.strip()):
                _tags_stage(("del_tag", del_tag), f"Удалить тег «{del_tag}»", files)
        st.caption("Добавление идёт в первую тег-строку (идемпотентно). Удаление "
                   "убирает точный тег из всех тег-строк, прозу не трогает.")

    with op_tabs[3]:
        if st.button("Посчитать частоты"):
            ss.tags_freq = ds.tag_frequencies(files)
        if ss.tags_freq:
            counter, read = ss.tags_freq
            fc = st.columns([2, 1])
            asc = fc[0].checkbox("Редкие сверху (искать опечатки/мусор)")
            n = fc[1].number_input("Строк", 5, 1000, 40, 5)
            items = counter.most_common()
            if asc:
                items = items[::-1]
            rows = [{"тег": t, "файлов": c} for t, c in items[: int(n)]]
            st.caption(f"Уникальных тегов: {len(counter)} · прочитано файлов: {read}")
            st.dataframe(rows, width="stretch", height=380)

    with op_tabs[4]:
        hist = op_history.load_history(ss.tags_folder) if ss.tags_folder else []
        if not hist:
            st.caption("История операций пуста.")
        else:
            st.caption("Откат работает по `.bak`. Если после операции был ещё один "
                       "прогон — `.bak` перезаписан новым.")
            for rec in reversed(hist[-10:]):
                st.text(f"[{rec.ts}]  {rec.label}  ({len(rec.files)} файлов)")
            if st.button("↩️ Откатить последнюю операцию", width="stretch"):
                n_restored, lbl = op_history.rollback_last(ss.tags_folder)
                if n_restored:
                    st.toast(f"Откачено {n_restored} файлов: «{lbl}»")
                    ss.tags_freq = None
                    st.rerun()
                else:
                    st.warning("Нечего откатывать (нет .bak)")

    # --- общая staged-область: предпросмотр + применение ---
    pend = ss.tags_pending
    if pend:
        prev = pend["preview"]
        st.divider()
        st.markdown(f"### Предпросмотр — {pend['label']}")
        line = (f"Затронет **{prev['changed']}** из {prev['total']} файлов")
        if prev["unreadable"]:
            line += f" · нечитаемых: {prev['unreadable']}"
        st.write(line)
        for name, before, after in prev["samples"]:
            with st.expander(name):
                dc = st.columns(2)
                dc[0].text_area("до", before, height=150, disabled=True,
                                key=f"prev_before_{name}")
                dc[1].text_area("после", after, height=150, disabled=True,
                                key=f"prev_after_{name}")
        ss.tags_backup = st.checkbox("Сделать .bak перед записью (страховка)",
                                     ss.tags_backup)
        pc = st.columns(2)
        if pc[0].button("✅ Применить", type="primary",
                        disabled=prev["changed"] == 0, width="stretch"):
            res = ds.apply_operation(files, _tags_build_op(pend["desc"]),
                                     backup=ss.tags_backup)
            logger.info(f"Массовая правка «{pend['label']}»: изменено "
                        f"{res['changed']}/{res['total']}, ошибок {res['errors']}")
            if res["changed"] > 0 and ss.tags_folder:
                op_history.log_operation(
                    ss.tags_folder,
                    f"{pend['label']} → {res['changed']} файлов",
                    files,
                )
            st.toast(f"Изменено {res['changed']} файлов"
                     + (f", ошибок {res['errors']}" if res["errors"] else ""))
            ss.tags_pending = None
            ss.tags_freq = None
            st.rerun()
        if pc[1].button("Отмена", width="stretch"):
            ss.tags_pending = None
            st.rerun()

    # --- откат последней правки ---
    baks = ds.count_backups(files)
    if baks:
        st.divider()
        if st.button(f"↩️ Откатить последнюю правку (.bak → .txt, {baks} шт.)"):
            restored = ds.restore_backups(files)
            logger.info(f"Откат из .bak: {restored} файлов")
            st.toast(f"Откачено {restored} файлов")
            ss.tags_freq = None
            ss.tags_pending = None
            st.rerun()


# --------------------------------------------------------------------------- #
# Вкладка «Галерея»: просмотр/правка по одному фото + мультидействия
# --------------------------------------------------------------------------- #
GALLERY_COLS = 6          # миниатюр в ряд
GALLERY_PAGE = 24         # миниатюр на страницу (кратно колонкам)
THUMB_PX = 200            # размер миниатюры (длинная сторона)


@st.cache_data(show_spinner=False, max_entries=4096)
def _thumbnail(path: str, mtime: float, size: int = THUMB_PX) -> bytes | None:
    """Уменьшенное превью (PNG-байты), с дисковым кэшем в .thumbs/.

    Порядок: RAM-кэш (@st.cache_data) → дисковый кэш (.thumbs/*.png) → PIL.
    Дисковый кэш переживает перезапуск приложения — повторный вход в галерею
    мгновенный даже на сотнях картинок.
    """
    import io

    from PIL import Image

    cache_dir = os.path.join(os.path.dirname(path), config.THUMBS_DIR)
    stem = os.path.splitext(os.path.basename(path))[0]
    cache_path = os.path.join(cache_dir, stem + ".png")

    try:
        if os.path.isfile(cache_path) and os.path.getmtime(cache_path) >= mtime:
            with open(cache_path, "rb") as f:
                return f.read()
    except OSError:
        pass

    try:
        im = Image.open(path)
        im.draft("RGB", (size, size))
        im = im.convert("RGB")
        im.thumbnail((size, size))
        buf = io.BytesIO()
        im.save(buf, format="PNG")
        data = buf.getvalue()
    except Exception:
        return None

    try:
        os.makedirs(cache_dir, exist_ok=True)
        with open(cache_path, "wb") as f:
            f.write(data)
    except OSError:
        pass

    return data


@st.cache_data(show_spinner=False, max_entries=8192)
def _probe_cached(path: str, mtime: float, size: int) -> dict:
    """Кэш пер-файлового probe+hash по (path, mtime, size).

    Повторный скан датасета пересчитывает только изменившиеся файлы — на тысячах
    картинок это разница между «секунды» и «десятки секунд». mtime/size в ключе
    гарантируют инвалидацию при подмене файла.
    """
    return health.probe_and_hash(path, mtime, size)


def _gallery_filtered() -> list[dict]:
    """Применить фильтры «без капшена»/поиск к ss.gallery_all (in-memory, быстро)."""
    items = ss.gallery_all
    if ss.gallery_only_missing:
        items = [it for it in items if not it["has_caption"]]
    needle = ss.gallery_search.strip().lower()
    if needle:
        items = [it for it in items if needle in it["caption"].lower()]
    return items


def _gallery_scan(folder: str, recursive: bool) -> None:
    """Читает список изображений и капшены ОДИН раз, кладёт в session_state.

    Дальше фильтрация/поиск идут по памяти, диск на ререндерах не трогается —
    это и держит UI отзывчивым на больших датасетах.
    """
    ss.gallery_folder = folder
    ss.gallery_recursive = recursive
    ss.gallery_all = ds.list_gallery(folder, recursive)
    ss.gallery_page = 0
    ss.gallery_open = None
    ss.gallery_selected = set()
    ss.gallery_pending = None


def _gallery_editor(items: list[dict]) -> None:
    """Полноэкранный редактор одного фото с навигацией ‹ ›."""
    # Находим позицию открытого фото в ТЕКУЩЕМ отфильтрованном списке.
    paths = [it["image"] for it in items]
    if ss.gallery_open not in paths:
        # Фото выпало из фильтра (напр. капшен добавлен) — выходим в сетку.
        ss.gallery_open = None
        st.rerun()
    idx = paths.index(ss.gallery_open)
    item = items[idx]

    top = st.columns([1, 1, 4, 1])
    if top[0].button("⬅️ К сетке", width="stretch"):
        ss.gallery_open = None
        st.rerun()
    if top[1].button("‹ Пред", width="stretch", disabled=idx == 0):
        ss.gallery_open = paths[idx - 1]
        st.rerun()
    top[2].markdown(
        f"**{os.path.basename(item['image'])}** &nbsp; · &nbsp; {idx + 1} из {len(items)}"
    )
    if top[3].button("След ›", width="stretch", disabled=idx >= len(items) - 1):
        ss.gallery_open = paths[idx + 1]
        st.rerun()

    img_col, edit_col = st.columns([1, 1])
    with img_col:
        if os.path.exists(item["image"]):
            st.image(item["image"], width="stretch")
    with edit_col:
        # Ключ привязан к пути + nonce — при переходе на другое фото ИЛИ после
        # перегенерации (nonce++) text_area пересоздаётся со свежим капшеном
        # (а не держит старый текст своего ключа).
        key = f"gal_edit_{ss.gallery_edit_nonce}_{item['image']}"
        edited = st.text_area("Капшен", item["caption"], height=320, key=key)

        busy = worker.is_alive()
        bcols = st.columns(2)
        if bcols[0].button("💾 Сохранить", type="primary", width="stretch",
                           disabled=busy):
            if ds.write_caption(item["image"], edited, backup=True):
                item["caption"] = edited.strip()
                item["has_caption"] = bool(item["caption"])
                st.toast("Капшен сохранён")
                st.rerun()
            else:
                st.error("Не удалось записать файл")
        if bcols[1].button("🔄 Перегенерировать", width="stretch",
                           disabled=busy,
                           help="Сгенерировать капшен этого фото заново через LLM. "
                                "Займёт столько же, сколько обычная генерация одного файла."):
            task = ImageTask(image_path=item["image"], txt_path=item["txt"])
            # manual_review=False → воркер запишет результат сразу, без паузы.
            params = {**get_params(), "manual_review": False}
            worker.start([task], ss.gallery_folder, params, logger,
                         get_registry(), get_client())
            ss.gallery_regen = {item["image"]}  # подхватим новый .txt по завершении
            st.toast("Перегенерация запущена…")
            st.rerun()

        if busy:
            st.caption("⏳ Идёт генерация — сохранение и перегенерация временно "
                       "заблокированы.")

        # Предупреждения по капшену
        _warns = ds.caption_warnings(edited, ss.trigger_word)
        for _w in _warns:
            st.warning(_w)

        # Показ тегов чипами для наглядности.
        tags = ds.extract_tags(item["caption"])
        if tags:
            st.caption("Теги: " + "  ".join(f"`{t}`" for t in tags[:40]))


def _gallery_multiaction(items: list[dict]) -> None:
    """Панель действий над выбранными фото (мультивыбор)."""
    sel = [it for it in items if it["image"] in ss.gallery_selected]
    st.markdown(f"**Выбрано: {len(sel)}**")
    if not sel:
        st.caption("Отметьте фото галочками, чтобы применить действие к нескольким сразу.")
        return

    sel_txt = [it["txt"] for it in sel]
    sel_imgs = [it["image"] for it in sel]
    ac = st.columns([2, 2, 1])
    tag = ac[0].text_input("Тег для добавления/удаления", key="gal_ma_tag")
    trig = ac[1].text_input("Триггер", ss.trigger_word, key="gal_ma_trig")

    b = st.columns(5)
    if b[0].button("➕ Добавить тег", disabled=not tag.strip(), width="stretch"):
        ss.gallery_pending = (("add_tag", tag, False), f"Добавить тег «{tag}»", sel_txt)
    if b[1].button("➖ Удалить тег", disabled=not tag.strip(), width="stretch"):
        ss.gallery_pending = (("del_tag", tag), f"Удалить тег «{tag}»", sel_txt)
    if b[2].button("🎯 +Триггер", disabled=not trig.strip(), width="stretch"):
        ss.gallery_pending = (("trigger_add", trig), f"Добавить триггер «{trig}»", sel_txt)
    if b[3].button("🗑️ Удалить капшены", width="stretch"):
        ss.gallery_pending = (("delete", sel_imgs), "Удалить капшены выбранных", sel_txt)
    _busy = worker.is_alive()
    if b[4].button("🔄 Перегенерировать", width="stretch", disabled=_busy):
        tasks = [ImageTask(image_path=it["image"], txt_path=it["txt"]) for it in sel]
        params = {**get_params(), "manual_review": False}
        worker.start(tasks, ss.gallery_folder, params, logger,
                     get_registry(), get_client())
        ss.gallery_regen = {it["image"] for it in sel}  # обновим по завершении
        st.toast(f"Перегенерация {len(tasks)} файлов запущена…")
        ss.gallery_pending = None
        ss.gallery_selected = set()
        st.rerun()

    pend = ss.gallery_pending
    if pend:
        desc, label, target = pend
        st.divider()
        st.markdown(f"### Предпросмотр — {label}")
        if desc[0] == "delete":
            st.write(f"Будет удалено **{len(desc[1])}** .txt (с .bak-копией).")
        else:
            prev = ds.preview_operation(target, _tags_build_op(desc))
            st.write(f"Затронет **{prev['changed']}** из {prev['total']} файлов.")
            for name, before, after in prev["samples"][:4]:
                with st.expander(name):
                    dc = st.columns(2)
                    dc[0].text_area("до", before, height=120, disabled=True,
                                    key=f"gma_b_{name}")
                    dc[1].text_area("после", after, height=120, disabled=True,
                                    key=f"gma_a_{name}")
        pc = st.columns(2)
        if pc[0].button("✅ Применить", type="primary", width="stretch"):
            if desc[0] == "delete":
                n = ds.delete_captions(desc[1], backup=True)
                msg = f"Удалено капшенов: {n}"
            else:
                res = ds.apply_operation(target, _tags_build_op(desc), backup=True)
                msg = f"Изменено {res['changed']} файлов"
            logger.info(f"Галерея, мультидействие «{label}»: {msg}")
            if ss.gallery_folder:
                op_history.log_operation(ss.gallery_folder, f"{label}: {msg}", target)
            st.toast(msg)
            # Обновляем капшены выбранных в памяти, чтобы сетка показала актуальное.
            for it in sel:
                it["caption"] = ds.read_caption(it["image"])
                it["has_caption"] = bool(it["caption"].strip())
            ss.gallery_pending = None
            st.rerun()
        if pc[1].button("Отмена", width="stretch"):
            ss.gallery_pending = None
            st.rerun()


def _gallery_grid(items: list[dict]) -> None:
    """Сетка миниатюр с пагинацией, чекбоксами выбора и кнопкой «открыть»."""
    total = len(items)
    pages = max(1, (total + GALLERY_PAGE - 1) // GALLERY_PAGE)
    ss.gallery_page = min(ss.gallery_page, pages - 1)

    nav = st.columns([1, 2, 1, 2])
    if nav[0].button("‹", width="stretch", disabled=ss.gallery_page == 0):
        ss.gallery_page -= 1
        st.rerun()
    nav[1].markdown(f"<div style='text-align:center'>Стр. {ss.gallery_page + 1} / {pages} "
                    f"· фото: {total}</div>", unsafe_allow_html=True)
    if nav[2].button("›", width="stretch", disabled=ss.gallery_page >= pages - 1):
        ss.gallery_page += 1
        st.rerun()
    with nav[3]:
        sc = st.columns(2)
        if sc[0].button("Выбрать стр.", width="stretch"):
            for it in items[ss.gallery_page * GALLERY_PAGE:(ss.gallery_page + 1) * GALLERY_PAGE]:
                ss.gallery_selected.add(it["image"])
            st.rerun()
        if sc[1].button("Снять выбор", width="stretch"):
            ss.gallery_selected = set()
            st.rerun()

    start = ss.gallery_page * GALLERY_PAGE
    page_items = items[start:start + GALLERY_PAGE]

    # Рисуем ТОЛЬКО текущую страницу → максимум GALLERY_PAGE миниатюр за ререндер.
    for row_start in range(0, len(page_items), GALLERY_COLS):
        row = page_items[row_start:row_start + GALLERY_COLS]
        cols = st.columns(GALLERY_COLS)
        for col, it in zip(cols, row):
            with col:
                try:
                    mt = os.path.getmtime(it["image"])
                except OSError:
                    mt = 0.0
                thumb = _thumbnail(it["image"], mt)
                if thumb is not None:
                    st.image(thumb, width="stretch")
                else:
                    st.caption("🖼️ (нет превью)")
                mark = "✅" if it["has_caption"] else "⚠️"
                checked = it["image"] in ss.gallery_selected
                new_checked = st.checkbox(
                    f"{mark} {os.path.basename(it['image'])[:16]}",
                    value=checked, key=f"gal_sel_{it['image']}",
                )
                if new_checked and not checked:
                    ss.gallery_selected.add(it["image"])
                elif not new_checked and checked:
                    ss.gallery_selected.discard(it["image"])
                if st.button("✏️ Открыть", key=f"gal_open_{it['image']}",
                             width="stretch"):
                    ss.gallery_open = it["image"]
                    ss.gallery_pending = None
                    st.rerun()


def render_gallery_tab() -> None:
    ss.setdefault("gallery_folder", "")
    ss.setdefault("gallery_recursive", False)
    ss.setdefault("gallery_all", [])
    ss.setdefault("gallery_page", 0)
    ss.setdefault("gallery_open", None)
    ss.setdefault("gallery_selected", set())
    ss.setdefault("gallery_only_missing", False)
    ss.setdefault("gallery_search", "")
    ss.setdefault("gallery_pending", None)
    ss.setdefault("gallery_regen", set())   # пути, отданные на перегенерацию
    ss.setdefault("gallery_edit_nonce", 0)  # сброс text_area редактора после refresh

    # Если галерея запускала перегенерацию и воркер закончил — капшены в памяти
    # (ss.gallery_all) устарели: воркер записал новый .txt на диск, а список мы
    # читали один раз при скане. Подтягиваем свежие капшены только для затронутых
    # файлов и «пересобираем» text_area редактора через nonce (иначе виджет
    # держит старый текст по своему ключу).
    if ss.gallery_regen and not worker.is_alive():
        by_path = {it["image"]: it for it in ss.gallery_all}
        refreshed = 0
        for img in ss.gallery_regen:
            it = by_path.get(img)
            if it is None:
                continue
            it["caption"] = ds.read_caption(img)
            it["has_caption"] = bool(it["caption"].strip())
            refreshed += 1
        ss.gallery_regen = set()
        ss.gallery_edit_nonce += 1
        if refreshed:
            st.toast(f"Капшены обновлены: {refreshed}")

    st.subheader("Галерея — просмотр и правка капшенов")

    ss.setdefault("gallery_folder_input", ss.gallery_folder or ss.folder)
    c = st.columns([5, 1, 1, 1], vertical_alignment="bottom")
    folder = c[0].text_input("Папка датасета", key="gallery_folder_input")
    c[1].button("📁 Обзор", key="gallery_browse", width="stretch",
                on_click=_browse_into, args=("gallery_folder_input",))
    recursive = c[2].checkbox("Рекурсивно", ss.gallery_recursive,
                              key="gallery_recursive_cb")
    if c[3].button("🔍 Сканировать", width="stretch"):
        if os.path.isdir(folder):
            _gallery_scan(folder, recursive)
            st.toast(f"Изображений: {len(ss.gallery_all)}")
            st.rerun()
        else:
            st.error("Папка не найдена")

    if not ss.gallery_all:
        st.info("Укажите папку и нажмите «Сканировать».")
        return

    # Фильтры (in-memory, быстрые даже на тысячах файлов).
    fc = st.columns([1, 3])
    ss.gallery_only_missing = fc[0].checkbox("Только без капшена",
                                             ss.gallery_only_missing)
    ss.gallery_search = fc[1].text_input("Поиск по тексту капшена (тег/слово)",
                                         ss.gallery_search)

    items = _gallery_filtered()
    if not items:
        st.warning("Под фильтр ничего не подходит.")
        return

    # Открытое фото → редактор; иначе сетка + панель мультидействий.
    if ss.gallery_open:
        _gallery_editor(items)
    else:
        _gallery_grid(items)
        st.divider()
        with st.expander(f"⚙️ Действия над выбранными ({len(ss.gallery_selected)})",
                         expanded=bool(ss.gallery_selected)):
            _gallery_multiaction(items)


def _fmt_duration(seconds: float) -> str:
    """Человекочитаемая длительность: '< 1 мин' / '~12 мин' / '~2 ч 5 мин'."""
    if seconds < 60:
        return "< 1 мин"
    m = int(seconds) // 60
    h = m // 60
    if h == 0:
        return f"~{m} мин"
    return f"~{h} ч {m % 60} мин"


# --------------------------------------------------------------------------- #
# Sidebar — настройки
# --------------------------------------------------------------------------- #
with st.sidebar:
    st.header("⚙️ Настройки API")
    ss.api_url = st.text_input("API URL", ss.api_url)
    mcol1, mcol2 = st.columns([4, 1])
    with mcol1:
        ss.model = st.text_input("Модель", ss.model,
                                 help="Заполняется активной моделью сервера. "
                                      "🔄 — обновить с текущего API URL.")
    with mcol2:
        st.write("")
        st.write("")
        if st.button("🔄", width="stretch", help="Подтянуть активную модель с сервера"):
            detected = get_client().active_model()
            if detected:
                ss.model = detected
                st.toast(f"Модель: {detected}")
                st.rerun()
            else:
                st.toast("Сервер недоступен — модель не получена")
    ss.temperature = st.slider("Temperature", 0.0, 2.0, float(ss.temperature), 0.05)
    ss.top_p = st.slider("Top-p", 0.0, 1.0, float(ss.top_p), 0.05)
    ss.max_tokens = st.number_input("Max tokens (для thinking-моделей ставьте больше)",
                                    16, 16384, int(ss.max_tokens), 128)
    ss.timeout = st.number_input("Таймаут запроса (сек)", 30, 1800, int(ss.timeout), 30)
    ss.auto_retry = st.checkbox(
        "Авто-ретрай при плохом капшене",
        value=ss.auto_retry,
        help="Перегенерировать, если капшен слишком короткий / только теги / с залипанием. "
             "Каждый повтор ~10 мин. Выключите, если теговый стиль вас устраивает.",
    )
    ss.manual_review = st.checkbox(
        "Проверять каждый капшен вручную",
        value=ss.manual_review,
        help="После генерации каждого файла обработка приостановится и покажет капшен "
             "для ручного решения: принять / правка / перегенерировать / пропустить.",
    )
    ss.disable_thinking = st.checkbox(
        "Отключить размышления (thinking)",
        value=ss.disable_thinking,
        help="Просит модель не выводить блок рассуждений: сильно ускоряет генерацию "
             "и убирает пустые ответы «лимит ушёл на размышления». Шлём известные "
             "серверу переключатели (enable_thinking=false, reasoning_budget=0) и "
             "текстовые маркеры для Qwen/Gemma. Модель без thinking просто игнорирует.",
    )
    ss.notify_on_finish = st.checkbox(
        "Уведомлять о завершении",
        value=ss.notify_on_finish,
        help="Браузерное уведомление (Web Notification) когда прогон завершён. "
             "Удобно, если ушли от вкладки на время обработки.",
    )

    # Автосохранение «липких» настроек: любое изменение полей выше пишем в
    # settings.json, чтобы при следующем запуске они восстановились сами.
    app_settings.save_settings({k: ss[k] for k in app_settings.PERSISTED_KEYS if k in ss})

    with st.expander("Стоп-лист тегов"):
        from core.stoplist import load_stoplist as _load_sl, save_stoplist as _save_sl
        _sl_path = config.STOPLIST_FILE
        _sl_current = ""
        if os.path.isfile(_sl_path):
            try:
                with open(_sl_path, encoding="utf-8") as _f:
                    _sl_current = _f.read()
            except OSError:
                pass
        _sl_edited = st.text_area(
            "Один тег на строку, # = комментарий",
            _sl_current, height=120, key="stoplist_edit",
        )
        _sl_tags = _load_sl(_sl_path)
        st.caption(f"Тегов в стоп-листе: {len(_sl_tags)}")
        if st.button("Сохранить стоп-лист", width="stretch"):
            _save_sl(_sl_edited, _sl_path)
            st.toast("Стоп-лист сохранён")

    if st.button("🔌 Проверить соединение", width="stretch"):
        ok, msg = get_client().check_connection()
        (st.success if ok else st.error)(msg)
        logger.info(f"Проверка соединения: {msg}")

    st.divider()
    st.caption("Прогресс")
    if st.button("💾 Продолжить прошлый прогон", width="stretch",
                 disabled=worker.is_alive()):
        if proc.load_progress():
            # Восстанавливаем папку и СРАЗУ продолжаем цикл с сохранённого места
            # в фоновом воркере (▶️ Запустить пересобрал бы список с нуля).
            ss.folder = proc.folder
            if not proc.is_finished():
                worker.start_resumed(get_params(), logger, get_registry(), get_client())
            st.success(f"Восстановлено: {proc.done_count}/{proc.total} — продолжаю")
            st.rerun()
        else:
            st.warning("Сохранённый прогресс не найден")
    if st.button("🗑️ Сбросить прогресс", width="stretch",
                 disabled=worker.is_alive()):
        proc.clear_progress()
        st.info("Файл прогресса удалён")

    st.divider()
    with st.expander("📥 Экспорт конфига для тренера"):
        from core.export import export_kohya_toml, export_onetrainer
        _exp_fmt = st.selectbox("Формат", ["OneTrainer (JSON)", "kohya (TOML)"],
                                key="export_fmt")
        _exp_rep = st.number_input("Repeats", 1, 100, 10, key="export_rep")
        _exp_res = st.number_input("Resolution", 256, 2048, 512, 64,
                                   key="export_res")
        if ss.folder and os.path.isdir(ss.folder):
            if "OneTrainer" in _exp_fmt:
                _data = export_onetrainer(ss.folder, ss.trigger_word,
                                          _exp_rep, _exp_res)
                _fname = "dataset.json"
            else:
                _data = export_kohya_toml(ss.folder, ss.trigger_word,
                                          _exp_rep, _exp_res)
                _fname = "dataset.toml"
            st.download_button("📥 Скачать конфиг", _data, _fname,
                               width="stretch")
        else:
            st.caption("Укажите папку на вкладке «Генерация».")


# --------------------------------------------------------------------------- #
# Ручной просмотр текущего файла (режим «проверять каждый капшен вручную»)
# --------------------------------------------------------------------------- #
def render_review(task, preview_col, caption_col) -> None:
    with preview_col:
        st.markdown(f"**Текущий файл:** `{task.name}`")
        if os.path.exists(task.image_path):
            st.image(task.image_path, width="stretch")
    with caption_col:
        st.markdown("**Сгенерированный капшен:**")
        # Streamlit-виджет с фиксированным key держит своё значение в session_state
        # и ИГНОРИРУЕТ аргумент value при перерисовке — поэтому при переходе на новый
        # файл (или после «Перегенерировать») поле показывало бы старый текст. Ручная
        # синхронизация через session_state оказалась хрупкой: между файлами панель
        # ревью ~10 мин не рисуется (has_review=False, пока генерится следующий), и
        # Streamlit подчищает ключ review_caption как «исчезнувший виджет», рассинхронив
        # его с guard-ключом. Поэтому — как в галерее (nonce в ключе): при смене
        # (файл, капшен) наращиваем nonce, ключ становится новым → рождается свежий
        # виджет, чей value=task.caption честно применяется. Пока пользователь правит
        # текст, (файл, капшен) не меняется → ключ стабилен → правки не затираются.
        ident = (task.name, task.caption)
        if ss.get("_review_ident") != ident:
            ss["_review_ident"] = ident
            ss["_review_nonce"] = ss.get("_review_nonce", 0) + 1
        edited = st.text_area(
            "Капшен", task.caption, height=220,
            key=f"review_caption_{ss.get('_review_nonce', 0)}",
        )
        b1, b2, b3, b4 = st.columns(4)
        if b1.button("✅ Принять"):
            worker.submit_review("accept", edited)
            time.sleep(0.3)
            st.rerun()
        if b2.button("🔄 Перегенерировать"):
            worker.submit_review("regenerate")
            time.sleep(0.3)
            st.rerun()
        if b3.button("✏️ Сохранить правку"):
            worker.submit_review("edit", edited)
            time.sleep(0.3)
            st.rerun()
        if b4.button("⏭️ Пропустить"):
            worker.submit_review("skip")
            time.sleep(0.3)
            st.rerun()


# --------------------------------------------------------------------------- #
# Вкладка «Здоровье датасета»: аудит перед обучением + карантин
# --------------------------------------------------------------------------- #
def _health_thumbs(paths: list[str], cols: int = 6, limit: int = 24) -> None:
    """Сетка миниатюр для списка путей (обрезается до limit)."""
    shown = paths[:limit]
    for row_start in range(0, len(shown), cols):
        row = st.columns(cols)
        for i, path in enumerate(shown[row_start:row_start + cols]):
            with row[i]:
                try:
                    data = _thumbnail(path, os.path.getmtime(path))
                except OSError:
                    data = None
                if data:
                    st.image(data, caption=os.path.basename(path))
                else:
                    st.caption(os.path.basename(path))
    if len(paths) > limit:
        st.caption(f"…и ещё {len(paths) - limit}")


def _health_quarantine(paths: list[str], reason: str) -> None:
    """Перенести paths в карантин и сбросить скан (счётчики устареют)."""
    moved = health.quarantine(paths, ss.health_folder, reason)
    ss.health = None
    st.toast(f"В карантин перенесено: {moved} (папка _rejected/{reason}/)")


def render_health_tab() -> None:
    ss.setdefault("health_folder", "")
    ss.setdefault("health_recursive", False)
    ss.setdefault("health", None)

    st.subheader("Аудит датасета перед обучением")

    if worker.is_alive():
        st.warning("Идёт генерация капшенов. Аудит и карантин заблокированы, чтобы "
                   "не конфликтовать с записью файлов — остановите обработку.")
        return

    folder, recursive = _folder_picker_row(
        "health_folder_input", "health_rec", ss.health_recursive,
        ss.health_folder or ss.folder)

    if st.button("🩺 Сканировать датасет"):
        if not os.path.isdir(folder):
            st.error("Папка не найдена")
        else:
            images = find_images(folder, recursive)
            probes: dict[str, dict] = {}
            bar = st.progress(0.0, "Сканирование…")
            for i, path in enumerate(images, 1):
                try:
                    stt = os.stat(path)
                    probes[path] = _probe_cached(path, stt.st_mtime, stt.st_size)
                except OSError as exc:
                    probes[path] = {"ok": False, "error": str(exc), "md5": None,
                                    "dhash": None, "mode": "", "animated": False,
                                    "width": 0, "height": 0, "format": ""}
                if i % 25 == 0 or i == len(images):
                    bar.progress(i / max(1, len(images)),
                                 f"Сканирование… {i}/{len(images)}")
            bar.empty()
            ss.health_folder = folder
            ss.health_recursive = recursive
            ss.health = {
                "images": images,
                "probes": probes,
                "broken": [p for p, pr in probes.items() if not pr.get("ok")],
                "exact": health.group_exact(
                    {p: pr.get("md5") for p, pr in probes.items()}),
                "orphans": health.orphan_captions(folder, recursive),
                "collisions": health.stem_collisions(images),
                "captions": health.caption_issues(images, ss.trigger_word),
                "formats": health.format_issues(probes),
            }
            st.toast(f"Просканировано картинок: {len(images)}")

    data = ss.health
    if not data:
        st.info("Укажите папку датасета и нажмите «Сканировать датасет».")
        return

    probes = data["probes"]
    images = data["images"]

    # --- 1. Сводка ---
    with st.expander("📋 Сводка", expanded=True):
        good = [p for p in images if probes.get(p, {}).get("ok")]
        captioned = sum(1 for p in images if ds.read_caption(p).strip())
        total_bytes = 0
        dims = []
        for p in good:
            pr = probes[p]
            dims.append((pr["width"], pr["height"]))
            try:
                total_bytes += os.path.getsize(p)
            except OSError:
                pass
        mc = st.columns(4)
        mc[0].metric("Картинок", len(images))
        mc[1].metric("С капшеном", captioned)
        cover = f"{100 * captioned / len(images):.0f}%" if images else "—"
        mc[2].metric("Покрытие", cover)
        mc[3].metric("Размер на диске", f"{total_bytes / (1 << 20):.1f} МБ")
        if dims:
            ws = sorted(w for w, _ in dims)
            hs = sorted(h for _, h in dims)
            med = (ws[len(ws) // 2], hs[len(hs) // 2])
            st.caption(f"Разрешение (инфо): min {min(ws)}×{min(hs)}, "
                       f"медиана {med[0]}×{med[1]}, max {max(ws)}×{max(hs)}.")
        if len(images) < config.HEALTH_MIN_DATASET:
            st.warning(f"Картинок меньше {config.HEALTH_MIN_DATASET} — маловато для "
                       "устойчивого обучения LoRA.")
        if data["collisions"]:
            st.warning(f"Коллизии имён (одно имя, разные расширения → делят один "
                       f".txt): {len(data['collisions'])}. Разберите в «Дубли».")

    # --- 2. Битые / нечитаемые ---
    broken = data["broken"]
    with st.expander(f"🧨 Битые / нечитаемые — {len(broken)}"):
        if not broken:
            st.success("Битых файлов не найдено.")
        else:
            for p in broken[:50]:
                st.text(f"{os.path.basename(p)} — {probes[p].get('error', '')}")
            if len(broken) > 50:
                st.caption(f"…и ещё {len(broken) - 50}")
            if st.button("В карантин все битые", key="q_broken"):
                _health_quarantine(broken, "broken")
                st.rerun()

    # --- 3. Сироты ---
    orphans = data["orphans"]
    with st.expander(f"👻 Сироты (.txt без картинки) — {len(orphans)}"):
        if not orphans:
            st.success("Осиротевших .txt нет.")
        else:
            for p in orphans[:50]:
                st.text(os.path.basename(p))
            if len(orphans) > 50:
                st.caption(f"…и ещё {len(orphans) - 50}")
            if st.button("В карантин все сироты", key="q_orphans"):
                _health_quarantine(orphans, "orphan_txt")
                st.rerun()

    # --- 4. Дубли ---
    with st.expander(f"👯 Дубли — точных {len(data['exact'])}"):
        thr = st.slider("Порог похожести (Hamming dhash)", 0, 16,
                        config.DUP_HAMMING_THRESHOLD, key="dup_thr",
                        help="Меньше — строже (только очень похожие). Больше — ловит "
                             "и слабо похожие, но растёт риск ложных совпадений.")
        near = health.group_near(
            {p: pr.get("dhash") for p, pr in probes.items() if pr.get("ok")}, thr)
        st.caption(f"Точные дубли (md5): {len(data['exact'])} групп · "
                   f"Похожие (dhash ≤ {thr}): {len(near)} групп.")

        def _dup_groups(groups: list[list[str]], key_prefix: str) -> None:
            for gi, group in enumerate(groups):
                st.markdown(f"**Группа {gi + 1}** — {len(group)} шт. "
                            f"(оставляем первый, остальные → карантин)")
                _health_thumbs(group, cols=6, limit=12)
                if st.button("Лишние → карантин", key=f"{key_prefix}_{gi}"):
                    _health_quarantine(group[1:], "duplicates")
                    st.rerun()

        if data["exact"]:
            st.markdown("##### Точные (одинаковые байты)")
            _dup_groups(data["exact"], "q_exact")
        if near:
            st.markdown("##### Похожие (перцептивно)")
            _dup_groups(near, "q_near")
        if not data["exact"] and not near:
            st.success("Дублей не найдено.")

    # --- 5. Здоровье капшенов ---
    ci = data["captions"]
    total_issues = sum(len(v) for v in ci.values())
    with st.expander(f"📝 Здоровье капшенов — проблем {total_issues}"):
        labels = {
            "empty": "Пустые / нет .txt", "short": "Слишком короткие",
            "only_tags": "Только теги", "too_long": "Слишком длинные",
            "missing_trigger": "Без триггера", "unreadable": "Не читаются (не UTF-8)",
        }
        for key, label in labels.items():
            paths = ci.get(key, [])
            if not paths:
                continue
            st.markdown(f"**{label}** — {len(paths)}")
            st.caption("  ".join(os.path.basename(p) for p in paths[:20])
                       + (" …" if len(paths) > 20 else ""))
        if ci.get("missing_trigger") and ss.trigger_word.strip():
            if st.button(f"Проставить триггер «{ss.trigger_word}» этим файлам",
                         key="fix_trigger"):
                op = lambda t: ds.apply_trigger(t, ss.trigger_word)  # noqa: E731
                res = ds.apply_operation(
                    [ds.txt_path_for(p) for p in ci["missing_trigger"]],
                    op, backup=True)
                st.toast(f"Обновлено файлов: {res['changed']}")
                ss.health = None
                st.rerun()
        if ci.get("empty"):
            st.caption("Пустые капшены удобно дозаполнить на вкладке «Галерея» "
                       "(фильтр «без капшена») или прогнать генерацию.")
        if total_issues == 0:
            st.success("Проблем с капшенами не найдено.")

    # --- 6. Формат / цвет ---
    fmt = data["formats"]
    with st.expander(f"🎨 Формат / цвет — non-RGB {len(fmt['non_rgb'])}, "
                     f"анимаций {len(fmt['animated'])}"):
        if fmt["non_rgb"]:
            st.markdown(f"**Не-RGB** — {len(fmt['non_rgb'])} "
                        "(RGBA/L/P/CMYK: цвет/альфа могут поехать при обучении)")
            st.caption("  ".join(os.path.basename(p) for p in fmt["non_rgb"][:20]))
            if st.button("Конвертировать все в RGB (оригиналы в _rejected/nonrgb/)",
                         key="fix_rgb"):
                n = sum(health.convert_to_rgb(p, ss.health_folder)
                        for p in fmt["non_rgb"])
                st.toast(f"Сконвертировано: {n}")
                ss.health = None
                st.rerun()
        if fmt["animated"]:
            st.markdown(f"**Анимированные** — {len(fmt['animated'])} "
                        "(тренер возьмёт только первый кадр)")
            st.caption("  ".join(os.path.basename(p) for p in fmt["animated"][:20]))
            if st.button("Анимации → карантин", key="q_anim"):
                _health_quarantine(fmt["animated"], "animated")
                st.rerun()
        if not fmt["non_rgb"] and not fmt["animated"]:
            st.success("Проблем с форматом/цветом не найдено.")


# --------------------------------------------------------------------------- #
# Основная область
# --------------------------------------------------------------------------- #
st.title("🏷️ Tag Manager")
st.caption("Генерация детальных капшенов для изображений через локальный LLM")

tab_gen, tab_gallery, tab_tags, tab_health = st.tabs(
    ["🤖 Генерация", "🖼️ Галерея", "🏷️ Теги", "🩺 Здоровье"])

with tab_gallery:
    render_gallery_tab()

with tab_tags:
    render_tags_tab()

with tab_health:
    render_health_tab()

with tab_gen:
    # --- Выбор папки и режима ---
    st.subheader("1. Папка и режим")
    col_f1, col_f2 = st.columns([5, 1], vertical_alignment="bottom")
    with col_f1:
        ss.folder = st.text_input("Путь к папке с изображениями", ss.folder)
    with col_f2:
        if st.button("📁 Обзор", width="stretch"):
            picked = pick_folder(ss.folder)
            if picked:
                ss.folder = picked
                st.rerun()
            else:
                st.toast("Диалог недоступен — введите путь вручную")

    col_m1, col_m2 = st.columns([2, 3])
    with col_m1:
        ss.recursive = st.checkbox("Рекурсивно (включая подпапки)", ss.recursive)
    with col_m2:
        st.selectbox("Режим обработки", config.UI_MODES, key="mode")

    # --- Настройки обновления (только для MODE_UPDATE) ---
    if ss.mode == config.MODE_UPDATE:
        with st.expander("⚙️ Настройки обновления", expanded=True):
            uc1, uc2 = st.columns(2)
            with uc1:
                ss.update_mechanism = st.selectbox(
                    "Механизм", config.UPDATE_MECHANISMS,
                    index=config.UPDATE_MECHANISMS.index(
                        ss.get("update_mechanism", config.DEFAULT_UPDATE_MECHANISM)),
                    help="«Дополнить» — модель видит старый капшен и дописывает "
                         "недостающее (дешевле). «Полная регенерация» — генерит "
                         "с нуля, потом мёржит по стратегии.",
                )
                ss.tag_strategy = st.selectbox(
                    "Теги", config.TAG_STRATEGIES,
                    index=config.TAG_STRATEGIES.index(
                        ss.get("tag_strategy", config.DEFAULT_TAG_STRATEGY)),
                )
            with uc2:
                ss.prose_strategy = st.selectbox(
                    "Проза", config.PROSE_STRATEGIES,
                    index=config.PROSE_STRATEGIES.index(
                        ss.get("prose_strategy", config.DEFAULT_PROSE_STRATEGY)),
                )
                ss.manual_policy = st.selectbox(
                    "Ручные правки", config.MANUAL_POLICIES,
                    index=config.MANUAL_POLICIES.index(
                        ss.get("manual_policy", config.DEFAULT_MANUAL_POLICY)),
                    help="Что делать с капшенами, которые редактировал "
                         "человек после генерации.",
                )
            st.markdown("**Фильтры — какие файлы обновлять:**")
            fc1, fc2, fc3, fc4 = st.columns(4)
            with fc1:
                ss.upd_filter_prompt = st.checkbox("Устаревший промпт",
                                                   ss.get("upd_filter_prompt", True))
            with fc2:
                ss.upd_filter_model = st.checkbox("Сменилась модель",
                                                  ss.get("upd_filter_model", False))
            with fc3:
                ss.upd_filter_quality = st.checkbox("Плохое качество",
                                                    ss.get("upd_filter_quality", False))
            with fc4:
                ss.upd_filter_all = st.checkbox("Все файлы",
                                                ss.get("upd_filter_all", False))

    if st.button("🔍 Сканировать"):
        if os.path.isdir(ss.folder):
            ss.scan_info = scan_summary(ss.folder, ss.recursive)
            reg = get_registry()
            imgs = find_images(ss.folder, ss.recursive)
            ss.scan_info["done_by_app"] = sum(1 for p in imgs if reg.is_done(p))
        else:
            ss.scan_info = None
            st.error("Папка не найдена")

    if ss.scan_info:
        s = ss.scan_info
        st.info(f"Найдено изображений: **{s['total']}**  ·  "
                f"с капшенами: **{s['with_caption']}**  ·  "
                f"без капшенов: **{s['missing']}**  ·  "
                f"сделано этим приложением: **{s.get('done_by_app', 0)}**")

    # --- Пресеты и промпты ---
    st.subheader("2. Пресет и промпты")
    preset_names = list(ss.presets.keys())
    col_p1, col_p2, col_p3 = st.columns([3, 1, 1])
    with col_p1:
        chosen = st.selectbox("Пресет", preset_names,
                              index=preset_names.index(ss.preset_name)
                              if ss.preset_name in preset_names else 0)
        if chosen != ss.preset_name:
            ss.preset_name = chosen
            ss.system_prompt = ss.presets[chosen]["system"]
            ss.user_prompt = ss.presets[chosen]["user"]
            st.rerun()

    ss.system_prompt = st.text_area("System Prompt", ss.system_prompt, height=90)
    ss.user_prompt = st.text_area("User Prompt", ss.user_prompt, height=200)
    ss.trigger_word = st.text_input(
        "Триггер-слово стиля (style-LoRA)",
        ss.trigger_word,
        help="Подставляется ПЕРВЫМ тегом в каждый .txt, одинаково во всём датасете. "
             "Модель его не пишет (перевирала бы) — добавляем программно при записи. "
             "Стиль оседает в этом слове; поэтому промт НЕ описывает стиль. "
             "Пусто = не добавлять.",
    )

    col_sp1, col_sp2, col_sp3 = st.columns([2, 1, 1])
    with col_sp1:
        new_preset_name = st.text_input("Имя пресета для сохранения", ss.preset_name)
    with col_sp2:
        st.write("")
        st.write("")
        if st.button("💾 Сохранить пресет", width="stretch"):
            try:
                presets_mod.save_preset(new_preset_name, ss.system_prompt, ss.user_prompt)
                ss.presets = presets_mod.load_presets()
                ss.preset_name = new_preset_name
                st.success(f"Пресет «{new_preset_name}» сохранён")
            except ValueError as e:
                st.error(str(e))
    with col_sp3:
        st.write("")
        st.write("")
        if st.button("🗑️ Удалить пресет", width="stretch"):
            if presets_mod.delete_preset(ss.preset_name):
                ss.presets = presets_mod.load_presets()
                st.success("Пресет удалён")
                st.rerun()
            else:
                st.warning("Встроенный пресет удалить нельзя")

    # ----------------------------------------------------------------------- #
    # Управление обработкой
    # ----------------------------------------------------------------------- #
    st.subheader("3. Обработка")

    snap = worker.snapshot()
    running = snap["running"]
    paused = snap["paused"]
    has_review = snap["has_review"]

    c1, c2, c3, c4 = st.columns(4)
    with c1:
        start_clicked = st.button("▶️ Запустить", width="stretch",
                                  disabled=running)
    with c2:
        pause_clicked = st.button("⏸️ Пауза", width="stretch",
                                  disabled=not running or paused or has_review)
    with c3:
        resume_clicked = st.button("⏵️ Возобновить", width="stretch",
                                   disabled=not running or not paused)
    with c4:
        stop_clicked = st.button("⏹️ Остановить", width="stretch",
                                 disabled=not running)

    if start_clicked:
        ss._notified = False
        if not os.path.isdir(ss.folder):
            st.error("Укажите существующую папку")
        elif ss.mode == config.MODE_UPDATE:
            registry = get_registry()
            cur_hash = prompt_signature(ss.system_prompt, ss.user_prompt)
            filters = {
                "prompt_changed": ss.get("upd_filter_prompt", True),
                "model_changed": ss.get("upd_filter_model", False),
                "quality": ss.get("upd_filter_quality", False),
                "all": ss.get("upd_filter_all", False),
            }
            update_tasks = build_update_plan(
                ss.folder, ss.recursive, registry,
                current_prompt_hash=cur_hash,
                current_model=ss.model,
                filters=filters,
            )
            if not update_tasks:
                st.warning("Нет файлов для обновления по выбранным фильтрам")
            else:
                manual_count = sum(1 for t in update_tasks if t.manually_edited)
                logger.info(f"Старт обновления: {len(update_tasks)} файлов "
                            f"(из них ручных правок: {manual_count})")
                worker.start_update(update_tasks, ss.folder, get_params(),
                                    logger, registry, get_client())
                st.rerun()
        else:
            tasks = build_task_list(ss.folder, ss.recursive, ss.mode, get_registry())
            if not tasks:
                st.warning("Нет файлов для обработки в выбранном режиме")
            else:
                logger.info(f"Старт обработки: {len(tasks)} файлов, режим «{ss.mode}»")
                worker.start(tasks, ss.folder, get_params(), logger,
                             get_registry(), get_client())
                st.rerun()

    if pause_clicked:
        worker.pause()          # мгновенный обрыв генерации на сервере + удержание
        st.rerun()

    if resume_clicked:
        worker.resume()
        st.rerun()

    if stop_clicked:
        worker.stop()           # обрыв генерации + завершение потока + сохранение
        st.rerun()

    # --- Прогресс и статистика ---
    # clamp в [0,1]: при рассинхроне сохранённого прогресса (index > len(tasks))
    # отношение может выйти за 1.0 и уронить st.progress — не даём этому случиться.
    if snap["update_total"] > 0:
        _ratio = snap["update_done"] / snap["update_total"]
        st.progress(min(1.0, max(0.0, _ratio)))
        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Всего", snap["update_total"])
        m2.metric("Обновлено", snap["update_done"] - snap["update_skipped"] - snap["update_errors"])
        m3.metric("Пропущено", snap["update_skipped"])
        m4.metric("Ошибок", snap["update_errors"])
    else:
        _ratio = snap["done"] / snap["total"] if snap["total"] else 0.0
        st.progress(min(1.0, max(0.0, _ratio)))
        m1, m2, m3, m4, m5 = st.columns(5)
        m1.metric("Всего", snap["total"])
        m2.metric("Обработано", snap["processed"])
        m3.metric("Пропущено", snap["skipped"])
        m4.metric("Ошибок", snap["errors"])
        m5.metric("Готово", f"{snap['done']}/{snap['total']}")

    # ETA и скорость
    _done_n = snap["update_done"] if snap["update_total"] > 0 else snap["done"]
    _total_n = snap["update_total"] if snap["update_total"] > 0 else snap["total"]
    _elapsed = time.time() - snap["start_ts"] if snap["start_ts"] else 0.0
    if running and _done_n > 0 and _elapsed > 5:
        _avg = _elapsed / _done_n
        _remaining = (_total_n - _done_n) * _avg
        ec = st.columns(3)
        ec[0].metric("Скорость", f"{_avg:.0f} с/файл")
        ec[1].metric("Прошло", _fmt_duration(_elapsed))
        ec[2].metric("Осталось", _fmt_duration(_remaining))

    status_placeholder = st.empty()
    preview_col, caption_col = st.columns(2)

    # --- Отображение состояния + polling ---
    _poll = False
    if has_review:
        review = worker.get_review()
        if review is not None:
            render_review(review, preview_col, caption_col)
        else:
            _poll = True  # ревью уже закрылось — обновимся в конце
    elif running:
        status_placeholder.info(snap["status_msg"])
        _poll = True
    elif snap["finished"]:
        status_placeholder.success(snap["status_msg"] or "✅ Обработка завершена")
        if ss.get("notify_on_finish") and not ss.get("_notified"):
            import streamlit.components.v1 as stc
            stc.html(
                '<script>'
                'if(Notification.permission==="granted")'
                '  new Notification("Tag Manager",{body:"Обработка завершена!"});'
                'else if(Notification.permission!=="denied")'
                '  Notification.requestPermission();'
                '</script>',
                height=0,
            )
            ss._notified = True
        # Диффы обновления
        _diffs = snap.get("update_diffs", [])
        if _diffs:
            # В ключ виджета вплетаем таймстемп прогона: без него при повторном
            # обновлении тех же файлов key совпал бы с прошлым разом и text_area
            # показал бы залипшее старое значение (Streamlit игнорирует value у
            # keyed-виджета). Нонс = свежая идентичность виджета на каждый прогон.
            _nonce = int(snap.get("start_ts", 0))
            with st.expander(f"📝 Что изменилось ({len(_diffs)} файлов)"):
                for _dn, _old, _new in _diffs[:30]:
                    st.markdown(f"**{_dn}**")
                    _dc = st.columns(2)
                    _dc[0].text_area("до", _old, height=120, disabled=True,
                                     key=f"diff_old_{_nonce}_{_dn}")
                    _dc[1].text_area("после", _new, height=120, disabled=True,
                                     key=f"diff_new_{_nonce}_{_dn}")
        # Отложенные на ручной просмотр (политика «Отложить»). Показываем список,
        # иначе выбор этой политики был бы невидим — файлы копятся в скрытом JSON.
        if snap.get("update_deferred", 0) and ss.folder:
            _rev_path = os.path.join(ss.folder, config.DEFERRED_REVIEW_FILE)
            _deferred: list[str] = []
            if os.path.isfile(_rev_path):
                try:
                    import json as _json
                    with open(_rev_path, encoding="utf-8") as _rf:
                        _deferred = _json.load(_rf)
                except (OSError, ValueError):
                    _deferred = []
            with st.expander(f"🔎 Отложено на ручной просмотр ({len(_deferred)})"):
                st.caption("Эти капшены правил человек — обновление их не трогало. "
                           "Проверьте вручную (список в "
                           f"`{config.DEFERRED_REVIEW_FILE}`).")
                for _dp in _deferred[:100]:
                    st.text(os.path.basename(_dp))
    else:
        status_placeholder.write(snap["status_msg"] or "Готов к запуску.")

    # --- Реал-тайм лог ---
    st.subheader("4. Лог")
    st.text_area("processing_log", logger.get_text(), height=220, key="log_view")


# Polling в самом конце скрипта: UI живёт в отдельном потоке от генерации, поэтому
# периодически перерисовываемся — обновляем прогресс/лог/статус и ловим клики по
# «Стоп»/«Пауза». Делаем это ПОСЛЕ отрисовки лога, чтобы он успел обновиться.
# `gallery_regen` в условии: держим polling живым, пока галерея не подхватила
# свежие капшены после перегенерации. Иначе есть узкое окно (воркер уже выставил
# running=False, но поток ещё не умер → is_alive() True), где генерация перестаёт
# поллить, а галерея пропускает refresh — и капшен снова «застревает» старым.
if _poll or ss.get("gallery_regen"):
    time.sleep(1.0)
    st.rerun()
