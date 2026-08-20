"""Command-line entry point.

    python -m bench dataset            # download + canonicalise the dataset
    python -m bench run                # full benchmark against every platform
    python -m bench run --quick        # short smoke run
    python -m bench report             # regenerate tables/charts from results
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from . import config as config_mod
from . import dataset as dataset_mod
from .runner import capture_environment, check_parity, run_platform
from .workloads import build_plan

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG = REPO_ROOT / "config" / "platforms.yaml"
DEFAULT_DATA = REPO_ROOT / "data"
DEFAULT_RESULTS = REPO_ROOT / "results"
DEFAULT_ENV = REPO_ROOT / ".env"


def cmd_dataset(args) -> int:
    manifest = dataset_mod.build(
        Path(args.data_dir), sample_edges=args.sample_edges, seed=args.seed
    )
    print(json.dumps(manifest.to_dict(), indent=2))
    return 0


def cmd_run(args) -> int:
    cfg = config_mod.load(Path(args.config), env_file=Path(args.env_file))
    data_dir = Path(args.data_dir)

    if not (data_dir / "manifest.json").exists():
        print("Dataset not built. Run: python -m bench dataset", file=sys.stderr)
        return 2

    manifest = json.loads((data_dir / "manifest.json").read_text())
    nodes, edges = dataset_mod.load_csvs(data_dir)
    print(f"Dataset: {manifest['node_count']:,} nodes / {manifest['relationship_count']:,} rels")

    defaults = cfg.defaults
    settings = {
        "batch_size": args.batch_size or defaults.get("batch_size", 10000),
        "iterations": args.iterations or defaults.get("iterations", 100),
        "warmup_iterations": args.warmup or defaults.get("warmup_iterations", 20),
        "seed": defaults.get("seed", 1337),
        "repeats": args.repeats,
        "concurrency_levels": [] if args.no_concurrency else args.concurrency,
        "concurrency_seconds": args.concurrency_seconds,
        "read_ratio": args.read_ratio,
    }

    plan = build_plan(
        nodes,
        edges,
        start_node_count=defaults.get("start_node_count", 100),
        seed=settings["seed"],
    )
    print(f"Workload plan: {json.dumps(plan.to_dict())}")

    selected = cfg.platforms
    if args.platforms:
        wanted = {p.strip() for p in args.platforms.split(",")}
        selected = [p for p in cfg.platforms if p.id in wanted]
        if not selected:
            print(f"No platforms matched {sorted(wanted)}", file=sys.stderr)
            return 2

    runs = []
    for platform_cfg in selected:
        print(f"\n=== {platform_cfg.name} ===")
        runs.append(
            run_platform(
                platform_cfg,
                lambda pc=platform_cfg: config_mod.build_adapter(pc),
                nodes,
                edges,
                plan,
                settings,
            )
        )

    parity = check_parity(runs)
    print(f"\nResult parity across platforms: {parity['status']}")
    if parity["status"] == "MISMATCH":
        for name, detail in parity["workloads"].items():
            if not detail["agree"]:
                print(f"  {name}: {detail['values']}")

    results = {
        "run_id": datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"),
        "environment": capture_environment(),
        "parity_tier": cfg.parity_tier,
        "settings": settings,
        "dataset": manifest,
        "plan": plan.to_dict(),
        "parity_check": parity,
        "platforms": [r.to_dict() for r in runs],
    }

    results_dir = Path(args.results_dir)
    results_dir.mkdir(parents=True, exist_ok=True)
    out = results_dir / "results.json"
    out.write_text(json.dumps(results, indent=2, default=str))
    print(f"\nResults written to {out}")

    from .report import write_report

    report_path = write_report(results, results_dir)
    print(f"Report written to {report_path}")
    return 0


def cmd_report(args) -> int:
    results_dir = Path(args.results_dir)
    results = json.loads((results_dir / "results.json").read_text())
    from .report import write_report

    print(f"Report written to {write_report(results, results_dir)}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="bench", description=__doc__)
    parser.add_argument("--data-dir", default=str(DEFAULT_DATA))
    parser.add_argument("--results-dir", default=str(DEFAULT_RESULTS))
    sub = parser.add_subparsers(dest="command", required=True)

    p_data = sub.add_parser("dataset", help="download and canonicalise the dataset")
    p_data.add_argument("--sample-edges", type=int, default=None)
    p_data.add_argument("--seed", type=int, default=42)
    p_data.set_defaults(func=cmd_dataset)

    p_run = sub.add_parser("run", help="run the benchmark")
    p_run.add_argument("--config", default=str(DEFAULT_CONFIG))
    p_run.add_argument("--env-file", default=str(DEFAULT_ENV))
    p_run.add_argument("--platforms", default=None, help="comma-separated platform ids")
    p_run.add_argument("--iterations", type=int, default=None)
    p_run.add_argument("--warmup", type=int, default=None)
    p_run.add_argument("--batch-size", type=int, default=None)
    p_run.add_argument("--repeats", type=int, default=3, help="repeat the read suite N times")
    p_run.add_argument(
        "--concurrency",
        type=lambda s: [int(x) for x in s.split(",")],
        default=[1, 10, 40],
    )
    p_run.add_argument("--concurrency-seconds", type=float, default=20.0)
    p_run.add_argument("--read-ratio", type=float, default=0.8)
    p_run.add_argument("--no-concurrency", action="store_true")
    p_run.add_argument(
        "--quick",
        action="store_true",
        help="short run for smoke-testing the harness (not for reporting)",
    )
    p_run.set_defaults(func=cmd_run)

    p_report = sub.add_parser("report", help="regenerate the report from results.json")
    p_report.set_defaults(func=cmd_report)
    return parser


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if getattr(args, "quick", False):
        args.iterations = args.iterations or 10
        args.warmup = args.warmup or 3
        args.repeats = 1
        args.concurrency = [1, 4]
        args.concurrency_seconds = 3.0
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
