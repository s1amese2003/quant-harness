# Quant Harness Research Rules

This file is the strategy map and operating boundary for AI-assisted research.
Every research run must read it before proposing or testing a factor.

## Strategy Objective

- Market: China A-share CSI 500 / CSI 1000 style universes.
- Strategy type: medium/low-frequency index enhancement.
- Rebalance frequency: weekly by default; daily is allowed only for risk control tests.
- Annualized excess return target: 5%-8%.
- Tracking error target: 4%-6%.
- Maximum drawdown constraint: no more than benchmark drawdown plus 5 percentage points.

## Universe Constraints

- Exclude ST names, delisting-board names, stocks listed for less than 60 trading days, and long-suspended stocks.
- Exclude names that cannot be traded on rebalance day because of limit-up or limit-down restrictions.
- Single-name active weight must not exceed 3%.
- Industry active deviation versus benchmark must not exceed 5%.

## Hard Rules

- Do not use future data.
- Do not select parameters on the full sample.
- Do not repeatedly tune against the test period.
- Do not report returns without IC, turnover, cost, exposure, and failure diagnosis.
- New factors must later pass a permutation/random-label test before promotion.
- AI may generate research suggestions and reports, but must not place live orders.

## Phase-One Definition Of Done

- `qh factor compute` can create factor values from curated daily data.
- `qh backtest run` can run a standardized long-short factor backtest.
- `qh eval factor` can generate IC, turnover, drawdown, and gate-pass diagnostics.
- Factor results are reproducible from config, input files, and run artifacts.
- Failed factors leave useful evidence in `runs/` and `research_log.md`.
