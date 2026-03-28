"""
execution/position_tracker.py — Suivi des positions ouvertes et P&L en temps réel.
"""

from __future__ import annotations

import datetime
from dataclasses import dataclass
from typing import Dict, List, Optional

from loguru import logger

from core.data_feed import DataFeed
from core.portfolio import Portfolio
from database.repository import TradeRepository
from strategies.base_strategy import BaseStrategy
from config import utc_now


@dataclass
class TrackedPosition:
    trade_id: int
    symbol: str
    strategy_name: str
    direction: str
    quantity: float
    entry_price: float
    entry_time: datetime.datetime
    target_price: Optional[float] = None
    stop_loss: Optional[float] = None
    current_price: float = 0.0
    unrealized_pnl: float = 0.0


class PositionTracker:
    """Monitors open positions and triggers exits."""

    def __init__(
        self,
        portfolio: Portfolio,
        data_feed: DataFeed,
        trade_repo: TradeRepository,
    ):
        self._portfolio = portfolio
        self._data_feed = data_feed
        self._repo = trade_repo
        self._tracked: Dict[str, TrackedPosition] = {}

    @property
    def tracked_positions(self) -> Dict[str, TrackedPosition]:
        return dict(self._tracked)

    def register_position(
        self,
        trade_id: int,
        symbol: str,
        strategy_name: str,
        direction: str,
        quantity: float,
        entry_price: float,
        target_price: Optional[float] = None,
        stop_loss: Optional[float] = None,
    ) -> None:
        self._tracked[symbol] = TrackedPosition(
            trade_id=trade_id,
            symbol=symbol,
            strategy_name=strategy_name,
            direction=direction,
            quantity=quantity,
            entry_price=entry_price,
            entry_time=utc_now(),
            target_price=target_price,
            stop_loss=stop_loss,
        )
        logger.debug(f"Tracking position: {symbol} {direction} qty={quantity}")

    def update_prices(self) -> None:
        """Update current prices for all tracked positions from data feed."""
        for symbol, pos in self._tracked.items():
            price = self._data_feed.get_latest_price(symbol)
            if price is not None and price > 0:
                pos.current_price = price
                if pos.direction == "BUY":
                    pos.unrealized_pnl = (price - pos.entry_price) * pos.quantity
                else:
                    pos.unrealized_pnl = (pos.entry_price - price) * pos.quantity

    def check_exits(
        self,
        strategies: Dict[str, BaseStrategy],
    ) -> List[TrackedPosition]:
        """Check if any tracked positions should be closed."""
        to_exit: List[TrackedPosition] = []
        for symbol, pos in list(self._tracked.items()):
            strat = strategies.get(pos.strategy_name)
            if strat is None:
                continue

            data = self._data_feed.get_cached(symbol)
            if data is None or data.empty:
                continue

            if strat.should_exit(
                symbol=symbol,
                data=data,
                entry_price=pos.entry_price,
                current_price=pos.current_price,
                direction=pos.direction,
                target_price=pos.target_price,
                stop_loss=pos.stop_loss,
            ):
                to_exit.append(pos)

        return to_exit

    def close_position(self, symbol: str, exit_price: float) -> Optional[float]:
        """Remove from tracking and update the DB."""
        pos = self._tracked.pop(symbol, None)
        if pos is None:
            return None

        if pos.direction == "BUY":
            pnl = (exit_price - pos.entry_price) * pos.quantity
        else:
            pnl = (pos.entry_price - exit_price) * pos.quantity

        self._repo.close_trade(pos.trade_id, exit_price=exit_price, pnl=pnl)
        self._portfolio.remove_position(symbol, exit_price)
        logger.info(f"Closed position {symbol} pnl={pnl:.2f}")
        return pnl

    def get_position(self, symbol: str) -> Optional[TrackedPosition]:
        return self._tracked.get(symbol)

