from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from statistics import fmean, stdev
from typing import Any

from .io import parse_date, parse_float, read_csv_rows, write_csv_rows


@dataclass(frozen=True)
class FactorSpec:
    factor_id: str
    family: str
    default_lookback: int | None
    required_any: tuple[str, ...]
    description: str


FACTOR_SPECS: dict[str, FactorSpec] = {
    "short_reversal_5d": FactorSpec(
        factor_id="short_reversal_5d",
        family="short_reversal",
        default_lookback=5,
        required_any=("close",),
        description="Negative trailing return; higher score means stronger reversal candidate.",
    ),
    "momentum_20d": FactorSpec(
        factor_id="momentum_20d",
        family="momentum",
        default_lookback=20,
        required_any=("close",),
        description="Positive trailing return; higher score means stronger momentum.",
    ),
    "low_volatility_20d": FactorSpec(
        factor_id="low_volatility_20d",
        family="low_volatility",
        default_lookback=20,
        required_any=("close",),
        description="Negative realized volatility; higher score means lower volatility.",
    ),
    "turnover_20d": FactorSpec(
        factor_id="turnover_20d",
        family="turnover",
        default_lookback=20,
        required_any=("turnover",),
        description="Average trailing turnover.",
    ),
    "quality_roe": FactorSpec(
        factor_id="quality_roe",
        family="quality",
        default_lookback=None,
        required_any=("roe", "roa", "gross_margin", "net_profit_growth"),
        description="Average of available quality fields.",
    ),
}

FACTOR_ALIASES = {
    "short_reversal": "short_reversal_5d",
    "reversal": "short_reversal_5d",
    "momentum": "momentum_20d",
    "low_volatility": "low_volatility_20d",
    "volatility": "low_volatility_20d",
    "turnover": "turnover_20d",
    "quality": "quality_roe",
}

FEATURE_FIELDS = ["date", "symbol", "factor_id", "value", "asof_date", "effective_date", "source"]


def resolve_factor_id(factor: str) -> str:
    factor_id = FACTOR_ALIASES.get(factor, factor)
    if factor_id not in FACTOR_SPECS:
        supported = ", ".join(sorted(FACTOR_SPECS))
        raise ValueError(f"Unsupported factor {factor!r}. Supported factors: {supported}")
    return factor_id


def compute_factor(
    input_path: Path,
    output_dir: Path,
    factor: str,
    lookback_override: int | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
) -> dict[str, Any]:
    factor_id = resolve_factor_id(factor)
    spec = FACTOR_SPECS[factor_id]
    lookback = lookback_override if lookback_override is not None else spec.default_lookback
    if lookback is not None and lookback < 1:
        raise ValueError("--lookback must be positive")

    raw_rows = read_csv_rows(input_path)
    if not raw_rows:
        raise ValueError(f"No rows found in {input_path}")

    header = set(raw_rows[0])
    if not {"date", "symbol"}.issubset(header):
        raise ValueError("Input CSV must include date and symbol columns")
    if not any(column in header for column in spec.required_any):
        required = " or ".join(spec.required_any)
        raise ValueError(f"{factor_id} requires input column: {required}")

    start = parse_date(start_date, "start_date") if start_date else None
    end = parse_date(end_date, "end_date") if end_date else None
    warnings: list[str] = []
    grouped: dict[str, list[dict[str, str]]] = defaultdict(list)

    for row in raw_rows:
        row_date = parse_date(row.get("date", ""), "date")
        if start and row_date < start:
            continue
        if end and row_date > end:
            continue
        if "asof_date" in row and row.get("asof_date"):
            asof = parse_date(row["asof_date"], "asof_date")
            if asof > row_date:
                raise ValueError(
                    f"Future data risk: asof_date {asof.isoformat()} is after date {row_date.isoformat()} "
                    f"for {row.get('symbol')}"
                )
        grouped[row["symbol"]].append(row)

    if "asof_date" not in header:
        warnings.append("Input has no asof_date column; output uses date as asof_date.")
    if "effective_date" not in header:
        warnings.append("Input has no effective_date column; output uses date as effective_date.")
    if "source" not in header:
        warnings.append("Input has no source column; output uses the input filename.")

    feature_rows: list[dict[str, Any]] = []
    for symbol, symbol_rows in grouped.items():
        symbol_rows.sort(key=lambda item: parse_date(item["date"]))
        feature_rows.extend(_compute_symbol_features(symbol, symbol_rows, spec, lookback, input_path.name))

    feature_rows.sort(key=lambda item: (item["date"], item["symbol"]))
    output_path = output_dir / f"{factor_id}.csv"
    write_csv_rows(output_path, feature_rows, FEATURE_FIELDS)

    dates = [row["date"] for row in feature_rows]
    return {
        "factor_id": factor_id,
        "description": spec.description,
        "input": str(input_path),
        "output": str(output_path),
        "rows_written": len(feature_rows),
        "start_date": min(dates) if dates else None,
        "end_date": max(dates) if dates else None,
        "lookback": lookback,
        "warnings": warnings,
    }


