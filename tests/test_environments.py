import logging
import subprocess
from unittest.mock import Mock

import pytest

from execloc import environments
from execloc.environments import ManagedDockerEnvironment


@pytest.fixture
def env(monkeypatch):
    def fake_start(self):
        self.container_id = "owned-container-id"

    monkeypatch.setattr(ManagedDockerEnvironment, "_start_container", fake_start)
    instance = ManagedDockerEnvironment(image="unused-image")
    yield instance
    instance.container_id = None


def test_cleanup_uses_windows_safe_argv_for_only_owned_container(env, monkeypatch):
    env.config.executable = r"C:\Program Files\Docker\Docker\resources\bin\docker.exe"
    run = Mock(return_value=subprocess.CompletedProcess([], 0, "owned-container-id\n", ""))
    monkeypatch.setattr(environments.subprocess, "run", run)

    env.cleanup()

    args, kwargs = run.call_args
    assert args[0] == [env.config.executable, "rm", "--force", "--", "owned-container-id"]
    assert kwargs["shell"] is False
    assert 0 < kwargs["timeout"] <= 10
    assert kwargs["check"] is False
    assert kwargs["capture_output"] is True
    assert env.container_id is None


def test_cleanup_is_idempotent_including_destructor(env, monkeypatch):
    run = Mock(return_value=subprocess.CompletedProcess([], 0, "", ""))
    monkeypatch.setattr(environments.subprocess, "run", run)

    env.cleanup()
    env.cleanup()
    env.__del__()

    assert run.call_count == 1


def test_cleanup_of_partially_initialized_environment_does_nothing(monkeypatch):
    run = Mock()
    monkeypatch.setattr(environments.subprocess, "run", run)
    instance = ManagedDockerEnvironment.__new__(ManagedDockerEnvironment)

    instance.cleanup()
    instance.__del__()

    run.assert_not_called()


def test_cleanup_accepts_container_already_removed(env, monkeypatch, caplog):
    run = Mock(return_value=subprocess.CompletedProcess(
        [], 1, "", "Error response from daemon: No such container: owned-container-id\n"
    ))
    monkeypatch.setattr(environments.subprocess, "run", run)

    with caplog.at_level(logging.WARNING):
        env.cleanup()

    assert not caplog.records
    assert env.container_id is None


@pytest.mark.parametrize("error", [
    subprocess.TimeoutExpired(["docker", "rm"], 10),
    FileNotFoundError("Docker executable is unavailable"),
    OSError("Docker process could not be started"),
])
def test_cleanup_failure_preserves_owned_id_for_successful_retry(env, monkeypatch, caplog, error):
    run = Mock(side_effect=error)
    monkeypatch.setattr(environments.subprocess, "run", run)

    with caplog.at_level(logging.WARNING):
        env.cleanup()

    assert run.call_count == 1
    assert "Could not remove Docker container owned-container-id" in caplog.text
    assert env.container_id == "owned-container-id"

    run.side_effect = None
    run.return_value = subprocess.CompletedProcess([], 0, "owned-container-id\n", "")
    env.cleanup()
    env.__del__()

    assert run.call_count == 2
    assert run.call_args.args[0][-1] == "owned-container-id"
    assert env.container_id is None


def test_cleanup_logs_nonzero_docker_response(env, monkeypatch, caplog):
    monkeypatch.setattr(environments.subprocess, "run", Mock(
        return_value=subprocess.CompletedProcess([], 1, "", "Cannot connect to the Docker daemon\n")
    ))

    with caplog.at_level(logging.WARNING):
        env.cleanup()

    assert "owned-container-id" in caplog.text
    assert "Cannot connect to the Docker daemon" in caplog.text
    assert env.container_id == "owned-container-id"


def test_cleanup_does_not_mask_original_failure(env, monkeypatch):
    monkeypatch.setattr(environments.subprocess, "run", Mock(
        side_effect=subprocess.TimeoutExpired(["docker", "rm"], 10)
    ))

    with pytest.raises(ValueError, match="original agent failure"):
        try:
            raise ValueError("original agent failure")
        finally:
            env.cleanup()
