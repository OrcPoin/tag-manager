"""Single-window Tag Manager launcher for Windows users.

Double-clicking this file starts the local API and production React UI inside one
native window. No development server or console window is required.
"""

from __future__ import annotations

import socket
import os
import sys
import threading
import time
import urllib.request
from pathlib import Path


HOST = "127.0.0.1"
PORT = 8000
URL = f"http://{HOST}:{PORT}"
_window_save_timer: threading.Timer | None = None
_window_save_lock = threading.Lock()


def _runtime_paths() -> tuple[Path, Path, Path]:
    if getattr(sys, "frozen", False):
        bundle = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent)).resolve()
        local = Path(os.getenv("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
        return bundle, local / "TagManager" / "service", Path(sys.executable).resolve()
    root = Path(__file__).resolve().parent
    return root, root / ".tagmanager-service", Path(__file__).resolve()


class DesktopApi:
    def __init__(self, launcher_path: Path):
        self.launcher_path = launcher_path

    def select_dataset_folder(self, initial: str = "") -> str:
        from core.folder_dialog import pick_folder
        return pick_folder(initial) or ""

    def select_gguf_file(self, kind: str, initial: str = "") -> str:
        from core.folder_dialog import pick_gguf_file
        title = "Выберите projection mmproj.gguf" if kind == "mmproj" else "Выберите VLM-модель GGUF"
        return pick_gguf_file(initial, title) or ""

    def get_desktop_settings(self) -> dict:
        from backend.desktop.autostart import is_enabled
        from core.app_settings import load_settings
        settings = load_settings()
        return {
            "keep_background": bool(settings.get("desktop_keep_background", False)),
            "autostart": is_enabled(),
            "notifications": bool(settings.get("notify_on_finish", True)),
        }

    def set_desktop_settings(self, values: dict) -> dict:
        from backend.desktop.autostart import set_enabled
        from core.app_settings import load_settings, save_settings
        settings = load_settings()
        settings["desktop_keep_background"] = bool(values.get("keep_background", False))
        settings["notify_on_finish"] = bool(values.get("notifications", True))
        requested_autostart = bool(values.get("autostart", False))
        settings["desktop_autostart"] = set_enabled(requested_autostart, self.launcher_path)
        save_settings(settings)
        return self.get_desktop_settings()


def _confirm_full_exit() -> bool:
    try:
        from tkinter import messagebox
        return bool(messagebox.askyesno("Tag Manager", "Полностью завершить Tag Manager? Активная обработка будет безопасно остановлена."))
    except Exception:
        return True


def _service_ready(timeout: float = 0.3) -> bool:
    try:
        with urllib.request.urlopen(f"{URL}/api/system/status", timeout=timeout) as response:
            return response.status == 200
    except Exception:
        return False


def _show_error(message: str) -> None:
    try:
        from tkinter import messagebox
        messagebox.showerror("Tag Manager", message)
    except Exception:
        pass


def _window_state(settings: dict) -> tuple[int, int, int | None, int | None, bool]:
    width = max(900, min(3840, int(settings.get("desktop_window_width", 1280))))
    height = max(620, min(2160, int(settings.get("desktop_window_height", 820))))
    # Let the operating system choose a visible monitor. Saved absolute
    # coordinates become invalid after disconnecting a display or changing DPI.
    return width, height, None, None, bool(settings.get("desktop_window_maximized", False))


def _save_window_state(window, *, maximized: bool | None = None) -> None:
    from core.app_settings import load_settings, save_settings
    settings = load_settings()
    if not bool(getattr(window, "maximized", False)):
        settings.update({"desktop_window_width": int(window.width), "desktop_window_height": int(window.height), "desktop_window_x": int(window.x), "desktop_window_y": int(window.y)})
    if maximized is not None:
        settings["desktop_window_maximized"] = maximized
    save_settings(settings)


def _schedule_window_state_save(window, *, maximized: bool | None = None) -> None:
    global _window_save_timer
    with _window_save_lock:
        if _window_save_timer is not None:
            _window_save_timer.cancel()
        _window_save_timer = threading.Timer(0.35, _save_window_state, args=(window,), kwargs={"maximized": maximized})
        _window_save_timer.daemon = True
        _window_save_timer.start()


def main() -> int:
    root, data_directory, launcher_path = _runtime_paths()
    # Double-click launchers can inherit an arbitrary working directory.
    os.chdir(root)
    if not (root / "frontend" / "dist" / "index.html").is_file():
        _show_error("Интерфейс Tag Manager не собран. Переустановите приложение или выполните npm run build в frontend.")
        return 1
    try:
        import uvicorn
        import webview
    except ImportError as error:
        _show_error(f"Не установлены компоненты приложения: {error}")
        return 1

    server = None
    server_thread = None
    if not _service_ready():
        from backend.main import create_app
        config = uvicorn.Config(
            create_app(data_directory=data_directory,
                       frontend_directory=root / "frontend" / "dist"),
            host=HOST, port=PORT, log_level="warning",
            access_log=False, log_config=None,
        )
        server = uvicorn.Server(config)
        server_thread = threading.Thread(target=server.run, name="tag-manager-service", daemon=True)
        server_thread.start()
        deadline = time.monotonic() + 15
        while not _service_ready(0.5) and time.monotonic() < deadline:
            if not server_thread.is_alive():
                _show_error("Локальный сервис Tag Manager не запустился.")
                return 1
            time.sleep(0.1)
        if not _service_ready():
            server.should_exit = True
            _show_error("Локальный сервис Tag Manager не ответил вовремя.")
            return 1

    from core.app_settings import load_settings
    window_width, window_height, window_x, window_y, window_maximized = _window_state(load_settings())
    window = webview.create_window("Tag Manager", URL, width=window_width, height=window_height,
                                   x=window_x, y=window_y, maximized=window_maximized,
                                   min_size=(900, 620), js_api=DesktopApi(launcher_path),
                                   confirm_close=False)

    def close_owned_service() -> None:
        if server is not None:
            server.should_exit = True

    from backend.desktop import DesktopController, DesktopServiceClient
    from backend.desktop.tray import NotificationMonitor, create_tray
    from core.app_settings import load_settings

    tray_holder = {"available": True}
    controller = DesktopController(
        window, DesktopServiceClient(URL),
        keep_background=lambda: bool(load_settings().get("desktop_keep_background", False)),
        stop_service=close_owned_service,
        confirm_exit=_confirm_full_exit,
        background_available=lambda: tray_holder["available"],
    )
    tray = create_tray(controller)
    tray_holder["available"] = tray is not None
    monitor = NotificationMonitor(
        controller, tray,
        enabled=lambda: bool(load_settings().get("notify_on_finish", True)),
    ) if tray is not None else None

    def on_closing() -> bool:
        return controller.close_requested()

    def on_closed() -> None:
        if tray is not None:
            tray.stop()
        if monitor is not None:
            monitor.stop()
        close_owned_service()

    window.events.closing += on_closing
    window.events.closed += on_closed
    window.events.resized += lambda *_args: _schedule_window_state_save(window)
    window.events.moved += lambda *_args: _schedule_window_state_save(window)
    window.events.maximized += lambda *_args: _schedule_window_state_save(window, maximized=True)
    window.events.restored += lambda *_args: _schedule_window_state_save(window, maximized=False)

    if tray is not None:
        threading.Thread(target=tray.run, name="tag-manager-tray", daemon=True).start()
        threading.Thread(target=monitor.run, name="tag-manager-notifications", daemon=True).start()
    webview.start(debug=False)
    if server_thread is not None:
        server_thread.join(timeout=10)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
