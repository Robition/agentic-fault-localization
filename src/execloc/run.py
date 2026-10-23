"""Run one arm of the experiment with mini-swe-agent.

Both arms use configs/base.yaml merged with configs/<arm>.yaml. They share the
agent, prompt, model, step limit, timeout, and output truncation, and differ
only in the Docker container:

- static: the image built from docker/static.Dockerfile (no Python), with the
  repository at the base commit (depth-1 fetch by SHA) mounted read-only at
  /testbed.
- exec: the official SWE-bench image for the instance.

Before the agent starts, a preflight runs `python --version` in the container.
It must fail in the static arm and succeed in the exec arm; otherwise the run
stops, because the arms would not be what the experiment claims.

For each instance this writes runs/<run_id>/<arm>/<instance_id>.traj.json and
replaces that instance's row for this arm in results/<run_id>/predictions.jsonl.
A crash still produces a row, with empty locations and the reason in
exit_status.
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import socket
import subprocess
import sys
import time
from pathlib import Path

import yaml

from execloc.dataset import load_verified, read_jsonl, write_jsonl
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
    return {
        "base_url": base_url,
        "model": model,
        "api_key": os.environ.get("EXECLOC_API_KEY", "").strip() or "local",
    }


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


def _docker(*args: str, timeout: float | None = None, check: bool = True) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(
            ["docker", *args], capture_output=True, text=True, timeout=timeout, check=check,
            encoding="utf-8", errors="replace",
        )
    except FileNotFoundError:
        raise RunError("docker is not installed or not on PATH.")
    except subprocess.CalledProcessError as e:
        raise RunError(f"docker {' '.join(args[:2])} failed: {(e.stderr or e.stdout).strip()}")


def image_exists(image: str) -> bool:
    return _docker("image", "inspect", image, check=False).returncode == 0


def build_static_image(image: str) -> None:
    print(f"Building {image} from {STATIC_DOCKERFILE.relative_to(REPO_ROOT)} ...", file=sys.stderr)
    proc = subprocess.run(
        ["docker", "build", "-f", str(STATIC_DOCKERFILE), "-t", image, str(STATIC_DOCKERFILE.parent)]
    )
    if proc.returncode != 0:
        raise RunError(f"building {image} failed")


def ensure_image(image: str, arm: str) -> None:
    if image_exists(image):
        return
    if arm == "static":
        build_static_image(image)
    else:
        # Pull here, without a timeout: the environment's own pull_timeout is too
        # short for multi-GB SWE-bench images.
        print(f"Pulling {image} ...", file=sys.stderr)
        if subprocess.run(["docker", "pull", image]).returncode != 0:
            raise RunError(f"pulling {image} failed")


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


def preflight(env, arm: str) -> None:
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
    from minisweagent.agents.default import DefaultAgent
    from minisweagent.environments import get_environment
    from minisweagent.models import get_model

    arm, iid = cfg["arm"], instance["instance_id"]
    start = time.time()
    env = agent = None
    try:
        env_cfg = environment_config(cfg, instance)
        ensure_image(env_cfg["image"], arm)
        env = get_environment(env_cfg)
        preflight(env, arm)
        model = get_model(config=model_config(cfg, endpoint))
        agent = DefaultAgent(model, env, **cfg["agent"], output_path=traj_path)
        result = agent.run(task=task)
        exit_status = result.get("exit_status", "")
        submission = result.get("submission", "")
    except RunError:
        raise
    except Exception as e:  # never drop an instance
        exit_status = f"{type(e).__name__}: {e}"[:300]
        submission = ""
    finally:
        if env is not None:
            env.cleanup()

    messages = agent.messages if agent else []
    locations, parse_error = parse_locations(submission)
    inp, out = count_tokens(messages)
    return _row(
        iid, arm, locations=locations, parse_error=parse_error, steps=agent.n_calls if agent else 0,
        input_tokens=inp, output_tokens=out, wall_seconds=round(time.time() - start, 1),
        exec_attempts=count_exec_attempts(messages), exit_status=exit_status,
    )


def upsert_prediction(path: Path, row: dict) -> None:
    rows = read_jsonl(path) if path.exists() else []
    rows = [r for r in rows if (r["instance_id"], r["arm"]) != (row["instance_id"], row["arm"])]
    write_jsonl(path, [*rows, row])


def _git_commit() -> tuple[str, bool]:
    def git(*a):
        p = subprocess.run(["git", *a], cwd=REPO_ROOT, capture_output=True, text=True)
        return p.stdout.strip() if p.returncode == 0 else ""

    return git("rev-parse", "HEAD") or "unknown", bool(git("status", "--porcelain"))


def write_meta(run_dir: Path, arm: str, cfg: dict, endpoint: dict) -> None:
    from minisweagent import __version__ as mini_version

    path = run_dir / "meta.json"
    meta = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    shared = {k: v for k, v in cfg.items() if k not in ARM_KEYS}
    new = {
        "model": endpoint["model"],
        "model_revision": os.environ.get("EXECLOC_MODEL_REVISION") or None,
        "serving_engine": os.environ.get("EXECLOC_SERVING_ENGINE") or None,
        "context_length": int(os.environ["EXECLOC_CONTEXT_LENGTH"]) if os.environ.get("EXECLOC_CONTEXT_LENGTH") else None,
        "shared_config_hash": config_hash(shared),
    }
    for key, value in new.items():
        if key in meta and meta[key] != value:
            raise RunError(
                f"{path} records {key}={meta[key]!r} but this run has {value!r}. "
                "Use a new --run-id so the arms stay comparable."
            )
    commit, dirty = _git_commit()
    meta.update(new)
    meta.setdefault("arms", {})[arm] = {
        "config_hash": config_hash(cfg),
        "git_commit": commit,
        "git_dirty": dirty,
        "mini_swe_agent_version": mini_version,
        "hostname": socket.gethostname(),
        "platform": f"{platform.system()} {platform.machine()}",
    }
    run_dir.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8", newline="\n")


def _quiet_minisweagent() -> None:
    # mini-swe-agent loads a global .env from the user's config dir on import.
    # Point it at an empty dir in the repo so no outside keys or model names leak in.
    os.environ["MSWEA_GLOBAL_CONFIG_DIR"] = str(CACHE_DIR / "mswea")
    os.environ.setdefault("MSWEA_SILENT_STARTUP", "1")


def main(arm: str, instance_ids: list[str] | None, run_id: str, instances_path: Path) -> int:
    try:
        if arm == "exec" and platform.machine().lower() in ("arm64", "aarch64"):
            print("The exec arm needs x86_64 SWE-bench images and is unavailable on ARM machines.", file=sys.stderr)
            return 2
        if not instance_ids:
            print("Pass --instances ID [ID ...].", file=sys.stderr)
            return 2
        _quiet_minisweagent()
        cfg = load_arm_config(arm)
        endpoint = endpoint_from_env()

        known = {r["instance_id"]: r for r in read_jsonl(instances_path)}
        missing = [i for i in instance_ids if i not in known]
        if missing:
            raise RunError(f"not in {instances_path.name}: {', '.join(missing)}")
        problems = {r["instance_id"]: r["problem_statement"] for r in load_verified() if r["instance_id"] in instance_ids}

        run_dir = RESULTS_DIR / run_id
        write_meta(run_dir, arm, cfg, endpoint)
        for iid in instance_ids:
            print(f"[{arm}] {iid} ...", file=sys.stderr)
            traj = RUNS_DIR / run_id / arm / f"{iid}.traj.json"
            row = run_instance(known[iid], problems[iid], cfg, endpoint, traj)
            upsert_prediction(run_dir / "predictions.jsonl", row)
            print(
                f"[{arm}] {iid}: {row['exit_status']}, {len(row['locations'])} locations, "
                f"{row['steps']} steps, {row['wall_seconds']}s",
                file=sys.stderr,
            )
        return 0
    except RunError as e:
        print(f"execloc run: {e}", file=sys.stderr)
        return 1
