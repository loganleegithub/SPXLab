"""Read-only IB market evidence collector. Outbound protocol is allowlisted."""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import math
import signal
import time
import uuid
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from .events import EventStore, atomic_json, utc_now
from .market import MarketState

# IB message IDs: market subscribe/cancel, contract details, server time,
# market data type, API handshake. Orders/account mutations cannot pass.
ALLOWED_MESSAGES = frozenset({1, 2, 9, 49, 59, 71})


def guarded_send(send):
    def safe(*fields, **kwargs):
        if not fields or fields[0] not in ALLOWED_MESSAGES:
            raise PermissionError(f"Read-only protocol blocked message {fields[0] if fields else None}")
        return send(*fields, **kwargs)
    return safe


class Collector:
    def __init__(self, directory, *, client_id=27151, port=4001, observer=None, before_event=None):
        from ib_async import IB
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.store = EventStore(self.directory / "events.sqlite")
        self.run_id = str(uuid.uuid4())
        self.generation = 0
        self.ib = IB()
        self.ib.client.send = guarded_send(self.ib.client.send)
        self.client_id, self.port = client_id, port
        self.state = MarketState()
        self.observer = observer
        self.before_event = before_event
        self.tickers = []
        self.count = 0
        self.stopping = False
        self._hooks()

    def emit(self, kind, payload):
        mono, utc = time.monotonic_ns(), utc_now()
        if self.before_event:
            self.before_event(mono, utc, kind)
        e = self.store.append(kind, payload, run_id=self.run_id, generation=self.generation,
                              mono=mono, utc=utc)
        self.count += 1
        self.state.apply(e)
        if self.observer:
            self.observer(e, self.state)
        return e

    def cid(self, req_id):
        t = self.ib.wrapper.reqId2Ticker.get(req_id)
        return t.contract.conId if t else None

    def field(self, req_id, name, value):
        cid = self.cid(req_id)
        if cid is not None:
            try:
                value = float(value)
            except (ValueError, TypeError):
                value = None
            if value is not None and not math.isfinite(value):
                value = None
            self.emit("FIELD", {"con_id": cid, "field": name, "value": value,
                                "req_id": req_id, "time_basis": "local_callback_receipt"})

    def _hooks(self):
        w = self.ib.wrapper
        original_price, original_size, original_type = w.priceSizeTick, w.tickSize, w.marketDataType

        def price(req, tick, value, size):
            names = {1: ("bid", "bid_size"), 2: ("ask", "ask_size"),
                     4: ("last", "last_size"), 6: ("high", None),
                     7: ("low", None), 9: ("previous_close", None)}
            # Delayed tick IDs intentionally never refresh qualified fields.
            if tick in names:
                name, size_name = names[tick]
                self.field(req, name, value)
                if size_name:
                    self.field(req, size_name, size)
            original_price(req, tick, value, size)

        def sizes(req, tick, size):
            name = {0: "bid_size", 3: "ask_size", 5: "last_size", 8: "volume"}.get(tick)
            if name:
                self.field(req, name, size)
            original_size(req, tick, size)

        def types(req, value):
            cid = self.cid(req)
            if cid is not None:
                self.emit("MARKET_TYPE", {"con_id": cid, "market_type": value})
            original_type(req, value)

        w.priceSizeTick, w.tickSize, w.marketDataType = price, sizes, types
        # Decoder caches this callback at construction.
        self.ib.client.decoder.wrapper = w
        self.ib.disconnectedEvent += lambda: self.emit("DISCONNECTED", {"reason": "socket_closed"})
        # Evaluate only after a complete incoming packet, never between price/size fields.
        self.ib.updateEvent += lambda: self.emit("MARKET_BARRIER", {})

        def error(req, code, message, contract):
            self.emit("IB_ERROR", {"req_id": req, "code": code, "message": message})
            if code in (1100, 1101, 1102, 1300):
                self.emit("DATA_LOST", {"code": code})
                # Rebuild subscriptions in a fresh generation, never reuse cache.
                self.ib.disconnect()
        self.ib.errorEvent += error

    async def connect(self):
        self.generation += 1
        self.ib.wrapper.clientId = self.client_id
        # Low-level handshake avoids high-level automatic position/account fetch.
        await self.ib.client.connectAsync("127.0.0.1", self.port, self.client_id, timeout=8)
        self.emit("CONNECTED", {"host": "127.0.0.1", "port": self.port,
                                "client_id": self.client_id,
                                "server_version": self.ib.client.serverVersion(),
                                "allowed_messages": sorted(ALLOWED_MESSAGES)})
        self.ib.reqMarketDataType(1)
        before = time.time()
        server = await asyncio.wait_for(self.ib.reqCurrentTimeAsync(), 5)
        after = time.time()
        delta = server.timestamp() - (before+after)/2
        self.emit("CLOCK_CHECK", {"server_utc": server.isoformat(),
                                   "roundtrip_s": after-before, "offset_s": delta,
                                   "server_precision_s": 1})
        if abs(delta) > 3:
            raise RuntimeError("Clock offset exceeds diagnostic tolerance")

    async def qualify(self, contract):
        details = await asyncio.wait_for(self.ib.reqContractDetailsAsync(contract), 8)
        good = [d for d in details if d.contract.secType == contract.secType
                and (contract.secType != "OPT" or (
                    d.contract.tradingClass == "SPXW" and d.contract.right == "C"
                    and d.contract.lastTradeDateOrContractMonth[:8] == contract.lastTradeDateOrContractMonth
                    and d.contract.multiplier == "100" and d.contract.strike == contract.strike))]
        if len(good) != 1:
            self.emit("CONTRACT_REJECTED", {"symbol": contract.symbol,
                      "strike": contract.strike, "matching_details": len(good)})
            return None
        d, c = good[0], good[0].contract
        c.exchange = "CBOE" if c.secType == "IND" else "SMART"
        self.emit("CONTRACT", {"con_id": c.conId, "symbol": c.symbol,
                  "sec_type": c.secType, "strike": c.strike, "right": c.right,
                  "expiry": c.lastTradeDateOrContractMonth, "trading_class": c.tradingClass,
                  "multiplier": c.multiplier, "currency": c.currency,
                  "exchange": c.exchange, "local_symbol": c.localSymbol,
                  "min_tick": d.minTick, "time_zone": d.timeZoneId,
                  "trading_hours": d.tradingHours, "liquid_hours": d.liquidHours})
        return c

    def subscribe(self, contract):
        t = self.ib.reqMktData(contract, "", False, False)
        self.tickers.append(t)
        req_id=self.ib.wrapper.ticker2ReqId['mktData'][t]
        self.emit("SUBSCRIBED", {"con_id": contract.conId,'req_id':req_id})

    async def prepare(self, expiry, median):
        from ib_async import Index, Option
        from decimal import Decimal, ROUND_FLOOR
        index = await self.qualify(Index("SPX", "CBOE", "USD"))
        if index is None:
            raise RuntimeError("SPX contract not uniquely qualified")
        self.subscribe(index)
        limit = time.monotonic()+20
        while time.monotonic() < limit:
            f = self.state.fields.get(str(index.conId), {}).get("last")
            if f and f["value"] is not None and f["value"] > 0:
                break
            await asyncio.sleep(.1)
        else:
            raise RuntimeError("No SPX last received within 20 seconds")
        spot = f["value"]
        grid = lambda x: int((Decimal(str(x))/5).quantize(Decimal('1'), rounding=ROUND_FLOOR))*5
        # Finite buffer: +/-50 around spot; forecast candidate wings +/-30.
        strikes = sorted(set(range(grid(spot)-50, grid(spot)+56, 5)) |
                         set(range(grid(median)-30, grid(median)+36, 5)))
        if len(strikes) > 40:
            raise RuntimeError("Finite subscription budget exceeded")
        self.emit("UNIVERSE", {"expiry": expiry, "prewarm_spot": spot,
                               "median": median, "strikes": strikes,
                               "max_option_subscriptions": 40})
        sem = asyncio.Semaphore(4)
        async def one(k):
            async with sem:
                try:
                    c = await self.qualify(Option("SPX", expiry, k, "C", "SMART",
                                                  multiplier="100", currency="USD", tradingClass="SPXW"))
                    if c is not None:
                        self.subscribe(c)
                except (asyncio.TimeoutError, RuntimeError) as ex:
                    self.emit("QUALIFICATION_FAILED", {"strike": k, "reason": str(ex)})
        await asyncio.gather(*(one(k) for k in strikes))
        self.emit("READY", {"subscriptions": len(self.tickers), "expiry": expiry})

    def write_health(self):
        atomic_json(self.directory / "health.json", {"updated_at": utc_now(),
                    "run_id": self.run_id, "event_count": self.count,
                    "mode": "READ_ONLY_MARKET", **self.state.snapshot()})

    async def run(self, expiry, median, duration):
        self.emit("RUN_STARTED", {"expiry": expiry, "median": median, "duration_s": duration})
        deadline = time.monotonic()+duration
        attempts = 0
        try:
            while time.monotonic() < deadline and not self.stopping:
                try:
                    await self.connect()
                    self.tickers = []
                    await self.prepare(expiry, median)
                    while self.ib.isConnected() and not self.stopping and time.monotonic() < deadline:
                        self.emit("HEARTBEAT", {})
                        self.write_health()
                        await asyncio.sleep(1)
                    attempts = 0
                except Exception as ex:
                    attempts += 1
                    self.emit("COLLECTOR_FAILURE", {"type": type(ex).__name__, "reason": str(ex)})
                    if attempts >= 5:
                        raise
                finally:
                    self.ib.disconnect()
                if not self.stopping and time.monotonic() < deadline:
                    await asyncio.sleep(5)
        finally:
            self.emit("RUN_STOPPED", {"requested": self.stopping})
            self.write_health()
            self.store.close()


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--directory", required=True)
    p.add_argument("--expiry", required=True)
    p.add_argument("--median", type=float, required=True)
    p.add_argument("--duration", type=float, default=300)
    p.add_argument("--client-id", type=int, default=27151)
    a = p.parse_args()
    logging.basicConfig(level=logging.WARNING)
    c = Collector(a.directory, client_id=a.client_id)
    for s in (signal.SIGTERM, signal.SIGINT):
        signal.signal(s, lambda *_: setattr(c, "stopping", True))
    asyncio.run(c.run(a.expiry, a.median, a.duration))


if __name__ == "__main__":
    main()
