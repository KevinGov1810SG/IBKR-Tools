"""
core/risk_manager.py — Règles de risk management globales.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Tuple

from loguru import logger

from config import RiskConfig, get_config
from core.portfolio import Portfolio
from core.data_feed import DataFeed


@dataclass
class RiskVerdict:
    approved: bool
    adjusted_quantity: float
    reason: str = ""


class RiskManager:
    """Centralised risk gate applied before every order."""

    def __init__(self, portfolio: Portfolio, data_feed: DataFeed, config: Optional[RiskConfig] = None):
        self._portfolio = portfolio
        self._data_feed = data_feed
        self._cfg = config or get_config().risk
        self._halted: bool = False

    # -- Public API ------------------------------------------------------------
    @property
    def is_halted(self) -> bool:
        return self._halted

    def check_order(
        self,
        symbol: str,
        quantity: float,
        price: float,
        direction: str,
    ) -> RiskVerdict:
        """Run all risk checks and return a verdict."""
        if self._halted:
            return RiskVerdict(False, 0, "Trading halted — daily drawdown limit breached")

        # 1. Daily drawdown
        if self._check_daily_drawdown():
            self._halted = True
            return RiskVerdict(False, 0, "Daily drawdown limit breached — trading halted")

        # 2. Max simultaneous positions
        if not self._portfolio.has_position(symbol):
            if self._portfolio.open_position_count >= self._cfg.max_simultaneous_positions:
                return RiskVerdict(
                    False, 0,
                    f"Max positions reached ({self._cfg.max_simultaneous_positions})"
                )

        # 3. Per-asset exposure
        adjusted_qty = self._adjust_for_exposure(symbol, quantity, price)
        if adjusted_qty <= 0:
            return RiskVerdict(False, 0, f"Exposure limit would be breached for {symbol}")

        # 4. Correlation
        if not self._portfolio.has_position(symbol):
            corr_ok, corr_msg = self._check_correlation(symbol)
            if not corr_ok:
                return RiskVerdict(False, 0, corr_msg)

        return RiskVerdict(True, adjusted_qty, "Approved")

    def reset_daily(self) -> None:
        self._halted = False
        logger.info("Risk manager daily state reset")

    # -- Private checks --------------------------------------------------------
    def _check_daily_drawdown(self) -> bool:
        dd_pct = abs(self._portfolio.daily_pnl_pct)
        limit = self._cfg.max_daily_drawdown_pct
        if dd_pct >= limit and self._portfolio.daily_pnl_pct < 0:
            logger.error(f"Daily drawdown {dd_pct:.2f}% exceeds limit {limit}%")
            return True
        return False

    def _adjust_for_exposure(self, symbol: str, quantity: float, price: float) -> float:
        equity = self._portfolio.total_equity
        if equity <= 0:
            return 0
        max_value = equity * (self._cfg.max_exposure_per_asset_pct / 100.0)
        current_value = 0.0
        pos = self._portfolio.get_position(symbol)
        if pos:
            current_value = abs(pos.market_value)
        remaining = max_value - current_value
        if remaining <= 0:
            return 0
        max_qty = remaining / price if price > 0 else 0
        return min(quantity, max_qty)

    def _check_correlation(self, symbol: str) -> Tuple[bool, str]:
        """Check if new position is highly correlated with existing ones."""
        new_data = self._data_feed.get_cached(symbol)
        if new_data is None or new_data.empty:
            return True, ""

        new_returns = DataFeed.compute_returns(new_data)

        for existing_symbol in self._portfolio.positions:
            existing_data = self._data_feed.get_cached(existing_symbol)
            if existing_data is None or existing_data.empty:
                continue
            existing_returns = DataFeed.compute_returns(existing_data)
            corr = DataFeed.compute_correlation(new_returns, existing_returns)
            if abs(corr) > self._cfg.max_correlation_threshold:
                msg = (
                    f"High correlation ({corr:.2f}) between {symbol} and "
                    f"{existing_symbol} — order blocked"
                )
                logger.warning(msg)
                return False, msg

        return True, ""

