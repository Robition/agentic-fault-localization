import json
import math

from execloc.score import main, score_instance, score_rows

GOLD = {
    "instance_id": "x__y-1",
    "files": ["pkg/mod.py", "pkg/util.py"],
    "functions": [{"file": "pkg/mod.py", "name": "Foo.bar"}],
    "lines": [{"file": "pkg/mod.py", "start": 10, "end": 12}],
}


def loc(file, function=None, start=None, end=None):
    return {"file": file, "function": function, "start_line": start, "end_line": end}


def hits(locations, gold=GOLD):
    return score_instance(locations, gold)


def test_all_levels_hit_at_rank_1():
    h = hits([loc("pkg/mod.py", "Foo.bar", 11, 11)])
    assert all(v == 1 for v in h.values())


def test_hit_at_rank_3_counts_for_3_and_5_only():
    h = hits([
        loc("a.py", "f", 1, 2),
        loc("b.py", "g", 1, 2),
        loc("pkg/mod.py", "Foo.bar", 12, 20),
    ])
    for lvl in ("file", "function", "line"):
        assert (h[f"{lvl}_hit@1"], h[f"{lvl}_hit@3"], h[f"{lvl}_hit@5"]) == (0, 1, 1)


def test_hit_at_rank_5():
    preds = [loc(f"other{i}.py", None, 1, 1) for i in range(4)] + [loc("pkg/util.py")]
    h = hits(preds)
    assert (h["file_hit@1"], h["file_hit@3"], h["file_hit@5"]) == (0, 0, 1)


def test_levels_are_independent():
    # Right file, wrong function, lines just outside the gold range.
    h = hits([loc("pkg/mod.py", "Foo.baz", 13, 15)])
    assert h["file_hit@1"] == 1
    assert h["function_hit@5"] == 0
    assert h["line_hit@5"] == 0


def test_line_overlap_boundaries():
    assert hits([loc("pkg/mod.py", None, 1, 10)])["line_hit@1"] == 1
    assert hits([loc("pkg/mod.py", None, 12, 12)])["line_hit@1"] == 1
    assert hits([loc("pkg/mod.py", None, 9, 9)])["line_hit@1"] == 0
    # Reversed range and missing end_line are tolerated.
    assert hits([loc("pkg/mod.py", None, 12, 5)])["line_hit@1"] == 1
    assert hits([loc("pkg/mod.py", None, 11, None)])["line_hit@1"] == 1


def test_line_requires_same_file():
    assert hits([loc("pkg/util.py", None, 10, 12)])["line_hit@5"] == 0


def test_duplicate_files_do_not_consume_file_ranks():
    preds = [loc("a.py", None, i, i) for i in range(1, 4)] + [loc("pkg/mod.py")]
    h = hits(preds)
    # Files dedupe to [a.py, pkg/mod.py], so the gold file sits at rank 2.
    assert (h["file_hit@1"], h["file_hit@3"]) == (0, 1)


def test_null_function_skipped_at_function_level():
    preds = [loc("pkg/mod.py", None, 1, 1), loc("pkg/mod.py", "Foo.bar", 1, 1)]
    h = hits(preds)
    assert h["function_hit@1"] == 1


def test_function_name_normalization():
    assert hits([loc("pkg/mod.py", "pkg.mod.Foo.bar", 1, 1)])["function_hit@1"] == 1
    assert hits([loc("pkg/mod.py", "Foo.bar()", 1, 1)])["function_hit@1"] == 1
    # A bare method name is not enough; nor is a suffix that isn't on a dot boundary.
    assert hits([loc("pkg/mod.py", "bar", 1, 1)])["function_hit@1"] == 0
    assert hits([loc("pkg/mod.py", "XFoo.bar", 1, 1)])["function_hit@1"] == 0
    # Right name, wrong file.
    assert hits([loc("pkg/util.py", "Foo.bar", 1, 1)])["function_hit@1"] == 0


