from execloc.parse import count_exec_attempts, count_tokens, parse_locations, runs_code


def test_parses_last_block_and_cleans_entries():
    sub = (
        "<<<LOCATIONS\n[]\nLOCATIONS>>>\n"
        "<<<LOCATIONS\n"
        '[{"file": "a.py", "function": "C.m", "start_line": 3, "end_line": 5},'
        ' {"file": "", "function": null},'
        ' {"file": "b.py", "function": "", "start_line": "7"}]\n'
        "LOCATIONS>>>"
    )
    locs, err = parse_locations(sub)
    assert not err
    assert locs == [
        {"file": "a.py", "function": "C.m", "start_line": 3, "end_line": 5},
        {"file": "b.py", "function": None, "start_line": 7, "end_line": 7},
    ]


def test_keeps_at_most_five():
    items = ",".join(f'{{"file": "f{i}.py"}}' for i in range(8))
    locs, err = parse_locations(f"<<<LOCATIONS [{items}] LOCATIONS>>>")
    assert not err and [l["file"] for l in locs] == [f"f{i}.py" for i in range(5)]


def test_accepts_object_with_locations_key():
    locs, err = parse_locations('<<<LOCATIONS {"locations": [{"file": "x.py"}]} LOCATIONS>>>')
    assert not err and locs[0]["file"] == "x.py"


def test_parse_errors():
    for sub in ["", None, "no block", "<<<LOCATIONS not json LOCATIONS>>>", "<<<LOCATIONS [] LOCATIONS>>>",
                '<<<LOCATIONS [{"function": "f"}] LOCATIONS>>>']:
        assert parse_locations(sub) == ([], True)


def test_runs_code():
    yes = [
        "python -c 'print(1)'", "cd /testbed && python3.9 repro.py", "timeout 30 pytest -x tests/",
        "PYTHONPATH=. python -m pytest", "cat x | python", "./tests/runtests.py admin", "bin/test sympy",
        "tox -e py39", "env FOO=1 python x.py", "/opt/miniconda3/bin/python x.py",
    ]
    no = [
        "grep -rn python .", "cat setup.py", "find . -name '*.py'", "sed -n 1,20p pytest.ini",
        "echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT && cat <<'EOF'\n<<<LOCATIONS\n[]\nLOCATIONS>>>\nEOF",
        "git log -1", "ls tests/",
    ]
    assert [c for c in yes if not runs_code(c)] == []
    assert [c for c in no if runs_code(c)] == []


def _assistant(cmd, prompt=10, completion=2):
    return {"role": "assistant", "extra": {"actions": [{"command": cmd}],
            "response": {"usage": {"prompt_tokens": prompt, "completion_tokens": completion}}}}


def test_counts_from_trajectory():
    messages = [
        {"role": "system", "content": "s"},
        _assistant("grep -rn foo ."),
        {"role": "user", "content": "obs", "extra": {"returncode": 0}},
        _assistant("python repro.py && pytest -x"),  # one command, counted once
        # a format-error response: tokens count, no actions
        {"role": "user", "extra": {"response": {"usage": {"prompt_tokens": 5, "completion_tokens": 1}}}},
        _assistant("pytest tests/test_x.py"),
    ]
    assert count_exec_attempts(messages) == 2
    assert count_tokens(messages) == (35, 7)
