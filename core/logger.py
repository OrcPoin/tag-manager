"""Логирование в файл processing_log.txt + буфер последних строк для UI."""

from __future__ import annotations

import os
from collections import deque
from datetime import datetime


class Logger:
    """Пишет строки лога в файл и держит буфер для отображения в интерфейсе."""

    def __init__(self, log_path: str, ui_buffer_size: int = 300):
        self.log_path = log_path
        self.buffer: deque[str] = deque(maxlen=ui_buffer_size)

    def log(self, message: str, level: str = "INFO") -> str:
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        line = f"[{ts}] [{level}] {message}"
        self.buffer.append(line)
        try:
            with open(self.log_path, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except OSError:
            # Логирование не должно рушить обработку
            pass
        return line

    def info(self, message: str) -> str:
        return self.log(message, "INFO")

    def warning(self, message: str) -> str:
        return self.log(message, "WARN")

    def error(self, message: str) -> str:
        return self.log(message, "ERROR")

    def get_text(self) -> str:
        """Весь буфер как единый текст (новые строки снизу)."""
        return "\n".join(self.buffer)

    def clear_buffer(self) -> None:
        self.buffer.clear()
