# Doctor, batch/resume, and teammate quickstart

Implementation validation on the Windows RTX 4090 machine, 2026-10-27.

## Implemented

- M1: `uv run execloc doctor` reports pass/warn/fail for Python, architecture, workspace disk, Docker Linux connectivity, the local model endpoint, model availability, and exposed active context/revision. Checks are bounded and read-only; they do not load a model or download images. Warnings alone return zero.
- M5: `--all`, `--workers N`, on-demand images, targeted non-force `--prune`, and atomic per-instance prediction checkpoints. An OS lock prevents concurrent writers to a run ID. Ctrl+C stops scheduling, drains active work, and returns 130. Rerunning skips saved terminal rows; interrupted rows resume automatically, and other failed rows require `--retry-failed`.
- Resume checks source, dependency, config, model, and frozen-sample identity. Both arms retain their own rows and provenance under one run ID.
- M7: README includes an eight-command static quickstart, a paired execution run, partial scoring, five-instance pilot, full-sample commands, resume behavior, and separate Ollama serving instructions.

## Automated checks

`uv run pytest` with a fresh workspace-local `--basetemp`: **145 passed**. Coverage includes a scripted five-instance interruption/resume without repeating completed instances, actual Windows spawn workers, cross-process lock contention and crash release, atomic replacement failure, failure retry, targeted pruning, endpoint restrictions, and doctor failure/warning behavior. Model matching preserves namespace prefixes and accepts Ollama's `:latest` alias. No GPU dependencies or lockfile changes were needed. `git diff --check` also passed.

## Machine and live pilot

The doctor initially detected a Docker engine HTTP 500 while the model endpoint remained healthy. After the owner manually opened Docker Desktop, the doctor confirmed the Linux daemon and endpoint were reachable. The unloaded model produced warnings, as intended; it must be loaded before checking actual serving context/revision.

The real interruption/resume check passed in `pilot-4090-20261027`: SIGINT after the first checkpoint drained the active second issue and returned 130. Resuming completed the remaining three issues. All five static submissions parsed; the two saved prediction rows and trajectory SHA-256 hashes remained unchanged. See `results/pilot-4090-20261027/resume-validation.json`.

The first execution response then generated more than 16,000 tokens without returning an action. The harness process was stopped and only its owned container removed. No completed execution prediction or exact API token usage was available, and no paired accuracy is claimed for that diagnostic run. Its `validation-status.json` records the interruption explicitly.

The shared configuration now caps each response at 2048 output tokens in both arms, with the existing length/format correction retained. A fresh five-instance paired pilot, `pilot-4090-20261027-bounded`, is in progress under that configuration; outcomes from the two protocols are not combined.

The earlier paired single-issue real-model smoke passed both arms; see [4090-smoke-fixed.md](4090-smoke-fixed.md). Neither that smoke nor these convenience pilots constitute the full 100-issue experiment. All five pilot IDs are the first frozen rows, from Astropy, so they do not establish accuracy across repositories.
