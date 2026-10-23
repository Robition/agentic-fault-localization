"""Turn a mini-swe-agent trajectory into one predictions.jsonl row.

Locations: the last block between `<<<LOCATIONS` and `LOCATIONS>>>` in the
submission, parsed as a JSON list (or an object with a "locations" list). Keep
at most 5 entries that have a non-empty "file"; drop the others. "function"
must be a string or null; line numbers must be integers or null. If there is
no block, the JSON is invalid, or no entry survives, `locations` is empty and
`parse_error` is true.

exec_attempts: the number of agent commands that try to run code. A command is
split into simple commands on `;`, `&&`, `||`, `|`, `(`, `)` and newlines. A
simple command counts if, after leading `VAR=value` assignments and wrappers
such as `timeout 30` or `env`, its program's basename is python, pythonX[.Y],
pytest, py.test, tox, nox, or a test runner script (runtests.py, bin/test).
Each agent command counts at most once. Both arms are counted the same way, so
in the static arm this measures attempts that failed.

Tokens: the sum of `usage.prompt_tokens` and `usage.completion_tokens` over
every model response in the trajectory, including responses rejected for a
format error.
"""

from __future__ import annotations

import json
import re
import shlex

MAX_LOCATIONS = 5
_BLOCK_RE = re.compile(r"<<<LOCATIONS\s*(.*?)\s*LOCATIONS>>>", re.DOTALL)
_SPLIT_RE = re.compile(r"\|\||&&|[;|()\n]")
_ASSIGN_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
_WRAPPERS = {"timeout", "env", "time", "nice", "nohup", "exec", "command", "xargs", "sudo"}
_RUNNER_RE = re.compile(r"^(python[0-9.]*|pytest|py\.test|tox|nox|runtests\.py)$")


# --------------------------------------------------------------------------- #
# Locations
# --------------------------------------------------------------------------- #


def _int_or_none(v: object) -> int | None:
    if isinstance(v, bool):
        return None
    if isinstance(v, int):
        return v
    if isinstance(v, str) and v.strip().lstrip("-").isdigit():
        return int(v.strip())
    return None


def _clean_location(item: object) -> dict | None:
    if not isinstance(item, dict):
        return None
    file = item.get("file")
    if not isinstance(file, str) or not file.strip():
        return None
    func = item.get("function")
    if not isinstance(func, str) or not func.strip():
        func = None
    start = _int_or_none(item.get("start_line"))
    end = _int_or_none(item.get("end_line"))
    if start is not None and end is None:
        end = start
    if start is None and end is not None:
        start = end
    return {"file": file.strip(), "function": func.strip() if func else None, "start_line": start, "end_line": end}


def parse_locations(submission: str | None) -> tuple[list[dict], bool]:
    """Return (locations, parse_error)."""
    blocks = _BLOCK_RE.findall(submission or "")
    if not blocks:
        return [], True
    try:
        data = json.loads(blocks[-1])
    except json.JSONDecodeError:
        return [], True
    if isinstance(data, dict):
        data = data.get("locations")
    if not isinstance(data, list):
        return [], True
    locations = [loc for loc in map(_clean_location, data) if loc is not None][:MAX_LOCATIONS]
    return locations, not locations


# --------------------------------------------------------------------------- #
# Exec attempts
# --------------------------------------------------------------------------- #


def _program(simple_command: str) -> str | None:
    try:
        tokens = shlex.split(simple_command, comments=True)
    except ValueError:
        tokens = simple_command.split()
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        if _ASSIGN_RE.match(tok) or tok.startswith("-") or tok.replace(".", "").isdigit():
            i += 1  # VAR=value, a wrapper's flag, or timeout's duration
        elif tok in _WRAPPERS:
            i += 1
        else:
            return tok
    return None


def runs_code(command: str) -> bool:
    for part in _SPLIT_RE.split(command):
        prog = _program(part)
        if prog is None:
            continue
        if prog.endswith("bin/test") or _RUNNER_RE.match(prog.rsplit("/", 1)[-1]):
            return True
    return False


def agent_commands(messages: list[dict]) -> list[str]:
    commands = []
    for msg in messages:
        for action in (msg.get("extra") or {}).get("actions") or []:
            if isinstance(action, dict) and isinstance(action.get("command"), str):
                commands.append(action["command"])
    return commands


def count_exec_attempts(messages: list[dict]) -> int:
    return sum(runs_code(c) for c in agent_commands(messages))


# --------------------------------------------------------------------------- #
# Tokens
# --------------------------------------------------------------------------- #


def count_tokens(messages: list[dict]) -> tuple[int, int]:
    """Return (input_tokens, output_tokens) summed over all model responses."""
    inp = out = 0
    for msg in messages:
        response = (msg.get("extra") or {}).get("response")
        usage = response.get("usage") if isinstance(response, dict) else None
        if isinstance(usage, dict):
            inp += usage.get("prompt_tokens") or 0
            out += usage.get("completion_tokens") or 0
    return inp, out
