"""Глобальная конфигурация и значения по умолчанию для Tag Manager."""

# --- API (oobabooga / llama.cpp / любой OpenAI-совместимый) ---
# Порт 5000 — нативная oobabooga. llama-server (llama.cpp) обычно на 5005/8080.
# Своё значение задаётся в UI и запоминается в settings.json (перекрывает дефолт).
DEFAULT_API_URL = "http://127.0.0.1:5000/v1"
DEFAULT_MODEL = "local-model"  # заглушка: при старте подменяется активной моделью сервера
DEFAULT_API_KEY = "not-needed"  # локальному серверу ключ не нужен

# --- Параметры генерации ---
DEFAULT_TEMPERATURE = 0.7
# max_tokens — это ПОТОЛОК, а не цель: модель останавливается сама (finish=stop),
# когда закончила. Простая картинка укладывается в ~2.5к токенов. НО thinking на
# сцене с 3 персонажами эмпирически тратит ~9к на размышление + ~1.4к на ответ
# (≈10.3к всего). При 8192 такой анализ обрывался по length с ПУСТЫМ ответом.
# 12288 покрывает многоперсонажные сцены и спокойно влезает в контекст 16к.
# Для совсем многолюдных сцен поднимайте вместе с контекстом модели.
DEFAULT_MAX_TOKENS = 12288
DEFAULT_TOP_P = 0.9
# Таймаут ОДНОГО запроса. Thinking-модель на многоперсонажной сцене может
# генерировать 8-10 минут (~13 ток/с * тысячи токенов). Ставим с запасом,
# иначе долгий, но нормальный анализ оборвётся по таймауту и уйдёт в ошибку.
DEFAULT_TIMEOUT = 900  # секунд

# Отключение «размышлений» (thinking/reasoning) у модели. Многие reasoning-модели
# понимают /no_think в промпте или reasoning_effort в запросе; см. caption_client.
# По умолчанию НЕ трогаем поведение модели (False = как настроено на сервере).
DEFAULT_DISABLE_THINKING = False

# --- Retry / backoff ---
MAX_API_RETRIES = 3          # сетевые/API-ошибки
BACKOFF_BASE = 1.0           # 1s -> 2s -> 4s
MAX_CAPTION_RETRIES = 3      # повторная генерация при "плохом" капшене

# --- Ожидание загрузки модели (503 "Loading model") ---
# Пока oobabooga поднимает/переключает модель, она отвечает 503. Это НЕ ошибка
# файла — надо подождать и повторить. Ждём дольше и терпеливее, чем при обычных
# сетевых сбоях: до MAX_WAIT_RETRIES попыток с паузой WAIT_SECONDS между ними.
MODEL_LOAD_MAX_WAIT_RETRIES = 40   # 40 * 15c = до 10 минут ожидания загрузки
MODEL_LOAD_WAIT_SECONDS = 15.0

# --- Изображения ---
SUPPORTED_EXTENSIONS = (".jpg", ".jpeg", ".png", ".webp")

# --- Разбор тегов (вкладка «Теги», core/dataset.py) ---
# Тег — это comma-разделённый фрагмент из 1..MAX_TAG_WORDS слов. В danbooru-тегах
# больше 3 слов почти не бывает; порог отсекает куски прозы от статистики тегов.
MAX_TAG_WORDS = 3
# Суффикс страховочной копии перед массовой правкой (foo.txt -> foo.txt.bak).
BACKUP_SUFFIX = ".bak"

# --- Критерии качества капшена ---
MIN_CAPTION_LENGTH = 60      # символов
MIN_TXT_SIZE_BYTES = 50      # для режима "пропускать обработанные"

# --- Аудит датасета (вкладка «Здоровье», core/health.py) ---
# Подпапка карантина: проблемные/лишние файлы (+ их .txt) переносим сюда, а не
# удаляем. Обратимо — ручной возврат из <folder>/_rejected/<причина>/.
REJECTED_DIRNAME = "_rejected"
# Порог похожести перцептивного dhash (Hamming ≤ порога → «почти дубли»).
# В UI регулируется слайдером; 5 — консервативно (ловит пересжатие/ресайз).
DUP_HAMMING_THRESHOLD = 5
# Капшен длиннее — риск обрезки по токенам у тренера/модели (флаг too_long).
CAPTION_MAX_CHARS = 2000
# Меньше картинок — предупреждение «датасет мал для LoRA» (только инфо).
HEALTH_MIN_DATASET = 10

