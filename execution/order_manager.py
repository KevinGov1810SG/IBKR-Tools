"""
execution/order_manager.py — Construction et envoi des ordres.
"""

from __future__ import annotations

from typing import Dict, Optional

from loguru import logger

from config import AssetConfig
from core.broker import IBKRBroker
from database.repository import TradeRepository


class OrderManager:
    """High-level order abstraction: builds, sends and tracks orders."""

    def __init__(self, broker: IBKRBroker, trade_repo: TradeRepository):
        self._broker = broker
        self._repo = trade_repo
        self._pending_orders: Dict[int, dict] = {}

    # -- Public API ------------------------------------------------------------
    def send_market_order(
        self,
        asset: AssetConfig,
        action: str,
        quantity: float,
        strategy: str = "",
    ) -> int:
        order_id = self._broker.place_market_order(
            asset, action, quantity, callback=self._on_order_status,
        )
        self._pending_orders[order_id] = {
            "symbol": asset.symbol, "action": action, "quantity": quantity,
            "strategy": strategy, "order_type": "MKT",
        }
        self._repo.open_trade(
            symbol=asset.symbol, strategy=strategy, direction=action,
            quantity=quantity, entry_price=0.0, order_type="MKT",
            ibkr_order_id=order_id,
        )
        return order_id

    def send_limit_order(
        self,
        asset: AssetConfig,
        action: str,
        quantity: float,
        limit_price: float,
        strategy: str = "",
    ) -> int:
        order_id = self._broker.place_limit_order(
            asset, action, quantity, limit_price, callback=self._on_order_status,
        )
        self._pending_orders[order_id] = {
            "symbol": asset.symbol, "action": action, "quantity": quantity,
            "strategy": strategy, "order_type": "LMT", "price": limit_price,
        }
        self._repo.open_trade(
            symbol=asset.symbol, strategy=strategy, direction=action,
            quantity=quantity, entry_price=limit_price, order_type="LMT",
            ibkr_order_id=order_id,
        )
        return order_id

    def send_stop_order(
        self,
        asset: AssetConfig,
        action: str,
        quantity: float,
        stop_price: float,
        strategy: str = "",
    ) -> int:
        order_id = self._broker.place_stop_order(
            asset, action, quantity, stop_price, callback=self._on_order_status,
        )
        self._pending_orders[order_id] = {
            "symbol": asset.symbol, "action": action, "quantity": quantity,
            "strategy": strategy, "order_type": "STP", "price": stop_price,
        }
        self._repo.open_trade(
            symbol=asset.symbol, strategy=strategy, direction=action,
            quantity=quantity, entry_price=stop_price, order_type="STP",
            ibkr_order_id=order_id,
        )
        return order_id

    def send_bracket_order(
        self,
        asset: AssetConfig,
        action: str,
        quantity: float,
        entry_price: float,
        take_profit: float,
        stop_loss: float,
        strategy: str = "",
    ) -> tuple[int, int, int]:
        parent_id, tp_id, sl_id = self._broker.place_bracket_order(
            asset, action, quantity, entry_price, take_profit, stop_loss,
            callback=self._on_order_status,
        )
        self._pending_orders[parent_id] = {
            "symbol": asset.symbol, "action": action, "quantity": quantity,
            "strategy": strategy, "order_type": "BRACKET",
            "tp_id": tp_id, "sl_id": sl_id,
        }
        self._repo.open_trade(
            symbol=asset.symbol, strategy=strategy, direction=action,
            quantity=quantity, entry_price=entry_price, order_type="BRACKET",
            ibkr_order_id=parent_id,
            notes=f"TP={take_profit} SL={stop_loss}",
        )
        return parent_id, tp_id, sl_id

    def cancel(self, order_id: int) -> None:
        self._broker.cancel_order(order_id)
        self._pending_orders.pop(order_id, None)

    # -- Callback --------------------------------------------------------------
    def _on_order_status(
        self, order_id: int, status: str, filled: float, avg_fill_price: float
    ) -> None:
        info = self._pending_orders.get(order_id)
        if info is None:
            return

        if status in ("Filled", "Inactive", "Cancelled"):
            logger.info(
                f"Order {order_id} final status={status} filled={filled} "
                f"avg_price={avg_fill_price}"
            )
            # Update DB trade with actual fill price
            trades = self._repo.get_open_trades(strategy=info.get("strategy"))
            for t in trades:
                if t.ibkr_order_id == order_id:
                    if status == "Filled":
                        t.entry_price = avg_fill_price
                        t.status = "OPEN"
                    elif status in ("Cancelled", "Inactive"):
                        t.status = "CANCELLED"
                    self._repo._s.commit()
                    break

            if status != "Filled":
                self._pending_orders.pop(order_id, None)