def _compute_symbol_features(
    symbol: str,
    rows: list[dict[str, str]],
    spec: FactorSpec,
    lookback: int | None,
    source_fallback: str,
) -> list[dict[str, Any]]:
    if spec.family == "quality":
        return [_quality_row(row, spec.factor_id, source_fallback) for row in rows if _quality_value(row) is not None]

    if lookback is None:
        raise ValueError(f"{spec.factor_id} requires a lookback")

    closes = [parse_float(row.get("close"), "close") for row in rows]
    if any(value is None for value in closes):
        raise ValueError(f"{spec.factor_id} requires non-empty close values for {symbol}")

    out: list[dict[str, Any]] = []
    for index, row in enumerate(rows):
        if index < lookback:
            continue
        value = _factor_value(spec.family, rows, closes, index, lookback)
        if value is None:
            continue
        out.append(_feature_row(row, spec.factor_id, value, source_fallback))
    return out


def _factor_value(
    family: str,
    rows: list[dict[str, str]],
    closes: list[float | None],
    index: int,
    lookback: int,
) -> float | None:
    current = closes[index]
    previous = closes[index - lookback]
    if current is None or previous is None or previous <= 0:
        return None

    trailing_return = current / previous - 1.0
    if family == "short_reversal":
        return -trailing_return
    if family == "momentum":
        return trailing_return
    if family == "low_volatility":
        returns: list[float] = []
        for cursor in range(index - lookback + 1, index + 1):
            prev_close = closes[cursor - 1]
            close = closes[cursor]
            if prev_close is None or close is None or prev_close <= 0:
                return None
            returns.append(close / prev_close - 1.0)
        if len(returns) < 2:
            return None
        return -stdev(returns)
    if family == "turnover":
        values = [
            parse_float(row.get("turnover"), "turnover")
            for row in rows[index - lookback + 1 : index + 1]
        ]
        clean_values = [value for value in values if value is not None]
        if len(clean_values) != lookback:
            return None
        return fmean(clean_values)
    raise ValueError(f"Unknown factor family: {family}")


def _quality_value(row: dict[str, str]) -> float | None:
    values: list[float] = []
    for field in ("roe", "roa", "gross_margin", "net_profit_growth"):
        value = parse_float(row.get(field), field) if field in row else None
        if value is not None:
            values.append(value)
    if not values:
        return None
    return fmean(values)


def _quality_row(row: dict[str, str], factor_id: str, source_fallback: str) -> dict[str, Any]:
    value = _quality_value(row)
    if value is None:
        raise ValueError("quality row called without quality value")
    return _feature_row(row, factor_id, value, source_fallback)


def _feature_row(row: dict[str, str], factor_id: str, value: float, source_fallback: str) -> dict[str, Any]:
    row_date = row["date"][:10]
    return {
        "date": row_date,
        "symbol": row["symbol"],
        "factor_id": factor_id,
        "value": f"{value:.12g}",
        "asof_date": (row.get("asof_date") or row_date)[:10],
        "effective_date": (row.get("effective_date") or row_date)[:10],
        "source": row.get("source") or source_fallback,
    }
