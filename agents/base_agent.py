"""
agents/base_agent.py - Classe abstraite Agent.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict


@dataclass
class Recommendation:
    agent_name: str
    action: str              # e.g. "approve", "block", "adjust", "info"
    details: Dict[str, Any] = field(default_factory=dict)
    score: float = 0.0      # general-purpose numeric score


class BaseAgent(ABC):
    """Interface commune pour tous les agents du systeme."""

    name: str = "base_agent"

    def __init__(self):
        self._active: bool = True

    @property
    def is_active(self) -> bool:
        return self._active

    def activate(self) -> None:
        self._active = True

    def deactivate(self) -> None:
        self._active = False

    @abstractmethod
    async def analyze(self, context: Dict[str, Any]) -> None:
        """Perform analysis based on current context."""
        ...

    @abstractmethod
    def get_recommendation(self) -> Recommendation:
        """Return the latest recommendation produced by analyze()."""
        ...
