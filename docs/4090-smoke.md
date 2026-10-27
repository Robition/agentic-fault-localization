# RTX 4090 real-model smoke test, 2026-10-26

Run ID: `smoke-4090-20261026`.
Harness commit: `ba37f752f4faf494d52fcd6eb2e1be40289d34c3`.
Instance: `astropy__astropy-13579`, frozen base `0df94ff7097961e92fd7812036a24b145bc13ca8`.

## Outcome

Both arms exercised the real Devstral model and wrote valid prediction rows and full trajectories. Neither arm submitted locations before its 40-step limit. This smoke test does **not** pass the gate for batch experiments.

- Static: `LimitsExceeded`, 40 steps, 144.3 seconds, 463,627 input tokens, 4,738 output tokens, two execution attempts, empty locations.
- Execution: `LimitsExceeded`, 40 steps, 137.1 seconds, 363,518 input tokens, 4,922 output tokens, five execution attempts, empty locations.
- Both rows have `parse_error: true` because no final locations block was submitted. A CLI exit code of zero did not mean that localization succeeded.
- Token counts were independently recomputed from trajectories and matched the rows. They count the prompt on every call, including reused history. They are not counts of uncached tokens.
- Peak request prompt sizes were 20,410 tokens (static) and 15,860 (execution), below the active 32,768-token context. No context-overflow error was observed.

Predictions and metadata are in `results/smoke-4090-20261026/`. Raw trajectories and console logs are in the ignored directory `runs/smoke-4090-20261026/`.

## Environment and checks

- `uv sync --frozen` succeeded; all 36 committed tests passed in 2.05 seconds on Windows/Python 3.12.14.
- Ollama served `devstral-small-2`, 24.0B, Q4_K_M. Manifest digest: `24277f07f62db8f9cb68e9dfc679ea1818a7fbac47a50eff0a701d3f645b63c8`.
- `/api/ps` confirmed context length 32768 and `size == size_vram == 17130061823` after real `/v1` requests.
- Persistent settings remained context 32768, keep-alive 24h, flash attention enabled, and q8_0 KV cache.
- The server was warmed before both agent runs. Its approximately 199.75-second cold model load is separate from the per-arm times above. Runner times include container setup and preflight.
- Both arms used the committed shared config, temperature zero, 40 steps, 60-second command timeout, and disabled container networking. Shared config hash: `a6f74ab418b98fe3`.
- The static image built successfully. Its repository mount had `RW: false`; HEAD matched the frozen base, exactly one commit was visible, and both `python` and `python3` were unavailable.
- The execution image was `docker.io/swebench/sweb.eval.x86_64.astropy_1776_astropy-13579:latest`, previously pinned to digest `sha256:f3dc11a37649430d6a7a3aac4858477db8f19d9a941f056f875e8e566066ffe3`.
- Execution preflight passed. HEAD was setup commit `62194260181d107f2f6630d4588887fbf46f5a0c`. History counts were two total, one after the base, and zero off HEAD. The committed preflight rejects Python-source changes between base and HEAD. The tree was clean before the agent.
- History trimming hides ordinary references and reflogs; it does not prune unreachable Git objects. This test does not establish that all old objects are physically inaccessible.
- `meta.json` records the same source commit for both arms. Execution's `git_dirty: true` reflects the new untracked `results/` directory produced by the first arm; no source/config/data/test files changed.

## Problems found and verified next changes

### Execution uses the wrong Python environment

The shared config currently sets `interpreter: ["bash", "-c"]`. In that shell, Python is `/opt/miniconda3/bin/python`, version 3.11.5. Agent commands failed with missing `numpy`, `extension_helpers`, and `pytest`. Thus the current execution run could not use the image's installed test dependencies.

A separate diagnostic in the same container with `bash -lc` successfully loaded the prepared environment:

```text
/opt/miniconda3/envs/testbed/bin/python
3.9.20
1.25.2
7.4.0
```

The final two lines are NumPy and pytest versions. Proposed shared-config change: use `interpreter: ["bash", "-lc"]`, then recheck both arms so their shared configuration remains identical. No config was edited during this validation.

### No submission before the step cap

Both agents kept investigating until `LimitsExceeded`; the saved outcomes must remain failures. Add a shared mechanism that reserves part of the existing budget for final submission or explicitly warns the agent about remaining steps. Validate that it produces a final ranked list even when uncertain. Use a fresh run ID after any config or harness change; do not overwrite this diagnostic run.

### Native Windows container cleanup

The installed mini-SWE-agent 2.4.6 cleanup uses a POSIX shell command with `/dev/null`, which produced `The system cannot find the path specified.` on Windows. The two containers created by this test were explicitly removed after their diagnostics. A Windows-compatible subprocess cleanup is needed before batch runs.

### Prompt compliance

The execution agent created an untracked `test_issue.py` despite the shared instruction not to modify files. Its creation and failed execution are preserved in the trajectory. Container writability is part of the current design; the prompt alone did not enforce this instruction.

## Operational notes

Docker Desktop initially reported running while its Linux engine timed out. An initial static-image build failed before either experiment. Restarting restored a responsive engine and the subsequent build succeeded. The restart CLI eventually reported a stop timeout even though the restarted engine was working; no further restart was attempted during the runs.

No full batch was launched. `CLAUDE.md` assigns source/config changes to the main developer and says to stop and ask after two failed checks. The two failed localization outcomes are saved for review before changing the harness or running more instances.
