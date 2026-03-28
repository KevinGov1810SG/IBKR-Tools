"""
Database models — SQLAlchemy ORM pour la persistance des trades, signaux et snapshots.
"""

from __future__ import annotations

from sqlalchemy import (
    Column, Integer, String, Float, DateTime, Boolean, Text,
    create_engine, Index,
)
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from config import utc_now


class Base(DeclarativeBase):
    pass


class Trade(Base):
    __tablename__ = "trades"

    id = Column(Integer, primary_key=True, autoincrement=True)
    symbol = Column(String(20), nullable=False, index=True)
    strategy = Column(String(50), nullable=False, index=True)
    direction = Column(String(4), nullable=False)  # BUY / SELL
    quantity = Column(Float, nullable=False)
    entry_price = Column(Float, nullable=True)
    exit_price = Column(Float, nullable=True)
    entry_time = Column(DateTime(timezone=True), default=utc_now)
    exit_time = Column(DateTime, nullable=True)
    pnl = Column(Float, nullable=True)
    commission = Column(Float, default=0.0)
    order_type = Column(String(10), nullable=True)
    status = Column(String(20), default="OPEN")  # OPEN / CLOSED / CANCELLED
    ibkr_order_id = Column(Integer, nullable=True)
    notes = Column(Text, nullable=True)

    __table_args__ = (
        Index("ix_trades_status_strategy", "status", "strategy"),
    )


class Signal(Base):
    __tablename__ = "signals"

    id = Column(Integer, primary_key=True, autoincrement=True)
    timestamp = Column(DateTime(timezone=True), default=utc_now)
    symbol = Column(String(20), nullable=False, index=True)
    strategy = Column(String(50), nullable=False)
    direction = Column(String(4), nullable=False)
    strength = Column(Float, nullable=True)
    approved = Column(Boolean, default=False)
    reason = Column(Text, nullable=True)


class PerformanceSnapshot(Base):
    __tablename__ = "performance_snapshots"

    id = Column(Integer, primary_key=True, autoincrement=True)
    timestamp = Column(DateTime(timezone=True), default=utc_now)
    total_pnl = Column(Float, default=0.0)
    daily_pnl = Column(Float, default=0.0)
    sharpe_ratio = Column(Float, nullable=True)
    max_drawdown = Column(Float, nullable=True)
    win_rate = Column(Float, nullable=True)
    total_trades = Column(Integer, default=0)
    open_positions = Column(Integer, default=0)
    capital = Column(Float, nullable=True)


def create_db_engine(url: str = "sqlite:///trading.db"):
    engine = create_engine(url, echo=False, future=True)
    Base.metadata.create_all(engine)
    return engine


def create_session(engine) -> Session:
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    return factory()

