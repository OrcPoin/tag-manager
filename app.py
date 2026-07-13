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
from core.caption_client import CaptionClient
from core.logger import Logger
from core.worker import CaptionWorker
from ui.generation import render_generation_tab
from ui.gallery import render_gallery_tab
from ui.health import render_health_tab
from ui.sidebar import render_sidebar
from ui.tags import render_tags_tab

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

# После refresh (F5) session_state пуст, но воркер (singleton) может всё ещё
# обрабатывать папку. Восстанавливаем путь из живого состояния, чтобы UI не
# выглядел «сброшенным» и кнопки продолжения работали с правильной папкой.
if not ss.folder and ss.worker.state.folder:
    ss.folder = ss.worker.state.folder


# --------------------------------------------------------------------------- #
# Раскладка. Порядок исполнения сохранён из исходной версии: сайдбар → галерея →
# теги → здоровье → генерация. Он важен, т.к. вкладки читают ss.trigger_word/model,
# а генерация их пишет (эффект применяется на следующем ререндере).
# --------------------------------------------------------------------------- #
st.title("🏷️ Tag Manager")
st.caption("Генерация детальных капшенов для изображений через локальный LLM")

tab_gen, tab_gallery, tab_tags, tab_health = st.tabs(
    ["🤖 Генерация", "🖼️ Галерея", "🏷️ Теги", "🩺 Здоровье"])

render_sidebar()

with tab_gallery:
    render_gallery_tab()

with tab_tags:
    render_tags_tab()

with tab_health:
    render_health_tab()

with tab_gen:
    poll = render_generation_tab()

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
