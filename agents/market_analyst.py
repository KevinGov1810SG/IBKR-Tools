"""
agents/market_analyst.py — Agent d'analyse macro et sentiment de marché.

Analyse VIX, spread, volume global.
Produit un score de market regime : trending / ranging / volatile.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

import numpy as np
from loguru import logger

from config import MarketRegime
from agents.base_agent import BaseAgent, Recommendation
from core.indicators import adx as calc_adx, atr as calc_atr


class MarketAnalyst(BaseAgent):
    name = "market_analyst"

    def __init__(self):
        super().__init__()
        self._regime: MarketRegime = MarketRegime.RANGING
        self._regime_score: float = 0.0
        self._last_recommendation: Optional[Recommendation] = None

    @property
    def regime(self) -> MarketRegime:
        return self._regime

    async def analyze(self, context: Dict[str, Any]) -> None:
        """
        Context expects:
            - "market_data": Dict[symbol, pd.DataFrame] — at least one broad index (SPY)
            - "vix_data": pd.DataFrame (optional)
        """
        market_data: Dict[str, pd.DataFrame] = context.get("market_data", {})

        # Use the first available broad index
        index_df = None
        for sym in ("SPY", "QQQ", "ES"):
            if sym in market_data and not market_data[sym].empty:
                index_df = market_data[sym]
                break

        if index_df is None or len(index_df) < 30:
            self._last_recommendation = Recommendation(
                agent_name=self.name, action="info",
                details={"regime": self._regime.value, "note": "Insufficient data"},
            )
            return

        regime, score = self._compute_regime(index_df)
        self._regime = regime
        self._regime_score = score

        # Optional: VIX analysis
        vix_df = context.get("vix_data")
        if vix_df is not None and not vix_df.empty:
            vix_level = vix_df["close"].iloc[-1]
            if vix_level > 30:
                self._regime = MarketRegime.VOLATILE
                self._regime_score = min(1.0, vix_level / 50)

        self._last_recommendation = Recommendation(
            agent_name=self.name,
            action="info",
            score=self._regime_score,
            details={
                "regime": self._regime.value,
                "score": self._regime_score,
            },
        )
        logger.info(f"[MarketAnalyst] Regime={self._regime.value} score={self._regime_score:.2f}")

    def get_recommendation(self) -> Recommendation:
        if self._last_recommendation is None:
            return Recommendation(
                agent_name=self.name, action="info",
                details={"regime": self._regime.value},
            )
        return self._last_recommendation

    # ------------------------------------------------------------------
    def _compute_regime(self, df: pd.DataFrame) -> tuple[MarketRegime, float]:
        close = df["close"]

        # ADX for trend strength
        adx_df = calc_adx(df["high"], df["low"], close, length=14)
        adx_val = 0.0
        if adx_df is not None:
            adx_col = [c for c in adx_df.columns if "ADX" in c and "DI" not in c]
            if adx_col:
                adx_val = adx_df[adx_col[0]].iloc[-1]
                if np.isnan(adx_val):
                    adx_val = 0.0

        # Volatility via ATR relative to price
        atr_series = calc_atr(df["high"], df["low"], close, length=14)
        atr_pct = 0.0
        if atr_series is not None and not atr_series.empty and close.iloc[-1] > 0:
            atr_pct = atr_series.iloc[-1] / close.iloc[-1] * 100

        # Classify
        if atr_pct > 3.0:
            return MarketRegime.VOLATILE, min(1.0, atr_pct / 5.0)
        elif adx_val > 25:
            return MarketRegime.TRENDING, min(1.0, adx_val / 50.0)
        else:
            return MarketRegime.RANGING, max(0.0, 1.0 - adx_val / 25.0)




