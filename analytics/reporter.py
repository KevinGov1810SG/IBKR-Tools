"""
analytics/reporter.py — Génération de rapports lisibles (console ou fichier).
"""

from __future__ import annotations

import datetime
from typing import Optional

from loguru import logger

from analytics.performance import PerformanceCalculator, PerformanceMetrics
from database.repository import TradeRepository
from config import utc_now


class Reporter:
    """Generates human-readable performance reports."""

    SEPARATOR = "=" * 70

    def __init__(self, perf_calc: PerformanceCalculator, trade_repo: TradeRepository):
        self._perf = perf_calc
        self._repo = trade_repo

    # ------------------------------------------------------------------
    def generate_report(self, since: Optional[datetime.datetime] = None) -> str:
        metrics = self._perf.compute(since=since)
        return self._format(metrics)

    def print_report(self, since: Optional[datetime.datetime] = None) -> None:
        report = self.generate_report(since=since)
        print(report)

    def save_report(self, path: str, since: Optional[datetime.datetime] = None) -> None:
        report = self.generate_report(since=since)
        with open(path, "w", encoding="utf-8") as f:
            f.write(report)
        logger.info(f"Report saved to {path}")

    # ------------------------------------------------------------------
    def _format(self, m: PerformanceMetrics) -> str:
        now = utc_now().strftime("%Y-%m-%d %H:%M UTC")
        lines = [
            self.SEPARATOR,
            f"  IBKR TRADING SYSTEM — PERFORMANCE REPORT",
            f"  Generated: {now}",
            self.SEPARATOR,
            "",
            "  OVERALL",
            f"    Total PnL           : ${m.total_pnl:>12,.2f}",
            f"    Daily PnL           : ${m.daily_pnl:>12,.2f}",
            f"    Total Trades        : {m.total_trades:>12}",
            f"    Winning Trades      : {m.winning_trades:>12}",
            f"    Losing Trades       : {m.losing_trades:>12}",
            f"    Win Rate            : {m.win_rate * 100:>11.1f}%",
            "",
            "  RISK METRICS",
            f"    Sharpe Ratio        : {m.sharpe_ratio:>12.3f}",
            f"    Max Drawdown        : ${m.max_drawdown:>12,.2f}",
            f"    Max Drawdown %      : {m.max_drawdown_pct:>11.2f}%",
            f"    Profit Factor       : {m.profit_factor:>12.2f}",
            "",
            "  TRADE STATISTICS",
            f"    Avg Trade PnL       : ${m.avg_trade_pnl:>12,.2f}",
            f"    Avg Win             : ${m.avg_win:>12,.2f}",
            f"    Avg Loss            : ${m.avg_loss:>12,.2f}",
            f"    Avg Duration (min)  : {m.avg_trade_duration_min:>12.1f}",
            "",
        ]

        if m.pnl_by_strategy:
            lines.append("  PnL BY STRATEGY")
            for strat, pnl in sorted(m.pnl_by_strategy.items()):
                lines.append(f"    {strat:<22}: ${pnl:>12,.2f}")
            lines.append("")

        # Open positions summary
        open_trades = self._repo.get_open_trades()
        lines.append(f"  OPEN POSITIONS ({len(open_trades)})")
        if open_trades:
            lines.append(f"    {'Symbol':<10} {'Dir':<5} {'Qty':>8} {'Entry':>10} {'Strategy':<20}")
            lines.append(f"    {'-'*55}")
            for t in open_trades:
                lines.append(
                    f"    {t.symbol:<10} {t.direction:<5} {t.quantity:>8.1f} "
                    f"{t.entry_price:>10.2f} {t.strategy:<20}"
                )
        else:
            lines.append("    (none)")

        lines.append("")
        lines.append(self.SEPARATOR)
        return "\n".join(lines)

