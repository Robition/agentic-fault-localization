"""Score a run's predictions against gold at file, function, and line level.

Hit@k at a level is 1 if any of the top-k predicted locations matches any gold
item at that level, else 0. Ranks are taken per level after dropping locations
that carry no information at that level and de-duplicating, so repeating a file
five times doesn't use up five file-level slots.

- File: normalized paths are equal.
- Function: same file, and the predicted qualified name equals the gold name or
  ends with "." + gold name (tolerates a module-path prefix). A null function
  never matches.
- Line: same file, and [start_line, end_line] overlaps a gold line range.

An instance with no gold items at a level (for example, only module-level
changes have no gold function) gets None at that level and is left out of that
level's mean. A gold instance with no prediction row for an arm counts as a miss
at every level, because instances are never dropped.

Writes results/<run_id>/scores.jsonl and results/<run_id>/summary.csv.
"""

from __future__ import annotations

import sys
from pathlib import Path

from execloc.dataset import read_jsonl, write_jsonl

KS = (1, 3, 5)
LEVELS = ("file", "function", "line")


# --------------------------------------------------------------------------- #
# Normalization
# --------------------------------------------------------------------------- #


def norm_path(path: object) -> str | None:
    if not isinstance(path, str) or not path.strip():
        return None
    p = path.strip().replace("\\", "/")
    for prefix in ("/testbed/", "testbed/"):
        if p.startswith(prefix):
            p = p[len(prefix):]
    while p.startswith("./"):
        p = p[2:]
    return p.lstrip("/") or None


def norm_func(name: object) -> str | None:
    if not isinstance(name, str):
        return None
    n = name.strip().removesuffix("()").strip()
    return n or None


def _as_int(v: object) -> int | None:
    try:
        return int(v)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


# --------------------------------------------------------------------------- #
# Per-level ranked keys
# --------------------------------------------------------------------------- #


def _dedupe(items: list) -> list:
    seen, out = set(), []
    for it in items:
        if it not in seen:
            seen.add(it)
            out.append(it)
    return out


def ranked_files(locations: list[dict]) -> list[str]:
    return _dedupe([f for loc in locations if (f := norm_path(loc.get("file")))])


def ranked_functions(locations: list[dict]) -> list[tuple[str, str]]:
    keys = []
    for loc in locations:
        f, fn = norm_path(loc.get("file")), norm_func(loc.get("function"))
        if f and fn:
            keys.append((f, fn))
    return _dedupe(keys)


def ranked_lines(locations: list[dict]) -> list[tuple[str, int, int]]:
    keys = []
    for loc in locations:
        f = norm_path(loc.get("file"))
        s, e = _as_int(loc.get("start_line")), _as_int(loc.get("end_line"))
        if f is None or s is None:
            continue
        e = s if e is None else e
        keys.append((f, min(s, e), max(s, e)))
    return _dedupe(keys)


# --------------------------------------------------------------------------- #
# Matching
# --------------------------------------------------------------------------- #


def function_matches(pred: tuple[str, str], gold: tuple[str, str]) -> bool:
    (pf, pn), (gf, gn) = pred, gold
    return pf == gf and (pn == gn or pn.endswith("." + gn))


def line_matches(pred: tuple[str, int, int], gold: tuple[str, int, int]) -> bool:
    (pf, ps, pe), (gf, gs, ge) = pred, gold
    return pf == gf and ps <= ge and gs <= pe


def hits_at_k(preds: list, golds: list, match, ks=KS) -> dict[int, int | None]:
    if not golds:
        return {k: None for k in ks}
    first = next((i for i, p in enumerate(preds) if any(match(p, g) for g in golds)), None)
    return {k: int(first is not None and first < k) for k in ks}


