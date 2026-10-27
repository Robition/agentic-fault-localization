"""Run one arm of the experiment with mini-swe-agent.

Both arms use configs/base.yaml merged with configs/<arm>.yaml. They share the
agent, prompt, model, step limit, timeout, and output truncation, and differ
only in the Docker container:

- static: the image built from docker/static.Dockerfile (no Python), with the
  repository at the base commit (depth-1 fetch by SHA) mounted read-only at
  /testbed.
- exec: the official SWE-bench image for the instance.

Before the agent starts, a preflight:
- in the exec arm, hides git history older than the base commit and removes
  all branches, tags, and remotes (see trim_exec_history), so both arms see
  only the base commit (plus, in exec, SWE-bench's own setup commits);
- checks that no commit after the base is reachable in either arm;
- runs `python --version`, which must fail in the static arm and succeed in
  the exec arm.
If any check fails the run stops, because the arms would not be what the
experiment claims.

For each instance this writes runs/<run_id>/<arm>/<instance_id>.traj.json and
replaces that instance's row for this arm in results/<run_id>/predictions.jsonl.
A crash still produces a row, with empty locations and the reason in
exit_status.
"""

from __future__ import annotations

import hashlib
import importlib.metadata
import ipaddress
import json
import multiprocessing
import os
import platform
import re
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, wait
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

import yaml

from execloc.dataset import load_verified, read_jsonl
from execloc.parse import count_exec_attempts, count_tokens, parse_locations

REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIG_DIR = REPO_ROOT / "configs"
CACHE_DIR = REPO_ROOT / "cache"
RUNS_DIR = REPO_ROOT / "runs"
RESULTS_DIR = REPO_ROOT / "results"
STATIC_DOCKERFILE = REPO_ROOT / "docker" / "static.Dockerfile"
ARMS = ("static", "exec")
ARM_KEYS = {"arm", "repo_mount", "environment"}  # the only keys an arm file may set


class RunError(Exception):
    """A setup problem that should stop the whole run."""

    row: dict | None = None


class InstanceInterrupted(KeyboardInterrupt):
    """Carry the partial result if a worker itself is interrupted."""

    def __init__(self, row: dict):
        self.row = row


# --------------------------------------------------------------------------- #
# Config
# --------------------------------------------------------------------------- #


def _merge(a: dict, b: dict) -> dict:
    out = dict(a)
    for k, v in b.items():
        out[k] = _merge(out[k], v) if isinstance(out.get(k), dict) and isinstance(v, dict) else v
    return out


def load_arm_config(arm: str, config_dir: Path = CONFIG_DIR) -> dict:
    base = yaml.safe_load((config_dir / "base.yaml").read_text(encoding="utf-8"))
    over = yaml.safe_load((config_dir / f"{arm}.yaml").read_text(encoding="utf-8"))
    extra = set(over) - ARM_KEYS
    if extra:
        raise RunError(f"{arm}.yaml may only set {sorted(ARM_KEYS)}; found {sorted(extra)}")
    return _merge(base, over)


def endpoint_from_env() -> dict:
    """Read the model endpoint from .env / the environment. Never fall back."""
    from dotenv import load_dotenv

    load_dotenv(REPO_ROOT / ".env")
    base_url = os.environ.get("EXECLOC_BASE_URL", "").strip()
    model = os.environ.get("EXECLOC_MODEL", "").strip()
    if not base_url:
        raise RunError("EXECLOC_BASE_URL is not set. Copy .env.example to .env and fill it in.")
    if not model:
        raise RunError("EXECLOC_MODEL is not set. Copy .env.example to .env and fill it in.")
    _validate_base_url(base_url)
    return {
        "base_url": base_url,
        "model": model,
        "api_key": os.environ.get("EXECLOC_API_KEY", "").strip() or "local",
    }


