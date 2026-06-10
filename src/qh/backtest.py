from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

from .io import load_yaml, parse_date, parse_float, read_csv_rows, write_csv_rows, write_json


RETURN_FIELDS = [
    "rebalance_date",
    "entry_date",
    "exit_date",
    "factor_id",
    "long_return",
    "short_return",
    "long_short_return",
    "turnover",
    "cost",
    "long_short_after_cost",
    "n_long",
    "n_short",
]

POSITION_FIELDS = [
    "rebalance_date",
    "entry_date",
    "exit_date",
    "factor_id",
    "symbol",
    "side",
    "weight",
    "factor_value",
    "forward_return",
]

FORWARD_FIELDS = [
    "rebalance_date",
    "entry_date",
    "exit_date",
    "factor_id",
    "symbol",
    "factor_value",
    "forward_return",
    "bucket",
]


def run_backtest(
    factor_file: Path,
    market_file: Path,
    config_file: Path,
    risk_config_file: Path | None,
    output_root: Path,
    sample: str = "all",
    run_name: str | None = None,
) -> dict[str, Any]:
    config = load_yaml(config_file)
    risk_config = load_yaml(risk_config_file) if risk_config_file else {}
    factor_rows = read_csv_rows(factor_file)
    market_rows = read_csv_rows(market_file)
    if not factor_rows:
        raise ValueError(f"No factor rows found in {factor_file}")
    if not market_rows:
        raise ValueError(f"No market rows found in {market_file}")

    factor_id = factor_rows[0].get("factor_id") or factor_file.stem
    factor_by_date = _factor_by_date(factor_rows, sample, config)
    close_by_date_symbol, trading_dates = _close_maps(market_rows, sample, config)
    rebalance_dates = _select_rebalance_dates(sorted(factor_by_date), config)
    if len(rebalance_dates) < 2:
        raise ValueError("Need at least two rebalance dates after filtering")

    top_quantile = float(config.get("selection", {}).get("top_quantile", 0.2))
    bottom_quantile = float(config.get("selection", {}).get("bottom_quantile", 0.2))
    min_names = int(config.get("selection", {}).get("min_names", 5))
    lag = int(config.get("calendar", {}).get("execution_lag_days", 1))
    cost_bps = _cost_bps(config)

    return_rows: list[dict[str, Any]] = []
    position_rows: list[dict[str, Any]] = []
    forward_rows: list[dict[str, Any]] = []
    previous_weights: dict[str, float] = {}
    warnings: list[str] = []

    for idx, rebalance_date in enumerate(rebalance_dates[:-1]):
        next_rebalance = rebalance_dates[idx + 1]
        entry_date = _nth_trading_date_after(trading_dates, rebalance_date, lag)
        exit_date = _nth_trading_date_after(trading_dates, next_rebalance, lag)
        if entry_date is None or exit_date is None or exit_date <= entry_date:
            warnings.append(f"Skipped {rebalance_date}: cannot resolve entry/exit dates.")
            continue

        eligible = _eligible_forward_rows(
            factor_by_date[rebalance_date],
            close_by_date_symbol,
            rebalance_date,
            entry_date,
            exit_date,
            factor_id,
        )
        if len(eligible) < min_names:
            warnings.append(f"Skipped {rebalance_date}: only {len(eligible)} eligible names.")
            continue

        eligible.sort(key=lambda item: item["factor_value"], reverse=True)
        n_long = max(1, int(len(eligible) * top_quantile))
        n_short = max(1, int(len(eligible) * bottom_quantile))
        longs = eligible[:n_long]
        shorts = eligible[-n_short:]
        long_return = sum(item["forward_return"] for item in longs) / len(longs)
        short_return = sum(item["forward_return"] for item in shorts) / len(shorts)
        long_short_return = long_return - short_return

        weights = {item["symbol"]: 1.0 / n_long for item in longs}
        for item in shorts:
            weights[item["symbol"]] = weights.get(item["symbol"], 0.0) - 1.0 / n_short
        turnover = _turnover(previous_weights, weights)
        previous_weights = weights
        cost = turnover * cost_bps / 10000.0
        after_cost = long_short_return - cost

        return_rows.append(
            {
                "rebalance_date": rebalance_date,
                "entry_date": entry_date,
                "exit_date": exit_date,
                "factor_id": factor_id,
                "long_return": f"{long_return:.12g}",
                "short_return": f"{short_return:.12g}",
                "long_short_return": f"{long_short_return:.12g}",
                "turnover": f"{turnover:.12g}",
                "cost": f"{cost:.12g}",
                "long_short_after_cost": f"{after_cost:.12g}",
                "n_long": n_long,
                "n_short": n_short,
            }
        )

        long_symbols = {item["symbol"] for item in longs}
        short_symbols = {item["symbol"] for item in shorts}
        for item in eligible:
            bucket = "middle"
            if item["symbol"] in long_symbols:
                bucket = "long"
            elif item["symbol"] in short_symbols:
                bucket = "short"
            forward_rows.append({**item, "bucket": bucket})
        for item in longs:
            position_rows.append(_position_row(item, "long", 1.0 / n_long))
        for item in shorts:
            position_rows.append(_position_row(item, "short", -1.0 / n_short))

    if not return_rows:
        raise ValueError("Backtest produced no periods; check sample dates and data coverage.")

    run_id = _run_id(factor_id, run_name)
    run_dir = output_root / run_id
    write_csv_rows(run_dir / "portfolio_returns.csv", return_rows, RETURN_FIELDS)
    write_csv_rows(run_dir / "positions.csv", position_rows, POSITION_FIELDS)
    write_csv_rows(run_dir / "factor_forward_returns.csv", forward_rows, FORWARD_FIELDS)

    manifest = {
        "run_id": run_id,
        "run_dir": str(run_dir),
        "factor_id": factor_id,
        "factor_file": str(factor_file),
        "market_file": str(market_file),
        "config_file": str(config_file),
        "risk_config_file": str(risk_config_file) if risk_config_file else None,
        "sample": sample,
        "periods": len(return_rows),
        "rebalance_frequency": config.get("calendar", {}).get("rebalance_frequency", "weekly"),
        "cost_bps": cost_bps,
        "risk_gate_loaded": bool(risk_config),
        "warnings": warnings,
        "created_at": datetime.now().isoformat(timespec="seconds"),
    }
    write_json(run_dir / "backtest.json", manifest)
    return manifest


