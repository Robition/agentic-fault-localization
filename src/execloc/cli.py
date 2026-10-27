"""Single entry point for every execloc workflow: `uv run execloc <command>`."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = REPO_ROOT / "data"
RESULTS_DIR = REPO_ROOT / "results"


def positive_int(value: str) -> int:
    try:
        number = int(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("must be a positive integer") from error
    if number < 1:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return number


def cmd_doctor(args: argparse.Namespace) -> int:
    from execloc.doctor import main as doctor_main

    return doctor_main()


def cmd_sample(args: argparse.Namespace) -> int:
    from execloc.sample import main as sample_main

    return sample_main(out=args.out, n=args.n, seed=args.seed, force=args.force)


def cmd_gold(args: argparse.Namespace) -> int:
    from execloc.gold import main as gold_main

    return gold_main(instances=args.instances, out=args.out, force=args.force)


def cmd_run(args: argparse.Namespace) -> int:
    from execloc.run import main as run_main

    return run_main(
        arm=args.arm, instance_ids=args.instances, run_id=args.run_id, instances_path=args.instances_file,
        all_instances=args.all, workers=args.workers, prune=args.prune, retry_failed=args.retry_failed,
    )


def cmd_build_static(args: argparse.Namespace) -> int:
    from execloc.run import RunError, build_static_image, load_arm_config

    try:
        build_static_image(load_arm_config("static")["environment"]["image"])
    except RunError as e:
        print(f"execloc build-static: {e}", file=sys.stderr)
        return 1
    return 0


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
    selection = p.add_mutually_exclusive_group(required=True)
    selection.add_argument("--instances", nargs="+", metavar="ID", help="frozen instance IDs to run")
    selection.add_argument("--all", action="store_true", help="run all frozen instances")
    p.add_argument("--run-id", default="dev", help="results go to results/<run-id>/ (default: dev)")
    p.add_argument("--instances-file", type=Path, default=DATA_DIR / "instances.jsonl")
    p.add_argument("--workers", type=positive_int, default=1, help="parallel instances (default: 1; recommended for one GPU)")
    p.add_argument("--prune", action="store_true", help="remove each completed execution image without forcing deletion")
    p.add_argument("--retry-failed", action="store_true", help="retry saved terminal failures; interrupted rows always resume")
    p.set_defaults(func=cmd_run)

    p = sub.add_parser("build-static", help="build the static arm's Docker image")
    p.set_defaults(func=cmd_build_static)

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
