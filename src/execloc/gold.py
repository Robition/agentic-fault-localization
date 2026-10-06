"""Gold bug locations from each instance's gold patch against its base commit.

Rules (SETUP.md, "Data contracts"):
- Test files are excluded.
- Lines are the modified or removed lines in the pre-patch file, merged into
  contiguous ranges.
- A change block that only adds lines contributes the enclosing function of the
  insertion point. Its line range is the two pre-patch lines that bracket the
  insertion, so the block is still scoreable at line level.
- Functions are mapped with `ast`, using qualified names (`Class.method`,
  `outer.inner`). Changes outside any function (module or class body) add no
  function.
- A file the patch creates has no pre-patch lines, so it contributes only to
  `files`.
"""

from __future__ import annotations

import ast
import sys
import urllib.request
from pathlib import Path
from typing import Callable

from unidiff import PatchSet

from execloc.dataset import load_verified, read_jsonl, refuse_overwrite, write_jsonl

REPO_ROOT = Path(__file__).resolve().parents[2]
SOURCE_CACHE = REPO_ROOT / "cache" / "src"

SourceLookup = Callable[[str], "str | None"]


# --------------------------------------------------------------------------- #
# Test-file filter
# --------------------------------------------------------------------------- #

# Only `tests/`: `django/test/` and `sympy/testing/` are library code, not tests.
TEST_DIRS = {"tests"}


def is_test_file(path: str) -> bool:
    parts = path.split("/")
    name = parts[-1]
    if any(p in TEST_DIRS for p in parts[:-1]):
        return True
    return name.startswith("test_") or name.endswith("_test.py") or name == "conftest.py"


# --------------------------------------------------------------------------- #
# Function spans
# --------------------------------------------------------------------------- #


def function_spans(source: str) -> list[tuple[str, int, int]]:
    """(qualified_name, start, end) for every function; start includes decorators."""
    tree = ast.parse(source)
    spans: list[tuple[str, int, int]] = []

    def visit(node: ast.AST, prefix: str) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                qual = f"{prefix}{child.name}"
                if not isinstance(child, ast.ClassDef):
                    start = min([child.lineno] + [d.lineno for d in child.decorator_list])
                    spans.append((qual, start, child.end_lineno or child.lineno))
                visit(child, qual + ".")
            else:
                visit(child, prefix)

    visit(tree, "")
    return spans


def innermost(spans: list[tuple[str, int, int]], *lines: int) -> str | None:
    """Innermost function whose span contains every one of `lines`."""
    best: tuple[str, int, int] | None = None
    for name, start, end in spans:
        if all(start <= ln <= end for ln in lines):
            if best is None or (end - start) < (best[2] - best[1]):
                best = (name, start, end)
    return best[0] if best else None


# --------------------------------------------------------------------------- #
# Patch -> gold
# --------------------------------------------------------------------------- #


def _merge_ranges(lines: list[int]) -> list[tuple[int, int]]:
    ranges: list[tuple[int, int]] = []
    for ln in sorted(set(lines)):
        if ranges and ln <= ranges[-1][1] + 1:
            ranges[-1] = (ranges[-1][0], max(ranges[-1][1], ln))
        else:
            ranges.append((ln, ln))
    return ranges


def _change_blocks(hunk) -> list[tuple[list[int], int | None]]:
    """Split a hunk into contiguous change blocks.

    Returns (removed_source_lines, insertion_after) per block. For a block with
    removed lines, insertion_after is None. For an add-only block,
    insertion_after is the pre-patch line the additions follow (0 = file start).
    """
    blocks: list[tuple[list[int], int | None]] = []
    removed: list[int] = []
    added = False
    last_source = hunk.source_start - 1

    def flush() -> None:
        nonlocal removed, added
        if removed:
            blocks.append((removed, None))
        elif added:
            blocks.append(([], last_source))
        removed, added = [], False

    for line in hunk:
        if line.is_context:
            flush()
            last_source = line.source_line_no
        elif line.is_removed:
            removed.append(line.source_line_no)
            last_source = line.source_line_no
        elif line.is_added:
            added = True
    flush()
    return blocks


def gold_from_patch(instance_id: str, patch: str, get_source: SourceLookup) -> dict:
    """Build one gold.jsonl row. `get_source(path)` returns pre-patch file text."""
    files: list[str] = []
    functions: list[dict] = []
    lines: list[dict] = []

    for pf in PatchSet(patch):
        path = pf.path if not pf.is_rename else pf.source_file.removeprefix("a/")
        if is_test_file(path):
            continue
        files.append(path)
        if pf.is_added_file:
            continue

        source = get_source(path) if path.endswith(".py") else None
        spans = function_spans(source) if source is not None else []
        n_source_lines = source.count("\n") + 1 if source is not None else None

        file_lines: list[int] = []
        add_ranges: list[tuple[int, int]] = []
        funcs: list[str] = []
        for hunk in pf:
            for removed, after in _change_blocks(hunk):
                if removed:
                    file_lines.extend(removed)
                    funcs.extend(f for ln in removed if (f := innermost(spans, ln)))
                else:
                    start = max(after, 1)
                    end = after + 1
                    if n_source_lines is not None:
                        end = min(end, n_source_lines)
                    end = max(end, start)
                    add_ranges.append((start, end))
                    # Insertion lies between `after` and `after + 1`; the enclosing
                    # function must contain both sides of that gap.
                    gap = [ln for ln in (after, after + 1) if ln >= 1]
                    if (f := innermost(spans, *gap)) is not None:
                        funcs.append(f)

        ranges = _merge_ranges(file_lines)
        for r in add_ranges:
            if r not in ranges:
                ranges.append(r)
        lines.extend({"file": path, "start": s, "end": e} for s, e in sorted(ranges))
        seen = set()
        for f in funcs:
            if f not in seen:
                seen.add(f)
                functions.append({"file": path, "name": f})

    return {"instance_id": instance_id, "files": files, "functions": functions, "lines": lines}


# --------------------------------------------------------------------------- #
# Source fetching (cached; the gold build needs network once)
# --------------------------------------------------------------------------- #


def github_source(repo: str, commit: str) -> SourceLookup:
    def get(path: str) -> str | None:
        cached = SOURCE_CACHE / repo / commit / path
        if cached.exists():
            return cached.read_text(encoding="utf-8")
        url = f"https://raw.githubusercontent.com/{repo}/{commit}/{path}"
        # A failed fetch must abort the build: silently missing functions would
        # corrupt the frozen gold file.
        with urllib.request.urlopen(url, timeout=60) as resp:
            text = resp.read().decode("utf-8")
        cached.parent.mkdir(parents=True, exist_ok=True)
        cached.write_text(text, encoding="utf-8", newline="")
        return text

    return get


def main(instances: Path, out: Path, force: bool) -> int:
    if refuse_overwrite(out, force):
        return 1
    if not instances.exists():
        print(f"{instances} not found; run `uv run execloc sample` first.")
        return 1
    wanted = read_jsonl(instances)
    by_id = {r["instance_id"]: r for r in load_verified()}
    rows = []
    for inst in wanted:
        row = by_id[inst["instance_id"]]
        if row["base_commit"] != inst["base_commit"]:
            print(f"base_commit mismatch for {inst['instance_id']}", file=sys.stderr)
            return 1
        rows.append(gold_from_patch(row["instance_id"], row["patch"], github_source(row["repo"], row["base_commit"])))
    write_jsonl(out, rows)
    no_func = sum(1 for r in rows if not r["functions"])
    print(f"wrote {len(rows)} gold rows to {out} ({no_func} with no enclosing function)")
    return 0
