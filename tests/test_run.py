"""Batch coordination tests use fake tasks; no Docker, dataset download, or GPU."""

from __future__ import annotations

import copy
import json
import os
import signal
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from execloc import run


@pytest.fixture
def batch(tmp_path, monkeypatch):
    frozen = [
        {"instance_id": f"project__repo-{i}", "repo": "project/repo", "base_commit": "abc"}
        for i in range(5)
    ]
    sample = tmp_path / "instances.jsonl"
    sample.write_text("".join(json.dumps(row) + "\n" for row in frozen), encoding="utf-8")
    for name in ("REPO_ROOT", "RESULTS_DIR", "RUNS_DIR", "CACHE_DIR"):
        monkeypatch.setattr(run, name, tmp_path / name.lower())
    monkeypatch.setattr(run, "_git_commit", lambda: ("commit-a", False))
    monkeypatch.setattr(run, "_source_hash", lambda: "source-a")
    for key in ("EXECLOC_MODEL", "EXECLOC_MODEL_REVISION", "EXECLOC_CONTEXT_LENGTH", "EXECLOC_SERVING_ENGINE"):
        monkeypatch.delenv(key, raising=False)

    def config(arm):
        return {
            "arm": arm, "agent": {"step_limit": 40},
            "model": {"model_class": "unused"},
            "environment": {"image": f"unused-{arm}"},
            "repo_mount": "readonly" if arm == "static" else None,
        }

    endpoint = Mock(return_value={"model": "local-model", "base_url": "http://localhost:11434/v1", "api_key": "local"})
    dataset = Mock(return_value=[{**row, "problem_statement": "localize an issue"} for row in frozen])
    calls = []

    def successful(instance, task, cfg, endpoint, trajectory):
        calls.append((cfg["arm"], instance["instance_id"]))
        return run._row(
            instance["instance_id"], cfg["arm"], exit_status="Submitted", parse_error=False,
            locations=[{"file": "module.py", "function": "f", "start_line": 1, "end_line": 2}],
        )

    monkeypatch.setattr(run, "load_arm_config", config)
    monkeypatch.setattr(run, "endpoint_from_env", endpoint)
    monkeypatch.setattr(run, "load_verified", dataset)
    monkeypatch.setattr(run, "run_instance", successful)
    return SimpleNamespace(
        frozen=frozen, ids=[row["instance_id"] for row in frozen], sample=sample,
        endpoint=endpoint, dataset=dataset, calls=calls, successful=successful, config=config,
        predictions=run.RESULTS_DIR / "batch" / "predictions.jsonl",
    )


def invoke(batch, arm="static", ids=None, **kwargs):
    return run.main(arm, ids, "batch", batch.sample, all_instances=ids is None, **kwargs)


def test_all_runs_in_frozen_order_and_finished_resume_is_offline(batch):
    assert invoke(batch) == 0
    assert batch.calls == [("static", iid) for iid in batch.ids]
    batch.endpoint.side_effect = AssertionError("endpoint must not be needed")
    batch.dataset.side_effect = AssertionError("dataset must not be loaded")
    assert invoke(batch) == 0
    assert len(batch.calls) == 5
    assert len(run.read_jsonl(batch.predictions)) == 5


def test_requested_order_is_frozen_and_other_arm_rows_survive(batch):
    assert invoke(batch, ids=list(reversed(batch.ids[:3]))) == 0
    assert batch.calls == [("static", iid) for iid in batch.ids[:3]]
    assert invoke(batch, arm="exec", ids=batch.ids[:2]) == 0
    rows = run.read_jsonl(batch.predictions)
    assert len(rows) == 5
    assert {(r["instance_id"], r["arm"]) for r in rows} == {
        *((iid, "static") for iid in batch.ids[:3]),
        *((iid, "exec") for iid in batch.ids[:2]),
    }


