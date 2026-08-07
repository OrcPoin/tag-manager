"""Tag Manager — Streamlit-приложение для генерации капшенов к изображениям
через локальный LLM (oobabooga / text-generation-webui, OpenAI-совместимый API).

Запуск:  streamlit run app.py

Этот файл — тонкий оркестратор: singleton-ресурсы, инициализация session_state и
раскладка вкладок. Вся отрисовка вынесена в пакет `ui/` (по модулю на вкладку),
логика — в `core/`.
"""

from __future__ import annotations

import time

import streamlit as st

import config
from core import app_settings
from core import presets as presets_mod
from core.inference.external_api import ExternalApiBackend
from core.inference.profiles import load_backend_profiles
from core.logger import Logger
from core.worker import CaptionWorker
from core.taggers.manager import TaggerManager
from core.preview import PreviewRunner
from ui.generation import render_generation_tab
from ui.gallery import render_gallery_tab
from ui.health import render_health_tab
from ui.sidebar import render_sidebar
from ui.tags import render_tags_tab
from ui.workspace import render_workspace
from ui.runs import render_runs
from ui.diagnostics import render_diagnostics
from ui.library import render_library
from ui.design import inject_design_system, top_bar

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


@st.cache_resource
def get_shared_tagger_manager() -> TaggerManager:
    return TaggerManager()


@st.cache_resource
def get_shared_preview_runner() -> PreviewRunner:
    return PreviewRunner()


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
    ss.tagger_manager = get_shared_tagger_manager()
    ss.preview_runner = get_shared_preview_runner()
    ss.presets = presets_mod.load_presets()
    ss.backend_profiles = load_backend_profiles()
    ss.backend_profile_name = ""

    # Настройки API (дефолты из config)
    ss.backend_type = config.DEFAULT_BACKEND_TYPE
    ss.api_url = config.DEFAULT_API_URL
    ss.model = config.DEFAULT_MODEL
    ss.llama_executable = config.DEFAULT_LLAMA_EXECUTABLE
    ss.model_directory = config.DEFAULT_MODEL_DIRECTORY
    ss.mmproj_directory = config.DEFAULT_MMPROJ_DIRECTORY
    ss.llama_model = config.DEFAULT_LLAMA_MODEL
    ss.llama_mmproj = config.DEFAULT_LLAMA_MMPROJ
    ss.llama_host = config.DEFAULT_LLAMA_HOST
    ss.llama_port = config.DEFAULT_LLAMA_PORT
    ss.llama_api_prefix = config.DEFAULT_LLAMA_API_PREFIX
    ss.llama_startup_timeout = config.DEFAULT_LLAMA_STARTUP_TIMEOUT
    ss.llama_optimization_mode = config.DEFAULT_LLAMA_OPTIMIZATION_MODE
    ss.llama_reasoning_budget = config.DEFAULT_LLAMA_REASONING_BUDGET
    ss.llama_cache_k = config.DEFAULT_LLAMA_CACHE_K
    ss.llama_cache_v = config.DEFAULT_LLAMA_CACHE_V
    ss.llama_flash_attn = config.DEFAULT_LLAMA_FLASH_ATTN
    ss.llama_load_mode = config.DEFAULT_LLAMA_LOAD_MODE
    ss.llama_slots = config.DEFAULT_LLAMA_SLOTS
    ss.llama_threads = config.DEFAULT_LLAMA_THREADS
    ss.llama_batch = config.DEFAULT_LLAMA_BATCH
    ss.llama_ubatch = config.DEFAULT_LLAMA_UBATCH
    ss.llama_gpu_layers = config.DEFAULT_LLAMA_GPU_LAYERS
    ss.llama_fit_target = config.DEFAULT_LLAMA_FIT_TARGET
    ss.llama_context_size = config.DEFAULT_LLAMA_CONTEXT_SIZE
    ss.temperature = config.DEFAULT_TEMPERATURE
    ss.max_tokens = config.DEFAULT_MAX_TOKENS
    ss.top_p = config.DEFAULT_TOP_P
    ss.timeout = config.DEFAULT_TIMEOUT
    ss.auto_retry = True        # авто-перегенерация при «плохом» капшене (~10 мин/повтор)
    ss.manual_review = False    # проверять каждый капшен вручную перед записью
    ss.disable_thinking = config.DEFAULT_DISABLE_THINKING  # выключить размышления модели
    ss.trigger_word = config.DEFAULT_TRIGGER_WORD  # триггер стиля, подставляется первым тегом
    ss.notify_on_finish = True  # браузерное уведомление по завершении
    ss.caption_edit_height = config.DEFAULT_CAPTION_EDIT_HEIGHT  # высота полей правки капшена
    ss.folder = ""
    ss.recursive = False
    ss.pipeline_mode = "description_only"
    ss.pipeline_tagger_ids = []

    # Накладываем сохранённые «липкие» настройки поверх дефолтов, чтобы не
    # переставлять галки/слайдеры при каждом запуске (settings.json).
    for k, v in app_settings.load_settings().items():
        ss[k] = v

    # Старый default 12288 превышает проверенный managed context 8192 ещё до
    # учёта prompt/image tokens. Мигрируем только это заведомо несовместимое
    # значение; меньшие пользовательские настройки сохраняем.
    if ss.backend_type == "managed_llama" and int(ss.max_tokens) > 4096:
        ss.max_tokens = config.DEFAULT_MAX_TOKENS

    # Подтягиваем активную модель с сервера ПОСЛЕ восстановления api_url (сервер
    # обычно уже поднят). Тихо игнорируем недоступность — останется сохранённое.
    if ss.backend_type == "external":
        _detected = ExternalApiBackend(
            base_url=ss.api_url, api_key=config.DEFAULT_API_KEY,
            model=ss.model, timeout=10.0,
        ).active_model()
        if _detected:
            ss.model = _detected

    # Режим обработки. Последняя папка/recursive уже восстановлены из settings.
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
    if ss.get("preset_name") not in ss.presets:
        ss.preset_name = first_preset
    ss.system_prompt = ss.presets[ss.preset_name]["system"]
    ss.user_prompt = ss.presets[ss.preset_name]["user"]

    # Реестр текущей папки и служебное
    ss.scan_info = None
    ss.registry = None          # DoneRegistry текущей папки (реестр «сделано этим приложением»)


