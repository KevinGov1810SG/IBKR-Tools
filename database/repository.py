"""
Database repository — Accès aux données persistées.
"""

from __future__ import annotations

import datetime
from typing import List, Optional

from sqlalchemy import select, func
from sqlalchemy.orm import Session

from database.models import Trade, Signal, PerformanceSnapshot
from config import utc_now


class TradeRepository:
    """CRUD operations for trades."""

    def __init__(self, session: Session):
        self._s = session

    # -- Create ----------------------------------------------------------------
    def open_trade(
        self,
        symbol: str,
        strategy: str,
        direction: str,
        quantity: float,
        entry_price: float,
        order_type: str = "MKT",
        ibkr_order_id: Optional[int] = None,
        notes: Optional[str] = None,
    ) -> Trade:
        trade = Trade(
            symbol=symbol,
            strategy=strategy,
            direction=direction,
            quantity=quantity,
            entry_price=entry_price,
            order_type=order_type,
            ibkr_order_id=ibkr_order_id,
            notes=notes,
            status="OPEN",
            entry_time=utc_now(),
        )
        self._s.add(trade)
        self._s.commit()
        return trade

    def close_trade(self, trade_id: int, exit_price: float, pnl: float, commission: float = 0.0) -> Trade:
        trade = self._s.get(Trade, trade_id)
        if trade is None:
            raise ValueError(f"Trade {trade_id} not found")
        trade.exit_price = exit_price
        trade.exit_time = utc_now()
        trade.pnl = pnl
        trade.commission = commission
        trade.status = "CLOSED"
        self._s.commit()
        return trade

    # -- Read ------------------------------------------------------------------
    def get_open_trades(self, strategy: Optional[str] = None) -> List[Trade]:
        stmt = select(Trade).where(Trade.status == "OPEN")
        if strategy:
            stmt = stmt.where(Trade.strategy == strategy)
        return list(self._s.execute(stmt).scalars().all())

    def get_closed_trades(self, since: Optional[datetime.datetime] = None) -> List[Trade]:
        stmt = select(Trade).where(Trade.status == "CLOSED")
        if since:
            stmt = stmt.where(Trade.exit_time >= since)
        return list(self._s.execute(stmt).scalars().all())

    def get_trades_by_symbol(self, symbol: str) -> List[Trade]:
        return list(
            self._s.execute(select(Trade).where(Trade.symbol == symbol)).scalars().all()
        )

    def get_daily_pnl(self) -> float:
        today = utc_now().replace(hour=0, minute=0, second=0, microsecond=0)
        result = self._s.execute(
            select(func.coalesce(func.sum(Trade.pnl), 0.0)).where(
                Trade.status == "CLOSED", Trade.exit_time >= today
            )
        ).scalar()
        return float(result)

    def count_open_positions(self) -> int:
        return self._s.execute(
            select(func.count()).select_from(Trade).where(Trade.status == "OPEN")
        ).scalar() or 0


class SignalRepository:
    def __init__(self, session: Session):
        self._s = session

    def save_signal(self, symbol: str, strategy: str, direction: str,
                    strength: float = 0.0, approved: bool = False,
                    reason: str = "") -> Signal:
        sig = Signal(
            symbol=symbol, strategy=strategy, direction=direction,
            strength=strength, approved=approved, reason=reason,
        )
        self._s.add(sig)
        self._s.commit()
        return sig

    def get_recent_signals(self, limit: int = 50) -> List[Signal]:
        return list(
            self._s.execute(
                select(Signal).order_by(Signal.timestamp.desc()).limit(limit)
            ).scalars().all()
        )


class PerformanceRepository:
    def __init__(self, session: Session):
        self._s = session

    def save_snapshot(self, **kwargs) -> PerformanceSnapshot:
        snap = PerformanceSnapshot(**kwargs)
        self._s.add(snap)
        self._s.commit()
        return snap

    def get_latest(self) -> Optional[PerformanceSnapshot]:
        return self._s.execute(
            select(PerformanceSnapshot).order_by(PerformanceSnapshot.timestamp.desc()).limit(1)
        ).scalar()

