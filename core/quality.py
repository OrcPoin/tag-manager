"""Оценка качества сгенерированного капшена и решение о повторной генерации."""

from __future__ import annotations

import re

from config import MIN_CAPTION_LENGTH


def _looks_like_only_tags(text: str) -> bool:
    """True, если текст выглядит как список тегов без нормальных предложений."""
    # Признак прозы — наличие завершённых предложений (точка/!/?).
    sentences = [s.strip() for s in re.split(r"[.!?]+", text) if s.strip()]
    real_sentences = [s for s in sentences if len(s.split()) >= 5]

    # Если завершающей пунктуации нет вовсе, но текст — сплошной список,
    # разделённый запятыми из коротких фрагментов, это теги.
    has_sentence_punct = bool(re.search(r"[.!?]", text))
    fragments = [f.strip() for f in text.split(",") if f.strip()]
    short_fragments = [f for f in fragments if len(f.split()) <= 4]

    if not has_sentence_punct and len(fragments) >= 3 and \
            len(short_fragments) >= 0.8 * len(fragments):
        # Много коротких comma-фрагментов без единой точки → похоже на теги.
        return True

    # Есть хотя бы одно нормальное предложение с завершающей пунктуацией → это проза.
    if real_sentences and has_sentence_punct:
        return False

    # Нет ни одного полноценного предложения — считаем списком тегов.
    return not real_sentences


def _has_consecutive_phrase_repeat(text: str) -> bool:
    """True, если короткая фраза (1-4 слова) повторяется подряд 4+ раза.

    Ловит вырожденные циклы вида "she sits she sits she sits she sits" или
    "a girl standing. a girl standing. ...", которые по одному только проценту
    уникальных слов не отличить от валидного многоперсонажного капшена.
    Валидный формат повторяет фразу максимум 2-3 раза (по разу на похожего
    персонажа), поэтому порог в 4 повтора его не задевает.
    """
    words = re.findall(r"\w+", text.lower())
    n = len(words)
    for size in (1, 2, 3, 4):
        run = 1
        for i in range(size, n):
            if words[i - size] == words[i]:
                run += 1
                # Фраза длины size, повторённая m раз подряд, даёт пик run = 1+(m-1)*size.
                # Порог 3*size+1 срабатывает ровно при m>=4 повторах.
                if run >= 3 * size + 1:
                    return True
            else:
                run = 1
    return False


def _has_excessive_repetition(text: str) -> bool:
    """Детект мусора: вырожденный цикл (модель залипла и повторяет слова/фразы).

    ВАЖНО: наш структурированный формат сам по себе повторяет слова — одежда/поза
    каждого персонажа дублируется в теговом абзаце и в скобочном блоке. Валидный
    многоперсонажный капшен даёт ~0.30-0.40 уникальных слов. Настоящее залипание
    модели — это ~0.05-0.20 ЛИБО повтор одной фразы подряд. Пороги подобраны так,
    чтобы НЕ браковать нормальные капшены (ложный ретрай стоит ~10 минут).
    """
    words = re.findall(r"\w+", text.lower())
    if len(words) < 8:
        return False
    unique_ratio = len(set(words)) / len(words)
    # Меньше 20% уникальных — почти наверняка вырожденный цикл, а не наш формат.
    if unique_ratio < 0.20:
        return True

    # Короткая фраза, повторённая подряд 4+ раза (чередующиеся слова тоже).
    if _has_consecutive_phrase_repeat(text):
        return True

    return False


def _lacks_description(text: str) -> bool:
    """Нет описания действий/позиций/персонажей — слишком мало содержательного текста."""
    # Убираем возможную первую строку с тегами.
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    if len(lines) <= 1:
        # Всё в одну строку — проверяем, есть ли вообще предложения.
        return _looks_like_only_tags(text)
    body = " ".join(lines[1:])
    return len(body.split()) < 12  # меньше 12 слов описания — недостаточно


def evaluate_caption(caption: str) -> tuple[bool, str]:
    """
    Оценить капшен.

    Возвращает (is_good, reason). Если is_good == False, reason описывает проблему.
    """
    text = (caption or "").strip()

    if len(text) < MIN_CAPTION_LENGTH:
        return False, f"слишком короткий ({len(text)} < {MIN_CAPTION_LENGTH} симв.)"

    if _looks_like_only_tags(text):
        return False, "только теги без нормального описания"

    if _has_excessive_repetition(text):
        return False, "много повторений / мусора"

    if _lacks_description(text):
        return False, "отсутствует описание действий/позиций/персонажей"

    return True, "ok"
