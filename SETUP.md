# Setup brief: execloc

This brief tells a coding agent how to set up the repository for a CS527 course project. Work through the milestones in order. Don't start a milestone until the previous one passes its check.

## Project in one paragraph

The project measures whether code execution helps an LLM agent localize bugs. One agent scaffold and one open-weight model run on 100 SWE-bench Verified instances in two arms: a static arm that can only read and search the repository, and an execution arm that can also run code. Each run outputs a ranked list of suspected bug locations. The project scores those locations against the gold patch at file, function, and line level, and records tokens, wall-clock time, and steps. Nobody builds a new system. The repository exists to make setup, inference, and analysis reproducible.

## Hard rules

- **No paid APIs.** All inference goes to a self-hosted, OpenAI-compatible endpoint. The code must read the endpoint from `EXECLOC_BASE_URL` and fail if it's unset. Never fall back to a hosted provider.
- **Lock everything.** Use uv. Commit `pyproject.toml`, `uv.lock`, and `.python-version`. Every command in the docs starts with `uv run`.
- **Keep GPU and serving packages out of the lockfile.** Don't add vLLM, torch, or llama-cpp to the project dependencies. They break dependency resolution on macOS and Windows. Model serving is documented separately and runs outside this environment.
- **No make, no bash scripts for required workflows.** Teammates use Linux, macOS, and Windows. Expose every workflow as a subcommand of one Python CLI.
- **Verify, don't guess.** Before you use any mini-SWE-agent or SWE-bench API, read the installed package's source or docs. Don't rely on remembered flags or class names.
- **Stop and ask the human** when a section says "Decision needed," or when a check fails twice.

## Machines

| Machine | Role |
|---|---|
| The RTX 4090 machine | Serves the model on localhost and runs both arms. Its owner launches all experiment runs and commits `results/`. |
| Every other machine (Linux, macOS, Windows) | Data preparation, scoring, and analysis from the committed files. No Docker, GPU, or model endpoint required. |

Nobody reaches the model remotely. `EXECLOC_BASE_URL` points to localhost on the 4090 machine. On other machines, `sample`, `gold`, and `score` must work with no `.env` file and no Docker installed.

Decided (2026-10-15): the 4090 machine runs Windows 11 Home (25H2), and Docker works on it. It has an i7-14700K and 32 GB of DDR5. Its NVIDIA driver (610.88) runs in WDDM mode.

## Repository layout

```
execloc/
  pyproject.toml
  uv.lock
  .python-version          # 3.12
  .gitattributes           # * text=auto eol=lf
  .gitignore               # runs/, cache/, .env, .venv/
  .env.example
  README.md                # quickstart for teammates
  CLAUDE.md                # standing rules for coding agents (copy "Hard rules")
  configs/
    static.yaml
    exec.yaml
  data/
    instances.jsonl        # committed, frozen
    gold.jsonl             # committed, frozen
  docker/
    static.Dockerfile
  src/execloc/
    cli.py                 # doctor, sample, gold, run, score
    doctor.py
    sample.py
    gold.py
    run.py
    parse.py
    score.py
  tests/
  runs/                    # gitignored: raw trajectories
  results/                 # committed: predictions and metadata per run
```

## Environment variables

Define these in `.env.example`. Load them from `.env`.

| Variable | Meaning |
|---|---|
| `EXECLOC_BASE_URL` | OpenAI-compatible endpoint, for example `http://gpu-box:8000/v1` |
| `EXECLOC_MODEL` | Model name as the server exposes it |
| `EXECLOC_API_KEY` | Any non-empty string. The server ignores it. |

## The two arms

Both arms use mini-SWE-agent with the same model, system prompt, step limit, per-command timeout, output truncation, and temperature 0. They differ only in the container.

- **Static arm.** A single generic image built from `docker/static.Dockerfile`. It contains bash, coreutils, grep, find, sed, and git. It contains no Python interpreter and no project dependencies. The repository at the instance's base commit is mounted read-only at `/testbed`. Fetch that commit with a depth-1 fetch by SHA so no later history is present.
- **Execution arm.** The official SWE-bench image for the instance, which has the repository at `/testbed` and a working environment.

Don't add tools to one arm that the other arm lacks, apart from the ability to execute code.

The task prompt asks for localization only. The agent doesn't fix the bug. It finishes by printing up to five ranked locations as JSON between the markers `<<<LOCATIONS` and `LOCATIONS>>>`. Each location has `file`, `function` (qualified name, or null), and `start_line` and `end_line`.

