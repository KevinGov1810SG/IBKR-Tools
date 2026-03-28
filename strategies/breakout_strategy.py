"""
strategies/breakout_strategy.py — Volume Breakout Strategy.

Signal d'entrée : cassure d'un range de N bougies + volume > 200% de la moyenne 20p.
Signal de sortie : TP 2:1 risk/reward ou time-based exit.
Filtre : horaires de forte liquidité uniquement.
"""

from __future__ import annotations

import datetime
from typing import Dict, Optional

import numpy as np
from loguru import logger

from config import BreakoutParams, SignalDirection, get_config
from config import utc_now
from strategies.base_strategy import BaseStrategy, TradeSignal


class BreakoutStrategy(BaseStrategy):
    name = "breakout"

    def __init__(self, params: Optional[BreakoutParams] = None):
        super().__init__(params or get_config().breakout)
        self.p: BreakoutParams = self.params
        self._entry_times: Dict[str, datetime.datetime] = {}

    # ------------------------------------------------------------------
    def generate_signal(
        self,
        symbol: str,
        data: pd.DataFrame,
        multi_tf: Optional[Dict[str, pd.DataFrame]] = None,
    ) -> Optional[TradeSignal]:
        if data.empty or len(data) < self.p.lookback_bars + 5:
            return None

        # Liquidity hours filter
        now_utc = utc_now()
        if now_utc.hour not in self.p.allowed_hours_utc:
            return None

        lookback = data.iloc[-(self.p.lookback_bars + 1):-1]
        current = data.iloc[-1]

        range_high = lookback["high"].max()
        range_low = lookback["low"].min()

        # Volume spike check (skipped if volume data unavailable, e.g. crypto MIDPOINT)
        volume_ratio = 0.0
        has_volume = data["volume"].sum() > 0
        if has_volume:
            vol_ma = data["volume"].rolling(self.p.volume_ma_period).mean()
            if vol_ma.empty or np.isnan(vol_ma.iloc[-1]) or vol_ma.iloc[-1] <= 0:
                return None

            volume_ratio = current["volume"] / vol_ma.iloc[-1]
            if volume_ratio < self.p.volume_spike_ratio:
                return None
        else:
            # No volume data — rely on price breakout only
            volume_ratio = self.p.volume_spike_ratio  # neutral value

        direction = None
        stop_loss = None
        take_profit = None

        if current["close"] > range_high:
            direction = SignalDirection.LONG
            stop_loss = range_low
            risk = current["close"] - stop_loss
            take_profit = current["close"] + risk * self.p.reward_risk_ratio
        elif current["close"] < range_low:
            direction = SignalDirection.SHORT
            stop_loss = range_high
            risk = stop_loss - current["close"]
            take_profit = current["close"] - risk * self.p.reward_risk_ratio

        if direction is None:
            return None

        strength = min(1.0, volume_ratio / (self.p.volume_spike_ratio * 2))

        signal = TradeSignal(
            symbol=symbol,
            direction=direction,
            strength=strength,
            strategy_name=self.name,
            target_price=take_profit,
            stop_loss=stop_loss,
            metadata={
                "range_high": range_high,
                "range_low": range_low,
                "volume_ratio": volume_ratio,
            },
        )
        logger.info(
            f"[Breakout] Signal {direction.value} {symbol} "
            f"vol_ratio={volume_ratio:.1f}x strength={strength:.2f}"
        )
        return signal

    # ------------------------------------------------------------------
    def compute_position_size(
        self,
        signal: TradeSignal,
        data: pd.DataFrame,
        capital: float,
        current_price: float,
    ) -> float:
        if current_price <= 0 or signal.stop_loss is None:
            return 0.0

        risk_per_share = abs(current_price - signal.stop_loss)
        if risk_per_share <= 0:
            return 0.0

        risk_capital = capital * 0.02
        qty = risk_capital / risk_per_share
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
        direction = kwargs.get("direction", "BUY")
        target_price = kwargs.get("target_price")
        stop_loss = kwargs.get("stop_loss")

        # TP hit
        if target_price is not None:
            if direction == "BUY" and current_price >= target_price:
                logger.info(f"[Breakout] TP hit for {symbol}")
                return True
            if direction == "SELL" and current_price <= target_price:
                logger.info(f"[Breakout] TP hit for {symbol}")
                return True

        # SL hit
        if stop_loss is not None:
            if direction == "BUY" and current_price <= stop_loss:
                logger.info(f"[Breakout] SL hit for {symbol}")
                return True
            if direction == "SELL" and current_price >= stop_loss:
                logger.info(f"[Breakout] SL hit for {symbol}")
                return True

        # Time-based exit
        entry_time = self._entry_times.get(symbol)
        if entry_time:
            elapsed = (utc_now() - entry_time).total_seconds() / 3600
            if elapsed >= self.p.max_hold_hours:
                logger.info(f"[Breakout] Time exit for {symbol} ({elapsed:.1f}h)")
                self._entry_times.pop(symbol, None)
                return True

        return False

    def register_entry(self, symbol: str) -> None:
        self._entry_times[symbol] = utc_now()

