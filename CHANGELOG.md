# Changelog

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

