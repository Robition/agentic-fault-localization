"""Doctor checks use local mocks, never Docker or a model endpoint."""

from __future__ import annotations

import json
import subprocess
from types import SimpleNamespace
from urllib.error import HTTPError, URLError

import pytest

from execloc import doctor, run


@pytest.fixture
def ready(monkeypatch, tmp_path):
    monkeypatch.setattr(doctor, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(run, "REPO_ROOT", tmp_path)
    for key in ("EXECLOC_BASE_URL", "EXECLOC_MODEL", "EXECLOC_API_KEY", "EXECLOC_MODEL_REVISION"):
        # Record even originally absent keys, so dotenv's later writes are undone.
        monkeypatch.setenv(key, "")
        monkeypatch.delenv(key, raising=False)
    (tmp_path / ".env").write_text(
        "EXECLOC_BASE_URL=http://localhost:11434/v1\n"
        "EXECLOC_MODEL=devstral-small-2\nEXECLOC_API_KEY=secret-do-not-print\n"
        + "EXECLOC_MODEL_REVISION=" + "a" * 64 + "\n"
    )
    monkeypatch.setattr(doctor.sys, "version_info", (3, 12, 14))
    monkeypatch.setattr(doctor.platform, "machine", lambda: "AMD64")
    monkeypatch.setattr(doctor.shutil, "disk_usage", lambda path: SimpleNamespace(free=80 * doctor.GIB))
    docker_calls, http_calls = [], []

    def docker_call(command, **kwargs):
        docker_calls.append((command, kwargs))
        return subprocess.CompletedProcess(command, 0, json.dumps({"OSType": "linux", "DockerRootDir": "/var/lib/docker"}), "")

    def http_call(url, api_key):
        http_calls.append(url)
        assert api_key == "secret-do-not-print"
        if url.endswith("/models"):
            return {"data": [{"id": "devstral-small-2:latest"}]}
        return {"models": [{"name": "devstral-small-2:latest", "context_length": 32768, "digest": "a" * 64}]}

    monkeypatch.setattr(doctor.subprocess, "run", docker_call)
    monkeypatch.setattr(doctor, "_get_json", http_call)
    return tmp_path, docker_calls, http_calls


def by_name():
    return {check.name: check for check in doctor.collect_checks()}


def test_ready_machine_checks_env_alias_and_actual_context_without_inference(ready, capsys):
    assert doctor.main() == 0
    output = capsys.readouterr().out
    assert "actual loaded context 32768" in output
    assert "WARN  Docker storage" in output
    assert "secret-do-not-print" not in output
    assert "http://" not in output
    _, docker_calls, http_calls = ready
    assert len(docker_calls) == 1
    assert docker_calls[0][1]["timeout"] == 20
    assert http_calls == ["http://localhost:11434/v1/models", "http://localhost:11434/api/ps"]


@pytest.mark.parametrize("error", [FileNotFoundError(), subprocess.TimeoutExpired("docker", 20)])
def test_missing_or_hung_docker_fails_but_other_checks_still_run(ready, monkeypatch, error):
    def fail(*args, **kwargs):
        raise error
    monkeypatch.setattr(doctor.subprocess, "run", fail)
    checks = by_name()
    assert checks["Docker"].status == "FAIL"
    assert checks["Server context"].status == "PASS"
    assert doctor.main() == 1


def test_failed_docker_does_not_print_stderr_secrets(ready, monkeypatch, capsys):
    monkeypatch.setattr(doctor.subprocess, "run", lambda *args, **kwargs: subprocess.CompletedProcess(args[0], 1, "", "secret-do-not-print https://user:password@example.invalid"))
    assert doctor.main() == 1
    assert "secret-do-not-print" not in capsys.readouterr().out


@pytest.mark.parametrize("payload", [
    {"OSType": "", "DockerRootDir": "", "ServerErrors": ["secret-do-not-print"]},
    {"OSType": "", "DockerRootDir": ""},
    {"ClientInfo": {}},
    {"OSType": "linux", "ServerErrors": ["secret-do-not-print"]},
])
def test_zero_exit_client_only_docker_info_is_unavailable(ready, monkeypatch, capsys, payload):
    monkeypatch.setattr(doctor.subprocess, "run", lambda *args, **kwargs: subprocess.CompletedProcess(args[0], 0, json.dumps(payload), ""))
    checks = by_name()
    assert checks["Docker"].status == "FAIL"
    assert "daemon unavailable" in checks["Docker"].detail
    assert "Docker storage" not in checks
    assert doctor.main() == 1
    assert "secret-do-not-print" not in capsys.readouterr().out


def test_actual_windows_container_daemon_has_distinct_failure(ready, monkeypatch):
    monkeypatch.setattr(doctor.subprocess, "run", lambda *args, **kwargs: subprocess.CompletedProcess(args[0], 0, json.dumps({"OSType": "windows", "DockerRootDir": "C:/docker"}), ""))
    check = by_name()["Docker"]
    assert check.status == "FAIL"
    assert "Linux containers are required" in check.detail
    assert "unavailable" not in check.detail


@pytest.mark.parametrize("payload, expected", [
    ({"models": [{"name": "devstral-small-2", "context_length": 4096}]}, "FAIL"),
    ({"models": []}, "WARN"),
    ({"models": [{"model": "devstral-small-2:latest"}]}, "WARN"),
])
def test_context_is_checked_from_running_model_not_env_declaration(ready, monkeypatch, payload, expected):
    monkeypatch.setenv("EXECLOC_CONTEXT_LENGTH", "32768")
    previous = doctor._get_json
    monkeypatch.setattr(doctor, "_get_json", lambda url, key: previous(url, key) if url.endswith("/models") else payload)
    assert by_name()["Server context"].status == expected
    assert doctor.main() == (1 if expected == "FAIL" else 0)


def test_generic_openai_server_without_ollama_context_only_warns(ready, monkeypatch):
    previous = doctor._get_json
    def generic(url, key):
        if url.endswith("/api/ps"):
            raise HTTPError(url, 404, "not Ollama", {}, None)
        return previous(url, key)
    monkeypatch.setattr(doctor, "_get_json", generic)
    checks = by_name()
    assert checks["Server context"].status == "WARN"
    assert doctor.main() == 0


@pytest.mark.parametrize("error", [TimeoutError(), URLError("https://user:secret-do-not-print@example.invalid")])
def test_endpoint_failure_does_not_leak_url_or_key(ready, monkeypatch, capsys, error):
    def fail(*args):
        raise error
    monkeypatch.setattr(doctor, "_get_json", fail)
    assert doctor.main() == 1
    output = capsys.readouterr().out
    assert "secret-do-not-print" not in output
    assert "example.invalid" not in output


def test_missing_env_fails_without_any_http_request(ready, capsys):
    root, _, http_calls = ready
    (root / ".env").unlink()
    assert doctor.main() == 1
    assert "EXECLOC_BASE_URL is not set" in capsys.readouterr().out
    assert http_calls == []


@pytest.mark.parametrize("url", [
    "https://api.openai.com/v1", "http://user:secret-do-not-print@localhost:11434/v1",
    "http://localhost:11434/v1?key=secret-do-not-print", "http://localhost:wrong/v1",
])
def test_only_clean_loopback_endpoints_are_contacted(ready, monkeypatch, capsys, url):
    monkeypatch.setenv("EXECLOC_BASE_URL", url)
    assert doctor.main() == 1
    assert ready[2] == []
    assert "secret-do-not-print" not in capsys.readouterr().out


def test_configured_model_absent_is_a_failure(ready, monkeypatch):
    previous = doctor._get_json
    monkeypatch.setattr(doctor, "_get_json", lambda url, key: {"data": [{"id": "different-model"}]} if url.endswith("/models") else previous(url, key))
    assert by_name()["Configured model"].status == "FAIL"
    assert doctor.main() == 1


def test_loaded_digest_mismatch_fails_without_printing_revision(ready, monkeypatch, capsys):
    monkeypatch.setenv("EXECLOC_MODEL_REVISION", "b" * 64)
    assert by_name()["Model revision"].status == "FAIL"
    assert doctor.main() == 1
    assert "b" * 64 not in capsys.readouterr().out


def test_unpinned_or_unexposed_build_only_warns(ready, monkeypatch):
    monkeypatch.setenv("EXECLOC_MODEL_REVISION", "")
    assert by_name()["Model revision"].status == "WARN"
    assert doctor.main() == 0


def test_arm_warns_and_wrong_python_fails(ready, monkeypatch):
    monkeypatch.setattr(doctor.platform, "machine", lambda: "aarch64")
    assert by_name()["CPU architecture"].status == "WARN"
    assert doctor.main() == 0
    monkeypatch.setattr(doctor.sys, "version_info", (3, 13, 0))
    assert by_name()["Python"].status == "FAIL"
    assert doctor.main() == 1


@pytest.mark.parametrize("free_gib, status", [(4, "FAIL"), (20, "WARN"), (80, "PASS")])
def test_disk_thresholds_do_not_certify_docker_storage(ready, monkeypatch, free_gib, status):
    monkeypatch.setattr(doctor.shutil, "disk_usage", lambda path: SimpleNamespace(free=free_gib * doctor.GIB))
    checks = by_name()
    assert checks["Workspace disk"].status == status
    assert checks["Docker storage"].status == "WARN"


def test_http_transport_has_timeout_bypasses_proxies_and_uses_get(monkeypatch):
    requests = []
    class Response:
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def read(self, limit):
            assert limit <= 2 * 1024 * 1024 + 1
            return b'{"data": []}'
    class Opener:
        def open(self, request, timeout):
            requests.append(request)
            assert timeout == 5
            return Response()
    def opener(*handlers):
        assert handlers[0].proxies == {}
        assert isinstance(handlers[1], doctor._NoRedirects)
        return Opener()
    monkeypatch.setattr(doctor, "build_opener", opener)
    assert doctor._get_json("http://localhost:11434/v1/models", "secret") == {"data": []}
    assert requests[0].get_method() == "GET"
    assert requests[0].get_header("Authorization") == "Bearer secret"