def test_path_normalization():
    for path in ("/testbed/pkg/mod.py", "./pkg/mod.py", "testbed/pkg/mod.py"):
        assert hits([loc(path)])["file_hit@1"] == 1


def test_no_gold_functions_gives_none():
    gold = {**GOLD, "functions": []}
    h = hits([loc("pkg/mod.py", "Foo.bar", 11, 11)], gold)
    assert h["function_hit@1"] is None and h["function_hit@5"] is None
    assert h["file_hit@1"] == 1


def test_empty_locations_all_miss():
    h = hits([])
    assert all(v == 0 for v in h.values())


def test_malformed_locations_do_not_crash():
    h = hits([{"file": None}, {"function": "Foo.bar"}, {"file": "pkg/mod.py", "start_line": "x"}])
    assert h["file_hit@1"] == 1
    assert h["line_hit@5"] == 0


def _pred(iid, arm, locations, **kw):
    row = {"instance_id": iid, "arm": arm, "locations": locations, "parse_error": False,
           "steps": 10, "input_tokens": 100, "output_tokens": 10, "wall_seconds": 1.0,
           "exec_attempts": 0, "exit_status": "Submitted"}
    row.update(kw)
    return row


def test_score_rows_missing_instance_is_a_miss():
    gold2 = {**GOLD, "instance_id": "x__y-2"}
    rows = score_rows([_pred("x__y-1", "static", [loc("pkg/mod.py", "Foo.bar", 11, 11)])], [GOLD, gold2])
    by_id = {r["instance_id"]: r for r in rows}
    assert by_id["x__y-1"]["file_hit@1"] == 1
    assert by_id["x__y-2"]["missing"] is True
    assert by_id["x__y-2"]["file_hit@5"] == 0


def test_main_writes_per_instance_and_per_arm_summary(tmp_path, capsys):
    gold_rows = [GOLD, {**GOLD, "instance_id": "x__y-2", "functions": []}]
    gold_path = tmp_path / "gold.jsonl"
    gold_path.write_text("".join(json.dumps(g) + "\n" for g in gold_rows))

    run = tmp_path / "results" / "r1"
    run.mkdir(parents=True)
    preds = [
        _pred("x__y-1", "static", [loc("a.py"), loc("pkg/mod.py", "Foo.bar", 11, 11)]),
        _pred("x__y-2", "static", [], parse_error=True, exit_status="parse_error"),
        _pred("x__y-1", "exec", [loc("pkg/mod.py", "Foo.bar", 11, 11)], exec_attempts=3),
        _pred("x__y-2", "exec", [loc("pkg/util.py", None, 1, 1)], exec_attempts=1),
    ]
    (run / "predictions.jsonl").write_text("".join(json.dumps(p) + "\n" for p in preds))

    assert main("r1", gold_path, tmp_path / "results") == 0

    scores = [json.loads(l) for l in (run / "scores.jsonl").read_text().splitlines()]
    assert len(scores) == 4

    import pandas as pd

    s = pd.read_csv(run / "summary.csv").set_index("arm")
    assert s.loc["static", "file_hit@1"] == 0.0
    assert s.loc["static", "file_hit@3"] == 0.5
    assert s.loc["static", "parse_error_rate"] == 0.5
    assert s.loc["exec", "file_hit@1"] == 1.0
    # Only x__y-1 has gold functions, so the function mean is over one instance.
    assert s.loc["exec", "n_function"] == 1
    assert s.loc["exec", "function_hit@1"] == 1.0
    # a.py carries no function, so Foo.bar is function-level rank 1 for static.
    assert s.loc["static", "function_hit@1"] == 1.0
    assert s.loc["static", "line_hit@1"] == 0.5
    assert s.loc["exec", "line_hit@1"] == 0.5
    assert math.isclose(s.loc["exec", "mean_exec_attempts"], 2.0)


def test_main_missing_predictions_file(tmp_path):
    gold_path = tmp_path / "gold.jsonl"
    gold_path.write_text(json.dumps(GOLD) + "\n")
    assert main("nope", gold_path, tmp_path / "results") == 1