## Data contracts

Fix these schemas first. Teammates build scoring and analysis against them.

`data/instances.jsonl`
```json
{"instance_id": "...", "repo": "...", "base_commit": "..."}
```

`data/gold.jsonl`
```json
{"instance_id": "...", "files": ["..."], "functions": [{"file": "...", "name": "..."}], "lines": [{"file": "...", "start": 0, "end": 0}]}
```

`results/<run_id>/predictions.jsonl`
```json
{"instance_id": "...", "arm": "static|exec", "locations": [], "parse_error": false, "steps": 0, "input_tokens": 0, "output_tokens": 0, "wall_seconds": 0.0, "exec_attempts": 0, "exit_status": "..."}
```

`results/<run_id>/meta.json` records the git commit, model name, model revision or quantization, serving engine, context length, config hashes, and hostname.

Rules:

- Never drop an instance. A crash or an unparseable answer produces a row with empty `locations` and the reason.
- Gold locations come from the gold patch against the base commit, excluding test files. Lines are the modified or removed lines in the pre-patch file. For a hunk that only adds lines, use the enclosing function of the insertion point. Map lines to functions with the standard library `ast` module, because every instance is Python.
- `exec_attempts` counts commands that invoke python, pytest, tox, or a test runner. Record it in both arms.

## Milestones

### M0: skeleton

Create the layout, `pyproject.toml`, and the CLI with stub subcommands. Dependencies: `mini-swe-agent`, `datasets`, `unidiff`, `pandas`, `scipy`, `matplotlib`, `python-dotenv`, `pyyaml`, and `pytest` as a dev dependency. Run `uv lock`.

Check: `uv sync --frozen` and `uv run execloc --help` succeed.

### M1: doctor

`uv run execloc doctor` reports, with a clear pass, warn, or fail for each line: Python version, CPU architecture, Docker reachable, free disk space, endpoint reachable, model listed by the endpoint, and server context length if the endpoint exposes it. On ARM machines, warn that the execution arm is unavailable.

Check: the command runs on the current machine and exits nonzero only on a fail.

### M2: data

`uv run execloc sample` draws 100 instances from SWE-bench Verified with a fixed seed, stratified by repository: at least three per repository where available, and the remainder proportional. `uv run execloc gold` builds `gold.jsonl`. Commit both outputs. The commands refuse to overwrite existing files without `--force`.

Check: both files have 100 rows. Unit tests cover a modify-only hunk, an add-only hunk, a multi-file patch, and a module-level change with no enclosing function.

### M3: static arm, one instance

Build the static image. Implement `uv run execloc run --arm static --instances <id>`. Write the trajectory to `runs/` and a row to `predictions.jsonl`.

Check: one instance completes, the locations parse, and `python --version` fails inside the container.

### M4: execution arm, one instance

Implement `--arm exec` with the official instance image. Skip with a clear message on ARM.

Check: the same instance completes, and a Python command succeeds inside the container.

### M5: batch and resume

Add `--all`, `--workers N`, and resume: rerunning skips instances that already have a row for that arm and run ID. Pull images on demand and offer `--prune` to delete each image after use.

Check: interrupt a five-instance run and rerun it. It finishes without repeating completed instances.

### M6: scoring

`uv run execloc score <run_id>` writes per-instance Hit@1, Hit@3, and Hit@5 at file, function, and line level, plus a summary table per arm.

Check: unit tests with hand-built predictions produce the expected hits.

### M7: README

Write a quickstart that takes a teammate from clone to one static run in under ten commands: install uv, `uv sync --frozen`, copy `.env.example`, `doctor`, `run`, `score`. Add a separate section for serving the model.

## Model serving notes

Decided (2026-10-15): the model is `devstral-small-2` (about 24B), served with Ollama. Quantization and revision are still to be confirmed with `ollama show devstral-small-2` and recorded in `meta.json`. Original brief: The 4090 has 24 GB of memory, so choose a coding model of roughly 24B to 30B parameters at 4-bit quantization, with tool-free bash-style prompting support and at least a 32K context. Don't use Qwen2.5-Coder-32B, because prior work already reports it.

- Any OpenAI-compatible server works: vLLM, the llama.cpp server, or Ollama.
- Set the context length explicitly. Some servers default to a small context and silently truncate, which corrupts results without an error.
- Pin the exact model revision and quantization, and record them in `meta.json`.
- Run a five-instance smoke test in both arms before the full experiment. If most answers fail to parse or the context overflows, change the model or the step limit before running all 200.
