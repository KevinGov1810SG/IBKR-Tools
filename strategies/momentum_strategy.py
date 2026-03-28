"""
strategies/momentum_strategy.py — Momentum Cross-Asset Strategy.

Signal d'entrée : RSI > 60 + MACD bullish crossover sur deux timeframes simultanément.
Signal de sortie : RSI > 80 (overbought) ou trailing stop à -1.5%.
Position sizing basé sur l'ATR.
"""

from __future__ import annotations

from typing import Dict, Optional

import numpy as np
import pandas as pd
from loguru import logger

from config import MomentumParams, SignalDirection, get_config
from core.indicators import rsi as calc_rsi, macd as calc_macd, atr as calc_atr
from strategies.base_strategy import BaseStrategy, TradeSignal


class MomentumStrategy(BaseStrategy):
    name = "momentum"

    def __init__(self, params: Optional[MomentumParams] = None):
        super().__init__(params or get_config().momentum)
        self.p: MomentumParams = self.params
        self._trailing_highs: Dict[str, float] = {}

    # ------------------------------------------------------------------
    def generate_signal(
        self,
        symbol: str,
        data: pd.DataFrame,
        multi_tf: Optional[Dict[str, pd.DataFrame]] = None,
    ) -> Optional[TradeSignal]:
        if data.empty or len(data) < self.p.macd_slow + self.p.macd_signal:
            return None

        # Check primary timeframe
        primary_ok, primary_strength = self._check_momentum(data)
        if not primary_ok:
            return None

        # Need at least one more confirming timeframe
        confirmations = 0
        if multi_tf:
            for tf_name, tf_data in multi_tf.items():
                if tf_data.empty:
                    continue
                ok, _ = self._check_momentum(tf_data)
                if ok:
                    confirmations += 1

        # Require at least 2 TFs agreeing (including primary)
        if confirmations < 1:
            return None

        strength = min(1.0, primary_strength * (1 + confirmations * 0.2))

        signal = TradeSignal(
            symbol=symbol,
            direction=SignalDirection.LONG,
            strength=strength,
            strategy_name=self.name,
            metadata={"confirmations": confirmations + 1},
        )
        logger.info(f"[Momentum] Signal LONG {symbol} strength={strength:.2f}")
        return signal

    # ------------------------------------------------------------------
    def compute_position_size(
        self,
        signal: TradeSignal,
        data: pd.DataFrame,
        capital: float,
        current_price: float,
    ) -> float:
        if data.empty or current_price <= 0:
            return 0.0

        atr_series = calc_atr(data["high"], data["low"], data["close"], length=self.p.atr_period)
        if atr_series is None or atr_series.empty:
            return 0.0

        current_atr = atr_series.iloc[-1]
        if np.isnan(current_atr) or current_atr <= 0:
            return 0.0

        # Risk per trade = ATR * factor → dollar risk
        dollar_risk = current_atr * self.p.atr_risk_factor
        risk_capital = capital * 0.02  # risk 2% of capital per trade
        qty = risk_capital / dollar_risk if dollar_risk > 0 else 0

        # Floor to whole shares
        qty = int(max(1, qty))
        # Ensure we don't exceed 5% of capital
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
        if data.empty:
            return False

        # RSI overbought exit
        rsi_series = calc_rsi(data["close"], length=self.p.rsi_period)
        if rsi_series is not None and not rsi_series.empty:
            last_rsi = rsi_series.iloc[-1]
            if not np.isnan(last_rsi) and last_rsi > self.p.rsi_exit_threshold:
                logger.info(f"[Momentum] RSI exit for {symbol} (RSI={last_rsi:.1f})")
                return True

        # Trailing stop
        high = max(self._trailing_highs.get(symbol, entry_price), current_price)
        self._trailing_highs[symbol] = high
        drop_pct = (high - current_price) / high * 100 if high > 0 else 0
        if drop_pct >= self.p.trailing_stop_pct:
            logger.info(f"[Momentum] Trailing stop for {symbol} drop={drop_pct:.2f}%")
            self._trailing_highs.pop(symbol, None)
            return True

        return False

    # ------------------------------------------------------------------
    def _check_momentum(self, df: pd.DataFrame) -> tuple[bool, float]:
        """Return (is_bullish, strength) based on RSI + MACD."""
        if len(df) < self.p.macd_slow + self.p.macd_signal:
            return False, 0.0

        rsi_series = calc_rsi(df["close"], length=self.p.rsi_period)
        macd_df = calc_macd(df["close"], fast=self.p.macd_fast,
                            slow=self.p.macd_slow, signal_period=self.p.macd_signal)

        if rsi_series is None or macd_df is None:
            return False, 0.0

        current_rsi = rsi_series.iloc[-1]
        if np.isnan(current_rsi):
            return False, 0.0

        hist = macd_df["Histogram"]
        if len(hist) < 2:
            return False, 0.0

        # Bullish crossover: histogram turns positive
        macd_bullish = hist.iloc[-1] > 0 and hist.iloc[-2] <= 0
        rsi_ok = current_rsi > self.p.rsi_entry_threshold

        if rsi_ok and macd_bullish:
            strength = min(1.0, (current_rsi - 50) / 30)  # normalize 50-80 → 0-1
            return True, strength

        return False, 0.0

