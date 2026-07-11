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
from core import presets as presets_mod
from core.caption_client import CaptionClient
from core.folder_dialog import pick_folder
from core.image_scanner import ImageTask, build_task_list, find_images, scan_summary
from core.logger import Logger
from core.registry import DoneRegistry
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
    return {
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
    if kind == "add_tag":
        return lambda t: ds.add_tag_to_caption(t, desc[1], desc[2])
    if kind == "del_tag":
        return lambda t: ds.remove_tag_from_caption(t, desc[1])
    return lambda t: t


def _tags_stage(desc: tuple, label: str, files: list) -> None:
    """Посчитать предпросмотр операции и положить в ss.tags_pending (без записи)."""
    prev = ds.preview_operation(files, _tags_build_op(desc))
    ss.tags_pending = {"desc": desc, "label": label, "preview": prev}


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

    col1, col2 = st.columns([5, 1])
    with col1:
        folder = st.text_input("Папка датасета", ss.tags_folder or ss.folder)
    with col2:
        st.write("")
        st.write("")
        recursive = st.checkbox("Рекурсивно", ss.tags_recursive)

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
    op_tabs = st.tabs(["🎯 Триггер", "🔁 Найти/заменить", "➕➖ Тег", "📊 Частоты"])

    with op_tabs[0]:
        trig = st.text_input("Триггер-слово", ss.trigger_word)
        tc = st.columns(2)
        if tc[0].button("Добавить во все", use_container_width=True,
                        disabled=not trig.strip()):
            _tags_stage(("trigger_add", trig), f"Добавить триггер «{trig}»", files)
        if tc[1].button("Убрать из всех", use_container_width=True,
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
            st.dataframe(rows, use_container_width=True, height=380)

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
                        disabled=prev["changed"] == 0, use_container_width=True):
            res = ds.apply_operation(files, _tags_build_op(pend["desc"]),
                                     backup=ss.tags_backup)
            logger.info(f"Массовая правка «{pend['label']}»: изменено "
                        f"{res['changed']}/{res['total']}, ошибок {res['errors']}")
            st.toast(f"Изменено {res['changed']} файлов"
                     + (f", ошибок {res['errors']}" if res["errors"] else ""))
            ss.tags_pending = None
            ss.tags_freq = None
            st.rerun()
        if pc[1].button("Отмена", use_container_width=True):
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
    """Уменьшенное превью (PNG-байты), закэшированное по (path, mtime).

    Ключ включает mtime → при замене картинки миниатюра пересоздаётся. Кеш
    ограничен max_entries, чтобы память не росла на огромных датасетах. Грузим
    и ужимаем ОДИН раз; сетка потом отдаёт готовые байты, не открывая оригинал.
    """
    import io

    from PIL import Image

    try:
        im = Image.open(path)
        im.draft("RGB", (size, size))   # ускоряет декод JPEG (грубее, но быстро)
        im = im.convert("RGB")
        im.thumbnail((size, size))
        buf = io.BytesIO()
        im.save(buf, format="PNG")
        return buf.getvalue()
    except Exception:  # noqa: BLE001 — битый/непонятный файл не должен рушить сетку
        return None


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
    if top[0].button("⬅️ К сетке", use_container_width=True):
        ss.gallery_open = None
        st.rerun()
    if top[1].button("‹ Пред", use_container_width=True, disabled=idx == 0):
        ss.gallery_open = paths[idx - 1]
        st.rerun()
    top[2].markdown(
        f"**{os.path.basename(item['image'])}** &nbsp; · &nbsp; {idx + 1} из {len(items)}"
    )
    if top[3].button("След ›", use_container_width=True, disabled=idx >= len(items) - 1):
        ss.gallery_open = paths[idx + 1]
        st.rerun()

    img_col, edit_col = st.columns([1, 1])
    with img_col:
        if os.path.exists(item["image"]):
            st.image(item["image"], use_container_width=True)
    with edit_col:
        # Ключ привязан к пути — при переходе на другое фото text_area
        # пересоздаётся с капшеном нового файла (а не держит старый текст).
        key = f"gal_edit_{item['image']}"
        edited = st.text_area("Капшен", item["caption"], height=320, key=key)

        busy = worker.is_alive()
        bcols = st.columns(2)
        if bcols[0].button("💾 Сохранить", type="primary", use_container_width=True,
                           disabled=busy):
            if ds.write_caption(item["image"], edited, backup=True):
                item["caption"] = edited.strip()
                item["has_caption"] = bool(item["caption"])
                st.toast("Капшен сохранён")
                st.rerun()
            else:
                st.error("Не удалось записать файл")
        if bcols[1].button("🔄 Перегенерировать", use_container_width=True,
                           disabled=busy,
                           help="Сгенерировать капшен этого фото заново через LLM. "
                                "Займёт столько же, сколько обычная генерация одного файла."):
            task = ImageTask(image_path=item["image"], txt_path=item["txt"])
            # manual_review=False → воркер запишет результат сразу, без паузы.
            params = {**get_params(), "manual_review": False}
            worker.start([task], ss.gallery_folder, params, logger,
                         get_registry(), get_client())
            st.toast("Перегенерация запущена…")
            st.rerun()

        if busy:
            st.caption("⏳ Идёт генерация — сохранение и перегенерация временно "
                       "заблокированы.")
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

    b = st.columns(4)
    if b[0].button("➕ Добавить тег", disabled=not tag.strip(), use_container_width=True):
        ss.gallery_pending = (("add_tag", tag, False), f"Добавить тег «{tag}»", sel_txt)
    if b[1].button("➖ Удалить тег", disabled=not tag.strip(), use_container_width=True):
        ss.gallery_pending = (("del_tag", tag), f"Удалить тег «{tag}»", sel_txt)
    if b[2].button("🎯 +Триггер", disabled=not trig.strip(), use_container_width=True):
        ss.gallery_pending = (("trigger_add", trig), f"Добавить триггер «{trig}»", sel_txt)
    if b[3].button("🗑️ Удалить капшены", use_container_width=True):
        ss.gallery_pending = (("delete", sel_imgs), "Удалить капшены выбранных", sel_txt)

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
        if pc[0].button("✅ Применить", type="primary", use_container_width=True):
            if desc[0] == "delete":
                n = ds.delete_captions(desc[1], backup=True)
                msg = f"Удалено капшенов: {n}"
            else:
                res = ds.apply_operation(target, _tags_build_op(desc), backup=True)
                msg = f"Изменено {res['changed']} файлов"
            logger.info(f"Галерея, мультидействие «{label}»: {msg}")
            st.toast(msg)
            # Обновляем капшены выбранных в памяти, чтобы сетка показала актуальное.
            for it in sel:
                it["caption"] = ds.read_caption(it["image"])
                it["has_caption"] = bool(it["caption"].strip())
            ss.gallery_pending = None
            st.rerun()
        if pc[1].button("Отмена", use_container_width=True):
            ss.gallery_pending = None
            st.rerun()


def _gallery_grid(items: list[dict]) -> None:
    """Сетка миниатюр с пагинацией, чекбоксами выбора и кнопкой «открыть»."""
    total = len(items)
    pages = max(1, (total + GALLERY_PAGE - 1) // GALLERY_PAGE)
    ss.gallery_page = min(ss.gallery_page, pages - 1)

    nav = st.columns([1, 2, 1, 2])
    if nav[0].button("‹", use_container_width=True, disabled=ss.gallery_page == 0):
        ss.gallery_page -= 1
        st.rerun()
    nav[1].markdown(f"<div style='text-align:center'>Стр. {ss.gallery_page + 1} / {pages} "
                    f"· фото: {total}</div>", unsafe_allow_html=True)
    if nav[2].button("›", use_container_width=True, disabled=ss.gallery_page >= pages - 1):
        ss.gallery_page += 1
        st.rerun()
    with nav[3]:
        sc = st.columns(2)
        if sc[0].button("Выбрать стр.", use_container_width=True):
            for it in items[ss.gallery_page * GALLERY_PAGE:(ss.gallery_page + 1) * GALLERY_PAGE]:
                ss.gallery_selected.add(it["image"])
            st.rerun()
        if sc[1].button("Снять выбор", use_container_width=True):
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
                    st.image(thumb, use_container_width=True)
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
                             use_container_width=True):
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

    st.subheader("Галерея — просмотр и правка капшенов")

    c = st.columns([5, 1, 1])
    folder = c[0].text_input("Папка датасета", ss.gallery_folder or ss.folder,
                             key="gallery_folder_input")
    c[1].write("")
    c[1].write("")
    recursive = c[1].checkbox("Рекурсивно", ss.gallery_recursive,
                              key="gallery_recursive_cb")
    c[2].write("")
    c[2].write("")
    if c[2].button("🔍 Сканировать", use_container_width=True):
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
        if st.button("🔄", use_container_width=True, help="Подтянуть активную модель с сервера"):
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

    # Автосохранение «липких» настроек: любое изменение полей выше пишем в
    # settings.json, чтобы при следующем запуске они восстановились сами.
    app_settings.save_settings({k: ss[k] for k in app_settings.PERSISTED_KEYS if k in ss})

    if st.button("🔌 Проверить соединение", use_container_width=True):
        ok, msg = get_client().check_connection()
        (st.success if ok else st.error)(msg)
        logger.info(f"Проверка соединения: {msg}")

    st.divider()
    st.caption("Прогресс")
    if st.button("💾 Продолжить прошлый прогон", use_container_width=True,
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
    if st.button("🗑️ Сбросить прогресс", use_container_width=True,
                 disabled=worker.is_alive()):
        proc.clear_progress()
        st.info("Файл прогресса удалён")


# --------------------------------------------------------------------------- #
# Ручной просмотр текущего файла (режим «проверять каждый капшен вручную»)
# --------------------------------------------------------------------------- #
def render_review(task, preview_col, caption_col) -> None:
    with preview_col:
        st.markdown(f"**Текущий файл:** `{task.name}`")
        if os.path.exists(task.image_path):
            st.image(task.image_path, use_container_width=True)
    with caption_col:
        st.markdown("**Сгенерированный капшен:**")
        edited = st.text_area("Капшен", task.caption, height=220, key="review_caption")
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
# Основная область
# --------------------------------------------------------------------------- #
st.title("🏷️ Tag Manager")
st.caption("Генерация детальных капшенов для изображений через локальный LLM")

tab_gen, tab_gallery, tab_tags = st.tabs(["🤖 Генерация", "🖼️ Галерея", "🏷️ Теги"])

with tab_gallery:
    render_gallery_tab()

with tab_tags:
    render_tags_tab()

with tab_gen:
    # --- Выбор папки и режима ---
    st.subheader("1. Папка и режим")
    col_f1, col_f2 = st.columns([5, 1])
    with col_f1:
        ss.folder = st.text_input("Путь к папке с изображениями", ss.folder)
    with col_f2:
        st.write("")
        st.write("")
        if st.button("📁 Обзор", use_container_width=True):
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
        ss.mode = st.selectbox("Режим обработки", config.PROCESSING_MODES,
                               index=config.PROCESSING_MODES.index(ss.mode))

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
        if st.button("💾 Сохранить пресет", use_container_width=True):
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
        if st.button("🗑️ Удалить пресет", use_container_width=True):
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
        start_clicked = st.button("▶️ Запустить", use_container_width=True,
                                  disabled=running)
    with c2:
        pause_clicked = st.button("⏸️ Пауза", use_container_width=True,
                                  disabled=not running or paused or has_review)
    with c3:
        resume_clicked = st.button("⏵️ Возобновить", use_container_width=True,
                                   disabled=not running or not paused)
    with c4:
        stop_clicked = st.button("⏹️ Остановить", use_container_width=True,
                                 disabled=not running)

    if start_clicked:
        if not os.path.isdir(ss.folder):
            st.error("Укажите существующую папку")
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
    _ratio = snap["done"] / snap["total"] if snap["total"] else 0.0
    st.progress(min(1.0, max(0.0, _ratio)))
    m1, m2, m3, m4, m5 = st.columns(5)
    m1.metric("Всего", snap["total"])
    m2.metric("Обработано", snap["processed"])
    m3.metric("Пропущено", snap["skipped"])
    m4.metric("Ошибок", snap["errors"])
    m5.metric("Готово", f"{snap['done']}/{snap['total']}")

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
    else:
        status_placeholder.write(snap["status_msg"] or "Готов к запуску.")

    # --- Реал-тайм лог ---
    st.subheader("4. Лог")
    st.text_area("processing_log", logger.get_text(), height=220, key="log_view")


# Polling в самом конце скрипта: UI живёт в отдельном потоке от генерации, поэтому
# периодически перерисовываемся — обновляем прогресс/лог/статус и ловим клики по
# «Стоп»/«Пауза». Делаем это ПОСЛЕ отрисовки лога, чтобы он успел обновиться.
if _poll:
    time.sleep(1.0)
    st.rerun()