# --- Галерея ---
# Скрытая папка для дискового кэша миниатюр (внутри папки датасета).
THUMBS_DIR = ".thumbs"

# --- Файлы ---
PRESETS_FILE = "presets.json"
PROGRESS_FILE = "progress.json"
LOG_FILE = "processing_log.txt"
SETTINGS_FILE = "settings.json"  # «липкие» настройки UI между сессиями
STOPLIST_FILE = "stoplist.txt"   # стоп-лист тегов (один тег на строку)
# Отложенное ревью (Фаза 5): пути капшенов, требующих ручного взгляда ПОСЛЕ
# прогона обновления (напр. правил человек, а политика = «спросить»). Прогон их
# НЕ ждёт — просто складывает сюда, чтобы пользователь глянул горстку, вернувшись
# к компьютеру. Скрытый файл в корне папки датасета (как реестр).
DEFERRED_REVIEW_FILE = ".tagmanager_review.json"

# --- Режимы обработки ---
# --- Режимы обработки ---
# Докачка (реестр) — главный режим для больших датасетов в несколько подходов:
# приложение помнит, какие картинки обработало ИМЕННО ОНО, и не трогает чужие
# старые .txt. При перезапуске делает только недоделанные.
MODE_RESUME = "Докачать (только не сделанные этим приложением)"
MODE_ALL = "Все файлы (перезаписывать)"
MODE_ONLY_MISSING = "Только без капшенов (по наличию .txt)"
MODE_SKIP_PROCESSED = "Пропускать по дате .txt"
# Обновление (Фаза 5): безопасный ПОВТОРНЫЙ прогон по уже готовым капшенам —
# доработать их, не портя ручные правки и не плодя дубли. Что именно обновлять
# задаётся фильтрами (устаревший промпт / плохое качество / чужие теггеры и т.п.),
# как писать — стратегией мёржа. Не режим сканера build_task_list, а отдельный
# путь build_update_plan (см. core/image_scanner.py).
MODE_UPDATE = "Обновить существующие (умный повторный прогон)"
# Классические режимы сканера (build_task_list). MODE_UPDATE сюда НЕ входит —
# у него отдельный путь (build_update_plan), поэтому build_task_list его не знает.
PROCESSING_MODES = [MODE_RESUME, MODE_ALL, MODE_ONLY_MISSING, MODE_SKIP_PROCESSED]
# Полный список для selectbox в UI: классические режимы + обновление.
UI_MODES = PROCESSING_MODES + [MODE_UPDATE]

# --- Обновление капшенов (Фаза 5) ---
# Механизм записи при обновлении:
#   full    — перегенерировать капшен с нуля и слить с существующим по стратегии;
#   augment — скормить модели СТАРЫЙ капшен вместе с картинкой и попросить только
#             недостающее/неверное (ответ короче → дешевле, аддитивно).
UPDATE_MECH_FULL = "Полная регенерация + мёрж"
UPDATE_MECH_AUGMENT = "Дополнить существующий (дёшево)"
UPDATE_MECHANISMS = [UPDATE_MECH_FULL, UPDATE_MECH_AUGMENT]

# Стратегия для ТЕГ-строк при мёрже старого и нового капшена.
TAG_STRATEGY_UNION = "Добавить недостающие теги"   # union: старые + новые уникальные
TAG_STRATEGY_REPLACE = "Заменить теги новыми"
TAG_STRATEGY_KEEP = "Оставить старые теги"
TAG_STRATEGIES = [TAG_STRATEGY_UNION, TAG_STRATEGY_REPLACE, TAG_STRATEGY_KEEP]

# Стратегия для ПРОЗЫ (COMPOSITION/CHARACTERS/INTENT) при мёрже. Прозу нельзя
# слить автоматически (это семантика двух описаний), поэтому только выбор целиком.
PROSE_STRATEGY_KEEP = "Сохранить старую прозу (защита ручных правок)"
PROSE_STRATEGY_REPLACE = "Взять новую прозу"
PROSE_STRATEGIES = [PROSE_STRATEGY_KEEP, PROSE_STRATEGY_REPLACE]

