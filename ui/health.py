"""Вкладка «Здоровье»: аудит датасета перед обучением + карантин.

6 секций-экспандеров (сводка, битые, сироты, дубли, здоровье капшенов,
формат/цвет). Действия сводятся к карантину (обратимый перенос в _rejected/).
Пока идёт генерация — вкладка блокируется.
"""

from __future__ import annotations

import os

import streamlit as st

import config
from core import dataset as ds
from core import health
from core.image_scanner import find_images
from ui.common import folder_picker_row, probe_cached, thumbnail


def _thumbs(paths: list[str], cols: int = 6, limit: int = 24) -> None:
    """Сетка миниатюр для списка путей (обрезается до limit)."""
    shown = paths[:limit]
    for row_start in range(0, len(shown), cols):
        row = st.columns(cols)
        for i, path in enumerate(shown[row_start:row_start + cols]):
            with row[i]:
                try:
                    data = thumbnail(path, os.path.getmtime(path))
                except OSError:
                    data = None
                if data:
                    st.image(data, caption=os.path.basename(path))
                else:
                    st.caption(os.path.basename(path))
    if len(paths) > limit:
        st.caption(f"…и ещё {len(paths) - limit}")


def _quarantine(paths: list[str], reason: str) -> None:
    """Перенести paths в карантин и сбросить скан (счётчики устареют)."""
    ss = st.session_state
    moved = health.quarantine(paths, ss.health_folder, reason)
    ss.health = None
    st.toast(f"В карантин перенесено: {moved} (папка _rejected/{reason}/)")


def render_health_tab() -> None:
    ss = st.session_state
    ss.setdefault("health_folder", "")
    ss.setdefault("health_recursive", False)
    ss.setdefault("health", None)

    st.subheader("Аудит датасета перед обучением")

    if ss.worker.is_alive():
        st.warning("Идёт генерация капшенов. Аудит и карантин заблокированы, чтобы "
                   "не конфликтовать с записью файлов — остановите обработку.")
        return

    folder, recursive = folder_picker_row(
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
                    probes[path] = probe_cached(path, stt.st_mtime, stt.st_size)
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
                _quarantine(broken, "broken")
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
                _quarantine(orphans, "orphan_txt")
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
                _thumbs(group, cols=6, limit=12)
                if st.button("Лишние → карантин", key=f"{key_prefix}_{gi}"):
                    _quarantine(group[1:], "duplicates")
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
                _quarantine(fmt["animated"], "animated")
                st.rerun()
        if not fmt["non_rgb"] and not fmt["animated"]:
            st.success("Проблем с форматом/цветом не найдено.")
