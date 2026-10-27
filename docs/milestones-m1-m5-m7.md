# Doctor, batch/resume, and teammate quickstart

Implementation validation on the Windows RTX 4090 machine, 2026-10-27.

## Implemented

- M1: `uv run execloc doctor` reports pass/warn/fail for Python, architecture, workspace disk, Docker Linux connectivity, the local model endpoint, model availability, and exposed active context/revision. Checks are bounded and read-only; they do not load a model or download images. Warnings alone return zero.
- M5: `--all`, `--workers N`, on-demand images, targeted non-force `--prune`, and atomic per-instance prediction checkpoints. An OS lock prevents concurrent writers to a run ID. Ctrl+C stops scheduling, drains active work, and returns 130. Rerunning skips saved terminal rows; interrupted rows resume automatically, and other failed rows require `--retry-failed`.
- Resume checks source, dependency, config, model, and frozen-sample identity. Both arms retain their own rows and provenance under one run ID.
- M7: README includes an eight-command static quickstart, a paired execution run, partial scoring, five-instance pilot, full-sample commands, resume behavior, and separate Ollama serving instructions.

## Automated checks

`uv run pytest` with a fresh workspace-local `--basetemp`: **141 passed**. Coverage includes a scripted five-instance interruption/resume without repeating completed instances, actual Windows spawn workers, cross-process lock contention and crash release, atomic replacement failure, failure retry, targeted pruning, endpoint restrictions, and doctor failure/warning behavior. No GPU dependencies or lockfile changes were needed. `git diff --check` also passed.

## Machine and live pilot

The doctor initially detected a Docker engine HTTP 500 while the model endpoint remained healthy. After the owner manually opened Docker Desktop, the doctor confirmed the Linux daemon and endpoint were reachable. The unloaded model produced warnings, as intended; it must be loaded before checking actual serving context/revision.

The new five-instance live pilot and real interruption/resume validation are in progress. The earlier paired single-issue real-model smoke passed both arms; see [4090-smoke-fixed.md](4090-smoke-fixed.md). Neither that smoke nor the automated batch tests constitute the full 100-issue experiment.