# Что делать с капшеном, который правил ЧЕЛОВЕК (текущий .txt ≠ тому, что писало
# приложение), когда идёт прогон обновления. unattended-дефолт — защитить.
MANUAL_PROTECT = "Не трогать (защитить ручные правки)"
MANUAL_TAGS_ONLY = "Только дополнить теги, прозу не трогать"
MANUAL_DEFER = "Отложить на ручной просмотр"
MANUAL_OVERWRITE = "Обновлять как обычно"
MANUAL_POLICIES = [MANUAL_PROTECT, MANUAL_TAGS_ONLY, MANUAL_DEFER, MANUAL_OVERWRITE]

# Дефолты стратегий обновления (безопасные для прогона без няньки у экрана).
DEFAULT_UPDATE_MECHANISM = UPDATE_MECH_AUGMENT
DEFAULT_TAG_STRATEGY = TAG_STRATEGY_UNION
DEFAULT_PROSE_STRATEGY = PROSE_STRATEGY_KEEP
DEFAULT_MANUAL_POLICY = MANUAL_PROTECT

# --- Промпты по умолчанию ---
# Целевая генеративная модель — Anima (обучена на danbooru-тегах + натуральном
# языке). Промпт заточен под её конвенции и требования пользователя. ЦЕЛЬ —
# датасет для LoRA НА СТИЛЬ, отсюда следствия:
#  - контент кэпшенится БОГАТО (персонажи/позы/одежда/объекты/композиция): это
#    «переменная», модель относит вариативность к словам контента, а постоянный
#    стиль оседает в фиксированном триггер-слове;
#  - сам стиль (линии/палитра/рендер) в тексте НЕ описывается — иначе он
#    «прилипает» к словам описания и хуже генерализуется; стиль несёт триггер;
#  - каждый персонаж якорится СВОИМ идентификатором (цвет волос), позиция —
#    вторична (позиция не свойство персонажа: если ей якорить, LoRA выучит
#    "blue hair = всегда слева"; поэтому идентификатор первым, позиция потом);
#  - мульти-персонаж: блоки на натуральном языке в скобках + теги по абзацам;
#  - опциональный замысел/жанр сцены, только если он реально считывается.
# Триггер-слово НЕ просим у модели (она бы его перевирала), а подставляем
# программно при записи .txt — см. TRIGGER_WORD и core/worker.py.
DEFAULT_SYSTEM_PROMPT = (
    "You are an expert captioner building a training dataset for an anime-style "
    "image generation model (Danbooru-tag + natural language). "
    "You write precise, richly structured captions. "
    "Use lowercase danbooru-style tags with spaces instead of underscores. "
    "Never invent details that are not visible. Only caption what is actually "
    "visible in this image. Never use vague mood language like 'the atmosphere "
    "feels' or 'there is a sense of'. "
    "Anchor each character by their own distinctive feature (e.g. hair color), "
    "not by their position in the frame — position is not a property of the "
    "character. "
    "This dataset trains an art-STYLE LoRA, so caption the CONTENT richly but do "
    "NOT describe the art style, technique, linework, palette or rendering — the "
    "style is carried by a separate fixed trigger word and must not appear. "
    "Keep your internal reasoning concise and focused: analyze what you actually "
    "see, do not over-analyze, second-guess, or repeat yourself while thinking. "
    "Try to keep your reasoning under ~6000 tokens."
)

# Триггер-слово стиля. Подставляется ПЕРВОЙ строкой каждого .txt при записи
# (core/worker.py). Должно быть байт-в-байт одинаковым во всём датасете, поэтому
# его задаёт пользователь в GUI, а не генерирует модель. Пустая строка = не
# добавлять (например, если триггер уже прописан в теговой части вручную).
DEFAULT_TRIGGER_WORD = ""