init_state()
ss = st.session_state

# После refresh (F5) session_state пуст, но воркер (singleton) может всё ещё
# обрабатывать папку. Восстанавливаем путь из живого состояния, чтобы UI не
# выглядел «сброшенным» и кнопки продолжения работали с правильной папкой.
if not ss.folder and ss.worker.state.folder:
    ss.folder = ss.worker.state.folder


# --------------------------------------------------------------------------- #
# Новая плоская навигация группирует функции по пользовательским задачам. Старые
# render-функции сохраняются как миграционный слой, но тяжёлые экраны больше не
# исполняются все сразу на каждом rerun.
# --------------------------------------------------------------------------- #
ss.setdefault("ui_theme", "light")
inject_design_system()
_header_snapshot = ss.worker.snapshot()
_header_status = "Обработка выполняется" if _header_snapshot.get("running") else "Готов к работе"
_theme_change = top_bar(_header_status)
if _theme_change:
    ss.ui_theme = _theme_change
    app_settings.save_settings({
        key: ss[key] for key in app_settings.PERSISTED_KEYS if key in ss
    })
    st.rerun()

page_labels = ["Работа", "Результаты", "Запуски", "Ресурсы", "Система"]
ss.setdefault("main_page", "Работа")
ss.main_page = {"Библиотека": "Ресурсы", "Диагностика": "Система"}.get(
    ss.main_page, ss.main_page
)
nav_columns = st.columns(len(page_labels))
for nav_column, nav_page in zip(nav_columns, page_labels):
    active = ss.main_page == nav_page
    with nav_column:
        if st.button(nav_page, key=f"nav_{nav_page}",
                     width="stretch", type="primary" if active else "secondary"):
            ss.main_page = nav_page
            if not active:
                st.rerun()
page = ss.main_page

render_sidebar()
poll = ss.worker.is_alive() or ss.preview_runner.is_alive()

if page == "Работа":
    render_workspace()
    with st.expander("Настройка и запуск", expanded=bool(ss.folder)):
        poll = render_generation_tab() or poll
elif page == "Результаты":
    result_page = st.segmented_control(
        "Инструмент результатов", ["Галерея", "Массовые правки", "Здоровье"],
        default="Галерея", label_visibility="collapsed", key="results_page",
    )
    if result_page == "Галерея":
        render_gallery_tab()
    elif result_page == "Массовые правки":
        render_tags_tab()
    else:
        render_health_tab()
elif page == "Запуски":
    render_runs()
elif page == "Ресурсы":
    render_library()
else:
    render_diagnostics()

# Polling в самом конце скрипта: UI живёт в отдельном потоке от генерации, поэтому
# периодически перерисовываемся — обновляем прогресс/лог/статус и ловим клики по
# «Стоп»/«Пауза». Делаем это ПОСЛЕ отрисовки лога, чтобы он успел обновиться.
# `gallery_regen` в условии: держим polling живым, пока галерея не подхватила
# свежие капшены после перегенерации. Иначе есть узкое окно (воркер уже выставил
# running=False, но поток ещё не умер → is_alive() True), где генерация перестаёт
# поллить, а галерея пропускает refresh — и капшен снова «застревает» старым.
if poll or ss.get("gallery_regen"):
    time.sleep(1.0)
    st.rerun()
