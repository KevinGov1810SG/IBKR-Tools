"""
strategies/base_strategy.py — Classe abstraite pour toutes les stratégies.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

import pandas as pd

from config import SignalDirection


@dataclass
class TradeSignal:
    symbol: str
    direction: SignalDirection
    strength: float = 0.0           # 0..1
    strategy_name: str = ""
    target_price: Optional[float] = None
    stop_loss: Optional[float] = None
    suggested_quantity: float = 0.0
    metadata: Dict[str, Any] = field(default_factory=dict)


class BaseStrategy(ABC):
    """Interface commune pour toutes les stratégies de trading."""

    name: str = "base"

    def __init__(self, params: Any = None):
        self.params = params
        self._active: bool = True

    @property
    def is_active(self) -> bool:
        return self._active

    def activate(self) -> None:
        self._active = True

    def deactivate(self) -> None:
        self._active = False

    # -- Interface methods (must be implemented) --------------------------------
    @abstractmethod
    def generate_signal(self, symbol: str, data: pd.DataFrame,
                        multi_tf: Optional[Dict[str, pd.DataFrame]] = None) -> Optional[TradeSignal]:
        """Analyse les données et retourne un signal d'entrée ou None."""
        ...

    @abstractmethod
    def compute_position_size(
        self, signal: TradeSignal, data: pd.DataFrame,
        capital: float, current_price: float,
    ) -> float:
        """Calcule la taille de la position en unités."""
        ...

    @abstractmethod
    def should_exit(self, symbol: str, data: pd.DataFrame,
                    entry_price: float, current_price: float,
                    **kwargs) -> bool:
        """Retourne True si la position doit être clôturée."""
        ...

    # -- Helpers ---------------------------------------------------------------
    def update_params(self, new_params: Any) -> None:
        self.params = new_params

