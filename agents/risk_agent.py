"""
agents/risk_agent.py — Agent de risque pre-trade.

Intercepte chaque signal et vérifie l'exposition, le drawdown, la corrélation.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from loguru import logger

from agents.base_agent import BaseAgent, Recommendation
from core.risk_manager import RiskManager
from strategies.base_strategy import TradeSignal


class RiskAgent(BaseAgent):
    name = "risk_agent"

    def __init__(self, risk_manager: RiskManager):
        super().__init__()
        self._risk_mgr = risk_manager
        self._last_recommendation: Optional[Recommendation] = None

    async def analyze(self, context: Dict[str, Any]) -> None:
        """
        Context expects:
            - "signal": TradeSignal
            - "price": float — current market price
        """
        signal: Optional[TradeSignal] = context.get("signal")
        price: float = context.get("price", 0.0)

        if signal is None:
            self._last_recommendation = Recommendation(
                agent_name=self.name, action="block",
                details={"reason": "No signal provided"},
            )
            return

        verdict = self._risk_mgr.check_order(
            symbol=signal.symbol,
            quantity=signal.suggested_quantity,
            price=price,
            direction=signal.direction.value,
        )

        if verdict.approved:
            self._last_recommendation = Recommendation(
                agent_name=self.name,
                action="approve",
                score=1.0,
                details={
                    "adjusted_quantity": verdict.adjusted_quantity,
                    "reason": verdict.reason,
                },
            )
            logger.info(
                f"[RiskAgent] APPROVED {signal.symbol} qty={verdict.adjusted_quantity}"
            )
        else:
            self._last_recommendation = Recommendation(
                agent_name=self.name,
                action="block",
                score=0.0,
                details={"reason": verdict.reason},
            )
            logger.warning(f"[RiskAgent] BLOCKED {signal.symbol}: {verdict.reason}")

    def get_recommendation(self) -> Recommendation:
        if self._last_recommendation is None:
            return Recommendation(agent_name=self.name, action="block",
                                  details={"reason": "Not analyzed yet"})
        return self._last_recommendation


