from __future__ import annotations

import sqlite3
from pathlib import Path

from backend.domain import Project


class ProjectIndex:
    """Rebuildable machine-local index; sidecars remain authoritative."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute("""
                CREATE TABLE IF NOT EXISTS projects (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    dataset_path TEXT NOT NULL UNIQUE,
                    status TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
            """)
            connection.execute("""
                CREATE TABLE IF NOT EXISTS commands (
                    idempotency_key TEXT PRIMARY KEY,
                    command_type TEXT NOT NULL,
                    resource_id TEXT NOT NULL
                )
            """)

    def upsert(self, project: Project) -> None:
        with self._connect() as connection:
            connection.execute("""
                INSERT INTO projects(id, name, dataset_path, status, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    name=excluded.name,
                    dataset_path=excluded.dataset_path,
                    status=excluded.status,
                    updated_at=excluded.updated_at
            """, (project.id, project.name, project.dataset_path, project.status, project.updated_at))

    def list_entries(self) -> list[dict[str, str]]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT id, name, dataset_path, status, updated_at FROM projects ORDER BY updated_at DESC"
            ).fetchall()
        return [dict(row) for row in rows]

    def rebuild(self, dataset_paths: list[str | Path]) -> dict[str, int]:
        from backend.persistence.projects import ProjectRepository

        restored = invalid = 0
        with self._connect() as connection:
            connection.execute("DELETE FROM projects")
        for dataset_path in dataset_paths:
            try:
                project = ProjectRepository(dataset_path).load()
                self.upsert(project)
                restored += 1
            except (OSError, ValueError, TypeError):
                invalid += 1
        return {"restored": restored, "invalid": invalid}

    def get_path(self, project_id: str) -> str | None:
        with self._connect() as connection:
            row = connection.execute("SELECT dataset_path FROM projects WHERE id = ?", (project_id,)).fetchone()
        return str(row[0]) if row else None

    def remember_command(self, key: str, command_type: str, resource_id: str) -> str:
        with self._connect() as connection:
            existing = connection.execute(
                "SELECT command_type, resource_id FROM commands WHERE idempotency_key = ?", (key,)
            ).fetchone()
            if existing:
                if existing[0] != command_type:
                    raise ValueError("Idempotency key was used for another command")
                return str(existing[1])
            connection.execute(
                "INSERT INTO commands(idempotency_key, command_type, resource_id) VALUES (?, ?, ?)",
                (key, command_type, resource_id),
            )
        return resource_id

    def find_command(self, key: str) -> tuple[str, str] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT command_type, resource_id FROM commands WHERE idempotency_key = ?", (key,)
            ).fetchone()
        return (str(row[0]), str(row[1])) if row else None
