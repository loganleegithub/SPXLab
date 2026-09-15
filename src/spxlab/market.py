"""Pure market-state projection. No network or broker dependency."""
from copy import deepcopy


class MarketState:
    def __init__(self):
        self.generation = 0
        self.connected = False
        self.contracts = {}
        self.fields = {}
        self.types = {}

    def apply(self, e):
        kind, p = e["event_type"], e["payload"]
        if kind == "CONNECTED":
            self.generation = e["generation"]
            self.connected = True
            self.fields, self.types = {}, {}
        elif kind in ("DISCONNECTED", "DATA_LOST"):
            self.connected = False
            self.fields, self.types = {}, {}
        elif kind == "CONTRACT":
            self.contracts[str(p["con_id"])] = p
        elif kind == "MARKET_TYPE" and e["generation"] == self.generation and self.connected:
            cid = str(p["con_id"])
            old = self.types.get(cid)
            self.types[cid] = p["market_type"]
            if old is not None and old != p["market_type"]:
                self.fields.pop(cid, None)
        elif kind == "FIELD" and e["generation"] == self.generation and self.connected:
            self.fields.setdefault(str(p["con_id"]), {})[p["field"]] = {
                "value": p["value"], "mono": e["monotonic_ns"], "seq": e["seq"],
                "utc": e["recorded_at"], "generation": self.generation}

    def snapshot(self):
        return deepcopy({"generation": self.generation, "connected": self.connected,
                         "contracts": self.contracts, "fields": self.fields,
                         "types": self.types})