def _validate_base_url(base_url: str) -> None:
    """Fail closed without echoing a possibly credential-bearing URL."""
    message = (
        "EXECLOC_BASE_URL must be a loopback-only HTTP(S) endpoint without "
        "credentials, query, or fragment, with a valid port."
    )
    try:
        parsed = urlsplit(base_url)
        host = parsed.hostname or ""
        local = host.lower() == "localhost"
        if not local:
            try:
                local = ipaddress.ip_address(host).is_loopback
            except ValueError:
                local = False
        if (
            parsed.scheme not in ("http", "https") or not local
            or parsed.username is not None or parsed.password is not None
            or "?" in base_url or "#" in base_url
            or any(character.isspace() for character in base_url)
            or (parsed.port is not None and not 1 <= parsed.port <= 65535)
        ):
            raise ValueError
    except ValueError:
        raise RunError(message) from None


def model_config(cfg: dict, endpoint: dict) -> dict:
    m = dict(cfg["model"])
    # The "openai/" prefix makes litellm speak the OpenAI-compatible protocol to api_base.
    m["model_name"] = f"openai/{endpoint['model']}"
    m["model_kwargs"] = {
        **m.get("model_kwargs", {}),
        "api_base": endpoint["base_url"],
        "api_key": endpoint["api_key"],
    }
    return m


def config_hash(cfg: dict) -> str:
    return hashlib.sha256(json.dumps(cfg, sort_keys=True).encode()).hexdigest()[:16]


# --------------------------------------------------------------------------- #
# Docker and repository setup
# --------------------------------------------------------------------------- #


def _docker(*args: str, timeout: float = 15, check: bool = True) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(
            ["docker", *args], capture_output=True, text=True, timeout=timeout, check=check,
            encoding="utf-8", errors="replace",
        )
    except FileNotFoundError:
        raise RunError("docker is not installed or not on PATH.")
    except subprocess.TimeoutExpired:
        raise RunError(f"docker {' '.join(args[:2])} timed out after {timeout}s; check Docker Desktop.")
    except subprocess.CalledProcessError as e:
        raise RunError(f"docker {' '.join(args[:2])} failed: {(e.stderr or e.stdout or '').strip()}")


def image_exists(image: str) -> bool:
    return _docker("image", "inspect", image, check=False).returncode == 0


def build_static_image(image: str) -> None:
    print(f"Building {image} from {STATIC_DOCKERFILE.relative_to(REPO_ROOT)} ...", file=sys.stderr)
    _docker(
        "build", "-f", str(STATIC_DOCKERFILE), "-t", image, str(STATIC_DOCKERFILE.parent),
        timeout=1800,
    )


def ensure_image(image: str, arm: str) -> None:
    if image_exists(image):
        return
    if arm == "static":
        build_static_image(image)
    else:
        # Allow multi-GB image downloads while still bounding an unresponsive engine.
        print(f"Pulling {image} ...", file=sys.stderr)
        _docker("pull", image, timeout=1800)


def _git(*args: str, cwd: Path) -> None:
    proc = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RunError(f"git {' '.join(args)} failed: {proc.stderr.strip()}")


def checkout_base_commit(repo: str, commit: str, dest: Path) -> Path:
    """Depth-1 fetch of `commit` into dest, so no later history is present."""
    if (dest / ".git").is_dir():
        head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=dest, capture_output=True, text=True)
        if head.returncode == 0 and head.stdout.strip() == commit:
            return dest
        raise RunError(f"{dest} exists but is not at {commit}; delete it and rerun.")
    dest.mkdir(parents=True, exist_ok=True)
    _git("init", "-q", cwd=dest)
    # Keep LF line endings and long paths on Windows so files match the Linux images.
    _git("config", "core.autocrlf", "false", cwd=dest)
    _git("config", "core.longpaths", "true", cwd=dest)
    _git("fetch", "-q", "--depth", "1", f"https://github.com/{repo}.git", commit, cwd=dest)
    _git("checkout", "-q", "FETCH_HEAD", cwd=dest)
    return dest


def swebench_image(instance_id: str) -> str:
    from minisweagent.run.benchmarks.swebench import get_swebench_docker_image_name

    return get_swebench_docker_image_name({"instance_id": instance_id})


def environment_config(cfg: dict, instance: dict) -> dict:
    env = dict(cfg["environment"])
    env["run_args"] = list(env.get("run_args", []))
    if cfg["arm"] == "exec":
        env["image"] = swebench_image(instance["instance_id"])
    if cfg.get("repo_mount") == "readonly":
        repo_dir = checkout_base_commit(
            instance["repo"], instance["base_commit"], CACHE_DIR / "repos" / instance["instance_id"]
        )
        env["run_args"] += ["--mount", f"type=bind,source={repo_dir.resolve()},target=/testbed,readonly"]
    return env


