# Standing rules for coding agents

See SETUP.md for the full brief and milestones.

## Hard rules

- **No paid APIs.** All inference goes to a self-hosted, OpenAI-compatible endpoint. The code must read the endpoint from `EXECLOC_BASE_URL` and fail if it's unset. Never fall back to a hosted provider.
- **Lock everything.** Use uv. Commit `pyproject.toml`, `uv.lock`, and `.python-version`. Every command in the docs starts with `uv run`.
- **Keep GPU and serving packages out of the lockfile.** Don't add vLLM, torch, or llama-cpp to the project dependencies. They break dependency resolution on macOS and Windows. Model serving is documented separately and runs outside this environment.
- **No make, no bash scripts for required workflows.** Teammates use Linux, macOS, and Windows. Expose every workflow as a subcommand of one Python CLI.
- **Verify, don't guess.** Before you use any mini-SWE-agent or SWE-bench API, read the installed package's source or docs. Don't rely on remembered flags or class names.
- **Stop and ask the human** when a section says "Decision needed," or when a check fails twice.

## Status (updated 2026-10-27)

| Milestone | State |
|---|---|
| M0 skeleton | Done |
| M1 doctor | Done. Read-only environment checks cover Docker, disk, the local endpoint, active context, and model revision. |
| M2 data | Done. `data/instances.jsonl` and `data/gold.jsonl` are frozen. Never regenerate them. |
| M3 static arm | Done; one real-model run on the 4090 submitted valid locations and passed scoring. See docs/4090-smoke-fixed.md. |
| M4 exec arm | Done; one real-model run on the 4090 submitted valid locations and passed scoring; prepared Python environment and automatic cleanup verified. |
| M5 batch and resume | Done. A real five-instance static run was interrupted and resumed with unchanged completed rows/trajectory hashes. Batch, workers, atomic checkpoints, failures, and pruning are tested. Paired calibration details: docs/milestones-m1-m5-m7.md. |
| M6 scoring | Done |
| M7 README | Done. Cross-platform quickstart, serving setup, pilot/resume, batch, and scoring instructions. |

Decisions made: the model is `devstral-small-2`, served with Ollama on the Windows 11 Home 4090 machine. Docker Linux containers have been validated, but the backend currently needs manual recovery after recurring HTTP 500 errors; see docs/milestones-m1-m5-m7.md. See the "Decided" notes in SETUP.md.

## Who works where (parallel work, avoid merge conflicts)

- **4090 machine (Windows):** serving and machine validation only. Follow `docs/4090-machine.md`. Write results in `docs/serving.md`. Don't edit `src/`, `tests/`, `data/`, or `configs/`. Work on the branch `serving/4090`.
- **Main dev machine:** M1, then M3 to M5 code, then M7. Owns `src/`, `tests/`, `configs/`, and `docker/`.
- Before committing, both sides run `uv run pytest`, which must pass.

## Conventions

- Every workflow is an `execloc` subcommand in `src/execloc/cli.py`. The `sample`, `gold`, and `score` commands never need `.env`, Docker, or a model endpoint.
- Write JSONL with `\n` line endings and UTF-8 (`execloc.dataset.write_jsonl`), so files are byte-identical on Windows.
- The rules for building gold locations and for scoring are in the docstrings at the top of `src/execloc/gold.py` and `src/execloc/score.py`. If you change a rule, change its tests too.
