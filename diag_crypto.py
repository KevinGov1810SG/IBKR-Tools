"""
Diagnostic script — test historical data requests for CRYPTO on IBKR.
Logs every callback received to help identify the issue.
Run: python diag_crypto.py
"""

import time
import threading
from ibapi.client import EClient
from ibapi.wrapper import EWrapper
from ibapi.contract import Contract
from ibapi.common import BarData


class DiagWrapper(EWrapper):
    def __init__(self):
        super().__init__()
        self.done = False
        self.bars_received = 0

    def nextValidId(self, orderId):
        print(f"[OK] Connected — nextValidId={orderId}")

    def error(self, reqId, errorCode, errorString, advancedOrderRejectJson=""):
        print(f"[ERROR] reqId={reqId}  code={errorCode}  msg={errorString}")

    def historicalData(self, reqId, bar: BarData):
        self.bars_received += 1
        print(f"[BAR] reqId={reqId}  date={bar.date}  O={bar.open} H={bar.high} "
              f"L={bar.low} C={bar.close} V={bar.volume}")

    def historicalDataEnd(self, reqId, start, end):
        print(f"[DONE] reqId={reqId}  start={start}  end={end}  total_bars={self.bars_received}")
        self.done = True


def make_crypto_contract(symbol: str) -> Contract:
    c = Contract()
    c.symbol = symbol
    c.secType = "CRYPTO"
    c.exchange = "PAXOS"
    c.currency = "USD"
    return c


def main():
    wrapper = DiagWrapper()
    client = EClient(wrapper)

    print("Connecting to IBKR on 127.0.0.1:7497 ...")
    client.connect("127.0.0.1", 7497, clientId=99)
    thread = threading.Thread(target=client.run, daemon=True)
    thread.start()
    time.sleep(2)

    # Set delayed market data (type 3)
    print("\n--- Setting market data type to DELAYED (3) ---")
    client.reqMarketDataType(3)
    time.sleep(0.5)

    # Try multiple whatToShow values for BTC
    tests = [
        ("MIDPOINT", "1 D", "1 hour"),
        ("TRADES",   "1 D", "1 hour"),
        ("BID",      "1 D", "1 hour"),
        ("ASK",      "1 D", "1 hour"),
        ("BID_ASK",  "1 D", "1 hour"),
        ("AGGTRADES","1 D", "1 hour"),
    ]

    req_id = 5000
    for what_to_show, duration, bar_size in tests:
        req_id += 1
        wrapper.bars_received = 0
        wrapper.done = False

        print(f"\n{'='*60}")
        print(f"TEST reqId={req_id}: BTC CRYPTO/PAXOS  whatToShow={what_to_show}  "
              f"duration={duration}  barSize={bar_size}")
        print(f"{'='*60}")

        contract = make_crypto_contract("BTC")
        client.reqHistoricalData(
            req_id, contract, "", duration, bar_size, what_to_show, 1, 1, False, []
        )

        # Wait up to 15s for response
        for _ in range(30):
            time.sleep(0.5)
            if wrapper.done:
                break
        else:
            print(f"[TIMEOUT] No historicalDataEnd received for whatToShow={what_to_show}")

        time.sleep(1)  # rate limit between requests

    print("\n\nDone. Disconnecting.")
    client.disconnect()


if __name__ == "__main__":
    main()

