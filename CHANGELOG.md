# Changelog

## [3.0.0] - 2026-08-18

### Added

- Added a native single-window Windows launcher with tray, notifications, autostart, and persistent window state.
- Added a React/Vite workspace and a local FastAPI service with project, run, review, recipe, resource, and system APIs.
- Added dataset projects, automatic run planning, expert configuration, preview runs, and resumable execution.
- Added visual search using local image embeddings with scene, composition, object, pose, and text-query modes.
- Added gallery bulk editing, feedback-driven regeneration, project health, storage retention, and run comparison.
- Added a manual review queue and durable per-project event history.
- Added PyInstaller and Inno Setup packaging definitions.

### Changed

- Replaced the Streamlit-first workflow with a desktop-first project workspace.
- Reworked persistence around project sidecars, an indexed project registry, atomic events, and reproducible run snapshots.
- Replaced both README files and all published screenshots for the new product flow.

### Fixed

- Improved interrupted-run reconciliation, idempotent commands, safe shutdown, and recovery of remaining work.
- Improved local model, tagger, hardware, and visual-search diagnostics.

## [2.0.0] - 2026-08-07

### Added

- Added a unified dataset workflow: dataset, scan, Preview, Run, and Results.
- Added Run Center with run history, status, parameters, summaries, and provenance.
- Added background Preview without writing results to the dataset.
- Added managed Windows CUDA 12.4 llama.cpp installation with checksum verification.
- Added GGUF/mmproj discovery and model library support.
- Added automatic hardware and GGUF compatibility checks.
- Added named backend profiles for model-specific runtime settings.
- Added diagnostics export and effective configuration details.
- Added gallery caption regeneration hints for missed or incorrect image details.
- Added pipeline orchestration for tagger and VLM stages with retry policies.
- Added resumable run snapshots and pipeline provenance sidecars.

### Changed

- Redesigned the main workflow, gallery, generation, tags, health, and sidebar screens around task-focused actions.
- Added shared dataset context and simplified navigation across workflow stages.
- Added light and dark themes with consistent controls and empty/error states.
- Improved caption update, merge, stoplist, trigger-word, backup, pause, and resume behavior.
- Updated README documentation and screenshots for the v2.0.0 workflow.
- Refactored inference, hardware, pipeline, diagnostics, and UI code internally without changing the public launch command.

### Fixed

- Fixed stale gallery and manual-review caption widgets after regeneration.
- Fixed atomic caption writes.
- Fixed handling of 4xx API responses.
- Fixed GGUF and TOML escaping.
- Fixed gallery provenance tracking.
- Fixed dataset folder selection after a Streamlit widget was created.
