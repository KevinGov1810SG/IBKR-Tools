"""
analytics/performance.py — Calcul des métriques de performance.
"""

from __future__ import annotations

import datetime
from dataclasses import dataclass
from typing import Dict, Optional

import numpy as np

from database.repository import TradeRepository


@dataclass
class PerformanceMetrics:
    total_pnl: float = 0.0
    daily_pnl: float = 0.0
    total_trades: int = 0
    winning_trades: int = 0
    losing_trades: int = 0
    win_rate: float = 0.0
    sharpe_ratio: float = 0.0
    max_drawdown: float = 0.0
    max_drawdown_pct: float = 0.0
    avg_trade_pnl: float = 0.0
    avg_win: float = 0.0
    avg_loss: float = 0.0
    avg_trade_duration_min: float = 0.0
    profit_factor: float = 0.0
    pnl_by_strategy: Dict[str, float] = None

    def __post_init__(self):
        if self.pnl_by_strategy is None:
            self.pnl_by_strategy = {}


class PerformanceCalculator:
    """Computes performance metrics from trade history."""

    def __init__(self, trade_repo: TradeRepository, initial_capital: float = 100_000.0):
        self._repo = trade_repo
        self._initial_capital = initial_capital

    def compute(self, since: Optional[datetime.datetime] = None) -> PerformanceMetrics:
        trades = self._repo.get_closed_trades(since=since)
        if not trades:
            return PerformanceMetrics()

        pnls = [t.pnl for t in trades if t.pnl is not None]
        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p <= 0]

        total_pnl = sum(pnls)
        total_trades = len(pnls)
        winning = len(wins)
        losing = len(losses)
        win_rate = winning / total_trades if total_trades > 0 else 0.0

        avg_pnl = np.mean(pnls) if pnls else 0.0
        avg_win = np.mean(wins) if wins else 0.0
        avg_loss = np.mean(losses) if losses else 0.0

        # Sharpe ratio (annualised, assuming ~252 trading days)
        sharpe = 0.0
        if pnls and np.std(pnls) > 0:
            sharpe = (np.mean(pnls) / np.std(pnls)) * np.sqrt(252)

        # Max drawdown
        equity_curve = np.cumsum([0.0] + pnls) + self._initial_capital
        peak = np.maximum.accumulate(equity_curve)
        drawdown = peak - equity_curve
        max_dd = float(np.max(drawdown)) if len(drawdown) > 0 else 0.0
        max_dd_pct = (max_dd / peak[np.argmax(drawdown)] * 100) if max_dd > 0 else 0.0

        # Profit factor
        gross_profit = sum(wins) if wins else 0.0
        gross_loss = abs(sum(losses)) if losses else 0.0
        profit_factor = gross_profit / gross_loss if gross_loss > 0 else float("inf")

        # Average trade duration
        durations = []
        for t in trades:
            if t.entry_time and t.exit_time:
                d = (t.exit_time - t.entry_time).total_seconds() / 60.0
                durations.append(d)
        avg_duration = np.mean(durations) if durations else 0.0

        # PnL by strategy
        pnl_by_strat: Dict[str, float] = {}
        for t in trades:
            if t.pnl is not None:
                pnl_by_strat[t.strategy] = pnl_by_strat.get(t.strategy, 0.0) + t.pnl

        # Daily PnL
        daily_pnl = self._repo.get_daily_pnl()

        return PerformanceMetrics(
            total_pnl=total_pnl,
            daily_pnl=daily_pnl,
            total_trades=total_trades,
            winning_trades=winning,
            losing_trades=losing,
            win_rate=win_rate,
            sharpe_ratio=sharpe,
            max_drawdown=max_dd,
            max_drawdown_pct=max_dd_pct,
            avg_trade_pnl=avg_pnl,
            avg_win=avg_win,
            avg_loss=avg_loss,
            avg_trade_duration_min=avg_duration,
            profit_factor=profit_factor,
            pnl_by_strategy=pnl_by_strat,
        )