def _sh(env, command: str) -> dict:
    return env.execute({"command": command})


def trim_exec_history(env, base_commit: str) -> None:
    """Make the exec image's git history match the static arm's.

    SWE-bench images hold the full upstream history, and HEAD may sit on a
    setup commit made on top of the base commit (for astropy, a one-line
    pyproject.toml change the environment needs). Keep the working tree as the
    image built it, but hide every commit older than the base commit and drop
    all branches, tags, remotes, and reflogs. The agent then sees the base
    commit plus any setup commits, just as the static arm sees only the base
    commit. Stop if a setup commit changes a .py file, because that would
    shift line numbers relative to gold.
    """
    b = base_commit
    check = _sh(env, f"git merge-base --is-ancestor {b} HEAD && git diff --name-only {b} HEAD")
    if check["returncode"] != 0:
        raise RunError(f"exec image HEAD does not descend from base commit {b}: {check['output'].strip()}")
    changed_py = [f for f in check["output"].split() if f.endswith(".py")]
    if changed_py:
        raise RunError(f"exec image changes .py files after the base commit: {changed_py}")
    trim = _sh(env, (
        "set -e; git checkout -q --detach; "
        "git for-each-ref --format='%(refname)' | while read -r r; do git update-ref -d \"$r\"; done; "
        "for r in $(git remote); do git remote remove \"$r\"; done; "
        f"git rev-parse {b}^{{commit}} > .git/shallow; "
        "git reflog expire --expire=now --all"
    ))
    if trim["returncode"] != 0:
        raise RunError(f"trimming git history failed: {trim['output'].strip()}")


def check_history(env, arm: str, base_commit: str) -> None:
    """No commit after the base may be reachable, apart from setup commits on HEAD."""
    out = _sh(env, f"git rev-list --all --count && git rev-list --count {base_commit}..HEAD && git rev-list --all --count ^HEAD")
    nums = out["output"].split()
    if out["returncode"] != 0 or len(nums) != 3:
        raise RunError(f"could not inspect git history in the {arm} container: {out['output'].strip()}")
    total, after_base, off_head = map(int, nums)
    if off_head or total - after_base != 1:
        raise RunError(
            f"{arm} container git history not trimmed: {total} commits reachable, "
            f"{after_base} after base, {off_head} not on HEAD"
        )


def preflight(env, arm: str, base_commit: str) -> None:
    if arm == "exec":
        trim_exec_history(env, base_commit)
    check_history(env, arm, base_commit)
    out = env.execute({"command": "python --version || python3 --version"})
    has_python = out["returncode"] == 0
    if arm == "static" and has_python:
        raise RunError(f"static arm container can run Python: {out['output'].strip()}")
    if arm == "exec" and not has_python:
        raise RunError(f"exec arm container cannot run Python: {out['output'].strip()}")
    ls = env.execute({"command": "ls /testbed | head -1"})
    if ls["returncode"] != 0 or not ls["output"].strip():
        raise RunError(f"/testbed is empty or missing in the {arm} container")


# --------------------------------------------------------------------------- #
# Running
# --------------------------------------------------------------------------- #


def _row(instance_id: str, arm: str, **kw) -> dict:
    row = {
        "instance_id": instance_id, "arm": arm, "locations": [], "parse_error": True, "steps": 0,
        "input_tokens": 0, "output_tokens": 0, "wall_seconds": 0.0, "exec_attempts": 0, "exit_status": "",
    }
    row.update(kw)
    return row


