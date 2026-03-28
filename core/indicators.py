"""
core/indicators.py — Indicateurs techniques purs (pas de dépendance pandas-ta / ta-lib).

Implémentations classiques basées uniquement sur pandas + numpy.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def rsi(series: pd.Series, length: int = 14) -> pd.Series:
    """Relative Strength Index."""
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)

    avg_gain = gain.ewm(alpha=1 / length, min_periods=length, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / length, min_periods=length, adjust=False).mean()

    rs = avg_gain / avg_loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def macd(
    series: pd.Series,
    fast: int = 12,
    slow: int = 26,
    signal_period: int = 9,
) -> pd.DataFrame:
    """MACD — returns DataFrame with columns: MACD, Signal, Histogram."""
    ema_fast = series.ewm(span=fast, adjust=False).mean()
    ema_slow = series.ewm(span=slow, adjust=False).mean()
    macd_line = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=signal_period, adjust=False).mean()
    histogram = macd_line - signal_line
    return pd.DataFrame({
        "MACD": macd_line,
        "Signal": signal_line,
        "Histogram": histogram,
    }, index=series.index)


def bbands(
    series: pd.Series,
    length: int = 20,
    std: float = 2.0,
) -> pd.DataFrame:
    """Bollinger Bands — returns DataFrame with BBL, BBM, BBU."""
    mid = series.rolling(window=length).mean()
    rolling_std = series.rolling(window=length).std()
    upper = mid + std * rolling_std
    lower = mid - std * rolling_std
    return pd.DataFrame({
        f"BBL_{length}_{std}": lower,
        f"BBM_{length}_{std}": mid,
        f"BBU_{length}_{std}": upper,
    }, index=series.index)


def atr(
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    length: int = 14,
) -> pd.Series:
    """Average True Range."""
    prev_close = close.shift(1)
    tr1 = high - low
    tr2 = (high - prev_close).abs()
    tr3 = (low - prev_close).abs()
    true_range = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    return true_range.ewm(alpha=1 / length, min_periods=length, adjust=False).mean()


def adx(
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    length: int = 14,
) -> pd.DataFrame:
    """Average Directional Index — returns DataFrame with ADX, +DI, -DI."""
    prev_high = high.shift(1)
    prev_low = low.shift(1)
    prev_close = close.shift(1)

    plus_dm = (high - prev_high).clip(lower=0)
    minus_dm = (prev_low - low).clip(lower=0)

    # Only keep the larger DM
    mask = plus_dm > minus_dm
    plus_dm = plus_dm.where(mask, 0)
    minus_dm = minus_dm.where(~mask, 0)

    # True range
    tr1 = high - low
    tr2 = (high - prev_close).abs()
    tr3 = (low - prev_close).abs()
    true_range = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)

    # Smoothed averages
    atr_val = true_range.ewm(alpha=1 / length, min_periods=length, adjust=False).mean()
    plus_di = 100 * (plus_dm.ewm(alpha=1 / length, min_periods=length, adjust=False).mean() / atr_val)
    minus_di = 100 * (minus_dm.ewm(alpha=1 / length, min_periods=length, adjust=False).mean() / atr_val)

    dx = 100 * ((plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan))
    adx_val = dx.ewm(alpha=1 / length, min_periods=length, adjust=False).mean()

    return pd.DataFrame({
        f"ADX_{length}": adx_val,
        f"+DI_{length}": plus_di,
        f"-DI_{length}": minus_di,
    }, index=close.index)

