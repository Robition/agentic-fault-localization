from pathlib import Path

import pytest

from execloc.cli import main


@pytest.mark.parametrize("selection", [[], ["--all", "--instances", "bug"]])
def test_run_requires_exactly_one_selection(selection):
    with pytest.raises(SystemExit) as error:
        main(["run", "--arm", "static", *selection])
    assert error.value.code == 2


@pytest.mark.parametrize("workers", ["0", "-1", "not-a-number"])
def test_run_rejects_invalid_worker_count(workers):
    with pytest.raises(SystemExit) as error:
        main(["run", "--arm", "exec", "--all", "--workers", workers])
    assert error.value.code == 2


def test_run_routes_batch_options_without_starting_an_experiment(monkeypatch):
    from execloc import run

    received = {}

    def fake_main(**kwargs):
        received.update(kwargs)
        return 130

    monkeypatch.setattr(run, "main", fake_main)
    result = main([
        "run", "--arm", "exec", "--all", "--workers", "2", "--prune", "--retry-failed",
        "--run-id", "pilot", "--instances-file", "sample.jsonl",
    ])
    assert result == 130
    assert received == {
        "arm": "exec", "instance_ids": None, "run_id": "pilot",
        "instances_path": Path("sample.jsonl"), "all_instances": True,
        "workers": 2, "prune": True, "retry_failed": True,
    }


def test_doctor_routes_its_failure_exit_code(monkeypatch):
    from execloc import doctor

    monkeypatch.setattr(doctor, "main", lambda: 1)
    assert main(["doctor"]) == 1
