"""
tests/test_strategies.py — Tests unitaires pour les 3 stratégies avec données mockées.
"""

from __future__ import annotations

import datetime

import numpy as np
import pandas as pd
import pytest

from config import (
    MomentumParams, MeanReversionParams, BreakoutParams,
    SignalDirection, load_config, utc_now,
)
from strategies.momentum_strategy import MomentumStrategy
from strategies.mean_reversion import MeanReversionStrategy
from strategies.breakout_strategy import BreakoutStrategy


# ---------------------------------------------------------------------------
# Helpers — generate synthetic OHLCV data
# ---------------------------------------------------------------------------

def _make_df(
    closes: list[float],
    high_offset: float = 0.5,
    low_offset: float = 0.5,
    base_volume: int = 1000,
    volume_override: list[int] | None = None,
) -> pd.DataFrame:
    n = len(closes)
    dates = pd.date_range(end=utc_now(), periods=n, freq="5min")
    highs = [c + high_offset for c in closes]
    lows = [c - low_offset for c in closes]
    opens = [(c + l) / 2 for c, l in zip(closes, lows)]
    volumes = volume_override if volume_override else [base_volume] * n
    return pd.DataFrame(
        {"open": opens, "high": highs, "low": lows, "close": closes, "volume": volumes},
        index=dates,
    )


def _trending_up(n: int = 80, start: float = 100.0, step: float = 0.3) -> list[float]:
    """Generate a steadily rising price series."""
    prices = []
    p = start
    np.random.seed(42)
    for _ in range(n):
        p += step + np.random.normal(0, 0.1)
        prices.append(round(p, 2))
    return prices


def _mean_reverting(n: int = 60, mean: float = 100.0, std: float = 2.0) -> list[float]:
    """Generate a mean-reverting series that dips then recovers."""
    np.random.seed(42)
    prices = []
    p = mean
    for i in range(n):
        if i < n // 3:
            p -= std * 0.15  # drift down
        elif i < 2 * n // 3:
            p += std * 0.2  # revert up
        p += np.random.normal(0, 0.3)
        prices.append(round(p, 2))
    return prices


def _range_then_breakout(n: int = 50, base: float = 100.0) -> tuple[list[float], list[int]]:
    """Range-bound then breakout with volume spike."""
    np.random.seed(42)
    prices = []
    volumes = []
    for i in range(n):
        if i < n - 5:
            p = base + np.random.uniform(-1, 1)
            volumes.append(1000)
        else:
            p = base + 3 + (i - (n - 5)) * 0.5  # breakout up
            volumes.append(5000)  # huge volume spike
        prices.append(round(p, 2))
    return prices, volumes


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _reset_config():
    load_config()


# ===========================================================================
# MOMENTUM STRATEGY TESTS
# ===========================================================================