def _factor_by_date(rows: list[dict[str, str]], sample: str, config: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    out: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        row_date = row["date"][:10]
        if not _in_sample(row_date, sample, config):
            continue
        value = parse_float(row.get("value"), "factor value")
        if value is None:
            continue
        out[row_date].append({"symbol": row["symbol"], "factor_value": value})
    return out


def _close_maps(
    rows: list[dict[str, str]],
    sample: str,
    config: dict[str, Any],
) -> tuple[dict[str, dict[str, float]], list[str]]:
    close_by_date_symbol: dict[str, dict[str, float]] = defaultdict(dict)
    for row in rows:
        row_date = row["date"][:10]
        if not _in_sample(row_date, sample, config):
            continue
        close = parse_float(row.get("close"), "close")
        if close is not None:
            close_by_date_symbol[row_date][row["symbol"]] = close
    return close_by_date_symbol, sorted(close_by_date_symbol)


def _select_rebalance_dates(dates: list[str], config: dict[str, Any]) -> list[str]:
    frequency = str(config.get("calendar", {}).get("rebalance_frequency", "weekly")).lower()
    if frequency == "daily":
        return dates
    if frequency != "weekly":
        raise ValueError(f"Unsupported rebalance_frequency: {frequency}")
    by_week: dict[tuple[int, int], str] = {}
    for date_value in dates:
        parsed = parse_date(date_value)
        iso = parsed.isocalendar()
        key = (iso.year, iso.week)
        by_week[key] = max(by_week.get(key, date_value), date_value)
    return sorted(by_week.values())


def _nth_trading_date_after(trading_dates: list[str], anchor: str, lag: int) -> str | None:
    for index, trading_date in enumerate(trading_dates):
        if trading_date > anchor:
            target = index + max(0, lag - 1)
            if target < len(trading_dates):
                return trading_dates[target]
            return None
    return None


def _eligible_forward_rows(
    factor_rows: list[dict[str, Any]],
    close_by_date_symbol: dict[str, dict[str, float]],
    rebalance_date: str,
    entry_date: str,
    exit_date: str,
    factor_id: str,
) -> list[dict[str, Any]]:
    entry_prices = close_by_date_symbol.get(entry_date, {})
    exit_prices = close_by_date_symbol.get(exit_date, {})
    out: list[dict[str, Any]] = []
    for row in factor_rows:
        symbol = row["symbol"]
        entry = entry_prices.get(symbol)
        exit_ = exit_prices.get(symbol)
        if entry is None or exit_ is None or entry <= 0:
            continue
        out.append(
            {
                "rebalance_date": rebalance_date,
                "entry_date": entry_date,
                "exit_date": exit_date,
                "factor_id": factor_id,
                "symbol": symbol,
                "factor_value": row["factor_value"],
                "forward_return": exit_ / entry - 1.0,
            }
        )
    return out


def _position_row(item: dict[str, Any], side: str, weight: float) -> dict[str, Any]:
    return {
        "rebalance_date": item["rebalance_date"],
        "entry_date": item["entry_date"],
        "exit_date": item["exit_date"],
        "factor_id": item["factor_id"],
        "symbol": item["symbol"],
        "side": side,
        "weight": f"{weight:.12g}",
        "factor_value": f"{item['factor_value']:.12g}",
        "forward_return": f"{item['forward_return']:.12g}",
    }


def _turnover(previous: dict[str, float], current: dict[str, float]) -> float:
    symbols = set(previous) | set(current)
    return sum(abs(current.get(symbol, 0.0) - previous.get(symbol, 0.0)) for symbol in symbols)


def _cost_bps(config: dict[str, Any]) -> float:
    costs = config.get("costs", {})
    return float(costs.get("commission_bps", 0)) + float(costs.get("slippage_bps", 0)) + float(
        costs.get("stamp_duty_sell_bps", 0)
    )


def _run_id(factor_id: str, run_name: str | None) -> str:
    prefix = run_name or factor_id
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return f"{prefix}_{stamp}"


def _in_sample(row_date: str, sample: str, config: dict[str, Any]) -> bool:
    if sample == "all":
        return True
    samples = config.get("samples", {})
    if sample not in samples:
        known = ", ".join(["all", *sorted(samples)])
        raise ValueError(f"Unknown sample {sample!r}. Expected one of: {known}")
    start = samples[sample].get("start")
    end = samples[sample].get("end")
    if start and row_date < start:
        return False
    if end and row_date > end:
        return False
    return True
