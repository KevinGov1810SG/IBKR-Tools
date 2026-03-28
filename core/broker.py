"""
core/broker.py — Connexion IBKR, gestion des ordres et flux de données.
Encapsule l'API ibapi dans une interface asynchrone propre.
"""

from __future__ import annotations

import asyncio
import threading
from typing import Callable, Dict, List, Optional

from ibapi.client import EClient
from ibapi.wrapper import EWrapper
from ibapi.contract import Contract
from ibapi.order import Order
from ibapi.order_cancel import OrderCancel
from ibapi.common import BarData, TickerId
from loguru import logger

from config import AppConfig, AssetConfig, get_config


class IBKRWrapper(EWrapper):
    """Réceptions des callbacks IBKR."""

    def __init__(self):
        super().__init__()
        self.next_order_id: Optional[int] = None
        self._historical_data: Dict[int, list] = {}
        self._historical_done: Dict[int, asyncio.Event] = {}
        self._tick_data: Dict[int, dict] = {}
        self._order_status_callbacks: Dict[int, Callable] = {}
        self._position_callbacks: List[Callable] = []
        self._account_values: Dict[str, str] = {}
        self._connected_event: Optional[asyncio.Event] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None

    # -- Connection ------------------------------------------------------------
    def nextValidId(self, orderId: int):
        self.next_order_id = orderId
        logger.info(f"Connected to IBKR — next valid order ID: {orderId}")
        if self._connected_event and self._loop:
            self._loop.call_soon_threadsafe(self._connected_event.set)

    def error(self, reqId, errorCode, errorString, advancedOrderRejectJson=""):
        # Codes purement informatifs (connexion, farm data, etc.)
        _info_codes = (2104, 2106, 2158, 2107)
        # Avertissements non bloquants (données delayed, abo manquant, taille fractionnaire, AGGTRADES hint)
        _notice_codes = (10167, 10089, 10285, 10299)

        if errorCode in _info_codes:
            logger.debug(f"IBKR info {errorCode}: {errorString}")
        elif errorCode in _notice_codes:
            logger.info(f"IBKR notice reqId={reqId} code={errorCode}: {errorString}")
        else:
            logger.warning(f"IBKR error reqId={reqId} code={errorCode}: {errorString}")
            # Vraie erreur → débloquer l'event historique s'il y en a un en attente
            # pour éviter un timeout inutile de 30 s.
            if reqId in self._historical_done and self._loop:
                self._loop.call_soon_threadsafe(self._historical_done[reqId].set)

    # -- Historical data -------------------------------------------------------
    def historicalData(self, reqId: int, bar: BarData):
        if reqId not in self._historical_data:
            self._historical_data[reqId] = []
        self._historical_data[reqId].append({
            "date": bar.date,
            "open": bar.open,
            "high": bar.high,
            "low": bar.low,
            "close": bar.close,
            "volume": int(bar.volume) if bar.volume else 0,
        })

    def historicalDataEnd(self, reqId: int, start: str, end: str):
        logger.debug(f"Historical data complete for reqId={reqId}")
        if reqId in self._historical_done and self._loop:
            self._loop.call_soon_threadsafe(self._historical_done[reqId].set)

    # -- Tick (real-time) ------------------------------------------------------
    def tickPrice(self, reqId: TickerId, tickType, price: float, attrib):
        if reqId not in self._tick_data:
            self._tick_data[reqId] = {}
        self._tick_data[reqId][tickType] = price

    def tickSize(self, reqId: TickerId, tickType, size):
        if reqId not in self._tick_data:
            self._tick_data[reqId] = {}
        self._tick_data[reqId][f"size_{tickType}"] = int(size)

    # -- Orders ----------------------------------------------------------------
    def orderStatus(self, orderId, status, filled, remaining,
                    avgFillPrice, permId, parentId, lastFillPrice,
                    clientId, whyHeld, mktCapPrice=0.0):
        logger.info(
            f"Order {orderId} status={status} filled={filled} "
            f"avgPrice={avgFillPrice} remaining={remaining}"
        )
        cb = self._order_status_callbacks.get(orderId)
        if cb:
            cb(orderId, status, filled, avgFillPrice)

    def openOrder(self, orderId, contract, order, orderState):
        logger.debug(f"Open order {orderId}: {contract.symbol} {order.action} {order.totalQuantity}")

    # -- Account / Portfolio ---------------------------------------------------
    def updateAccountValue(self, key, val, currency, accountName):
        self._account_values[key] = val

    def updatePortfolio(self, contract, position, marketPrice, marketValue,
                        averageCost, unrealizedPNL, realizedPNL, accountName):
        for cb in self._position_callbacks:
            cb(contract.symbol, position, marketPrice, marketValue,
               averageCost, unrealizedPNL, realizedPNL)

    def accountDownloadEnd(self, accountName):
        logger.debug(f"Account download complete: {accountName}")