def run_instance(instance: dict, task: str, cfg: dict, endpoint: dict, traj_path: Path) -> dict:
    from execloc.agent import LocalizationAgent
    from minisweagent.environments import get_environment
    from minisweagent.models import get_model

    arm, iid = cfg["arm"], instance["instance_id"]
    start = time.time()
    env = agent = None
    fatal_error = None
    interrupted = False
    try:
        env_cfg = environment_config(cfg, instance)
        ensure_image(env_cfg["image"], arm)
        env = get_environment(env_cfg)
        preflight(env, arm, instance["base_commit"])
        model = get_model(config=model_config(cfg, endpoint))
        agent = LocalizationAgent(model, env, **cfg["agent"], output_path=traj_path)
        result = agent.run(task=task)
        exit_status = result.get("exit_status", "")
        submission = result.get("submission", "")
    except RunError as e:
        fatal_error = e
        exit_status, submission = f"RunError: {e}"[:300], ""
    except KeyboardInterrupt:
        interrupted = True
        exit_status, submission = "Interrupted", ""
    except Exception as e:  # never drop an instance
        exit_status = f"{type(e).__name__}: {e}"[:300]
        submission = ""
    finally:
        if env is not None:
            env.cleanup()

    messages = agent.messages if agent else []
    locations, parse_error = parse_locations(submission)
    inp, out = count_tokens(messages)
    row = _row(
        iid, arm, locations=locations, parse_error=parse_error, steps=agent.n_calls if agent else 0,
        input_tokens=inp, output_tokens=out, wall_seconds=round(time.time() - start, 1),
        exec_attempts=count_exec_attempts(messages), exit_status=exit_status,
    )
    if fatal_error is not None:
        fatal_error.row = row
        raise fatal_error
    if interrupted:
        raise InstanceInterrupted(row)
    return row


