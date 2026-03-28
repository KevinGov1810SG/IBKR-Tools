"""
agents/optimizer_agent.py — Agent d'optimisation des paramètres.

Analyse le PnL des trades clôturés toutes les N heures.
Ajuste les paramètres des stratégies via un grid search simple.
"""

from __future__ import annotations

import datetime
from typing import Any, Dict, List, Optional

import numpy as np
from loguru import logger

from agents.base_agent import BaseAgent, Recommendation
from config import OptimizerConfig, get_config, utc_now
from database.repository import TradeRepository


class OptimizerAgent(BaseAgent):
    name = "optimizer_agent"

    def __init__(self, trade_repo: TradeRepository, config: Optional[OptimizerConfig] = None):
        super().__init__()
        self._repo = trade_repo
        self._cfg = config or get_config().optimizer
        self._last_run: Optional[datetime.datetime] = None
        self._last_recommendation: Optional[Recommendation] = None

    async def analyze(self, context: Dict[str, Any]) -> None:
        """
        Context expects:
            - "strategies": Dict[str, BaseStrategy] — live strategy instances
            - "force": bool (optional) — skip time check
        """
        now = utc_now()
        force = context.get("force", False)

        if not force and self._last_run is not None:
            elapsed_h = (now - self._last_run).total_seconds() / 3600
            if elapsed_h < self._cfg.evaluation_interval_hours:
                return

        self._last_run = now

        # Gather closed trades
        since = now - datetime.timedelta(hours=self._cfg.evaluation_interval_hours * 3)
        trades = self._repo.get_closed_trades(since=since)

        if len(trades) < self._cfg.min_trades_for_optimization:
            self._last_recommendation = Recommendation(
                agent_name=self.name, action="info",
                details={"note": f"Not enough trades ({len(trades)}) for optimization"},
            )
            return

        # Group PnL by strategy
        strategy_pnl: Dict[str, List[float]] = {}
        for t in trades:
            strategy_pnl.setdefault(t.strategy, []).append(t.pnl or 0.0)

        suggestions: Dict[str, Any] = {}

        for strat_name, pnls in strategy_pnl.items():
            avg_pnl = np.mean(pnls)
            win_rate = sum(1 for p in pnls if p > 0) / len(pnls) if pnls else 0

            if strat_name == "momentum":
                suggestions["momentum"] = self._optimize_momentum(avg_pnl, win_rate)
            elif strat_name == "mean_reversion":
                suggestions["mean_reversion"] = self._optimize_mean_reversion(avg_pnl, win_rate)
            elif strat_name == "breakout":
                suggestions["breakout"] = self._optimize_breakout(avg_pnl, win_rate)

        self._last_recommendation = Recommendation(
            agent_name=self.name,
            action="adjust",
            details={"suggested_params": suggestions, "trade_stats": {
                k: {"count": len(v), "avg_pnl": float(np.mean(v)),
                    "win_rate": sum(1 for p in v if p > 0) / len(v)}
                for k, v in strategy_pnl.items()
            }},
        )
        logger.info(f"[Optimizer] Analysis complete — suggestions: {list(suggestions.keys())}")

    def get_recommendation(self) -> Recommendation:
        if self._last_recommendation is None:
            return Recommendation(agent_name=self.name, action="info",
                                  details={"note": "No analysis yet"})
        return self._last_recommendation

    # -- Simple grid-search style adjustments ---------------------------------
    @staticmethod
    def _optimize_momentum(avg_pnl: float, win_rate: float) -> Dict[str, Any]:
        cfg = get_config().momentum
        adjustments = {}

        # If win rate low, tighten entry
        if win_rate < 0.4:
            adjustments["rsi_entry_threshold"] = min(70.0, cfg.rsi_entry_threshold + 5)
            adjustments["trailing_stop_pct"] = max(0.8, cfg.trailing_stop_pct - 0.3)
        elif win_rate > 0.6 and avg_pnl > 0:
            adjustments["rsi_entry_threshold"] = max(50.0, cfg.rsi_entry_threshold - 5)

        if avg_pnl < 0:
            adjustments["trailing_stop_pct"] = max(0.5, cfg.trailing_stop_pct - 0.2)

        return adjustments

    @staticmethod
    def _optimize_mean_reversion(avg_pnl: float, win_rate: float) -> Dict[str, Any]:
        cfg = get_config().mean_reversion
        adjustments = {}

        if win_rate < 0.4:
            adjustments["bb_std_dev"] = min(3.0, cfg.bb_std_dev + 0.25)
        elif win_rate > 0.6 and avg_pnl > 0:
            adjustments["bb_std_dev"] = max(1.5, cfg.bb_std_dev - 0.25)

        return adjustments

    @staticmethod
    def _optimize_breakout(avg_pnl: float, win_rate: float) -> Dict[str, Any]:
        cfg = get_config().breakout
        adjustments = {}

        if win_rate < 0.4:
            adjustments["volume_spike_ratio"] = min(3.0, cfg.volume_spike_ratio + 0.3)
            adjustments["lookback_bars"] = min(40, cfg.lookback_bars + 5)
        elif win_rate > 0.6 and avg_pnl > 0:
            adjustments["volume_spike_ratio"] = max(1.5, cfg.volume_spike_ratio - 0.2)

        return adjustments
