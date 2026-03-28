"""
core/portfolio.py — État du portefeuille, positions ouvertes, cash disponible.
"""

from __future__ import annotations

import datetime
from dataclasses import dataclass
from typing import Dict, Optional

from loguru import logger

from config import get_config
from config import utc_now


@dataclass
class Position:
    symbol: str
    quantity: float
    avg_cost: float
    market_price: float = 0.0
    market_value: float = 0.0
    unrealized_pnl: float = 0.0
    realized_pnl: float = 0.0
    strategy: str = ""
    entry_time: Optional[datetime.datetime] = None


class Portfolio:
    """Tracks portfolio state in-memory, updated from broker callbacks and local trades."""

    def __init__(self):
        self._cfg = get_config()
        self._positions: Dict[str, Position] = {}
        self._cash: float = self._cfg.initial_capital
        self._daily_starting_capital: float = self._cfg.initial_capital

    # -- Positions -------------------------------------------------------------
    @property
    def positions(self) -> Dict[str, Position]:
        return dict(self._positions)

    @property
    def open_position_count(self) -> int:
        return len(self._positions)

    def get_position(self, symbol: str) -> Optional[Position]:
        return self._positions.get(symbol)

    def has_position(self, symbol: str) -> bool:
        return symbol in self._positions

    def update_position(
        self,
        symbol: str,
        quantity: float,
        market_price: float,
        market_value: float,
        avg_cost: float,
        unrealized_pnl: float,
        realized_pnl: float,
    ) -> None:
        if abs(quantity) < 1e-9:
            self._positions.pop(symbol, None)
            return
        if symbol in self._positions:
            pos = self._positions[symbol]
            pos.quantity = quantity
            pos.market_price = market_price
            pos.market_value = market_value
            pos.avg_cost = avg_cost
            pos.unrealized_pnl = unrealized_pnl
            pos.realized_pnl = realized_pnl
        else:
            self._positions[symbol] = Position(
                symbol=symbol,
                quantity=quantity,
                avg_cost=avg_cost,
                market_price=market_price,
                market_value=market_value,
                unrealized_pnl=unrealized_pnl,
                realized_pnl=realized_pnl,
                entry_time=utc_now(),
            )

    def add_position(self, symbol: str, quantity: float, price: float, strategy: str = "") -> None:
        self._positions[symbol] = Position(
            symbol=symbol,
            quantity=quantity,
            avg_cost=price,
            market_price=price,
            market_value=quantity * price,
            strategy=strategy,
            entry_time=utc_now(),
        )
        self._cash -= quantity * price
        logger.info(f"Portfolio: added position {symbol} qty={quantity} @ {price}")

    def remove_position(self, symbol: str, exit_price: float) -> float:
        pos = self._positions.pop(symbol, None)
        if pos is None:
            return 0.0
        pnl = (exit_price - pos.avg_cost) * pos.quantity
        self._cash += pos.quantity * exit_price
        logger.info(f"Portfolio: removed position {symbol} pnl={pnl:.2f}")
        return pnl

    # -- Cash & Capital --------------------------------------------------------
    @property
    def cash(self) -> float:
        return self._cash

    @cash.setter
    def cash(self, value: float):
        self._cash = value

    @property
    def total_equity(self) -> float:
        positions_value = sum(p.market_value for p in self._positions.values())
        return self._cash + positions_value

    @property
    def total_unrealized_pnl(self) -> float:
        return sum(p.unrealized_pnl for p in self._positions.values())

    @property
    def daily_pnl_pct(self) -> float:
        if self._daily_starting_capital == 0:
            return 0.0
        return ((self.total_equity - self._daily_starting_capital)
                / self._daily_starting_capital * 100)

    def reset_daily_capital(self) -> None:
        self._daily_starting_capital = self.total_equity
        logger.info(f"Daily capital reset to {self._daily_starting_capital:.2f}")

    # -- Exposure --------------------------------------------------------------
    def exposure_pct(self, symbol: str) -> float:
        pos = self._positions.get(symbol)
        if pos is None or self.total_equity == 0:
            return 0.0
        return abs(pos.market_value) / self.total_equity * 100

    def total_exposure_pct(self) -> float:
        if self.total_equity == 0:
            return 0.0
        total_val = sum(abs(p.market_value) for p in self._positions.values())
        return total_val / self.total_equity * 100

    # -- Broker callback adapter -----------------------------------------------
    def on_portfolio_update(
        self,
        symbol: str,
        position: float,
        market_price: float,
        market_value: float,
        avg_cost: float,
        unrealized_pnl: float,
        realized_pnl: float,
    ) -> None:
        """Callback for IBKRBroker.request_account_updates."""
        self.update_position(
            symbol, position, market_price, market_value,
            avg_cost, unrealized_pnl, realized_pnl,
        )

