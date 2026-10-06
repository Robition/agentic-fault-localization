"""Single entry point for every execloc workflow: `uv run execloc <command>`."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = REPO_ROOT / "data"
RESULTS_DIR = REPO_ROOT / "results"


def _not_implemented(name: str) -> int:
    print(f"execloc {name}: not implemented yet", file=sys.stderr)
    return 2


def cmd_doctor(args: argparse.Namespace) -> int:
    return _not_implemented("doctor")


def cmd_sample(args: argparse.Namespace) -> int:
    from execloc.sample import main as sample_main

    return sample_main(out=args.out, n=args.n, seed=args.seed, force=args.force)


def cmd_gold(args: argparse.Namespace) -> int:
    from execloc.gold import main as gold_main

    return gold_main(instances=args.instances, out=args.out, force=args.force)


def cmd_run(args: argparse.Namespace) -> int:
    return _not_implemented("run")


def cmd_score(args: argparse.Namespace) -> int:
    from execloc.score import main as score_main

    return score_main(run_id=args.run_id, gold=args.gold, results_dir=args.results_dir)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="execloc",
        description="Does code execution help an LLM agent localize bugs?",
    )
    sub = parser.add_subparsers(dest="command", required=True, metavar="COMMAND")

    p = sub.add_parser("doctor", help="check this machine's environment")
    p.set_defaults(func=cmd_doctor)

    p = sub.add_parser("sample", help="draw the frozen instance sample from SWE-bench Verified")
    p.add_argument("--out", type=Path, default=DATA_DIR / "instances.jsonl")
    p.add_argument("-n", type=int, default=100, help="sample size (default: 100)")
    p.add_argument("--seed", type=int, default=527, help="random seed (default: 527)")
    p.add_argument("--force", action="store_true", help="overwrite an existing output file")
    p.set_defaults(func=cmd_sample)

    p = sub.add_parser("gold", help="build gold locations from the sampled instances' patches")
    p.add_argument("--instances", type=Path, default=DATA_DIR / "instances.jsonl")
    p.add_argument("--out", type=Path, default=DATA_DIR / "gold.jsonl")
    p.add_argument("--force", action="store_true", help="overwrite an existing output file")
    p.set_defaults(func=cmd_gold)

    p = sub.add_parser("run", help="run one arm of the experiment")
    p.add_argument("--arm", choices=["static", "exec"], required=True)
    p.add_argument("--instances", nargs="+", metavar="ID")
    p.set_defaults(func=cmd_run)

    p = sub.add_parser("score", help="score a run's predictions against gold")
    p.add_argument("run_id")
    p.add_argument("--gold", type=Path, default=DATA_DIR / "gold.jsonl")
    p.add_argument("--results-dir", type=Path, default=RESULTS_DIR)
    p.set_defaults(func=cmd_score)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
