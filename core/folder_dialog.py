"""Вспомогательные функции UI: нативный диалог выбора папки через tkinter."""

from __future__ import annotations


def pick_folder(initial: str = "") -> str | None:
    """
    Открыть системный диалог выбора папки.

    Возвращает выбранный путь или None (отмена/недоступно).
    tkinter может быть недоступен в headless-окружении — тогда молча вернём None.
    """
    try:
        import tkinter as tk
        from tkinter import filedialog
    except Exception:  # noqa: BLE001
        return None

    try:
        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        folder = filedialog.askdirectory(initialdir=initial or None)
        root.destroy()
        return folder or None
    except Exception:  # noqa: BLE001
        return None
