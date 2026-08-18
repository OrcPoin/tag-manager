from __future__ import annotations

from dataclasses import asdict
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from core.hardware.detector import detect_hardware, refresh_hardware
from core.taggers.manager import TaggerManager
from core.taggers.registry import TAGGER_SPECS
from core.visual_models import VISUAL_MODEL_SPECS, VisualModelManager


router = APIRouter(prefix="/api/system", tags=["system"])


def _tagger_manager(request: Request) -> TaggerManager:
    return request.app.state.tagger_manager

def _visual_manager(request: Request) -> VisualModelManager:
    return request.app.state.visual_model_manager


class InstallRequest(BaseModel):
    confirmed: bool = False


@router.get("/resources")
def resources(request: Request):
    manager = _tagger_manager(request)
    # Older desktop sessions stored taggers in the application working directory.
    # Keep them visible while the service uses its dedicated data directory.
    legacy_manager = TaggerManager("taggers")
    return {"taggers": [{
        "id": spec.tagger_id, "name": spec.display_name, "installed": manager.installed(spec.tagger_id) or legacy_manager.installed(spec.tagger_id),
        "license": spec.license, "size_bytes": spec.download_size_bytes, "notes": spec.notes,
        "repo_id": spec.repo_id,
    } for spec in TAGGER_SPECS.values()], "visual_models": _visual_manager(request).inventory()}

@router.get("/visual-models")
def visual_models(request: Request):
    return _visual_manager(request).inventory()

@router.post("/visual-models/{model_id}/install")
def install_visual_model(model_id: str, body: InstallRequest, request: Request):
    if not body.confirmed:
        raise HTTPException(409, detail={"code": "confirmation_required", "message": "Подтвердите загрузку модели"})
    manager = _visual_manager(request)
    try: manager.install(model_id)
    except KeyError as error: raise HTTPException(404, detail={"code": "visual_model_not_found", "message": "Модель не найдена"}) from error
    except Exception as error: raise HTTPException(502, detail={"code": "visual_model_install_failed", "message": str(error)}) from error
    return {"id": model_id, "installed": manager.installed(model_id)}

@router.delete("/visual-models/{model_id}")
def remove_visual_model(model_id: str, request: Request):
    manager = _visual_manager(request)
    try: manager.remove(model_id)
    except KeyError as error: raise HTTPException(404, detail={"code": "visual_model_not_found", "message": "Модель не найдена"}) from error
    return {"id": model_id, "installed": False}


@router.post("/taggers/{tagger_id}/install")
def install_tagger(tagger_id: str, body: InstallRequest, request: Request):
    if not body.confirmed:
        raise HTTPException(409, detail={"code": "confirmation_required", "message": "Подтвердите загрузку модели"})
    manager = _tagger_manager(request)
    legacy_manager = TaggerManager("taggers")
    try:
        spec = manager.spec(tagger_id)
    except KeyError as error:
        raise HTTPException(404, detail={"code": "tagger_not_found", "message": "Tagger не найден"}) from error
    try:
        if not manager.installed(tagger_id) and not legacy_manager.installed(tagger_id):
            manager.installer.install(spec)
    except Exception as error:
        raise HTTPException(502, detail={"code": "tagger_install_failed", "message": str(error)}) from error
    return {"id": tagger_id, "installed": manager.installed(tagger_id)}


@router.delete("/taggers/{tagger_id}")
def remove_tagger(tagger_id: str, request: Request):
    manager = _tagger_manager(request)
    legacy_manager = TaggerManager("taggers")
    try:
        spec = manager.spec(tagger_id)
    except KeyError as error:
        raise HTTPException(404, detail={"code": "tagger_not_found", "message": "Tagger не найден"}) from error
    if manager.installed(tagger_id):
        manager.unload(tagger_id)
        manager.installer.remove(spec)
    elif legacy_manager.installed(tagger_id):
        legacy_manager.unload(tagger_id)
        legacy_manager.installer.remove(spec)
    return {"id": tagger_id, "installed": False}


@router.get("/hardware")
def hardware(refresh: bool = False):
    info = refresh_hardware() if refresh else detect_hardware()
    return {"logical_cores": info.logical_cores, "physical_cores": info.physical_cores,
            "ram_total_bytes": info.ram_total_bytes, "ram_available_bytes": info.ram_available_bytes,
            "gpus": [asdict(gpu) for gpu in info.gpus]}
