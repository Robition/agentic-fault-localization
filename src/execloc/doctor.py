"""Read-only machine checks; no model loading, inference, or image pulls.

Workspace disk thresholds are a 5 GiB minimum and a 50 GiB warning boundary,
not a batch-capacity estimate for Docker's backing disk.
"""

from __future__ import annotations

import ipaddress
import json
import os
import platform
import re
import shutil
import socket
import subprocess
import sys
from dataclasses import dataclass
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit, urlunsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

from execloc.run import REPO_ROOT, RunError, endpoint_from_env

GIB = 1024 ** 3
MIN_DISK_GIB = 5
WARN_DISK_GIB = 50
MIN_CONTEXT = 32768
DOCKER_TIMEOUT = 20
HTTP_TIMEOUT = 5


@dataclass(frozen=True)
class Check:
    status: str
    name: str
    detail: str


class _NoRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # A local URL must not redirect a credential-bearing request elsewhere.
        return None


def _get_json(url: str, api_key: str) -> dict:
    """Bound each local GET, bypass proxies, and limit response size."""
    request = Request(url, headers={"Authorization": f"Bearer {api_key}"})
    with build_opener(ProxyHandler({}), _NoRedirects()).open(request, timeout=HTTP_TIMEOUT) as response:
        raw = response.read(2 * 1024 * 1024 + 1)
    if len(raw) > 2 * 1024 * 1024:
        raise ValueError("response too large")
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError("expected a JSON object")
    return data


def _request_failure(error: Exception) -> str:
    # Exception strings can contain URLs, credentials, and response bodies.
    if isinstance(error, HTTPError):
        return f"HTTP {error.code}"
    if isinstance(error, (TimeoutError, socket.timeout)):
        return f"timed out after {HTTP_TIMEOUT}s"
    if isinstance(error, URLError):
        return "connection failed or timed out"
    return "invalid response or request failed"


def _endpoint_urls(base_url: str) -> tuple[str, str]:
    parts = urlsplit(base_url)
    if parts.scheme not in {"http", "https"} or not parts.hostname:
        raise ValueError("EXECLOC_BASE_URL must be a local HTTP(S) endpoint")
    if parts.username is not None or parts.password is not None or parts.query or parts.fragment:
        raise ValueError("URL credentials, query, and fragment are not allowed")
    host = parts.hostname.lower()
    try:
        is_local = host == "localhost" or ipaddress.ip_address(host).is_loopback
    except ValueError:
        is_local = False
    if not is_local:
        raise ValueError("the model endpoint must be local")
    parts.port  # Validate malformed ports before attempting a request.
    base_path = parts.path.rstrip("/")
    prefix = base_path[:-3] if base_path.endswith("/v1") else base_path
    return (
        urlunsplit((parts.scheme, parts.netloc, base_path + "/models", "", "")),
        urlunsplit((parts.scheme, parts.netloc, prefix + "/api/ps", "", "")),
    )


def _model_matches(actual: object, configured: str) -> bool:
    if not isinstance(actual, str):
        return False
    actual, configured = actual.removesuffix(":latest"), configured.removesuffix(":latest")
    return actual == configured


def _revision_check(loaded: list[dict]) -> Check:
    expected = os.environ.get("EXECLOC_MODEL_REVISION", "").strip().lower().removeprefix("sha256:")
    if not expected:
        return Check("WARN", "Model revision", "EXECLOC_MODEL_REVISION is unset; the model build is not pinned in run metadata")
    if not loaded:
        return Check("WARN", "Model revision", "loaded model digest is unavailable; configured pin is unverified")
    if re.fullmatch(r"[0-9a-f]{12,64}", expected) is None:
        return Check("WARN", "Model revision", "configured revision is not a hexadecimal digest; loaded build could not be compared")
    digests = [model.get("digest") for model in loaded]
    if not all(isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value.lower().removeprefix("sha256:")) for value in digests):
        return Check("WARN", "Model revision", "loaded model digest is unavailable; configured pin is unverified")
    matches = all(value.lower().removeprefix("sha256:").startswith(expected) for value in digests)
    suffix = "digest" if len(expected) == 64 else "digest prefix (use the full digest for reproducibility)"
    return Check("PASS" if matches else "FAIL", "Model revision", f"loaded build matches configured {suffix}" if matches else "loaded model digest differs from EXECLOC_MODEL_REVISION")


