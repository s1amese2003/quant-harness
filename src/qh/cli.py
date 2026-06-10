from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .backtest import run_backtest
from .evaluation import evaluate_factor
from .factors import FACTOR_ALIASES, FACTOR_SPECS, compute_factor
from .io import print_json


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="qh", description="Quant Harness phase-one CLI")
    parser.add_argument("--version", action="version", version="qh 0.1.0")
    subparsers = parser.add_subparsers(dest="domain", required=True)

    factor_parser = subparsers.add_parser("factor", help="Factor utilities")
    factor_subparsers = factor_parser.add_subparsers(dest="command", required=True)
    compute_parser = factor_subparsers.add_parser("compute", help="Compute a supported factor")
    compute_parser.add_argument(
        "--factor",
        required=True,
        choices=sorted(set(FACTOR_SPECS) | set(FACTOR_ALIASES)),
        help="Factor id or alias",
    )
    compute_parser.add_argument("--input", type=Path, default=Path("data/curated/daily_prices.csv"))
    compute_parser.add_argument("--output", type=Path, default=Path("data/features"))
    compute_parser.add_argument("--lookback", type=int, default=None, help="Override factor lookback")
    compute_parser.add_argument("--start-date", default=None)
    compute_parser.add_argument("--end-date", default=None)
    compute_parser.set_defaults(func=_cmd_factor_compute)

    backtest_parser = subparsers.add_parser("backtest", help="Backtest utilities")
    backtest_subparsers = backtest_parser.add_subparsers(dest="command", required=True)
    run_parser = backtest_subparsers.add_parser("run", help="Run a standard factor backtest")
    run_parser.add_argument("--factor-file", type=Path, required=True)
    run_parser.add_argument("--market", type=Path, default=Path("data/curated/daily_prices.csv"))
    run_parser.add_argument("--config", type=Path, default=Path("configs/backtest.yaml"))
    run_parser.add_argument("--risk-config", type=Path, default=Path("configs/risk_limits.yaml"))
    run_parser.add_argument("--output-root", type=Path, default=Path("runs"))
    run_parser.add_argument("--sample", default="all", help="all, train, validation, or test")
    run_parser.add_argument("--run-name", default=None)
    run_parser.set_defaults(func=_cmd_backtest_run)

    eval_parser = subparsers.add_parser("eval", help="Evaluation utilities")
    eval_subparsers = eval_parser.add_subparsers(dest="command", required=True)
    eval_factor_parser = eval_subparsers.add_parser("factor", help="Evaluate a factor backtest run")
    eval_factor_parser.add_argument("--run", type=Path, required=True)
    eval_factor_parser.add_argument("--risk-config", type=Path, default=Path("configs/risk_limits.yaml"))
    eval_factor_parser.add_argument("--config", type=Path, default=Path("configs/backtest.yaml"))
    eval_factor_parser.set_defaults(func=_cmd_eval_factor)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        result = args.func(args)
    except Exception as exc:  # noqa: BLE001 - CLI should return a clean error.
        print(f"qh: error: {exc}", file=sys.stderr)
        return 1
    print_json(result)
    return 0


def _cmd_factor_compute(args: argparse.Namespace) -> dict[str, object]:
    return compute_factor(
        input_path=args.input,
        output_dir=args.output,
        factor=args.factor,
        lookback_override=args.lookback,
        start_date=args.start_date,
        end_date=args.end_date,
    )


def _cmd_backtest_run(args: argparse.Namespace) -> dict[str, object]:
    return run_backtest(
        factor_file=args.factor_file,
        market_file=args.market,
        config_file=args.config,
        risk_config_file=args.risk_config,
        output_root=args.output_root,
        sample=args.sample,
        run_name=args.run_name,
    )


def _cmd_eval_factor(args: argparse.Namespace) -> dict[str, object]:
    return evaluate_factor(run_dir=args.run, risk_config_file=args.risk_config, config_file=args.config)
