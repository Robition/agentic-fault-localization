"""Pinned access to SWE-bench Verified and shared JSONL helpers."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable

DATASET_NAME = "princeton-nlp/SWE-bench_Verified"
DATASET_SPLIT = "test"
# Pin the dataset revision so `sample` and `gold` reproduce byte-for-byte.
DATASET_REVISION = "c104f840cc67f8b6eec6f759ebc8b2693d585d4a"


def load_verified() -> list[dict]:
    from datasets import load_dataset

    ds = load_dataset(DATASET_NAME, split=DATASET_SPLIT, revision=DATASET_REVISION)
    return [dict(row) for row in ds]


def read_jsonl(path: Path) -> list[dict]:
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def write_jsonl(path: Path, rows: Iterable[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def refuse_overwrite(path: Path, force: bool) -> bool:
    """Return True (and explain) if `path` exists and `force` is not set."""
    if path.exists() and not force:
        print(f"{path} already exists; pass --force to overwrite it.")
        return True
    return False
