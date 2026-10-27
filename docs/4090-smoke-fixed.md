# Successful RTX 4090 paired smoke test, 2026-10-27

Run ID: `smoke-4090-20261027-fixed`.
Harness revision: `1bd8c46d8d83d29bd1d624293a01a5ee846df131`, containing code fixes from `9f27d05`.
Instance: `astropy__astropy-13579`, frozen base `0df94ff7097961e92fd7812036a24b145bc13ca8`.

## Result

Both arms completed with `Submitted`, one nonempty parsed location, and no parse error. The real-model single-instance smoke gate now passes.

- Static: 28 model calls, 95.7 seconds, 245,389 input tokens, 3,384 output tokens, two attempted execution commands.
- Execution: 36 model calls, 211.3 seconds, 354,023 input tokens, 9,130 output tokens, eight attempted execution commands.
- Both ranked `astropy/wcs/wcsapi/wrappers/sliced_wcs.py`, `SlicedLowLevelWCS.world_to_pixel_values`, lines 245–270 first.
- Both scored Hit@1 at file, function, and line-overlap levels against the frozen gold. Line scoring means the predicted range overlaps a gold range; this does not claim an exact single-line prediction.
- Scoring used only this instance's gold row, copied to ignored `runs/smoke-4090-20261027-fixed/gold-smoke.jsonl`. Each arm has `n=1` and `missing=0`. The frozen 100-row files were unchanged and gold was never provided to the localization agents.

This is an environment and harness validation on one issue. It does not estimate whether execution improves accuracy or cost over the full benchmark. Times are end-to-end runner times with an already loaded model. Input-token counts sum API prompt usage across calls, including reused history, rather than counting only uncached tokens.

## Fixes verified

Both containers passed live preflight with the shared `bash -lc` interpreter. The static repository was at the frozen base with read-only mounting, one visible commit, and no Python. The execution container passed the committed history checks and loaded the prepared test environment:

```text
/opt/miniconda3/envs/testbed/bin/python
NumPy 1.25.2
pytest 7.4.0
```

The execution trajectory includes successful reproduction scripts. One printed different WCS outputs before and after slicing. Earlier script errors concerned the attempted reproduction, rather than the previous missing NumPy/pytest environment failure.

The static agent submitted before any budget reminder. The execution agent received the reminder with five calls remaining and submitted on call 36. Neither needed the final three-call submission-only phase in this particular rerun; scripted tests verify that phase's enforcement, failure behavior, and token accounting. The total configured cap remains 40 for both arms.

The two separate preflight containers and both model-run containers were removed by `ManagedDockerEnvironment.cleanup`. Post-run Docker inspection confirmed the model-run containers were absent. No manual removal was needed, and no POSIX cleanup error appeared.

## Reproducibility

- Model: `devstral-small-2`, 24.0B, Q4_K_M, manifest digest `24277f07f62db8f9cb68e9dfc679ea1818a7fbac47a50eff0a701d3f645b63c8`.
- Endpoint: local Ollama `/v1`, with temperature zero and no hosted-provider fallback.
- `/api/ps` after the runs confirmed context length 32768 and `size == size_vram == 17130061823`.
- mini-SWE-agent: 2.4.6; host: Windows AMD64.
- Shared config hash: `12e728b5b05ae875`; static hash: `ad53f4cdc959105a`; execution hash: `6cb2656a4044a58d`.
- Both arms record the same source revision. Execution metadata has `git_dirty: true` because static had already generated an untracked results directory; no source/config/test/data files changed between the runs.
- Saved prediction tokens and execution-attempt counts were independently recomputed from the trajectories and matched. Peak prompt sizes were 15,218 tokens (static) and 17,723 (execution), below the active 32K context.
- Full regression gate: 53 tests passed.

## Artifacts

Committed under `results/smoke-4090-20261027-fixed/`:

- `predictions.jsonl`: the two predictions and resource measurements.
- `meta.json`: model, shared/arm config hashes, and source/host metadata.
- `scores.jsonl`: two per-instance scoring rows.
- `summary.csv`: the single-instance summary for each arm.

Ignored local `runs/smoke-4090-20261027-fixed/` holds complete trajectories, console logs, the isolated scoring subset, validation details, and serving snapshot. A ZIP of these and the committed results was prepared for handoff outside the repository in `project-setup/smoke-4090-20261027-fixed.zip`.

## Remaining work

M1 doctor, M5 batch/resume, and M7 README remain to be implemented. Run a five-instance pilot in both arms before the full 100-by-two experiment; verify interruption/resume once M5 is available.

Two previously recorded methodology limitations remain: history trimming does not prune unreachable Git objects, and the execution agent writes reproduction files despite the prompt's prohibition on file modifications. The reproduction files stayed inside disposable containers. Resolve the intended reproduction-file policy before finalizing the full experiment protocol.

The prior failed run `smoke-4090-20261026` is preserved; see `docs/4090-smoke.md`. Its empty locations were not replaced or relabeled as successes.
