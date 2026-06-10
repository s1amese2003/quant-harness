from __future__ import annotations

import csv
import contextlib
import io
import json
import sys
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from qh.cli import main  # noqa: E402


def run_cli(args: list[str]) -> int:
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        return main(args)


class CliSmokeTest(unittest.TestCase):
    def test_phase_one_workflow(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            market = tmp_path / "daily_prices.csv"
            features = tmp_path / "features"
            runs = tmp_path / "runs"
            self._write_market(market)

            self.assertEqual(
                run_cli(
                    [
                        "factor",
                        "compute",
                        "--factor",
                        "short_reversal_5d",
                        "--input",
                        str(market),
                        "--output",
                        str(features),
                        "--lookback",
                        "2",
                    ]
                ),
                0,
            )
            factor_file = features / "short_reversal_5d.csv"
            self.assertTrue(factor_file.exists())

            self.assertEqual(
                run_cli(
                    [
                        "backtest",
                        "run",
                        "--factor-file",
                        str(factor_file),
                        "--market",
                        str(market),
                        "--config",
                        str(ROOT / "configs" / "backtest.yaml"),
                        "--risk-config",
                        str(ROOT / "configs" / "risk_limits.yaml"),
                        "--output-root",
                        str(runs),
                        "--run-name",
                        "smoke",
                    ]
                ),
                0,
            )
            run_dirs = sorted(runs.iterdir())
            self.assertEqual(len(run_dirs), 1)
            self.assertTrue((run_dirs[0] / "portfolio_returns.csv").exists())
            self.assertTrue((run_dirs[0] / "factor_forward_returns.csv").exists())

            self.assertEqual(
                run_cli(
                    [
                        "eval",
                        "factor",
                        "--run",
                        str(run_dirs[0]),
                        "--risk-config",
                        str(ROOT / "configs" / "risk_limits.yaml"),
                        "--config",
                        str(ROOT / "configs" / "backtest.yaml"),
                    ]
                ),
                0,
            )
            eval_payload = json.loads((run_dirs[0] / "eval_factor.json").read_text(encoding="utf-8"))
            self.assertEqual(eval_payload["factor_id"], "short_reversal_5d")
            self.assertIn("rank_ic_mean", eval_payload)
            self.assertIn("pass", eval_payload)

    @staticmethod
    def _write_market(path: Path) -> None:
        symbols = ["000001.SZ", "000002.SZ", "000003.SZ", "000004.SZ", "000005.SZ", "000006.SZ"]
        start = date(2024, 1, 1)
        fieldnames = [
            "date",
            "symbol",
            "close",
            "volume",
            "turnover",
            "roe",
            "asof_date",
            "effective_date",
            "source",
        ]
        rows = []
        trading_days = []
        cursor = start
        while len(trading_days) < 20:
            if cursor.weekday() < 5:
                trading_days.append(cursor)
            cursor += timedelta(days=1)
        for day_index, trading_day in enumerate(trading_days):
            for symbol_index, symbol in enumerate(symbols):
                base = 10 + symbol_index
                close = base + day_index * (0.04 + symbol_index * 0.01) + ((day_index + symbol_index) % 3) * 0.03
                rows.append(
                    {
                        "date": trading_day.isoformat(),
                        "symbol": symbol,
                        "close": f"{close:.4f}",
                        "volume": str(100000 + day_index * 100 + symbol_index),
                        "turnover": f"{0.01 + symbol_index * 0.002:.4f}",
                        "roe": f"{0.08 + symbol_index * 0.01:.4f}",
                        "asof_date": trading_day.isoformat(),
                        "effective_date": trading_day.isoformat(),
                        "source": "smoke",
                    }
                )
        with path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)