def test_failed_rows_resume_only_when_explicitly_requested(batch, monkeypatch):
    def once(instance, task, cfg, endpoint, trajectory):
        row = batch.successful(instance, task, cfg, endpoint, trajectory)
        if instance["instance_id"] == batch.ids[1]:
            row["exit_status"] = "LimitsExceeded"
            row["parse_error"] = True
        return row

    monkeypatch.setattr(run, "run_instance", once)
    assert invoke(batch) == 0
    assert invoke(batch) == 0
    assert len(batch.calls) == 5
    monkeypatch.setattr(run, "run_instance", batch.successful)
    assert invoke(batch, retry_failed=True) == 0
    assert batch.calls[-1] == ("static", batch.ids[1])
    assert len(batch.calls) == 6
    assert len(run.read_jsonl(batch.predictions)) == 5


def test_preflight_error_is_checkpointed_then_stops_batch(batch, monkeypatch):
    def fails(instance, *args):
        if instance["instance_id"] == batch.ids[1]:
            raise run.RunError("history is not isolated")
        return batch.successful(instance, *args)

    monkeypatch.setattr(run, "run_instance", fails)
    assert invoke(batch) == 1
    rows = run.read_jsonl(batch.predictions)
    assert [row["instance_id"] for row in rows] == batch.ids[:2]
    assert rows[1]["exit_status"] == "RunError: history is not isolated"
    assert rows[1]["parse_error"] is True


def test_ordinary_instance_exception_is_recorded_and_batch_continues(batch, monkeypatch):
    def fails(instance, *args):
        if instance["instance_id"] == batch.ids[1]:
            raise ValueError("bad response")
        return batch.successful(instance, *args)

    monkeypatch.setattr(run, "run_instance", fails)
    assert invoke(batch) == 0
    rows = run.read_jsonl(batch.predictions)
    assert len(rows) == 5
    assert rows[1]["exit_status"] == "ValueError: bad response"


def test_ctrl_c_finishes_active_task_and_resume_does_not_repeat_it(batch, monkeypatch):
    def interrupts(instance, *args):
        row = batch.successful(instance, *args)
        signal.raise_signal(signal.SIGINT)
        return row

    monkeypatch.setattr(run, "run_instance", interrupts)
    assert invoke(batch) == 130
    assert [row["instance_id"] for row in run.read_jsonl(batch.predictions)] == batch.ids[:1]
    monkeypatch.setattr(run, "run_instance", batch.successful)
    assert invoke(batch) == 0
    assert batch.calls == [("static", iid) for iid in batch.ids]


def test_worker_keyboard_interrupt_records_partial_failure_before_exit(batch, monkeypatch):
    monkeypatch.setattr(run, "run_instance", Mock(side_effect=KeyboardInterrupt))
    assert invoke(batch) == 130
    rows = run.read_jsonl(batch.predictions)
    assert len(rows) == 1
    assert rows[0]["exit_status"] == "Interrupted"
    monkeypatch.setattr(run, "run_instance", batch.successful)
    assert invoke(batch) == 0
    assert batch.calls == [("static", iid) for iid in batch.ids]
    assert len(run.read_jsonl(batch.predictions)) == 5


@pytest.mark.parametrize("worker_count", [0, -1, True, "2"])
def test_invalid_worker_count_fails_before_endpoint(batch, worker_count):
    assert invoke(batch, workers=worker_count) == 1
    batch.endpoint.assert_not_called()


@pytest.mark.parametrize("run_id", ["../outside", "a/b", "a\\b", "..", "C:outside", "NUL", "batch."])
def test_invalid_run_id_cannot_escape_outputs(batch, run_id):
    assert run.main("static", batch.ids[:1], run_id, batch.sample) == 1
    batch.endpoint.assert_not_called()


def test_missing_selection_unknown_and_duplicate_ids_fail_early(batch):
    assert run.main("static", None, "batch", batch.sample) == 1
    assert invoke(batch, ids=["unknown"]) == 1
    assert invoke(batch, ids=[batch.ids[0], batch.ids[0]]) == 1
    assert run.main("static", batch.ids[:1], "batch", batch.sample, all_instances=True) == 1
    batch.endpoint.assert_not_called()


def test_resume_rejects_changed_config_even_when_every_row_is_checkpointed(batch, monkeypatch):
    assert invoke(batch) == 0
    cfg = copy.deepcopy(batch.config("static"))
    cfg["agent"]["step_limit"] = 41
    monkeypatch.setattr(run, "load_arm_config", lambda arm: cfg)
    assert invoke(batch) == 1
    assert len(batch.calls) == 5


