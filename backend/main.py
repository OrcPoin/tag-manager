from __future__ import annotations

import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from backend.api.auto_plan import router as auto_plan_router
from backend.api.events import router as events_router
from backend.api.projects import router as projects_router
from backend.api.runs import router as runs_router
from backend.api.reviews import router as reviews_router
from backend.api.test_runs import router as test_runs_router
from backend.api.recipes import router as recipes_router
from backend.api.system import router as system_router
from core.taggers.manager import TaggerManager
from core.visual_models import VisualModelManager
from backend.services import AutoPlanService, ProjectService, RecipeService, ReviewService, RunCoordinator, RunService, TestRunService
from backend.inference import RunBackendFactory
from backend.pipeline import VlmPipelineExecutor


def create_app(data_directory: str | Path | None = None, test_executor=None, run_executor=None,
               frontend_directory: str | Path | None = None) -> FastAPI:
    root = Path(data_directory or os.getenv("TAG_MANAGER_DATA_DIR", ".tagmanager-service")).resolve()
    @asynccontextmanager
    async def lifespan(application: FastAPI):
        yield
        coordinator = getattr(application.state, "run_coordinator", None)
        if coordinator is not None:
            coordinator.shutdown()
        backend_factory = getattr(application.state, "backend_factory", None)
        if backend_factory is not None:
            backend_factory.stop_managed()

    app = FastAPI(title="Tag Manager Local Service", version="3.0.0", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://127.0.0.1:5173", "http://localhost:5173"],
        allow_methods=["GET", "POST", "PATCH", "PUT", "DELETE", "OPTIONS"],
        allow_headers=["Content-Type", "Idempotency-Key"],
    )
    app.state.project_service = ProjectService(root / "projects.sqlite3")
    app.state.recipe_service = RecipeService(app.state.project_service)
    app.state.run_service = RunService(app.state.project_service)
    app.state.run_service.reconcile_interrupted()
    app.state.review_service = ReviewService(app.state.project_service)
    app.state.backend_factory = RunBackendFactory()
    production_executor = run_executor or VlmPipelineExecutor(
        app.state.run_service, app.state.project_service, app.state.review_service,
        app.state.backend_factory,
    )
    app.state.run_coordinator = RunCoordinator(app.state.run_service, production_executor)
    app.state.auto_plan_service = AutoPlanService(app.state.project_service)
    app.state.test_run_service = TestRunService(
        app.state.project_service, app.state.run_service,
        app.state.run_coordinator, test_executor,
    )
    app.state.tagger_manager = TaggerManager(root / "taggers")
    app.state.visual_model_manager = VisualModelManager(root)
    app.include_router(projects_router)
    app.include_router(events_router)
    app.include_router(runs_router)
    app.include_router(auto_plan_router)
    app.include_router(reviews_router)
    app.include_router(test_runs_router)
    app.include_router(recipes_router)
    app.include_router(system_router)

    @app.get("/api/system/status", tags=["system"])
    def system_status():
        return {"status": "ready", "schema_version": 1, "service_version": app.version,
                "capabilities": {"inference": True, "test_run": True}}

    frontend = Path(frontend_directory or Path(__file__).resolve().parents[1] / "frontend" / "dist")
    assets = frontend / "assets"
    if assets.is_dir():
        app.mount("/assets", StaticFiles(directory=assets), name="frontend-assets")

    @app.get("/", include_in_schema=False)
    def frontend_index():
        index = frontend / "index.html"
        if index.is_file():
            return FileResponse(index)
        return JSONResponse({
            "status": "frontend_not_built",
            "message": "Выполните npm run build в каталоге frontend",
        }, status_code=503)

    return app


app = create_app()
