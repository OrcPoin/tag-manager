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


def pick_gguf_file(initial: str = "", title: str = "Выберите GGUF") -> str | None:
    """Открыть системный выбор GGUF-файла для VLM или mmproj."""
    try:
        import tkinter as tk
        from tkinter import filedialog
        from pathlib import Path
    except Exception:  # noqa: BLE001
        return None
    try:
        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        initial_path = Path(initial) if initial else None
        folder = str(initial_path.parent if initial_path and initial_path.is_file() else initial_path or "")
        selected = filedialog.askopenfilename(
            title=title, initialdir=folder or None,
            filetypes=[("GGUF model", "*.gguf"), ("All files", "*.*")],
        )
        root.destroy()
        return selected or None
    except Exception:  # noqa: BLE001
        return None