def test_finished_resume_checks_available_model_identity_without_endpoint(batch, monkeypatch):
    assert invoke(batch) == 0
    batch.endpoint.side_effect = AssertionError("must not call endpoint setup")
    monkeypatch.setenv("EXECLOC_MODEL", "a-different-model")
    assert invoke(batch) == 1


def test_other_arm_cannot_mix_source_commits(batch, monkeypatch):
    assert invoke(batch, ids=batch.ids[:1]) == 0
    monkeypatch.setattr(run, "_git_commit", lambda: ("different-commit", False))
    assert invoke(batch, arm="exec", ids=batch.ids[:1]) == 1
    assert batch.calls == [("static", batch.ids[0])]
    assert batch.dataset.call_count == 1


def test_sample_change_is_rejected_on_finished_resume(batch):
    assert invoke(batch) == 0
    batch.sample.write_text(batch.sample.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    assert invoke(batch) == 1


def test_atomic_checkpoint_failure_preserves_original_file(batch, monkeypatch):
    row = run._row(batch.ids[0], "static", exit_status="Submitted")
    run.upsert_prediction(batch.predictions, row)
    before = batch.predictions.read_bytes()
    monkeypatch.setattr(run.os, "replace", Mock(side_effect=OSError("replace failed")))
    with pytest.raises(OSError, match="replace failed"):
        run.upsert_prediction(batch.predictions, run._row(batch.ids[1], "exec"))
    assert batch.predictions.read_bytes() == before
    assert not list(batch.predictions.parent.glob("*.tmp"))


def test_run_lock_is_cross_process_and_releases_without_deleting_file(tmp_path):
    directory = tmp_path / "locked-run"
    script = (
        "import sys\nfrom pathlib import Path\nfrom execloc.run import _run_lock, RunError\n"
        "try:\n with _run_lock(Path(sys.argv[1])):\n  sys.exit(0)\n"
        "except RunError:\n sys.exit(17)\n"
    )
    with run._run_lock(directory):
        result = subprocess.run([sys.executable, "-c", script, str(directory)], timeout=15, capture_output=True, text=True)
    assert result.returncode == 17, result.stderr
    assert (directory / ".run.lock").exists()
    with run._run_lock(directory):
        pass


def test_os_releases_lock_after_abrupt_process_exit(tmp_path):
    directory = tmp_path / "crashed-run"
    script = (
        "import os, sys\nfrom pathlib import Path\nfrom execloc.run import _run_lock\n"
        "with _run_lock(Path(sys.argv[1])):\n os._exit(0)\n"
    )
    result = subprocess.run([sys.executable, "-c", script, str(directory)], timeout=15, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    with run._run_lock(directory):
        pass


def _spawned_fake_task(instance, task, cfg, endpoint, trajectory, prune):
    """Picklable fake task for the real Windows spawn pool; never touches Docker."""
    assert signal.getsignal(signal.SIGINT) == signal.SIG_IGN
    return run._TaskOutcome(run._row(
        instance["instance_id"], cfg["arm"], exit_status="Submitted", parse_error=False,
        locations=[{"file": f"worker_{os.getpid()}.py", "function": None, "start_line": 1, "end_line": 1}],
    ))


def test_real_spawn_pool_can_checkpoint_fake_tasks_without_model_or_docker(batch, monkeypatch):
    monkeypatch.setattr(run, "_execute_task", _spawned_fake_task)
    assert invoke(batch, workers=2) == 0
    rows = run.read_jsonl(batch.predictions)
    assert {row["instance_id"] for row in rows} == set(batch.ids)
    assert all(row["locations"][0]["file"] != f"worker_{os.getpid()}.py" for row in rows)


def _fake_process_pool(**kwargs):
    # Exercise actual concurrent completions while keeping fake task functions
    # in this process. Production uses the spawn ProcessPoolExecutor instead.
    assert kwargs["mp_context"].get_start_method() == "spawn"
    assert kwargs["initializer"] is run._worker_init
    return ThreadPoolExecutor(max_workers=kwargs["max_workers"])


def test_parallel_completions_are_serially_checkpointed_and_preserve_both_arms(batch, monkeypatch):
    monkeypatch.setattr(run, "ProcessPoolExecutor", _fake_process_pool)

    def delayed(instance, *args):
        time.sleep((4 - int(instance["instance_id"].rsplit("-", 1)[1])) * 0.005)
        return batch.successful(instance, *args)

    monkeypatch.setattr(run, "run_instance", delayed)
    assert invoke(batch, workers=3) == 0
    assert invoke(batch, arm="exec", workers=3) == 0
    rows = run.read_jsonl(batch.predictions)
    assert len(rows) == 10
    assert len({(row["instance_id"], row["arm"]) for row in rows}) == 10
    assert len(batch.calls) == 10


def test_parallel_fatal_error_drains_inflight_work_without_scheduling_more(batch, monkeypatch):
    monkeypatch.setattr(run, "ProcessPoolExecutor", _fake_process_pool)
    second_started = threading.Event()

    def fails(instance, *args):
        if instance["instance_id"] == batch.ids[0]:
            assert second_started.wait(2)
            raise run.RunError("invalid environment")
        second_started.set()
        time.sleep(0.05)
        return batch.successful(instance, *args)

    monkeypatch.setattr(run, "run_instance", fails)
    assert invoke(batch, workers=2) == 1
    assert {row["instance_id"] for row in run.read_jsonl(batch.predictions)} == set(batch.ids[:2])


def test_prune_targets_one_exec_image_after_task_cleanup_and_never_forces(batch, monkeypatch):
    events = []

    def finished(instance, *args):
        events.append("task cleanup finished")
        return batch.successful(instance, *args)

    def docker(*args, **kwargs):
        events.append((args, kwargs))

    monkeypatch.setattr(run, "run_instance", finished)
    monkeypatch.setattr(run, "swebench_image", lambda iid: f"official-{iid}")
    monkeypatch.setattr(run, "_docker", docker)
    assert invoke(batch, arm="exec", ids=batch.ids[:1], prune=True) == 0
    assert events == ["task cleanup finished", (("image", "rm", f"official-{batch.ids[0]}"), {"timeout": 60})]


def test_prune_failure_does_not_drop_result(batch, monkeypatch, capsys):
    monkeypatch.setattr(run, "swebench_image", lambda iid: "official-image")
    monkeypatch.setattr(run, "_docker", Mock(side_effect=run.RunError("image still in use")))
    assert invoke(batch, arm="exec", ids=batch.ids[:1], prune=True) == 0
    assert len(run.read_jsonl(batch.predictions)) == 1
    assert "could not remove exec image" in capsys.readouterr().err


def test_hung_docker_inspect_has_finite_timeout(monkeypatch):
    command = Mock(side_effect=subprocess.TimeoutExpired(["docker", "image", "inspect"], 15))
    monkeypatch.setattr(run.subprocess, "run", command)
    with pytest.raises(run.RunError, match="timed out"):
        run.image_exists("official-image")
    assert command.call_args.kwargs["timeout"] == 15


def test_successful_image_inspect_finds_cached_image(monkeypatch):
    docker = Mock(return_value=subprocess.CompletedProcess([], 0, '[{"Id":"sha256:abc"}]', ""))
    monkeypatch.setattr(run, "_docker", docker)
    assert run.image_exists("official-image") is True
    docker.assert_called_once_with("image", "inspect", "official-image", check=False)


@pytest.mark.parametrize("stdout,stderr", [
    ("[]", "Error response from daemon: No such image: official-image"),
    ("Error: no such image: official-image", ""),
])
def test_only_explicit_missing_image_is_a_cache_miss(monkeypatch, stdout, stderr):
    monkeypatch.setattr(run, "_docker", Mock(return_value=subprocess.CompletedProcess([], 1, stdout, stderr)))
    assert run.image_exists("official-image") is False


@pytest.mark.parametrize("stdout,stderr,expected", [
    ("[]", "request returned 500 Internal Server Error for API route /_ping", "500 Internal Server Error"),
    ("", "Cannot connect to the Docker daemon", "Cannot connect"),
    ("", "invalid reference format", "invalid reference format"),
    ("", "", "exit code 1"),
])
def test_backend_and_invalid_reference_errors_are_not_cache_misses(monkeypatch, stdout, stderr, expected):
    monkeypatch.setattr(run, "_docker", Mock(return_value=subprocess.CompletedProcess([], 1, stdout, stderr)))
    with pytest.raises(run.RunError, match=expected):
        run.image_exists("official-image")


@pytest.mark.parametrize("arm", ["static", "exec"])
def test_inspect_backend_failure_does_not_trigger_build_or_pull(monkeypatch, arm):
    docker = Mock(return_value=subprocess.CompletedProcess([], 1, "", "500 Internal Server Error"))
    build = Mock()
    monkeypatch.setattr(run, "_docker", docker)
    monkeypatch.setattr(run, "build_static_image", build)
    with pytest.raises(run.RunError, match="docker image inspect failed"):
        run.ensure_image("official-image", arm)
    docker.assert_called_once_with("image", "inspect", "official-image", check=False)
    build.assert_not_called()


@pytest.mark.parametrize("dependency", ["pyproject.toml", "uv.lock", ".python-version"])
def test_source_hash_tracks_dependency_inputs(tmp_path, monkeypatch, dependency):
    monkeypatch.setattr(run, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(run, "STATIC_DOCKERFILE", tmp_path / "absent.Dockerfile")
    path = tmp_path / dependency
    path.write_text("original pin\n", encoding="utf-8")
    before = run._source_hash()
    path.write_text("changed pin\n", encoding="utf-8")
    assert run._source_hash() != before


@pytest.mark.parametrize("base_url", [
    "http://localhost:11434/v1", "https://LOCALHOST/v1",
    "http://127.0.0.1:11434/v1", "http://127.0.0.2/v1", "http://[::1]:11434/v1",
])
def test_run_endpoint_accepts_loopback_aliases(tmp_path, monkeypatch, base_url):
    monkeypatch.setattr(run, "REPO_ROOT", tmp_path)
    monkeypatch.setenv("EXECLOC_BASE_URL", base_url)
    monkeypatch.setenv("EXECLOC_MODEL", "local-model")
    assert run.endpoint_from_env()["base_url"] == base_url


@pytest.mark.parametrize("base_url", [
    "https://api.openai.com/v1", "http://192.168.1.10/v1", "http://0.0.0.0:11434/v1",
    "http://localhost.evil.invalid/v1", "file:///localhost/v1", "http:///v1",
    "http://secret-password@localhost:11434/v1", "http://localhost:11434/v1?token=secret-password",
    "http://localhost:11434/v1#secret-password", "http://localhost:99999/v1",
    "http://localhost:bad/v1", "http://localhost:0/v1", "http://localhost\n:11434/v1",
])
def test_run_endpoint_rejects_remote_or_unsafe_urls_without_echoing_secrets(tmp_path, monkeypatch, base_url):
    monkeypatch.setattr(run, "REPO_ROOT", tmp_path)
    monkeypatch.setenv("EXECLOC_BASE_URL", base_url)
    monkeypatch.setenv("EXECLOC_MODEL", "local-model")
    with pytest.raises(run.RunError, match="loopback-only") as caught:
        run.endpoint_from_env()
    assert "secret-password" not in str(caught.value)
    assert base_url not in str(caught.value)


def _raw_change(path="module.py", *, old_mode="100644", new_mode="100644",
                old_blob="a" * 40, new_blob="b" * 40, status="M"):
    return f":{old_mode} {new_mode} {old_blob} {new_blob} {status}\0{path}\0"


@pytest.mark.parametrize("old_mode,new_mode", [("100644", "100755"), ("100755", "100644")])
def test_exec_history_keeps_identical_python_blobs_with_regular_file_mode_change(old_mode, new_mode):
    raw = _raw_change(old_mode=old_mode, new_mode=new_mode, new_blob="a" * 40)
    # Non-Python setup changes remain permitted, as before this guard correction.
    raw += _raw_change("pyproject.toml")
    env = SimpleNamespace(execute=Mock(side_effect=[
        {"returncode": 0, "output": raw}, {"returncode": 0, "output": ""},
    ]))
    run.trim_exec_history(env, "base-sha")
    first = env.execute.call_args_list[0].args[0]["command"]
    assert "git merge-base --is-ancestor base-sha HEAD" in first
    assert "git diff --raw --no-abbrev --no-renames -z base-sha HEAD" in first
    assert "git reflog expire" in env.execute.call_args_list[1].args[0]["command"]


@pytest.mark.parametrize("raw", [
    _raw_change(),
    _raw_change(new_mode="100755"),  # Executable bit plus a content change is unsafe.
    _raw_change(old_mode="000000", old_blob="0" * 40,
                new_blob="e69de29bb2d1d6434b8b29ae775ad8c2e48c5391", status="A"),
    _raw_change(new_mode="000000", new_blob="0" * 40,
                old_blob="e69de29bb2d1d6434b8b29ae775ad8c2e48c5391", status="D"),
    _raw_change(new_mode="120000", new_blob="a" * 40, status="T"),
    _raw_change(old_mode="120000", new_blob="a" * 40, status="T"),
    _raw_change(old_mode="160000", new_mode="160000", new_blob="a" * 40),
])
def test_exec_history_rejects_python_content_existence_and_type_changes(raw):
    env = SimpleNamespace(execute=Mock(return_value={"returncode": 0, "output": raw}))
    with pytest.raises(run.RunError, match=r"exec image changes \.py files after the base commit"):
        run.trim_exec_history(env, "base-sha")
    assert env.execute.call_count == 1  # Do not hide history after an unsafe diff.


@pytest.mark.parametrize("old_path,new_path,expected", [
    ("old.py", "new.py", ["old.py", "new.py"]),
    ("old.py", "new.txt", ["old.py"]),
    ("old.txt", "new.py", ["new.py"]),
])
def test_no_renames_diff_still_rejects_moved_python_paths(old_path, new_path, expected):
    raw = _raw_change(old_path, new_mode="000000", new_blob="0" * 40, status="D")
    raw += _raw_change(new_path, old_mode="000000", old_blob="0" * 40,
                       new_blob="a" * 40, status="A")
    assert run._changed_python_paths(raw) == expected


def test_raw_diff_preserves_spaces_newlines_and_full_sha256_object_ids():
    path = "directory with spaces/module\nname.py"
    assert run._changed_python_paths(_raw_change(path)) == [path]
    raw = _raw_change(old_mode="100755", new_mode="100644",
                      old_blob="a" * 64, new_blob="a" * 64)
    assert run._changed_python_paths(raw) == []
    assert run._changed_python_paths("") == []


@pytest.mark.parametrize("raw", [
    None, "not a raw diff", "\0", _raw_change()[:-1],
    _raw_change().replace("\0module.py\0", "\0\0"),
    _raw_change().replace(":100644", "100644", 1),
    _raw_change().replace("a" * 40, "a" * 12),
    _raw_change().replace("b" * 40, "b" * 64),
    _raw_change(status="R100") + "new.py\0",  # --no-renames must use A/D records.
    _raw_change(old_mode="000000"),  # Absent path cannot have a nonzero blob.
    _raw_change(old_blob="0" * 40),  # Regular file cannot have the null blob.
    _raw_change(status="A"),  # Addition must have an absent old path.
    _raw_change(status="D"),  # Deletion must have an absent new path.
    _raw_change() + "garbage\0",
])
def test_malformed_raw_diff_fails_closed_before_history_is_trimmed(raw):
    env = SimpleNamespace(execute=Mock(return_value={"returncode": 0, "output": raw}))
    with pytest.raises(run.RunError, match="malformed raw git diff"):
        run.trim_exec_history(env, "base-sha")
    assert env.execute.call_count == 1


def test_exec_image_must_still_descend_from_the_frozen_base():
    env = SimpleNamespace(execute=Mock(return_value={"returncode": 1, "output": "not an ancestor"}))
    with pytest.raises(run.RunError, match="HEAD does not descend from base commit"):
        run.trim_exec_history(env, "base-sha")
    assert env.execute.call_count == 1