def score_instance(locations: list[dict], gold: dict) -> dict[str, int | None]:
    gold_files = [f for f in (norm_path(x) for x in gold["files"]) if f]
    gold_funcs = [(norm_path(x["file"]), x["name"]) for x in gold["functions"]]
    gold_lines = [(norm_path(x["file"]), x["start"], x["end"]) for x in gold["lines"]]

    levels = {
        "file": hits_at_k(ranked_files(locations), gold_files, lambda p, g: p == g),
        "function": hits_at_k(ranked_functions(locations), gold_funcs, function_matches),
        "line": hits_at_k(ranked_lines(locations), gold_lines, line_matches),
    }
    return {f"{level}_hit@{k}": v for level, by_k in levels.items() for k, v in by_k.items()}


# --------------------------------------------------------------------------- #
# Run-level scoring
# --------------------------------------------------------------------------- #

COST_FIELDS = ("steps", "input_tokens", "output_tokens", "wall_seconds", "exec_attempts")


def score_rows(predictions: list[dict], gold_rows: list[dict]) -> list[dict]:
    gold_by_id = {g["instance_id"]: g for g in gold_rows}
    by_arm: dict[str, dict[str, dict]] = {}
    for row in predictions:
        iid, arm = row["instance_id"], row["arm"]
        if iid not in gold_by_id:
            print(f"warning: {iid} has no gold row; skipped", file=sys.stderr)
            continue
        if iid in by_arm.setdefault(arm, {}):
            print(f"warning: duplicate row for {iid} ({arm}); keeping the last", file=sys.stderr)
        by_arm[arm][iid] = row

    scored = []
    for arm in sorted(by_arm):
        rows = by_arm[arm]
        for iid, gold in gold_by_id.items():
            row = rows.get(iid)
            missing = row is None
            if missing:
                print(f"warning: {iid} has no prediction for arm {arm}; scored as a miss", file=sys.stderr)
                row = {"locations": [], "parse_error": False, "exit_status": "missing"}
            locations = row.get("locations") or []
            scores = score_instance(locations, gold)
            scored.append(
                {
                    "instance_id": iid,
                    "arm": arm,
                    "missing": missing,
                    "parse_error": bool(row.get("parse_error", False)),
                    "exit_status": row.get("exit_status"),
                    "n_locations": len(locations),
                    **scores,
                    **{f: row.get(f) for f in COST_FIELDS},
                }
            )
    return scored


def summarize(scored: list[dict]):
    import pandas as pd

    df = pd.DataFrame(scored)
    hit_cols = [f"{lvl}_hit@{k}" for lvl in LEVELS for k in KS]
    out = []
    for arm, g in df.groupby("arm", sort=True):
        rec: dict = {"arm": arm, "n": len(g), "missing": int(g["missing"].sum()),
                     "parse_error_rate": float(g["parse_error"].mean())}
        for lvl in LEVELS:
            rec[f"n_{lvl}"] = int(g[f"{lvl}_hit@1"].notna().sum())
        for col in hit_cols:
            rec[col] = float(pd.to_numeric(g[col], errors="coerce").mean())
        for f in COST_FIELDS:
            rec[f"mean_{f}"] = float(pd.to_numeric(g[f], errors="coerce").mean())
        out.append(rec)
    return pd.DataFrame(out)


def main(run_id: str, gold: Path, results_dir: Path) -> int:
    run_dir = results_dir / run_id
    pred_path = run_dir / "predictions.jsonl"
    if not pred_path.exists():
        print(f"{pred_path} not found.")
        return 1
    if not gold.exists():
        print(f"{gold} not found; run `uv run execloc gold` first.")
        return 1

    scored = score_rows(read_jsonl(pred_path), read_jsonl(gold))
    if not scored:
        print("no prediction rows to score.")
        return 1
    write_jsonl(run_dir / "scores.jsonl", scored)
    summary = summarize(scored)
    summary.to_csv(run_dir / "summary.csv", index=False, lineterminator="\n")

    import pandas as pd

    with pd.option_context("display.width", 200, "display.max_columns", None, "display.precision", 3):
        print(summary.set_index("arm").T.to_string())
    print(f"\nwrote {run_dir / 'scores.jsonl'} and {run_dir / 'summary.csv'}")
    return 0
