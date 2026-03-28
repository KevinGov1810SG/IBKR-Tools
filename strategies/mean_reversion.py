"""
strategies/mean_reversion.py — Mean Reversion Strategy (Bollinger Bands).

Signal d'entrée : prix dévie de > 2σ de la MM20 + volume < moyenne.
Signal de sortie : retour à la bande médiane.
"""

from __future__ import annotations

from typing import Dict, Optional

import numpy as np
import pandas as pd
from loguru import logger

from config import MeanReversionParams, SignalDirection, get_config
from core.indicators import bbands as calc_bbands
from strategies.base_strategy import BaseStrategy, TradeSignal


class MeanReversionStrategy(BaseStrategy):
    name = "mean_reversion"

    def __init__(self, params: Optional[MeanReversionParams] = None):
        super().__init__(params or get_config().mean_reversion)
        self.p: MeanReversionParams = self.params

    # ------------------------------------------------------------------
    def generate_signal(
        self,
        symbol: str,
        data: pd.DataFrame,
        multi_tf: Optional[Dict[str, pd.DataFrame]] = None,
    ) -> Optional[TradeSignal]:
        if data.empty or len(data) < self.p.bb_period + 5:
            return None

        bb = calc_bbands(data["close"], length=self.p.bb_period, std=self.p.bb_std_dev)
        if bb is None or bb.empty:
            return None

        lower_col = [c for c in bb.columns if c.startswith("BBL")]
        upper_col = [c for c in bb.columns if c.startswith("BBU")]
        mid_col = [c for c in bb.columns if c.startswith("BBM")]

        if not lower_col or not upper_col or not mid_col:
            return None

        lower = bb[lower_col[0]].iloc[-1]
        upper = bb[upper_col[0]].iloc[-1]
        mid = bb[mid_col[0]].iloc[-1]
        close = data["close"].iloc[-1]

        # Volume filter: only trade if volume < average
        if self.p.volume_filter and "volume" in data.columns:
            vol_ma = data["volume"].rolling(self.p.volume_ma_period).mean()
            if not vol_ma.empty and not np.isnan(vol_ma.iloc[-1]):
                if data["volume"].iloc[-1] > vol_ma.iloc[-1]:
                    return None  # volume too high → trending, skip

        direction = None
        strength = 0.0

        if close < lower:
            # Price below lower band → expect reversion up
            direction = SignalDirection.LONG
            band_width = mid - lower
            if band_width > 0:
                strength = min(1.0, (lower - close) / band_width)
        elif close > upper:
            # Price above upper band → expect reversion down
            direction = SignalDirection.SHORT
            band_width = upper - mid
            if band_width > 0:
                strength = min(1.0, (close - upper) / band_width)

        if direction is None:
            return None

        signal = TradeSignal(
            symbol=symbol,
            direction=direction,
            strength=strength,
            strategy_name=self.name,
            target_price=mid,
            metadata={"bb_mid": mid, "bb_lower": lower, "bb_upper": upper},
        )
        logger.info(f"[MeanReversion] Signal {direction.value} {symbol} strength={strength:.2f}")
        return signal

    # ------------------------------------------------------------------
    def compute_position_size(
        self,
        signal: TradeSignal,
        data: pd.DataFrame,
        capital: float,
        current_price: float,
    ) -> float:
        if current_price <= 0:
            return 0.0

        # Fixed fraction: risk 1.5% of capital
        risk_capital = capital * 0.015
        # Stop distance = distance from entry to opposite band
        mid = signal.metadata.get("bb_mid", current_price)
        stop_dist = abs(current_price - mid) * 2  # 2x the expected move as stop
        if stop_dist <= 0:
            stop_dist = current_price * 0.02

        qty = risk_capital / stop_dist
        qty = int(max(1, qty))
        max_qty = int((capital * 0.05) / current_price) if current_price > 0 else 0
        qty = min(qty, max_qty)
        signal.suggested_quantity = qty
        return qty

    # ------------------------------------------------------------------
    def should_exit(
        self,
        symbol: str,
        data: pd.DataFrame,
        entry_price: float,
        current_price: float,
        **kwargs,
    ) -> bool:
        if data.empty or len(data) < self.p.bb_period:
            return False

        bb = calc_bbands(data["close"], length=self.p.bb_period, std=self.p.bb_std_dev)
        if bb is None:
            return False

        mid_col = [c for c in bb.columns if c.startswith("BBM")]
        if not mid_col:
            return False

        mid = bb[mid_col[0]].iloc[-1]

        # Exit when price reverts to the middle band
        direction = kwargs.get("direction", "BUY")
        if direction == "BUY" and current_price >= mid:
            logger.info(f"[MeanReversion] Exit LONG {symbol}: price reached mid band {mid:.2f}")
            return True
        if direction == "SELL" and current_price <= mid:
            logger.info(f"[MeanReversion] Exit SHORT {symbol}: price reached mid band {mid:.2f}")
            return True

        return False

