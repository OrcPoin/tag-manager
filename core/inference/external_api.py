"""External OpenAI-compatible inference backend.

This compatibility adapter intentionally inherits the existing implementation.
Keeping the public ``CaptionClient`` class avoids breaking third-party imports,
while new application code can depend on the neutral backend name.
"""

from core.caption_client import CaptionClient


class ExternalApiBackend(CaptionClient):
    backend_name = "external_openai_compatible"

    def start(self):
        return self.health()

    def stop(self) -> bool:
        return True

    def restart(self):
        return self.health()
