from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from statistics import fmean, stdev
from typing import Any

from .io import load_yaml, parse_float, read_csv_rows, write_json


def evaluate_factor(run_dir: Path, risk_config_file: Path, config_file: Path) -> dict[str, Any]:
    returns_path = run_dir / "portfolio_returns.csv"
    forward_path = run_dir / "factor_forward_returns.csv"
    if not returns_path.exists():
        raise FileNotFoundError(f"Missing backtest returns: {returns_path}")
    if not forward_path.exists():
        raise FileNotFoundError(f"Missing factor forward returns: {forward_path}")

    risk_config = load_yaml(risk_config_file)
    config = load_yaml(config_file)
    return_rows = read_csv_rows(returns_path)
    forward_rows = read_csv_rows(forward_path)
    if not return_rows:
        raise ValueError("No portfolio return rows to evaluate")
    if not forward_rows:
        raise ValueError("No factor forward return rows to evaluate")

    factor_id = return_rows[0].get("factor_id") or "unknown"
    period_returns = [parse_float(row.get("long_short_after_cost"), "long_short_after_cost") or 0.0 for row in return_rows]
    turnover = [parse_float(row.get("turnover"), "turnover") or 0.0 for row in return_rows]
    rank_ics = _rank_ics(forward_rows)
    rank_ic_values = [value for value in rank_ics.values() if value is not None]

    frequency = str(config.get("calendar", {}).get("rebalance_frequency", "weekly")).lower()
    annual_periods = _annual_periods(config, frequency)
    rank_ic_mean = fmean(rank_ic_values) if rank_ic_values else None
    rank_ic_std = stdev(rank_ic_values) if len(rank_ic_values) > 1 else None
    icir = (rank_ic_mean / rank_ic_std) if rank_ic_mean is not None and rank_ic_std not in (None, 0) else None
    annual_return = _annualized_return(period_returns, annual_periods)
    max_drawdown = _max_drawdown(period_returns)
    turnover_annual = fmean(turnover) * annual_periods if turnover else None

    gates = risk_config.get("factor_gate", {})
    warnings: list[str] = []
    passed = True
    passed &= _check_min("rank_ic_mean", rank_ic_mean, gates.get("min_rank_ic_mean"), warnings)
    passed &= _check_min("icir", icir, gates.get("min_icir"), warnings)
    passed &= _check_min(
        "long_short_return_after_cost",
        annual_return,
        gates.get("min_long_short_return_after_cost"),
        warnings,
    )
    passed &= _check_max("turnover_annual", turnover_annual, gates.get("max_turnover_annual"), warnings)
    passed &= _check_drawdown(max_drawdown, gates.get("max_drawdown"), warnings)

    result = {
        "factor_id": factor_id,
        "sample": "backtest_run",
        "periods": len(period_returns),
        "rank_ic_mean": rank_ic_mean,
        "rank_ic_observations": len(rank_ic_values),
        "icir": icir,
        "long_short_return_after_cost": annual_return,
        "turnover_annual": turnover_annual,
        "max_drawdown": max_drawdown,
        "hit_rate": sum(1 for value in period_returns if value > 0) / len(period_returns),
        "corr_with_existing_max": None,
        "pass": bool(passed),
        "warnings": warnings,
        "artifacts": {
            "portfolio_returns": str(returns_path),
            "factor_forward_returns": str(forward_path),
        },
    }
    write_json(run_dir / "eval_factor.json", result)
    return result


def _rank_ics(rows: list[dict[str, str]]) -> dict[str, float | None]:
    grouped: dict[str, list[tuple[float, float]]] = defaultdict(list)
    for row in rows:
        factor_value = parse_float(row.get("factor_value"), "factor_value")
        forward_return = parse_float(row.get("forward_return"), "forward_return")
        if factor_value is None or forward_return is None:
            continue
        grouped[row["rebalance_date"]].append((factor_value, forward_return))

    out: dict[str, float | None] = {}
    for rebalance_date, values in grouped.items():
        if len(values) < 3:
            out[rebalance_date] = None
            continue
        factor_ranks = _ranks([item[0] for item in values])
        return_ranks = _ranks([item[1] for item in values])
        out[rebalance_date] = _pearson(factor_ranks, return_ranks)
    return out


def _ranks(values: list[float]) -> list[float]:
    sorted_pairs = sorted((value, index) for index, value in enumerate(values))
    ranks = [0.0] * len(values)
    cursor = 0
    while cursor < len(sorted_pairs):
        end = cursor + 1
        while end < len(sorted_pairs) and sorted_pairs[end][0] == sorted_pairs[cursor][0]:
            end += 1
        average_rank = (cursor + 1 + end) / 2.0
        for _, original_index in sorted_pairs[cursor:end]:
            ranks[original_index] = average_rank
        cursor = end
    return ranks


def _pearson(left: list[float], right: list[float]) -> float | None:
    if len(left) != len(right) or len(left) < 2:
        return None
    left_mean = fmean(left)
    right_mean = fmean(right)
    numerator = sum((x - left_mean) * (y - right_mean) for x, y in zip(left, right))
    left_var = sum((x - left_mean) ** 2 for x in left)
    right_var = sum((y - right_mean) ** 2 for y in right)
    denominator = (left_var * right_var) ** 0.5
    if denominator == 0:
        return None
    return numerator / denominator


def _annual_periods(config: dict[str, Any], frequency: str) -> int:
    annualization = config.get("annualization", {})
    if frequency == "daily":
        return int(annualization.get("daily_periods", 252))
    return int(annualization.get("weekly_periods", 52))


def _annualized_return(returns: list[float], annual_periods: int) -> float | None:
    if not returns:
        return None
    cumulative = 1.0
    for value in returns:
        cumulative *= 1.0 + value
    if cumulative <= 0:
        return -1.0
    return cumulative ** (annual_periods / len(returns)) - 1.0


def _max_drawdown(returns: list[float]) -> float | None:
    if not returns:
        return None
    equity = 1.0
    peak = 1.0
    max_dd = 0.0
    for value in returns:
        equity *= 1.0 + value
        peak = max(peak, equity)
        drawdown = equity / peak - 1.0
        max_dd = min(max_dd, drawdown)
    return max_dd


def _check_min(name: str, value: float | None, threshold: Any, warnings: list[str]) -> bool:
    if threshold is None:
        return True
    if value is None:
        warnings.append(f"{name} is unavailable.")
        return False
    threshold_value = float(threshold)
    if value < threshold_value:
        warnings.append(f"{name} {value:.6g} is below threshold {threshold_value:.6g}.")
        return False
    return True


def _check_max(name: str, value: float | None, threshold: Any, warnings: list[str]) -> bool:
    if threshold is None:
        return True
    if value is None:
        warnings.append(f"{name} is unavailable.")
        return False
    threshold_value = float(threshold)
    if value > threshold_value:
        warnings.append(f"{name} {value:.6g} is above threshold {threshold_value:.6g}.")
        return False
    return True


def _check_drawdown(value: float | None, threshold: Any, warnings: list[str]) -> bool:
    if threshold is None:
        return True
    if value is None:
        warnings.append("max_drawdown is unavailable.")
        return False
    threshold_value = float(threshold)
    if value < threshold_value:
        warnings.append(f"max_drawdown {value:.6g} is worse than threshold {threshold_value:.6g}.")
        return False
    return True
