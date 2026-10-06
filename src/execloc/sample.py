"""Draw a seeded sample of SWE-bench Verified, stratified by repository.

Each repository gets min(MIN_PER_REPO, its size) instances. The remaining slots
are split in proportion to repository size (largest-remainder method), capped
by how many instances each repository has left.
"""

from __future__ import annotations

import random
from collections import defaultdict
from pathlib import Path

from execloc.dataset import load_verified, refuse_overwrite, write_jsonl

MIN_PER_REPO = 3


def allocate(sizes: dict[str, int], n: int, min_per_repo: int = MIN_PER_REPO) -> dict[str, int]:
    """How many instances to draw from each repository."""
    total = sum(sizes.values())
    if n > total:
        raise ValueError(f"cannot sample {n} from {total} instances")
    alloc = {repo: min(min_per_repo, size) for repo, size in sizes.items()}
    if sum(alloc.values()) > n:
        raise ValueError(f"n={n} is too small to give every repository {min_per_repo}")

    while (remaining := n - sum(alloc.values())) > 0:
        open_repos = {r: sizes[r] for r in sizes if alloc[r] < sizes[r]}
        weight = sum(open_repos.values())
        quotas = {r: remaining * s / weight for r, s in open_repos.items()}
        extra = {r: min(int(q), sizes[r] - alloc[r]) for r, q in quotas.items()}
        leftover = remaining - sum(extra.values())
        # Hand out leftover slots by largest fractional part; ties by name.
        for r in sorted(open_repos, key=lambda r: (-(quotas[r] - int(quotas[r])), r)):
            if leftover == 0:
                break
            if alloc[r] + extra[r] < sizes[r]:
                extra[r] += 1
                leftover -= 1
        for r, e in extra.items():
            alloc[r] += e
    return alloc


def stratified_sample(rows: list[dict], n: int, seed: int) -> list[dict]:
    by_repo: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        by_repo[row["repo"]].append(row)
    alloc = allocate({r: len(v) for r, v in by_repo.items()}, n)
    rng = random.Random(seed)
    picked: list[dict] = []
    for repo in sorted(by_repo):
        pool = sorted(by_repo[repo], key=lambda r: r["instance_id"])
        picked.extend(rng.sample(pool, alloc[repo]))
    return sorted(picked, key=lambda r: r["instance_id"])


def main(out: Path, n: int, seed: int, force: bool) -> int:
    if refuse_overwrite(out, force):
        return 1
    rows = stratified_sample(load_verified(), n, seed)
    write_jsonl(
        out,
        ({"instance_id": r["instance_id"], "repo": r["repo"], "base_commit": r["base_commit"]} for r in rows),
    )
    counts: dict[str, int] = defaultdict(int)
    for r in rows:
        counts[r["repo"]] += 1
    print(f"wrote {len(rows)} instances to {out} (seed={seed})")
    for repo, c in sorted(counts.items(), key=lambda kv: -kv[1]):
        print(f"  {c:4d}  {repo}")
    return 0
