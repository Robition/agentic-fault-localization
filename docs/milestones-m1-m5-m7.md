# Doctor, batch/resume, and teammate quickstart

Implementation validation on the Windows RTX 4090 machine, 2026-10-27.

## Implemented

- M1: `uv run execloc doctor` reports pass/warn/fail for Python, architecture, workspace disk, Docker Linux connectivity, the local model endpoint, model availability, and exposed active context/revision. Checks are bounded and read-only; they do not load a model or download images. Warnings alone return zero.
- M5: `--all`, `--workers N`, on-demand images, targeted non-force `--prune`, and atomic per-instance prediction checkpoints. An OS lock prevents concurrent writers to a run ID. Ctrl+C stops scheduling, drains active work, and returns 130. Rerunning skips saved terminal rows; interrupted rows resume automatically, and other failed rows require `--retry-failed`.
- Resume checks source, dependency, config, model, and frozen-sample identity. Both arms retain their own rows and provenance under one run ID.
- M7: README includes an eight-command static quickstart, a paired execution run, partial scoring, five-instance pilot, full-sample commands, resume behavior, and separate Ollama serving instructions.

## Automated checks

`uv run pytest` with a fresh workspace-local `--basetemp`: **182 passed**. Coverage includes a scripted five-instance interruption/resume without repeating completed instances, actual Windows spawn workers, cross-process lock contention and crash release, atomic replacement failure, failure retry, targeted pruning, endpoint restrictions, doctor failure/warning behavior, source-content validation, and distinguishing missing images from engine errors. Model matching preserves namespace prefixes and accepts Ollama's `:latest` alias. No GPU dependencies or lockfile changes were needed. `git diff --check` also passed.

## Machine and live pilot

The doctor initially detected a Docker engine HTTP 500 while the model endpoint remained healthy. After the owner manually opened Docker Desktop, the doctor confirmed the Linux daemon and endpoint were reachable. The unloaded model produced warnings, as intended; it must be loaded before checking actual serving context/revision.

The real interruption/resume check passed in `pilot-4090-20261027`: SIGINT after the first checkpoint drained the active second issue and returned 130. Resuming completed the remaining three issues. All five static submissions parsed; the two saved prediction rows and trajectory SHA-256 hashes remained unchanged. See `results/pilot-4090-20261027/resume-validation.json`.

The first execution response then generated more than 16,000 tokens without returning an action. The harness process was stopped and only its owned container removed. No completed execution prediction or exact API token usage was available, and no paired accuracy is claimed for that diagnostic run. Its `validation-status.json` records the interruption explicitly.

The shared configuration now caps each response at 2048 output tokens in both arms, with the existing length/format correction retained. The fresh `pilot-4090-20261027-bounded` completed all five static cases and the first three execution cases. Its real interruption/resume check also passed with unchanged completed rows and trajectory hashes. Both arms recorded source commit `6644d5f3b69ff6b292a732fb8707b6358699d9c9` and the same shared settings. Source and frozen data were unchanged during that run; execution metadata's dirty flag reflects generated results.

Execution stopped before the fourth model run because the source guard mistook permission changes for content changes. Inspection of the official `astropy__astropy-14365` image found 935 Python paths with identical Git blob IDs; only regular-file executable bits differed. Its only non-Python content change was the expected `pyproject.toml` setup adjustment. The image digest was `sha256:70da5c0d4fcbbc2cbc46bbccf8d9ac2dfd4453cf7a19f9fa150b30bb7346f2f4`, with setup commit `7983109b67a87f4293b5b018bff348db0bed9c2b` immediately after the frozen base. This matches the official build's permission setup ([build recipe](https://github.com/SWE-bench/SWE-bench/blob/v4.1.0/swebench/harness/test_spec/python.py#L245-L295)).

The corrected guard compares full blob IDs and file types from a NUL-delimited, rename-disabled Git diff. It permits unchanged regular-file contents with an executable-bit change, while continuing to reject Python content, path, existence, and type changes. Malformed diffs fail closed. The real affected image now passes preflight with Python 3.9.20, without resetting its source or rebuilding dependencies.

The affected case and fifth case were scheduled in both arms under fresh run ID `pilot-4090-20261027-image-modes`. That follow-up stopped before its first model call when Docker's Linux engine again returned HTTP 500, including for `/_ping`. The subsequent doctor correctly failed Docker while passing the local model endpoint, pinned revision, and actual 32768 context. Its initial failed row remains recorded. A manual Docker Desktop restart has been requested; the final two execution cases and the full 100-per-arm study have not run.

The three completed matched cases in the bounded pilot were scored against a dedicated three-row subset: `astropy__astropy-13579`, `astropy__astropy-14096`, and `astropy__astropy-14309`. Both arms have n=3, missing=0, valid submissions, file/function Hit@1=1.0, and line-overlap Hit@1=2/3. Other saved rows were explicitly outside that scoring subset and remain in predictions. These are calibration checks on a convenience sample; no broader accuracy or ranking conclusion is claimed. Results from different source revisions/protocols are not pooled.

## Current Docker recovery blocker

Docker Desktop 4.41.2 predates a documented WSL Resource Saver hang fix. Docker's [4.42.1 release notes](https://docs.docker.com/desktop/release-notes/#4421) say Resource Saver was disabled on WSL 2 to prevent Docker CLI hangs. Resource Saver is a possible explanation for the recurring outage, not a confirmed local cause: the inspected settings file contained no explicit override and logs showed no matching pause event.

Restart Docker Desktop manually and check Resource Saver in its settings. Disable it if enabled while troubleshooting this older version. Rerun `uv run execloc doctor`; Docker must pass before launching more cases. Preserve the loaded model and its existing configuration. Use a fresh run ID after source commits, as documented in README, and validate the remaining two IDs in both arms before launching the full experiment. No factory reset, storage deletion, or dependency/source checkout was performed as recovery.

Image lookup now treats only an explicit `no such image` response as a cache miss. HTTP 500, daemon, and invalid-reference errors stop immediately with the actual diagnostic instead of triggering a misleading build or pull. The static image was not proven missing during this outage; its lookup had failed.

The earlier paired single-issue real-model smoke passed both arms; see [4090-smoke-fixed.md](4090-smoke-fixed.md). Neither that smoke nor these convenience pilots constitute the full 100-issue experiment. All five pilot IDs are the first frozen rows, from Astropy, so they do not establish accuracy across repositories.
