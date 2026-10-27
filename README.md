# Does running code help fault localization?

One local Devstral model and one mini-SWE-agent scaffold localize the same 100 frozen SWE-bench Verified issues in two arms. Static can read/search a read-only base checkout; execution uses the official instance image and its prepared Python environment. Each arm outputs up to five ranked file/function/line locations. The agents localize bugs rather than repair them.

## Prerequisites

Install Git and [uv](https://docs.astral.sh/uv/getting-started/installation/). `uv sync --frozen` selects the project's Python 3.12 environment; keep the committed lockfile unchanged.

The 4090 owner runs inference and both experiment arms. Start Docker Desktop manually with its Linux engine, keep Ubuntu open, and start the existing local Ollama service. The model endpoint must be `http://localhost:11434/v1`, with `devstral-small-2` available. Model serving is outside the Python environment; no GPU packages belong in this lockfile.

Teammates who only score committed results need uv and the repository. Scoring does not require `.env`, Docker, a GPU, or a model server. The execution arm is unavailable on ARM machines; the doctor reports this as a warning.

## Quickstart: one static issue in eight commands

These commands work in PowerShell and POSIX shells after installing uv and preparing the services above. These workflows currently live on `smoke/4090`, so clone that branch while the changes await review. The environment-copy command preserves an existing `.env`.

```text
git clone --branch smoke/4090 https://github.com/Robition/agentic-fault-localization.git
cd agentic-fault-localization
uv sync --frozen
uv run python -c "from pathlib import Path; p=Path('.env'); p.exists() or p.write_bytes(Path('.env.example').read_bytes())"
uv run execloc doctor
uv run execloc run --arm static --instances astropy__astropy-13579 --run-id quickstart
uv run python -c "from pathlib import Path; from execloc.dataset import read_jsonl,write_jsonl; write_jsonl(Path('runs/quickstart/gold-smoke.jsonl'),[r for r in read_jsonl(Path('data/gold.jsonl')) if r['instance_id']=='astropy__astropy-13579'])"
uv run execloc score quickstart --gold runs/quickstart/gold-smoke.jsonl
```

The runner builds the static image and fetches the exact base commit on demand. To build the image separately, run `uv run execloc build-static`. The one-row gold subset above is used only by the scorer and is never mounted into the agent's container. Default scoring evaluates the full frozen sample and counts absent predictions as misses, so use a matching gold subset for a small smoke test.

If doctor reports an unloaded model, load Devstral on the serving side and rerun doctor to observe its actual context length. Declaring 32768 in `.env` does not prove the active server has that context.

To add the execution arm to the same paired run:

```text
uv run execloc run --arm exec --instances astropy__astropy-13579 --run-id quickstart
uv run execloc score quickstart --gold runs/quickstart/gold-smoke.jsonl
```

## Local configuration and reproducibility

Edit `.env` using `.env.example` as the template. `EXECLOC_BASE_URL` and `EXECLOC_MODEL` are required; there is no hosted-provider fallback. `EXECLOC_API_KEY` is a nonempty local placeholder. Before a study run, set `EXECLOC_MODEL_REVISION` to the installed model's complete digest and retain `EXECLOC_SERVING_ENGINE=ollama` and `EXECLOC_CONTEXT_LENGTH=32768`.

The validated build was Devstral Small 2, 24.0B, Q4_K_M, digest `24277f07f62db8f9cb68e9dfc679ea1818a7fbac47a50eff0a701d3f645b63c8`. Check the actual installed build rather than assuming a mutable `latest` tag is unchanged. The doctor checks reachable model/context information without making an inference request or downloading anything.

Use one run ID for both arms and one source revision/model/config throughout that run. Use a fresh run ID after changes. The runner checks saved provenance before extending a run. Preserve the frozen `data/instances.jsonl` and `data/gold.jsonl`; do not regenerate them for the experiment.

The shared protocol allows 40 model calls and at most 2048 output tokens per response. The response cap prevents one runaway generation from occupying the server indefinitely; the existing format correction handles a response cut off before a complete action. Both arms use the same limits.

## Doctor

```text
uv run execloc doctor
```

Every line reports `PASS`, `WARN`, or `FAIL`. Checks cover Python, architecture, bounded Docker connectivity, workspace free space, the configured local endpoint/model, and active context where the server exposes it. Any failure gives a nonzero exit code; warnings alone do not. Workspace free space does not certify space in Docker Desktop's backing disk. Docker images may occupy another drive, and the full cache can exceed the workspace requirement.

## Five-instance pilot and resume

Use the first five frozen IDs for a reproducible pilot:

```text
uv run execloc run --arm static --instances astropy__astropy-13579 astropy__astropy-14096 astropy__astropy-14309 astropy__astropy-14365 astropy__astropy-14539 --run-id pilot --workers 1
uv run execloc run --arm exec --instances astropy__astropy-13579 astropy__astropy-14096 astropy__astropy-14309 astropy__astropy-14365 astropy__astropy-14539 --run-id pilot --workers 1
```

Press Ctrl+C during a pilot to stop scheduling new work. The first interrupt lets active work finish and checkpoints completed rows before returning 130. Rerun the identical command with the same run ID: saved terminal rows for that arm are skipped, while unfinished instances run. A saved `Interrupted` row is unfinished and is retried automatically. Other saved failures remain visible rather than silently retried. Use `--retry-failed` to explicitly retry failure rows under the same unchanged protocol; it replaces those rows and their trajectory files while preserving other results. Archive the previous trajectory if you need an attempt history.

Worker count defaults to one, which is recommended for this single-GPU Ollama server. `--workers N` supports multiple independent instances, but can increase GPU/server contention. The coordinator writes each outcome atomically. An OS-backed run lock prevents separate processes from writing the same run ID at once; run the two arms serially. Its ignored `.run.lock` file may remain after exit: the OS lock, rather than file presence, determines whether a run is active.

## Full frozen sample

After the five-instance pilot and interruption/resume checks pass:

```text
uv run execloc run --arm static --all --run-id verified-v1 --workers 1
uv run execloc run --arm exec --all --run-id verified-v1 --workers 1 --prune
uv run execloc score verified-v1
```

`--all` selects all 100 frozen rows. It cannot be combined with `--instances`. Images are pulled only when needed. `--prune` removes each completed execution image without forcing deletion; it does not run a global Docker prune or delete the shared static image. Subsequent retries may download a removed image again. Keep enough space on Docker's backing drive for at least the largest active image and its layers.

An ordinary per-instance exception or unparseable answer becomes a saved failure row. A failed study preflight is recorded and stops further scheduling so an invalid environment is not treated as a valid arm. Review `exit_status` and `parse_error`, not just the CLI's success code. Completed prediction rows are the resume checkpoints; raw trajectories alone are not checkpoints.

## Outputs and sharing

- `results/<run-id>/predictions.jsonl`: predictions, status, tokens, steps, time, and execution attempts.
- `results/<run-id>/meta.json`: source/config/sample and model provenance.
- `results/<run-id>/scores.jsonl` and `summary.csv`: per-issue and aggregate scoring.
- `runs/<run-id>/<arm>/*.traj.json`: ignored raw trajectories; preserve or zip them for diagnosis.
- `cache/`: ignored dataset/repository caches and internal runtime state.

Commit the `results/` files and relevant validation documentation. Do not commit `.env`, caches, or raw trajectories. Partial or failed outcomes remain part of the results. A broad predicted line range earns a line hit if it overlaps gold; this is not an exact single-line metric.

## Serving the model on Windows + Ubuntu/WSL

The validated machine runs Ollama inside Ubuntu and the Python harness natively in Windows. Docker Desktop uses its Linux/WSL engine. Keep Docker Desktop and Ubuntu running while experiments are active. Use the same loopback endpoint from the Windows harness; do not expose it for remote teammates.

The Ollama systemd service has these persistent settings:

```ini
[Service]
Environment="OLLAMA_CONTEXT_LENGTH=32768"
Environment="OLLAMA_KEEP_ALIVE=24h"
Environment="OLLAMA_FLASH_ATTENTION=1"
Environment="OLLAMA_KV_CACHE_TYPE=q8_0"
```

For a new serving installation, follow [Ollama's environment-variable instructions](https://docs.ollama.com/faq#setting-environment-variables-on-linux) to apply the override and restart the service. Load the model on that side, then use the project doctor to observe actual serving state. [Ollama's FAQ](https://docs.ollama.com/faq) explains context length, GPU residency, flash attention, and KV cache configuration.

If Docker Desktop says it is running but doctor times out, check the engine manually before launching a batch. Previous validation encountered a stuck Docker/WSL backend. A Windows restart and manually opening Docker restored it; the runner cannot repair a frozen engine. See [serving and harness validation](docs/4090-harness-fixes.md) and [the successful paired smoke](docs/4090-smoke-fixed.md).

## Development

```text
uv sync --frozen
uv run pytest
uv run execloc --help
```

Do not add model-serving/GPU packages to project dependencies. Both arms must retain the same prompt, model, budgets, temperature, timeout, and observation handling. `sample`, `gold`, and `score` must remain usable without a model endpoint or Docker.
