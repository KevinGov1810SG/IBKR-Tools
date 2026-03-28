"""
core/data_feed.py — Récupération et normalisation des données de marché.
"""

from __future__ import annotations

import re
from typing import Dict, List, Optional

import pandas as pd
from loguru import logger

from config import AssetConfig, get_config
from core.broker import IBKRBroker

# Regex pour stripper le suffixe timezone textuel d'IBKR
# ex: "20260325 16:01:00 US/Eastern" → "20260325 16:01:00"
_IBKR_TZ_SUFFIX = re.compile(r"\s+[A-Za-z/_]+$")


def _parse_ibkr_date(date_str: str) -> pd.Timestamp:
    """Parse une date IBKR quelle que soit sa forme :
    - "20260325 16:01:00 US/Eastern"  → strip le TZ, parse comme naive UTC
    - "20260325  16:01:00"            → parse directement
    - "20260325"                      → date journalière
    """
    cleaned = _IBKR_TZ_SUFFIX.sub("", str(date_str).strip())
    return pd.Timestamp(cleaned)


class DataFeed:
    """Gère la collecte, le cache et la normalisation des données de marché."""

    def __init__(self, broker: IBKRBroker):
        self._broker = broker
        self._cfg = get_config()
        self._cache: Dict[str, pd.DataFrame] = {}
        self._subscriptions: Dict[str, int] = {}  # symbol -> reqId

    # -- Historical bars -------------------------------------------------------
    async def fetch_historical(
        self,
        asset: AssetConfig,
        duration: Optional[str] = None,
        bar_size: Optional[str] = None,
        what_to_show: str = "TRADES",
    ) -> pd.DataFrame:
        duration = duration or self._cfg.historical_duration
        bar_size = bar_size or self._cfg.data_bar_size

        if asset.sec_type == "CASH":
            what_to_show = "MIDPOINT"
        elif asset.sec_type == "CRYPTO":
            what_to_show = "AGGTRADES"

        bars = await self._broker.get_historical_data(asset, duration, bar_size, what_to_show)
        if not bars:
            logger.warning(f"No historical data for {asset.symbol}")
            return pd.DataFrame()

        df = pd.DataFrame(bars)
        df["date"] = df["date"].apply(_parse_ibkr_date)
        df.set_index("date", inplace=True)
        df = df.astype({"open": float, "high": float, "low": float, "close": float, "volume": float})
        df.sort_index(inplace=True)

        self._cache[asset.symbol] = df
        logger.debug(f"Fetched {len(df)} bars for {asset.symbol} ({bar_size})")
        return df

    async def fetch_multi_timeframe(
        self,
        asset: AssetConfig,
        timeframes: List[str],
    ) -> Dict[str, pd.DataFrame]:
        """Fetch multiple timeframes for a single asset."""
        results: Dict[str, pd.DataFrame] = {}
        duration_map = {
            "5 mins": "2 D",
            "15 mins": "5 D",
            "1 hour": "20 D",
            "4 hours": "40 D",
            "1 day": "365 D",
        }
        for tf in timeframes:
            dur = duration_map.get(tf, "5 D")
            df = await self.fetch_historical(asset, duration=dur, bar_size=tf)
            results[tf] = df
        return results

    # -- Real-time subscriptions -----------------------------------------------
    def subscribe(self, asset: AssetConfig) -> None:
        if asset.symbol not in self._subscriptions:
            req_id = self._broker.subscribe_market_data(asset)
            self._subscriptions[asset.symbol] = req_id
            logger.info(f"Subscribed to real-time data for {asset.symbol}")

    def unsubscribe(self, symbol: str) -> None:
        req_id = self._subscriptions.pop(symbol, None)
        if req_id is not None:
            self._broker.unsubscribe_market_data(req_id)

    def get_latest_price(self, symbol: str) -> Optional[float]:
        req_id = self._subscriptions.get(symbol)
        if req_id is None:
            return None
        ticks = self._broker.get_tick_data(req_id)
        # tickType 4 = last price, 1 = bid, 2 = ask
        return ticks.get(4) or ticks.get(2) or ticks.get(1)

    # -- Cache -----------------------------------------------------------------
    def get_cached(self, symbol: str) -> Optional[pd.DataFrame]:
        return self._cache.get(symbol)

    def update_cache(self, symbol: str, df: pd.DataFrame) -> None:
        self._cache[symbol] = df

    # -- Utility ---------------------------------------------------------------
    @staticmethod
    def compute_returns(df: pd.DataFrame, column: str = "close") -> pd.Series:
        return df[column].pct_change().dropna()

    @staticmethod
    def compute_correlation(series_a: pd.Series, series_b: pd.Series) -> float:
        aligned = pd.concat([series_a, series_b], axis=1).dropna()
        if len(aligned) < 5:
            return 0.0
        return float(aligned.iloc[:, 0].corr(aligned.iloc[:, 1]))