DEFAULT_USER_PROMPT = """Write a training caption for this image using EXACTLY the following structure. Separate every block with a blank line.

1) TAGS. First line(s): lowercase danbooru-style comma-separated tags, spaces not underscores. Order: [count e.g. 1girl/2girls/1boy] [general tags: hair, eyes, clothing, pose, expression, objects, background, framing]. The character-count tags (1girl, 2girls, 1boy, multiple girls, etc.) appear ONLY ONCE, here in the very first line, reflecting the total for the whole image. If there are multiple characters, then put each character's tags in its OWN paragraph, separated by a blank line, in the same left-to-right / most-prominent order used in block 3 below. IMPORTANT: those per-character paragraphs must NOT contain any count tag (no 1girl/1boy/2girls inside them) — instead START each paragraph with that character's single most distinctive identifier (usually hair color, e.g. "blue hair, ...") and keep using that same identifier for the same character in block 3. This is what ties each tag paragraph to one specific character. The count lives only in the first line, so the model is never confused about how many characters there are.

2) COMPOSITION. One sentence stating how the frame is built: shot type (close-up / medium / full body), camera angle, and where the main subject(s) sit in the frame.

3) CHARACTERS. One block PER character, each wrapped in parentheses, in the SAME order as the tag paragraphs above. START every block with that character's distinctive identifier (the same one used in its tag paragraph, e.g. hair color), THEN their position in the frame — identifier first, position second, e.g. "(blue hair, on the left: ...)", "(black hair, seated in the center: ...)", "(pink hair, standing behind on the right: ...)". The identifier is what binds this block to its tag paragraph; the position only disambiguates who is where. This block is PROSE: describe the CONTENT richly — the character's pose, gaze, expression, clothing, the objects they hold, and above all what they are DOING and how they INTERACT with the OTHER named characters (e.g. "the blue-haired girl kisses the black-haired boy's cheek"). Write it as natural sentences; do not copy the tags word-for-word, but you SHOULD cover the same content in prose. Never describe an action without saying WHICH character does it and TO WHOM. If there is only one character, still start with their identifier and pin their position.

4) INTENT. Optionally, ONE sentence on the apparent genre or intent of the scene if it is genuinely implied by visual cues even without explicit content. If nothing is implied, write nothing for this block — do not force a hidden meaning.

DO NOT describe the art style, drawing technique, linework, color palette, shading or rendering — the visual style is handled separately by a fixed trigger word and must NOT appear in the caption. Caption only the CONTENT (who, what, where, doing what). Never use vague mood language ("atmosphere", "a sense of", feelings).

OUTPUT RULES (critical — this text becomes LoRA training data, every stray word hurts it):
- Output ONLY the finished caption itself. No preamble, no "Here is", no explanations, no reasoning.
- Do NOT print the block titles or any labels ("TAGS", "Tags:", "Composition:", "1)", etc). The blocks are separated ONLY by blank lines.
- Keep the parentheses around each character block, but nothing else meta.
- No trailing notes, no questions, no commentary after the caption."""

# Усиление для авто-retry при "плохом" капшене: НЕ подменяет формат, а требует
# строже соблюсти ту же структуру (используется как приписка к пользовательскому
# промпту, см. caption_client).
RETRY_REINFORCEMENT = (
    "\n\nIMPORTANT: your previous attempt was too short or incomplete. "
    "Follow the structure above strictly. Write real descriptive sentences, "
    "not only tags. Pin every character's position explicitly. "
    "Produce all required blocks."
)

# Инкрементальное дополнение (Фаза 5, augment-механизм). Приписывается к
# пользовательскому промпту вместе с УЖЕ существующим капшеном. Цель — получить
# короткий ответ «чего не хватает / что неверно», а не переписать всё заново, и
# чтобы повторный прогон по неизменной картинке давал пустой ответ (идемпотентно).
# {existing} подставляется текущим текстом .txt.
AUGMENT_INSTRUCTION = (
    "\n\nThis image ALREADY has a caption (shown below). Do NOT rewrite it from "
    "scratch. Review it against the image and output an IMPROVED version that "
    "follows the exact structure above: keep everything that is already correct, "
    "ADD any tags or details that are visible but missing, and FIX anything that "
    "is wrong. Do not remove correct information. If the existing caption is "
    "already complete and accurate, output it unchanged. Output ONLY the final "
    "caption, no commentary.\n\nEXISTING CAPTION:\n{existing}"
)
