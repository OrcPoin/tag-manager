from __future__ import annotations

import threading
from typing import Any, Callable


def create_tray(controller: Any, title: str = "Tag Manager"):
    """Create an optional pystray icon; importing it remains lazy for CI/headless use."""
    try:
        import pystray
        from PIL import Image, ImageDraw
    except ImportError:
        return None
    image = Image.new("RGBA", (64, 64), (25, 35, 45, 255))
    ImageDraw.Draw(image).rectangle((12, 12, 52, 52), fill=(70, 180, 150, 255))
    menu = pystray.Menu(
        pystray.MenuItem("Open Tag Manager", lambda icon, item: controller.open_window(), default=True),
        pystray.MenuItem(lambda item: controller.progress_label(), None, enabled=False),
        pystray.MenuItem("Pause / Resume", lambda icon, item: controller.toggle_pause()),
        pystray.MenuItem("Safe stop", lambda icon, item: controller.safe_stop()),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Exit completely", lambda icon, item: (icon.stop(), controller.full_exit())),
    )
    return pystray.Icon("tag-manager", image, title, menu)


class NotificationMonitor:
    def __init__(self, controller: Any, notifier: Any, enabled: Callable[[], bool], interval: float = 2.0):
        self.controller, self.notifier, self.enabled, self.interval = controller, notifier, enabled, interval
        self._known: dict[str, Any] = {}
        self._stop = threading.Event()

    def poll_once(self) -> None:
        current = {run.run_id: run for run in self.controller.active()}
        if self.controller.background and self.enabled():
            for run_id, run in current.items():
                previous = self._known.get(run_id)
                if previous is not None and run.review_count > previous.review_count:
                    self.notifier.notify("A result is waiting for your decision.", "Tag Manager review")
            for run_id, previous in self._known.items():
                if run_id in current:
                    continue
                try:
                    state = self.controller.client.run(run_id)
                except Exception:
                    continue
                status = str(state.get("status", ""))
                if status == "completed":
                    self.notifier.notify(f"Processing completed: {previous.done} / {previous.total}.", "Tag Manager")
                elif status == "failed":
                    self.notifier.notify("Processing stopped with an error. Open Tag Manager for details.", "Tag Manager error")
                elif status == "stopped":
                    self.notifier.notify("Processing stopped safely.", "Tag Manager")
        self._known = current

    def run(self) -> None:
        while not self._stop.wait(self.interval):
            self.poll_once()

    def stop(self) -> None:
        self._stop.set()