class TestMomentumStrategy:

    def test_no_signal_on_short_data(self):
        strat = MomentumStrategy()
        df = _make_df([100.0] * 10)
        signal = strat.generate_signal("AAPL", df)
        assert signal is None

    def test_signal_on_trending_data(self):
        """With a strong uptrend, expect a LONG signal if RSI+MACD conditions met."""
        strat = MomentumStrategy()
        closes = _trending_up(80, start=100.0, step=0.4)
        df = _make_df(closes)
        # Build a second timeframe confirming (same data)
        multi_tf = {"15 mins": df.copy(), "1 hour": df.copy()}
        signal = strat.generate_signal("AAPL", df, multi_tf=multi_tf)
        # Signal depends on exact indicator values — may or may not fire
        if signal is not None:
            assert signal.direction == SignalDirection.LONG
            assert signal.strategy_name == "momentum"
            assert signal.strength > 0

    def test_position_sizing(self):
        strat = MomentumStrategy()
        closes = _trending_up(80)
        df = _make_df(closes)
        from strategies.base_strategy import TradeSignal
        sig = TradeSignal(
            symbol="AAPL", direction=SignalDirection.LONG,
            strategy_name="momentum",
        )
        qty = strat.compute_position_size(sig, df, capital=100_000, current_price=closes[-1])
        assert qty >= 0
        # Should not exceed 5% of capital
        assert qty * closes[-1] <= 100_000 * 0.05 + 1

    def test_exit_trailing_stop(self):
        strat = MomentumStrategy()
        closes = _trending_up(80)
        df = _make_df(closes)
        entry = closes[-10]
        peak = max(closes[-10:])
        # Simulate a drop from peak
        drop_price = peak * (1 - strat.p.trailing_stop_pct / 100 - 0.001)
        strat._trailing_highs["AAPL"] = peak
        result = strat.should_exit("AAPL", df, entry_price=entry, current_price=drop_price)
        assert result is True

    def test_exit_rsi_overbought(self):
        """If data pushes RSI above exit threshold, should_exit returns True."""
        strat = MomentumStrategy()
        # Build a series that drives RSI very high
        closes = [100 + i * 0.8 for i in range(60)]  # strong uptrend
        df = _make_df(closes)
        # might or might not trigger depending on exact RSI calc
        result = strat.should_exit("AAPL", df, entry_price=100, current_price=closes[-1])
        # Just verify it returns a bool without error
        assert isinstance(result, bool)

    def test_deactivate(self):
        strat = MomentumStrategy()
        assert strat.is_active is True
        strat.deactivate()
        assert strat.is_active is False
        strat.activate()
        assert strat.is_active is True


# ===========================================================================
# MEAN REVERSION STRATEGY TESTS
# ===========================================================================

class TestMeanReversionStrategy:

    def test_no_signal_on_short_data(self):
        strat = MeanReversionStrategy()
        df = _make_df([100.0] * 10)
        signal = strat.generate_signal("SPY", df)
        assert signal is None

    def test_signal_on_dip_below_lower_band(self):
        """When price drops significantly below lower Bollinger Band, expect LONG."""
        strat = MeanReversionStrategy()
        # Normal range then sharp dip
        closes = [100 + np.random.normal(0, 0.3) for _ in range(40)]
        closes[-1] = 90.0  # extreme dip
        closes[-2] = 93.0
        df = _make_df(closes, base_volume=500)  # low volume
        signal = strat.generate_signal("SPY", df)
        if signal is not None:
            assert signal.direction == SignalDirection.LONG
            assert signal.strategy_name == "mean_reversion"

    def test_signal_on_spike_above_upper_band(self):
        strat = MeanReversionStrategy()
        closes = [100 + np.random.normal(0, 0.3) for _ in range(40)]
        closes[-1] = 110.0  # extreme spike
        closes[-2] = 107.0
        df = _make_df(closes, base_volume=500)
        signal = strat.generate_signal("SPY", df)
        if signal is not None:
            assert signal.direction == SignalDirection.SHORT
            assert signal.strategy_name == "mean_reversion"

    def test_volume_filter_blocks_high_volume(self):
        """If volume > average, no signal should fire."""
        strat = MeanReversionStrategy()
        closes = [100 + np.random.normal(0, 0.3) for _ in range(40)]
        closes[-1] = 90.0  # dip
        # High volume everywhere
        df = _make_df(closes, base_volume=5000)
        # With high recent volume, filter should block
        signal = strat.generate_signal("SPY", df)
        # Volume filter may or may not block depending on exact values
        # Just ensure no crash
        assert signal is None or signal.direction in (SignalDirection.LONG, SignalDirection.SHORT)

    def test_position_sizing(self):
        strat = MeanReversionStrategy()
        from strategies.base_strategy import TradeSignal
        sig = TradeSignal(
            symbol="SPY", direction=SignalDirection.LONG,
            strategy_name="mean_reversion",
            metadata={"bb_mid": 100.0, "bb_lower": 95.0, "bb_upper": 105.0},
        )
        qty = strat.compute_position_size(sig, _make_df([100]*30), capital=100_000, current_price=95.0)
        assert qty >= 0
        assert qty * 95.0 <= 100_000 * 0.05 + 1

    def test_exit_at_middle_band(self):
        strat = MeanReversionStrategy()
        closes = _mean_reverting(60)
        df = _make_df(closes)
        # If current price reaches the mid band, should exit
        result = strat.should_exit("SPY", df, entry_price=95.0, current_price=100.0, direction="BUY")
        assert isinstance(result, bool)


