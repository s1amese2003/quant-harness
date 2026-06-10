# quant-harness

Phase-one personal quantitative research harness for A-share factor research.

The first phase implements a minimal, reproducible research loop:

1. `qh factor compute`
2. `qh backtest run`
3. `qh eval factor`

The CLI is intentionally data-vendor neutral. It reads curated daily CSV data,
writes factor files, writes run artifacts, and evaluates each run against the
configured factor gate.

## Install

```powershell
python -m pip install -e .
```

After installation, run:

```powershell
qh --help
```

Without installing, use:

```powershell
$env:PYTHONPATH = "src"
python -m qh --help
```

## Curated Data Contract

Phase one expects a daily CSV file at `data/curated/daily_prices.csv` by
default. Required columns:

```text
date,symbol,close
```

Recommended governance columns:

```text
asof_date,effective_date,source
```

Optional factor inputs:

```text
volume,turnover,roe,roa,gross_margin,net_profit_growth
```

`date`, `asof_date`, and `effective_date` use `YYYY-MM-DD`.

## Supported Factors

- `short_reversal_5d`
- `momentum_20d`
- `low_volatility_20d`
- `turnover_20d`
- `quality_roe`

## Workflow

Compute a factor:

```powershell
qh factor compute --factor short_reversal_5d --input data/curated/daily_prices.csv --output data/features
```

Run the standard backtest:

```powershell
qh backtest run --factor-file data/features/short_reversal_5d.csv --market data/curated/daily_prices.csv
```

Evaluate the run:

```powershell
qh eval factor --run runs/<run_id>
```

Run artifacts are written under `runs/<run_id>/`:

- `backtest.json`
- `portfolio_returns.csv`
- `positions.csv`
- `factor_forward_returns.csv`
- `eval_factor.json`

## Phase-One Boundaries

- The CLI reads curated data only; raw data download and audit are phase two.
- Backtests use lagged execution to reduce future-data risk.
- Outputs are diagnostics, not trading instructions.
- Factor promotion still requires human review.