class IBKRBroker:
    """High-level broker façade wrapping EClient+EWrapper."""

    def __init__(self, config: Optional[AppConfig] = None):
        self.cfg = config or get_config()
        self._wrapper = IBKRWrapper()
        self._client = EClient(self._wrapper)
        self._req_id_counter = 1000
        self._thread: Optional[threading.Thread] = None

    # -- Helpers ---------------------------------------------------------------
    def _next_req_id(self) -> int:
        self._req_id_counter += 1
        return self._req_id_counter

    def _next_order_id(self) -> int:
        oid = self._wrapper.next_order_id
        self._wrapper.next_order_id += 1
        return oid

    @staticmethod
    def make_contract(asset: AssetConfig) -> Contract:
        c = Contract()
        c.symbol = asset.symbol
        c.secType = asset.sec_type
        c.exchange = asset.exchange
        c.currency = asset.currency
        if asset.primary_exchange:
            c.primaryExchange = asset.primary_exchange
        if asset.multiplier:
            c.multiplier = asset.multiplier
        if asset.last_trade_date:
            c.lastTradeDateOrContractMonth = asset.last_trade_date
        return c

    # -- Connection ------------------------------------------------------------
    async def connect(self) -> None:
        loop = asyncio.get_running_loop()
        self._wrapper._loop = loop
        self._wrapper._connected_event = asyncio.Event()

        self._client.connect(self.cfg.ibkr_host, self.cfg.ibkr_port, self.cfg.ibkr_client_id)
        self._thread = threading.Thread(target=self._client.run, daemon=True)
        self._thread.start()

        try:
            await asyncio.wait_for(self._wrapper._connected_event.wait(), timeout=10)
        except asyncio.TimeoutError:
            raise ConnectionError("Could not connect to IBKR Gateway within 10 s")

        # Basculer sur le type de données de marché configuré pour éviter
        # l'erreur 10089 si aucun abonnement temps-réel n'est souscrit.
        # Type 1 = live, 2 = frozen, 3 = delayed, 4 = delayed-frozen
        mdt = self.cfg.market_data_type
        self._client.reqMarketDataType(mdt)
        logger.info(f"Market data type set to {mdt} (1=live, 3=delayed)")

        logger.info("IBKR Broker connected successfully")

    def disconnect(self) -> None:
        self._client.disconnect()
        logger.info("IBKR Broker disconnected")

    @property
    def is_connected(self) -> bool:
        return self._client.isConnected()

    # -- Historical data -------------------------------------------------------
    async def get_historical_data(
        self,
        asset: AssetConfig,
        duration: str = "2 D",
        bar_size: str = "5 mins",
        what_to_show: str = "TRADES",
    ) -> list:
        req_id = self._next_req_id()
        event = asyncio.Event()
        self._wrapper._historical_data[req_id] = []
        self._wrapper._historical_done[req_id] = event

        contract = self.make_contract(asset)
        self._client.reqHistoricalData(
            req_id, contract, "", duration, bar_size, what_to_show, 1, 1, False, []
        )

        try:
            await asyncio.wait_for(event.wait(), timeout=30)
        except asyncio.TimeoutError:
            logger.error(f"Timeout fetching historical data for {asset.symbol}")
            return []

        bars = self._wrapper._historical_data.pop(req_id, [])
        self._wrapper._historical_done.pop(req_id, None)
        return bars

    # -- Real-time ticks -------------------------------------------------------
    def subscribe_market_data(self, asset: AssetConfig) -> int:
        req_id = self._next_req_id()
        contract = self.make_contract(asset)
        self._client.reqMktData(req_id, contract, "", False, False, [])
        return req_id

    def unsubscribe_market_data(self, req_id: int) -> None:
        self._client.cancelMktData(req_id)

    def get_tick_data(self, req_id: int) -> dict:
        return self._wrapper._tick_data.get(req_id, {})

    # -- Orders ----------------------------------------------------------------
    def place_market_order(self, asset: AssetConfig, action: str, quantity: float,
                           callback: Optional[Callable] = None) -> int:
        order_id = self._next_order_id()
        order = Order()
        order.action = action
        order.orderType = "MKT"
        order.totalQuantity = quantity
        if callback:
            self._wrapper._order_status_callbacks[order_id] = callback
        self._client.placeOrder(order_id, self.make_contract(asset), order)
        logger.info(f"Placed MARKET order #{order_id}: {action} {quantity} {asset.symbol}")
        return order_id

    def place_limit_order(self, asset: AssetConfig, action: str, quantity: float,
                          limit_price: float, callback: Optional[Callable] = None) -> int:
        order_id = self._next_order_id()
        order = Order()
        order.action = action
        order.orderType = "LMT"
        order.totalQuantity = quantity
        order.lmtPrice = limit_price
        if callback:
            self._wrapper._order_status_callbacks[order_id] = callback
        self._client.placeOrder(order_id, self.make_contract(asset), order)
        logger.info(f"Placed LIMIT order #{order_id}: {action} {quantity} {asset.symbol} @ {limit_price}")
        return order_id

    def place_stop_order(self, asset: AssetConfig, action: str, quantity: float,
                         stop_price: float, callback: Optional[Callable] = None) -> int:
        order_id = self._next_order_id()
        order = Order()
        order.action = action
        order.orderType = "STP"
        order.totalQuantity = quantity
        order.auxPrice = stop_price
        if callback:
            self._wrapper._order_status_callbacks[order_id] = callback
        self._client.placeOrder(order_id, self.make_contract(asset), order)
        logger.info(f"Placed STOP order #{order_id}: {action} {quantity} {asset.symbol} stop@{stop_price}")
        return order_id

    def place_bracket_order(
        self,
        asset: AssetConfig,
        action: str,
        quantity: float,
        entry_price: float,
        take_profit_price: float,
        stop_loss_price: float,
        callback: Optional[Callable] = None,
    ) -> tuple[int, int, int]:
        parent_id = self._next_order_id()
        tp_id = self._next_order_id()
        sl_id = self._next_order_id()

        reverse_action = "SELL" if action == "BUY" else "BUY"
        contract = self.make_contract(asset)

        # Parent — limit entry
        parent = Order()
        parent.orderId = parent_id
        parent.action = action
        parent.orderType = "LMT"
        parent.totalQuantity = quantity
        parent.lmtPrice = entry_price
        parent.transmit = False

        # Take-profit
        tp = Order()
        tp.orderId = tp_id
        tp.action = reverse_action
        tp.orderType = "LMT"
        tp.totalQuantity = quantity
        tp.lmtPrice = take_profit_price
        tp.parentId = parent_id
        tp.transmit = False

        # Stop-loss
        sl = Order()
        sl.orderId = sl_id
        sl.action = reverse_action
        sl.orderType = "STP"
        sl.totalQuantity = quantity
        sl.auxPrice = stop_loss_price
        sl.parentId = parent_id
        sl.transmit = True  # last child transmits the group

        if callback:
            self._wrapper._order_status_callbacks[parent_id] = callback

        self._client.placeOrder(parent_id, contract, parent)
        self._client.placeOrder(tp_id, contract, tp)
        self._client.placeOrder(sl_id, contract, sl)

        logger.info(
            f"Placed BRACKET order: parent={parent_id} tp={tp_id} sl={sl_id} "
            f"{action} {quantity} {asset.symbol}"
        )
        return parent_id, tp_id, sl_id

    def cancel_order(self, order_id: int) -> None:
        self._client.cancelOrder(order_id, OrderCancel())
        logger.info(f"Cancelled order #{order_id}")

    # -- Account ---------------------------------------------------------------
    def request_account_updates(self, callback: Optional[Callable] = None) -> None:
        if callback:
            self._wrapper._position_callbacks.append(callback)
        self._client.reqAccountUpdates(True, "")

    def get_account_value(self, key: str) -> Optional[str]:
        return self._wrapper._account_values.get(key)

