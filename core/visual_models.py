from __future__ import annotations

import json
import shutil
import urllib.request
from pathlib import Path
from PIL import Image

VISUAL_MODEL_SPECS = {
    "clip-vit-base-patch32": {
        "id": "clip-vit-base-patch32", "name": "CLIP ViT-B/32 (ONNX, CPU/GPU)",
        "repo": "Xenova/clip-vit-base-patch32", "license": "Apache-2.0",
        "files": ("onnx/vision_model.onnx", "preprocessor_config.json"),
        "size_bytes": 354_000_000,
        "notes": "Локальные image embeddings для сцены, композиции и объектов. Не требует внешнего API.",
    }
}


class VisualModelManager:
    def __init__(self, root: str | Path):
        self.root = Path(root).resolve() / "visual-models"

    def spec(self, model_id: str) -> dict:
        return VISUAL_MODEL_SPECS[model_id]

    def model_dir(self, model_id: str) -> Path:
        return self.root / model_id

    def installed(self, model_id: str) -> bool:
        folder = self.model_dir(model_id)
        return (folder / "onnx" / "vision_model.onnx").is_file() and (folder / "preprocessor_config.json").is_file()

    def inventory(self) -> list[dict]:
        return [{**spec, "installed": self.installed(model_id)} for model_id, spec in VISUAL_MODEL_SPECS.items()]

    def install(self, model_id: str, progress=None) -> None:
        spec = self.spec(model_id)
        folder = self.model_dir(model_id); folder.mkdir(parents=True, exist_ok=True)
        for filename in spec["files"]:
            target = folder / filename; target.parent.mkdir(parents=True, exist_ok=True)
            if target.is_file(): continue
            url = f"https://huggingface.co/{spec['repo']}/resolve/main/{filename}?download=true"
            temporary = target.with_suffix(target.suffix + ".part")
            try:
                request = urllib.request.Request(url, headers={"User-Agent": "TagManager/1.0"})
                with urllib.request.urlopen(request, timeout=60) as response, temporary.open("wb") as stream:
                    total = int(response.headers.get("Content-Length", "0")); done = 0
                    while chunk := response.read(1024 * 1024):
                        stream.write(chunk); done += len(chunk)
                        if progress: progress(filename, done, total)
                temporary.replace(target)
            except Exception:
                temporary.unlink(missing_ok=True)
                raise

    def remove(self, model_id: str) -> None:
        shutil.rmtree(self.model_dir(model_id), ignore_errors=True)

    def create_embedder(self, model_id: str):
        if not self.installed(model_id):
            return None
        return ClipOnnxEmbedder(self.model_dir(model_id))


class ClipOnnxEmbedder:
    """Lazy CLIP vision encoder. onnxruntime/numpy remain optional at import time."""
    def __init__(self, folder: Path):
        self.folder = folder
        self._session = None

    def _load(self):
        import onnxruntime as ort
        available = ort.get_available_providers()
        providers = [name for name in ("CUDAExecutionProvider", "CPUExecutionProvider") if name in available]
        try:
            self._session = ort.InferenceSession(str(self.folder / "onnx" / "vision_model.onnx"), providers=providers or ["CPUExecutionProvider"])
        except Exception:
            self._session = ort.InferenceSession(str(self.folder / "onnx" / "vision_model.onnx"), providers=["CPUExecutionProvider"])

    def __call__(self, path: Path) -> list[float]:
        import numpy as np
        if self._session is None: self._load()
        with Image.open(path) as source:
            image = source.convert("RGB").resize((224, 224), Image.Resampling.BICUBIC)
            pixels = np.asarray(image, dtype=np.float32) / 255.0
        pixels = ((pixels - 0.5) / 0.5).transpose(2, 0, 1)[None, ...]
        input_meta = self._session.get_inputs()[0]
        output = self._session.run(None, {input_meta.name: pixels})[0]
        vector = np.asarray(output).reshape(-1).astype(np.float32)
        norm = float(np.linalg.norm(vector)) or 1.0
        return (vector / norm).round(7).tolist()
