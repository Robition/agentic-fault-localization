# Harness fixes and retest status, 2026-10-27

The machine owner authorized these changes after reviewing the first real-model smoke failures. Code commit: `9f27d05d424df9348a58afaa039ab394b763c189` on `smoke/4090`.

## Changes

- Both arms now use `bash -lc`. On the original execution container this activated `/opt/miniconda3/envs/testbed/bin/python` (Python 3.9.20) with NumPy 1.25.2 and pytest 7.4.0, instead of the base Python without those dependencies. The activation probe is recorded in `docs/4090-smoke.md`.
- Both arms use the same `LocalizationAgent`. Budget reminders occur with five, three, and one model calls remaining. The final three calls of the existing 40-call cap accept only the documented final-submission command. Further investigation commands receive a correction and are not executed. Corrections still consume the already-counted call; token usage is retained once.
- The submission phase reports the model's own candidates. It does not read gold locations, create guesses, or make extra calls. Explicit empty submissions remain localization failures. Refusal or malformed responses still produce a failure rather than a manufactured successful result.
- Both arms use `ManagedDockerEnvironment`. Cleanup removes only its own container with an argv-based subprocess call, `shell=False`, and a ten-second timeout. Cleanup is idempotent after success or confirmed absence. Other failures retain the owned container ID for retry and log the problem without masking the agent outcome.
- No dependency, frozen dataset, or scoring-rule changes were made. The original diagnostic run and its failures remain intact.

## Validation completed

```text
tests/test_agent.py          8 passed
tests/test_environments.py   9 passed
Full regression suite      53 passed in 2.11s
```

The scripted agent tests cover successful final submission within 40 calls, correction of late investigative commands, refusal without extra calls, token accounting after model/guard format errors, malformed submissions, and honest abstention. Cleanup tests cover Windows executable paths, removing only the owned container, partial initialization, duplicate/destructor cleanup, missing containers, timeout/OS errors, retry, and preservation of an original exception. Both arm configs retain identical shared settings.

## Live retest blocked

The fixed real-model paired test has **not run**, and automatic cleanup has **not yet been verified against live containers**.

Before inference, a live container preflight attempted to start the static container `minisweagent-b59e8605`. Docker did not return an ID within its 120-second startup timeout. Independent diagnostics found engine-wide API timeouts connecting to `192.168.65.7:2375`; entering the `docker-desktop` WSL distribution also stalled. Ubuntu initially remained responsive.

Docker's kernel log also contained repeated read I/O errors on `loop1`. Its backing file was not identified, so these observations do not establish drive failure or data corruption.

Recovery attempts:

- Docker Desktop stop with a 25-second timeout returned a timeout while WSL clients remained alive. Backend logs showed services closing.
- The setup's own Ubuntu `sleep infinity` keep-alive clients were stopped.
- A subsequent `wsl --shutdown` call did not return within 35 seconds; only the timed-out client was canceled.

At this point the live validation is stopped pending a Windows restart. No additional issue runs, results, or successful smoke claims were created. M1 doctor, M5 batch/resume, and M7 quickstart remain outside these fixes.

## Resume after Windows restart

Start Docker Desktop and Ubuntu/Ollama, confirm the local Devstral endpoint and its 32K GPU context, then repeat live preflight and automatic-cleanup checks. Inspect the known attempted container name above and remove it only if it exists and still belongs to this test.

Use a fresh paired run ID, for example `smoke-4090-20261027-fixed`, with the same frozen `astropy__astropy-13579` instance in both arms. Preserve the prior `smoke-4090-20261026` outcomes. Do not start the full batch until both arms return parseable nonempty locations and the prepared execution environment and automatic cleanup are confirmed.