def _server_checks() -> list[Check]:
    try:
        endpoint = endpoint_from_env()
    except RunError as error:
        # This helper raises only fixed missing-variable instructions.
        return [
            Check("FAIL", "Model endpoint", str(error)),
            Check("WARN", "Configured model", "not checked without endpoint configuration"),
            Check("WARN", "Server context", "not checked without endpoint configuration"),
        ]
    try:
        models_url, ps_url = _endpoint_urls(endpoint["base_url"])
    except ValueError:
        return [
            Check("FAIL", "Model endpoint", "EXECLOC_BASE_URL must be local HTTP(S), without URL credentials, query, fragment, or an invalid port"),
            Check("WARN", "Configured model", "not checked because the endpoint configuration is invalid"),
            Check("WARN", "Server context", "not checked because the endpoint configuration is invalid"),
        ]
    try:
        listed = _get_json(models_url, endpoint["api_key"])
    except Exception as error:
        return [
            Check("FAIL", "Model endpoint", _request_failure(error)),
            Check("WARN", "Configured model", "not checked because the models endpoint failed"),
            Check("WARN", "Server context", "not checked because the models endpoint failed"),
        ]
    checks = [Check("PASS", "Model endpoint", "local OpenAI-compatible models endpoint responded")]
    models = listed.get("data")
    found = isinstance(models, list) and any(
        isinstance(model, dict) and _model_matches(model.get("id"), endpoint["model"])
        for model in models
    )
    checks.append(Check(
        "PASS" if found else "FAIL", "Configured model",
        "configured model is listed (including :latest aliases)" if found else "configured model is absent from the models response",
    ))
    try:
        running = _get_json(ps_url, endpoint["api_key"])
    except Exception as error:
        checks.append(Check("WARN", "Server context", "actual context unavailable via optional Ollama /api/ps: " + _request_failure(error)))
        checks.append(_revision_check([]))
        return checks
    loaded = [
        model for model in running.get("models", [])
        if isinstance(model, dict) and any(
            _model_matches(model.get(key), endpoint["model"]) for key in ("name", "model")
        )
    ] if isinstance(running.get("models"), list) else []
    if not loaded:
        checks.append(Check("WARN", "Server context", "configured model is not reported loaded; actual context is unverified; doctor does not load it"))
        checks.append(_revision_check([]))
        return checks
    checks.append(_revision_check(loaded))
    contexts = [model.get("context_length") for model in loaded]
    if not all(isinstance(value, int) and not isinstance(value, bool) and value > 0 for value in contexts):
        checks.append(Check("WARN", "Server context", "loaded model found, but the server did not expose a valid actual context length"))
        return checks
    actual = min(contexts)
    checks.append(Check(
        "PASS" if actual >= MIN_CONTEXT else "FAIL", "Server context",
        f"actual loaded context {actual}; minimum required {MIN_CONTEXT}; no inference request was sent",
    ))
    return checks


def collect_checks() -> list[Check]:
    checks = []
    version = ".".join(map(str, sys.version_info[:3]))
    checks.append(Check("PASS" if sys.version_info[:2] == (3, 12) else "FAIL", "Python", f"{version}; project requires Python 3.12"))
    arch = platform.machine().lower()
    if arch in {"arm64", "aarch64"}:
        checks.append(Check("WARN", "CPU architecture", f"{arch}; execution arm is unavailable on ARM; data/scoring remain usable"))
    elif arch in {"amd64", "x86_64"}:
        checks.append(Check("PASS", "CPU architecture", f"{arch}; compatible with x86_64 execution images"))
    else:
        checks.append(Check("WARN", "CPU architecture", "unrecognized architecture; execution images require x86_64"))
    try:
        free_gib = shutil.disk_usage(REPO_ROOT).free / GIB
        status = "FAIL" if free_gib < MIN_DISK_GIB else "WARN" if free_gib < WARN_DISK_GIB else "PASS"
        checks.append(Check(status, "Workspace disk", f"{free_gib:.1f} GiB free; minimum {MIN_DISK_GIB} GiB, warning below {WARN_DISK_GIB} GiB; batch capacity is not certified"))
    except OSError:
        checks.append(Check("FAIL", "Workspace disk", "could not inspect free workspace disk space"))
    try:
        info = subprocess.run(
            ["docker", "info", "--format", "{{json .}}"], capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=DOCKER_TIMEOUT, check=False,
        )
        if info.returncode:
            checks.append(Check("FAIL", "Docker", "daemon unavailable; start Docker and check its Linux engine"))
        else:
            data = json.loads(info.stdout)
            if not isinstance(data, dict):
                raise ValueError("invalid Docker info")
            server_os = data.get("OSType")
            # Some Docker CLI builds return zero and client-only JSON even
            # though contacting the server failed. An empty OSType does not
            # mean that the daemon is running Windows containers.
            if data.get("ServerErrors") or not isinstance(server_os, str) or not server_os.strip():
                checks.append(Check("FAIL", "Docker", "daemon unavailable; Docker returned no healthy server information"))
            else:
                linux = server_os.lower() == "linux"
                checks.append(Check("PASS" if linux else "FAIL", "Docker", "Linux daemon reachable" if linux else "daemon responded, but Linux containers are required"))
                root = data.get("DockerRootDir")
                detail = f"daemon DockerRootDir={root!r}; " if isinstance(root, str) and root else "daemon storage path unavailable; "
                checks.append(Check("WARN", "Docker storage", detail + "workspace free space does not measure the daemon's backing disk/VM; check its capacity before a batch"))
    except FileNotFoundError:
        checks.append(Check("FAIL", "Docker", "docker executable is not on PATH"))
    except subprocess.TimeoutExpired:
        checks.append(Check("FAIL", "Docker", f"daemon probe timed out after {DOCKER_TIMEOUT}s"))
    except (OSError, ValueError):
        checks.append(Check("FAIL", "Docker", "could not read daemon information"))
    checks.extend(_server_checks())
    return checks


def main() -> int:
    checks = collect_checks()
    for check in checks:
        print(f"{check.status:4}  {check.name}: {check.detail}")
    return int(any(check.status == "FAIL" for check in checks))
