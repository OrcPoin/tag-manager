from __future__ import annotations

from pathlib import Path
from uuid import uuid4

from backend.domain import Event, Project
from backend.domain.common import utc_now
from backend.persistence import EventStore, ProjectIndex, ProjectRepository

from .scanner import scan_dataset
from core.image_scanner import find_images


class ProjectNotFoundError(LookupError):
    pass


class ProjectService:
    def __init__(self, index_path: str | Path):
        self.index = ProjectIndex(index_path)

    def list_projects(self) -> list[Project]:
        projects = []
        for entry in self.index.list_entries():
            try:
                projects.append(ProjectRepository(entry["dataset_path"]).load())
            except (OSError, ValueError, TypeError):
                continue
        return projects

    def rebuild_index(self, dataset_paths: list[str | Path]) -> dict[str, int]:
        return self.index.rebuild(dataset_paths)

    def open_project(self, dataset_path: str | Path) -> Project:
        path = Path(dataset_path).resolve()
        repository = ProjectRepository(path)
        if repository.project_path.is_file():
            project = repository.load()
            if Path(project.dataset_path).resolve() != path:
                project.dataset_path = str(path)
        else:
            project = Project(id=uuid4().hex, name=path.name or str(path), dataset_path=str(path))
        recursive = bool(project.settings.get("include_subfolders", False))
        project.last_scan = scan_dataset(path, recursive=recursive)
        project.updated_at = utc_now()
        project.attention_summary = {"missing_captions": project.last_scan.missing_captions}
        repository.save(project)
        self.index.upsert(project)
        self._write_event(repository, Event(
            "project.opened", project.id, None,
            f"Выбран dataset: {project.last_scan.images} изображений",
            {"dataset_path": str(path), "images": project.last_scan.images},
        ))
        return project

    def get_project(self, project_id: str) -> Project:
        path = self.index.get_path(project_id)
        if path is None:
            raise ProjectNotFoundError(project_id)
        try:
            return ProjectRepository(path).load()
        except OSError as error:
            raise ProjectNotFoundError(project_id) from error

    def scan_project(self, project_id: str, include_subfolders: bool | None = None) -> Project:
        project = self.get_project(project_id)
        repository = ProjectRepository(project.dataset_path)
        if include_subfolders is not None:
            project.settings["include_subfolders"] = include_subfolders
        recursive = bool(project.settings.get("include_subfolders", False))
        project.last_scan = scan_dataset(project.dataset_path, recursive=recursive)
        project.updated_at = utc_now()
        project.attention_summary = {"missing_captions": project.last_scan.missing_captions}
        repository.save(project)
        self.index.upsert(project)
        self._write_event(repository, Event(
            "project.scanned", project.id, None,
            f"Найдено {project.last_scan.missing_captions} изображений без captions",
            {"images": project.last_scan.images, "captions": project.last_scan.captions,
             "missing_captions": project.last_scan.missing_captions,
             "signature": project.last_scan.signature, "include_subfolders": recursive},
        ))
        return project

    def update_project(self, project_id: str, changes: dict) -> Project:
        project = self.get_project(project_id)
        for key, value in changes.items():
            setattr(project, key, value)
        project.updated_at = utc_now()
        repository = ProjectRepository(project.dataset_path)
        repository.save(project)
        self.index.upsert(project)
        self._write_event(repository, Event(
            "project.updated", project.id, None, "Настройки проекта обновлены",
            {"fields": sorted(changes)},
        ))
        return project

    def gallery(self, project_id: str, *, search: str = "", missing_only: bool = False,
                page: int = 1, page_size: int = 60) -> dict:
        project = self.get_project(project_id)
        root = Path(project.dataset_path)
        recursive = bool(project.settings.get("include_subfolders", False))
        needle = search.strip().casefold()
        items = []
        for value in find_images(str(root), recursive):
            image = Path(value)
            relative = image.relative_to(root).as_posix()
            caption_path = image.with_suffix(".txt")
            try:
                caption = caption_path.read_text(encoding="utf-8").strip() if caption_path.is_file() else ""
            except OSError:
                caption = ""
            if missing_only and caption:
                continue
            if needle and needle not in relative.casefold() and needle not in caption.casefold():
                continue
            items.append({"path": relative, "name": image.name, "caption": caption,
                          "has_caption": bool(caption)})
        total = len(items)
        start = max(0, page - 1) * page_size
        return {"items": items[start:start + page_size], "page": page, "page_size": page_size,
                "total": total, "pages": max(1, (total + page_size - 1) // page_size)}

    def health(self, project_id: str) -> dict:
        from core import health
        project = self.get_project(project_id)
        root = Path(project.dataset_path)
        recursive = bool(project.settings.get("include_subfolders", False))
        images = [str(Path(value)) for value in find_images(str(root), recursive)]
        probes = {}
        total_bytes = 0
        for image in images:
            try:
                stat = Path(image).stat()
                total_bytes += stat.st_size
                probes[image] = health.probe_and_hash(image, stat.st_mtime, stat.st_size)
            except OSError as error:
                probes[image] = {"ok": False, "error": str(error), "md5": None, "dhash": None,
                                 "mode": "", "animated": False, "width": 0, "height": 0, "format": ""}
        captions = health.caption_issues(images)
        formats = health.format_issues(probes)
        exact = health.group_exact({path: probe.get("md5") for path, probe in probes.items()})
        relative = lambda path: Path(path).relative_to(root).as_posix()
        samples = lambda paths: [relative(path) for path in paths[:50]]
        issues = {
            "broken": samples([path for path, probe in probes.items() if not probe.get("ok")]),
            "orphan_captions": samples(health.orphan_captions(str(root), recursive)),
            "stem_collisions": [[relative(path) for path in group] for group in health.stem_collisions(images)[:20]],
            "exact_duplicates": [[relative(path) for path in group] for group in exact[:20]],
            "empty_captions": samples(captions["empty"]), "short_captions": samples(captions["short"]),
            "unreadable_captions": samples(captions["unreadable"]),
            "non_rgb": samples(formats["non_rgb"]), "animated": samples(formats["animated"]),
        }
        return {"project_id": project_id, "images": len(images), "captioned": len(images) - len(captions["empty"]),
                "total_bytes": total_bytes, "issues": issues,
                "issue_count": sum(len(value) for value in issues.values())}

    def list_events(self, after: str | None = None, limit: int = 200) -> list[dict]:
        events: list[dict] = []
        for project in self.list_projects():
            repository = ProjectRepository(project.dataset_path)
            for stream_id in ["project", *project.run_refs]:
                events.extend(EventStore(repository.sidecar_path / "runs", stream_id).read_all())
        events.sort(key=lambda item: (item.get("ts", ""), item.get("id", "")))
        if after:
            position = next((index for index, item in enumerate(events) if item.get("id") == after), None)
            if position is not None:
                events = events[position + 1:]
        return events[:limit]

    @staticmethod
    def _write_event(repository: ProjectRepository, event: Event) -> None:
        EventStore(repository.sidecar_path / "runs", "project").append(event)