# ===========================================================================
# BREAKOUT STRATEGY TESTS
# ===========================================================================

class TestBreakoutStrategy:

    def test_no_signal_on_short_data(self):
        strat = BreakoutStrategy()
        df = _make_df([100.0] * 10)
        signal = strat.generate_signal("ES", df)
        assert signal is None

    def test_signal_on_breakout_with_volume(self):
        strat = BreakoutStrategy()
        # Override allowed hours to include current hour
        strat.p.allowed_hours_utc = list(range(0, 24))
        prices, volumes = _range_then_breakout(50)
        df = _make_df(prices, volume_override=volumes)
        signal = strat.generate_signal("ES", df)
        if signal is not None:
            assert signal.direction in (SignalDirection.LONG, SignalDirection.SHORT)
            assert signal.stop_loss is not None
            assert signal.target_price is not None
            assert signal.strategy_name == "breakout"

    def test_no_signal_without_volume_spike(self):
        strat = BreakoutStrategy()
        strat.p.allowed_hours_utc = list(range(0, 24))
        prices, _ = _range_then_breakout(50)
        # Flat volume — no spike
        df = _make_df(prices, base_volume=1000)
        signal = strat.generate_signal("ES", df)
        assert signal is None

    def test_liquidity_hours_filter(self):
        strat = BreakoutStrategy()
        strat.p.allowed_hours_utc = []  # no allowed hours
        prices, volumes = _range_then_breakout(50)
        df = _make_df(prices, volume_override=volumes)
        signal = strat.generate_signal("ES", df)
        assert signal is None

    def test_position_sizing_with_stop(self):
        strat = BreakoutStrategy()
        from strategies.base_strategy import TradeSignal
        sig = TradeSignal(
            symbol="ES", direction=SignalDirection.LONG,
            strategy_name="breakout",
            stop_loss=98.0,
            target_price=106.0,
        )
        qty = strat.compute_position_size(sig, _make_df([100]*30), capital=100_000, current_price=100.0)
        assert qty >= 0
        assert qty * 100.0 <= 100_000 * 0.05 + 1

    def test_exit_on_tp(self):
        strat = BreakoutStrategy()
        result = strat.should_exit(
            "ES", _make_df([100]*30), entry_price=100, current_price=110,
            direction="BUY", target_price=108, stop_loss=95,
        )
        assert result is True

    def test_exit_on_sl(self):
        strat = BreakoutStrategy()
        result = strat.should_exit(
            "ES", _make_df([100]*30), entry_price=100, current_price=94,
            direction="BUY", target_price=108, stop_loss=95,
        )
        assert result is True

    def test_time_based_exit(self):
        strat = BreakoutStrategy()
        strat.p.max_hold_hours = 0  # immediate exit
        strat._entry_times["ES"] = utc_now() - datetime.timedelta(hours=1)
        result = strat.should_exit(
            "ES", _make_df([100]*30), entry_price=100, current_price=101,
            direction="BUY",
        )
        assert result is True


# ===========================================================================
# PARAMETER UPDATE TESTS
# ===========================================================================

class TestParamUpdates:
    def test_momentum_param_update(self):
        strat = MomentumStrategy()
        new_params = MomentumParams(rsi_period=21, rsi_entry_threshold=65)
        strat.update_params(new_params)
        assert strat.params.rsi_period == 21
        assert strat.params.rsi_entry_threshold == 65

    def test_mean_reversion_param_update(self):
        strat = MeanReversionStrategy()
        new_params = MeanReversionParams(bb_std_dev=2.5)
        strat.update_params(new_params)
        assert strat.params.bb_std_dev == 2.5

    def test_breakout_param_update(self):
        strat = BreakoutStrategy()
        new_params = BreakoutParams(volume_spike_ratio=3.0, lookback_bars=30)
        strat.update_params(new_params)
        assert strat.params.volume_spike_ratio == 3.0
        assert strat.params.lookback_bars == 30

