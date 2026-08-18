from __future__ import annotations

from backend.domain import Run
from core.inference.llama_server import LlamaServerConfig
from core.inference.manager import BackendManager
from config import DEFAULT_LLAMA_EXTRA_ARGS


class RunBackendFactory:
    def __init__(self, manager: BackendManager | None = None):
        self.manager = manager or BackendManager()

    def __call__(self, run: Run):
        model = run.model_snapshot
        backend_type = str(model.get("backend_type", "managed_llama"))
        timeout = float(model.get("timeout", 900))
        if backend_type == "external":
            return self.manager.external(
                base_url=str(model.get("base_url", "http://127.0.0.1:5000/v1")),
                api_key=str(model.get("api_key", "not-needed")),
                model=str(model.get("model", "local-model")), timeout=timeout,
            )
        config = LlamaServerConfig(
            executable=str(model.get("executable", "")), model=str(model.get("model_path", "")),
            mmproj=str(model.get("mmproj_path", "")), host=str(model.get("host", "127.0.0.1")),
            port=int(model.get("port", 8080)), api_prefix=str(model.get("api_prefix", "/v1")),
            startup_timeout=float(model.get("startup_timeout", 180)),
            extra_args=tuple(run.effective_resource_configuration.get("llama_args", DEFAULT_LLAMA_EXTRA_ARGS)),
            log_path=str(model.get("log_path", "llama-server.log")),
        )
        return self.manager.managed(config, timeout=timeout)

    def stop_managed(self) -> bool:
        return self.manager.stop_managed()

    def release(self, backend, keep_alive_seconds: float = 0) -> bool:
        if keep_alive_seconds > 0 and getattr(backend, "backend_name", "") == "managed_llama_cpp":
            return self.manager.stop_managed_after(keep_alive_seconds)
        return backend.stop()
