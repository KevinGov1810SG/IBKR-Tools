"""
Configuration centralisée du système de trading IBKR.
"""

from __future__ import annotations

import datetime
from enum import Enum
from typing import List, Optional

from pydantic import BaseModel, Field


class MarketRegime(str, Enum):
    TRENDING = "trending"
    RANGING = "ranging"
    VOLATILE = "volatile"


class OrderType(str, Enum):
    MARKET = "MKT"
    LIMIT = "LMT"
    STOP = "STP"
    STOP_LIMIT = "STP LMT"


class SignalDirection(str, Enum):
    LONG = "BUY"
    SHORT = "SELL"
    FLAT = "FLAT"


class AssetConfig(BaseModel):
    symbol: str
    sec_type: str = "STK"
    exchange: str = "SMART"
    currency: str = "USD"
    primary_exchange: Optional[str] = None
    multiplier: Optional[str] = None
    last_trade_date: Optional[str] = None  # YYYYMM or YYYYMMDD — requis pour FUT


def _next_futures_expiry() -> str:
    """Calcule le mois d'expiration du contrat front-month CME (format YYYYMM).

    Les futures CME expirent le 3ᵉ vendredi du mois du contrat.
    On roule au mois suivant si on est à moins de 7 jours de l'expiry.
    """
    today = datetime.date.today()
    year, month = today.year, today.month

    # Trouver le 3ᵉ vendredi du mois courant
    import calendar
    cal = calendar.monthcalendar(year, month)
    fridays = [week[calendar.FRIDAY] for week in cal if week[calendar.FRIDAY] != 0]
    third_friday = fridays[2]
    expiry_date = datetime.date(year, month, third_friday)

    # Si on est à moins de 7 jours de l'expiration, on prend le mois suivant
    if (expiry_date - today).days < 7:
        if month == 12:
            year += 1
            month = 1
        else:
            month += 1

    return f"{year}{month:02d}"


class MomentumParams(BaseModel):
    rsi_period: int = 14
    rsi_entry_threshold: float = 60.0
    rsi_exit_threshold: float = 80.0
    macd_fast: int = 12
    macd_slow: int = 26
    macd_signal: int = 9
    trailing_stop_pct: float = 1.5
    atr_period: int = 14
    atr_risk_factor: float = 1.0
    timeframes: List[str] = Field(default_factory=lambda: ["15 mins", "1 hour", "4 hours"])


class MeanReversionParams(BaseModel):
    bb_period: int = 20
    bb_std_dev: float = 2.0
    volume_ma_period: int = 20
    volume_filter: bool = True


class BreakoutParams(BaseModel):
    lookback_bars: int = 20
    volume_ma_period: int = 20
    volume_spike_ratio: float = 2.0
    reward_risk_ratio: float = 2.0
    max_hold_hours: int = 8
    allowed_hours_utc: List[int] = Field(
        default_factory=lambda: list(range(0, 24))  # 24/7 pour crypto
    )


class RiskConfig(BaseModel):
    max_daily_drawdown_pct: float = 3.0
    max_exposure_per_asset_pct: float = 5.0
    max_simultaneous_positions: int = 5
    max_correlation_threshold: float = 0.75


class OptimizerConfig(BaseModel):
    evaluation_interval_hours: int = 4
    min_trades_for_optimization: int = 10
    param_grid_steps: int = 3


class AppConfig(BaseModel):
    ibkr_host: str = "127.0.0.1"
    ibkr_port: int = 7497
    ibkr_client_id: int = 1
    database_url: str = "sqlite:///trading.db"
    log_level: str = "INFO"
    log_file: str = "trading.log"
    initial_capital: float = 1_000_000.0
    main_loop_interval_sec: float = 5.0
    market_data_type: int = 3  # 1=live, 2=frozen, 3=delayed, 4=delayed-frozen
    data_bar_size: str = "5 mins"
    historical_duration: str = "2 D"
    active_strategies: List[str] = Field(
        default_factory=lambda: ["momentum", "mean_reversion", "breakout"]
    )
    momentum: MomentumParams = Field(default_factory=MomentumParams)
    mean_reversion: MeanReversionParams = Field(default_factory=MeanReversionParams)
    breakout: BreakoutParams = Field(default_factory=BreakoutParams)
    risk: RiskConfig = Field(default_factory=RiskConfig)
    optimizer: OptimizerConfig = Field(default_factory=OptimizerConfig)
    assets: List[AssetConfig] = Field(default_factory=lambda: [
        # Cryptomonnaies via IBKR / PAXOS — marché ouvert 24/7
        AssetConfig(symbol="BTC", sec_type="CRYPTO", exchange="PAXOS", currency="USD"),
        AssetConfig(symbol="ETH", sec_type="CRYPTO", exchange="PAXOS", currency="USD"),
        AssetConfig(symbol="LTC", sec_type="CRYPTO", exchange="PAXOS", currency="USD"),
        AssetConfig(symbol="BCH", sec_type="CRYPTO", exchange="PAXOS", currency="USD"),
    ])


_config: Optional[AppConfig] = None


def get_config() -> AppConfig:
    global _config
    if _config is None:
        _config = AppConfig()
    return _config


def load_config(**overrides) -> AppConfig:
    global _config
    _config = AppConfig(**overrides)
    return _config


def utc_now() -> datetime.datetime:
    """Timezone-aware UTC now (replaces deprecated datetime.utcnow)."""
    return datetime.datetime.now(datetime.timezone.utc)