def _atomic_write(path: Path, text: str) -> None:
    """Replace one complete checkpoint, on the same filesystem as its temp file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", newline="\n", dir=path.parent,
            prefix=f".{path.name}.", suffix=".tmp", delete=False,
        ) as stream:
            temporary = Path(stream.name)
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def upsert_prediction(path: Path, row: dict) -> None:
    """Coordinator-only update; the caller holds the run lock across the batch."""
    rows = read_jsonl(path) if path.exists() else []
    rows = [r for r in rows if (r["instance_id"], r["arm"]) != (row["instance_id"], row["arm"])]
    _atomic_write(path, "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in [*rows, row]))


def _git_commit() -> tuple[str, bool]:
    def git(*a):
        p = subprocess.run(["git", *a], cwd=REPO_ROOT, capture_output=True, text=True, timeout=15)
        return p.stdout.strip() if p.returncode == 0 else ""

    return git("rev-parse", "HEAD") or "unknown", bool(git("status", "--porcelain"))


def _source_hash() -> str:
    digest = hashlib.sha256()
    for path in sorted((REPO_ROOT / "src" / "execloc").glob("*.py")):
        digest.update(path.name.encode())
        digest.update(path.read_bytes())
    if STATIC_DOCKERFILE.exists():
        digest.update(STATIC_DOCKERFILE.read_bytes())
    for name in ("pyproject.toml", "uv.lock", ".python-version"):
        path = REPO_ROOT / name
        if path.exists():
            digest.update(name.encode())
            digest.update(path.read_bytes())
    return digest.hexdigest()


def _metadata_identity(cfg: dict, endpoint: dict | None) -> dict:
    """Omit unavailable serving identity only for a fully checkpointed selection."""
    shared = {k: v for k, v in cfg.items() if k not in ARM_KEYS}
    identity = {"shared_config_hash": config_hash(shared)}
    if endpoint is not None:
        identity["model"] = endpoint["model"]
    elif os.environ.get("EXECLOC_MODEL"):
        identity["model"] = os.environ["EXECLOC_MODEL"]
    for key, variable in (
        ("model_revision", "EXECLOC_MODEL_REVISION"),
        ("serving_engine", "EXECLOC_SERVING_ENGINE"),
        ("context_length", "EXECLOC_CONTEXT_LENGTH"),
    ):
        value = os.environ.get(variable) or None
        if value is not None and key == "context_length":
            value = int(value)
        if endpoint is not None or value is not None:
            identity[key] = value
    return identity


def _check_meta(meta: dict, arm: str, cfg: dict, identity: dict, commit: str, source_hash: str) -> None:
    for key, value in identity.items():
        if key in meta and meta[key] != value:
            raise RunError(
                f"meta.json records {key}={meta[key]!r} but this run has {value!r}. "
                "Use a new --run-id so the arms stay comparable."
            )
    for prior_arm, prior in meta.get("arms", {}).items():
        for key, value in (("git_commit", commit), ("source_hash", source_hash)):
            if key in prior and prior[key] != value:
                raise RunError(f"{prior_arm} already records a different {key}; use a new --run-id.")
        if prior_arm == arm and prior.get("config_hash") != config_hash(cfg):
            raise RunError(f"{arm} configuration changed; use a new --run-id.")


def write_meta(
    run_dir: Path, arm: str, cfg: dict, endpoint: dict, instances_digest: str | None = None,
) -> None:
    path = run_dir / "meta.json"
    meta = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    new = _metadata_identity(cfg, endpoint)
    if instances_digest is not None:
        new["instances_sha256"] = instances_digest
    commit, dirty = _git_commit()
    source_hash = _source_hash()
    _check_meta(meta, arm, cfg, new, commit, source_hash)
    meta.update(new)
    # Preserve the original provenance on resume instead of silently replacing it.
    meta.setdefault("arms", {}).setdefault(arm, {
        "config_hash": config_hash(cfg),
        "git_commit": commit,
        "source_hash": source_hash,
        "git_dirty": dirty,
        "mini_swe_agent_version": importlib.metadata.version("mini-swe-agent"),
        "hostname": socket.gethostname(),
        "platform": f"{platform.system()} {platform.machine()}",
    })
    _atomic_write(path, json.dumps(meta, indent=2) + "\n")


def _quiet_minisweagent() -> None:
    # mini-swe-agent loads a global .env from the user's config dir on import.
    # Point it at an empty dir in the repo so no outside keys or model names leak in.
    os.environ["MSWEA_GLOBAL_CONFIG_DIR"] = str(CACHE_DIR / "mswea")
    os.environ.setdefault("MSWEA_SILENT_STARTUP", "1")


def _confined_run_dir(root: Path, run_id: str) -> Path:
    if (
        not isinstance(run_id, str)
        or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", run_id)
        or run_id.endswith(".")
        or re.fullmatch(r"(?:CON|PRN|AUX|NUL|COM[0-9]|LPT[0-9])(?:\..*)?", run_id, re.IGNORECASE)
    ):
        raise RunError("--run-id must be a simple directory name (letters, numbers, '.', '_', '-').")
    path = root / run_id
    if not path.resolve().is_relative_to(root.resolve()):
        raise RunError("--run-id resolves outside the output directory.")
    return path


@contextmanager
def _run_lock(run_dir: Path):
    """An OS lock releases on exit/crash; the harmless lock file may remain."""
    run_dir.mkdir(parents=True, exist_ok=True)
    stream = (run_dir / ".run.lock").open("a+b")
    acquired = False
    try:
        stream.seek(0, os.SEEK_END)
        if not stream.tell():
            stream.write(b"\0")
            stream.flush()
        stream.seek(0)
        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            raise RunError(f"Run {run_dir.name!r} is already locked by another process.") from error
        acquired = True
        yield
    finally:
        if acquired:
            stream.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(stream, fcntl.LOCK_UN)
        stream.close()


@dataclass
class _InterruptState:
    interrupted: bool = False


@contextmanager
def _batch_interrupts():
    """Stop scheduling on Ctrl+C; let active tasks finish and checkpoint them."""
    state = _InterruptState()
    previous = None
    if threading.current_thread() is threading.main_thread():
        previous = signal.getsignal(signal.SIGINT)
        signal.signal(signal.SIGINT, lambda signum, frame: setattr(state, "interrupted", True))
    try:
        yield state
    finally:
        if previous is not None:
            signal.signal(signal.SIGINT, previous)


def _worker_init() -> None:
    # The coordinator handles console Ctrl+C. Each spawned process owns its
    # mini-swe-agent globals; no model stats or credentials are shared by threads.
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    _quiet_minisweagent()


@dataclass
class _TaskOutcome:
    row: dict
    fatal: bool = False
    interrupted: bool = False


def _prune_instance_image(instance_id: str) -> None:
    """A targeted, non-force image removal; Docker protects any live containers."""
    try:
        image = swebench_image(instance_id)
        _docker("image", "rm", image, timeout=60)
    except Exception as error:
        print(f"Warning: could not remove exec image for {instance_id}: {error}", file=sys.stderr)


def _execute_task(instance: dict, task: str, cfg: dict, endpoint: dict, traj_path: Path, prune: bool) -> _TaskOutcome:
    start = time.time()
    iid, arm = instance["instance_id"], cfg["arm"]
    try:
        row = run_instance(instance, task, cfg, endpoint, traj_path)
        outcome = _TaskOutcome(row)
    except RunError as error:
        row = error.row or _row(iid, arm, exit_status=f"RunError: {error}"[:300])
        outcome = _TaskOutcome(row, fatal=True)
    except InstanceInterrupted as error:
        outcome = _TaskOutcome(error.row, interrupted=True)
    except KeyboardInterrupt:
        outcome = _TaskOutcome(_row(iid, arm, exit_status="Interrupted"), interrupted=True)
    except Exception as error:
        outcome = _TaskOutcome(_row(iid, arm, exit_status=f"{type(error).__name__}: {error}"[:300]))
    if not outcome.row["wall_seconds"]:
        outcome.row["wall_seconds"] = round(time.time() - start, 1)
    # run_instance's finally has already attempted container cleanup. Never
    # remove the static shared image, use --force, or invoke a global prune.
    if prune and arm == "exec":
        _prune_instance_image(iid)
    return outcome


def _failed(row: dict) -> bool:
    return row.get("exit_status") != "Submitted" or bool(row.get("parse_error"))


def _checkpoint(path: Path, outcome: _TaskOutcome) -> None:
    row = outcome.row
    upsert_prediction(path, row)
    print(
        f"[{row['arm']}] {row['instance_id']}: {row['exit_status']}, {len(row['locations'])} locations, "
        f"{row['steps']} steps, {row['wall_seconds']}s",
        file=sys.stderr,
    )


def _run_batch(tasks: list[tuple], predictions: Path, workers: int) -> int:
    fatal = False
    with _batch_interrupts() as state:
        if workers == 1:
            for arguments in tasks:
                if state.interrupted:
                    break
                print(f"[{arguments[2]['arm']}] {arguments[0]['instance_id']} ...", file=sys.stderr)
                outcome = _execute_task(*arguments)
                _checkpoint(predictions, outcome)
                fatal = fatal or outcome.fatal
                state.interrupted = state.interrupted or outcome.interrupted
                if fatal:
                    break
        else:
            # Bound the submitted queue, so interrupt/preflight failure prevents
            # all remaining instances from being launched in the background.
            with ProcessPoolExecutor(
                max_workers=min(workers, len(tasks)),
                mp_context=multiprocessing.get_context("spawn"), initializer=_worker_init,
            ) as pool:
                remaining = iter(tasks)
                pending = {}

                def fill():
                    nonlocal fatal
                    while len(pending) < workers and not state.interrupted and not fatal:
                        arguments = next(remaining, None)
                        if arguments is None:
                            break
                        try:
                            pending[pool.submit(_execute_task, *arguments)] = arguments
                        except Exception as error:
                            instance, _, cfg, *_ = arguments
                            _checkpoint(predictions, _TaskOutcome(_row(
                                instance["instance_id"], cfg["arm"],
                                exit_status=f"WorkerError: {type(error).__name__}: {error}"[:300],
                            ), fatal=True))
                            fatal = True

                fill()
                while pending:
                    if state.interrupted or fatal:
                        for future in list(pending):
                            if future.cancel():
                                del pending[future]
                    if not pending:
                        break
                    completed, _ = wait(pending, timeout=0.2, return_when=FIRST_COMPLETED)
                    for future in completed:
                        arguments = pending.pop(future)
                        try:
                            outcome = future.result()
                        except Exception as error:
                            instance, _, cfg, *_ = arguments
                            outcome = _TaskOutcome(_row(
                                instance["instance_id"], cfg["arm"],
                                exit_status=f"WorkerError: {type(error).__name__}: {error}"[:300],
                            ), fatal=True)
                        _checkpoint(predictions, outcome)
                        fatal = fatal or outcome.fatal
                        state.interrupted = state.interrupted or outcome.interrupted
                    fill()
        if state.interrupted:
            print("Interrupted: finished work is checkpointed; resume with the same --run-id.", file=sys.stderr)
            return 130
    return 1 if fatal else 0


def main(
    arm: str, instance_ids: list[str] | None, run_id: str, instances_path: Path,
    all_instances: bool = False, workers: int = 1, prune: bool = False, retry_failed: bool = False,
) -> int:
    try:
        if arm not in ARMS:
            raise RunError(f"unknown arm {arm!r}; choose static or exec.")
        if not isinstance(workers, int) or isinstance(workers, bool) or workers < 1:
            raise RunError("--workers must be a positive integer.")
        if bool(instance_ids) == bool(all_instances):
            raise RunError("Pass either --all or --instances ID [ID ...].")
        if instance_ids and len(instance_ids) != len(set(instance_ids)):
            raise RunError("--instances contains duplicate IDs.")
        if prune and arm != "exec":
            raise RunError("--prune only applies to the exec arm's per-instance images.")
        run_dir = _confined_run_dir(RESULTS_DIR, run_id)
        trajectory_dir = _confined_run_dir(RUNS_DIR, run_id) / arm
        instances_path = Path(instances_path)
        instances_bytes = instances_path.read_bytes()
        frozen = [json.loads(line) for line in instances_bytes.decode("utf-8").splitlines() if line.strip()]
        ids = [row["instance_id"] for row in frozen]
        if len(ids) != len(set(ids)):
            raise RunError("the frozen sample contains duplicate instance IDs.")
        if any(not isinstance(iid, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", iid) for iid in ids):
            raise RunError("the frozen sample contains an unsafe instance ID.")
        missing = set(instance_ids or []) - set(ids)
        if missing:
            raise RunError(f"not in {instances_path.name}: {', '.join(sorted(missing))}")
        requested = set(instance_ids or [])
        selected = [row for row in frozen if all_instances or row["instance_id"] in requested]
        if not selected:
            raise RunError("the selected frozen sample is empty.")
        with _run_lock(run_dir):
            predictions = run_dir / "predictions.jsonl"
            rows = read_jsonl(predictions) if predictions.exists() else []
            existing = {(row["instance_id"], row["arm"]): row for row in rows}
            pending = [
                row for row in selected
                if (previous := existing.get((row["instance_id"], arm))) is None
                or previous.get("exit_status") == "Interrupted"
                or (retry_failed and _failed(previous))
            ]
            cfg = load_arm_config(arm)
            instances_digest = hashlib.sha256(instances_bytes).hexdigest()
            if not pending:
                # Local identity guards still apply when no endpoint or Docker
                # is needed. If .env is available, validate supplied model data.
                if (REPO_ROOT / ".env").exists():
                    from dotenv import load_dotenv

                    load_dotenv(REPO_ROOT / ".env")
                if os.environ.get("EXECLOC_BASE_URL"):
                    _validate_base_url(os.environ["EXECLOC_BASE_URL"])
                path = run_dir / "meta.json"
                if path.exists():
                    identity = _metadata_identity(cfg, None)
                    identity["instances_sha256"] = instances_digest
                    _check_meta(
                        json.loads(path.read_text(encoding="utf-8")), arm, cfg, identity,
                        _git_commit()[0], _source_hash(),
                    )
                print(f"[{arm}] All {len(selected)} selected instances already checkpointed; nothing to run.", file=sys.stderr)
                return 0
            if arm == "exec" and platform.machine().lower() in ("arm64", "aarch64"):
                raise RunError("The exec arm needs x86_64 SWE-bench images and is unavailable on ARM machines.")
            _quiet_minisweagent()
            endpoint = endpoint_from_env()
            write_meta(run_dir, arm, cfg, endpoint, instances_digest)
            wanted = {row["instance_id"] for row in pending}
            problems = {row["instance_id"]: row["problem_statement"] for row in load_verified() if row["instance_id"] in wanted}
            absent = wanted - problems.keys()
            if absent:
                raise RunError(f"instances absent from the pinned dataset: {', '.join(sorted(absent))}")
            tasks = [
                (row, problems[row["instance_id"]], cfg, endpoint, trajectory_dir / f"{row['instance_id']}.traj.json", prune)
                for row in pending
            ]
            return _run_batch(tasks, predictions, workers)
    except KeyboardInterrupt:
        print("Interrupted; existing checkpoints are preserved.", file=sys.stderr)
        return 130
    except (RunError, OSError, ValueError, KeyError) as error:
        print(f"execloc run: {error}", file=sys.stderr)
        return 1
