# Standing rules for coding agents

See SETUP.md for the full brief and milestones.

## Hard rules

- **No paid APIs.** All inference goes to a self-hosted, OpenAI-compatible endpoint. The code must read the endpoint from `EXECLOC_BASE_URL` and fail if it's unset. Never fall back to a hosted provider.
- **Lock everything.** Use uv. Commit `pyproject.toml`, `uv.lock`, and `.python-version`. Every command in the docs starts with `uv run`.
- **Keep GPU and serving packages out of the lockfile.** Don't add vLLM, torch, or llama-cpp to the project dependencies. They break dependency resolution on macOS and Windows. Model serving is documented separately and runs outside this environment.
- **No make, no bash scripts for required workflows.** Teammates use Linux, macOS, and Windows. Expose every workflow as a subcommand of one Python CLI.
- **Verify, don't guess.** Before you use any mini-SWE-agent or SWE-bench API, read the installed package's source or docs. Don't rely on remembered flags or class names.
- **Stop and ask the human** when a section says "Decision needed," or when a check fails twice.

## Status (updated 2026-10-23)

| Milestone | State |
|---|---|
| M0 skeleton | Done |
| M1 doctor | Not started (stub) |
| M2 data | Done. `data/instances.jsonl` and `data/gold.jsonl` are frozen. Never regenerate them. |
| M3 static arm | Code done; harness verified on macOS with a scripted model. Needs one real-model run on the 4090. |
| M4 exec arm | Code done; untested (needs x86_64 Docker, so the 4090 machine). |
| M5 batch and resume | Not started |
| M6 scoring | Done |
| M7 README | Not started |

Decisions made: the model is `devstral-small-2`, served with Ollama on the 4090 machine. That machine runs Windows 11 Home and has a working Docker. See the "Decided" notes in SETUP.md.

## Who works where (parallel work, avoid merge conflicts)

- **4090 machine (Windows):** serving and machine validation only. Follow `docs/4090-machine.md`. Write results in `docs/serving.md`. Don't edit `src/`, `tests/`, `data/`, or `configs/`. Work on the branch `serving/4090`.
- **Main dev machine:** M1, then M3 to M5 code, then M7. Owns `src/`, `tests/`, `configs/`, and `docker/`.
- Before committing, both sides run `uv run pytest`, which must pass.

## Conventions

- Every workflow is an `execloc` subcommand in `src/execloc/cli.py`. The `sample`, `gold`, and `score` commands never need `.env`, Docker, or a model endpoint.
- Write JSONL with `\n` line endings and UTF-8 (`execloc.dataset.write_jsonl`), so files are byte-identical on Windows.
- The rules for building gold locations and for scoring are in the docstrings at the top of `src/execloc/gold.py` and `src/execloc/score.py`. If you change a rule, change its tests too.
