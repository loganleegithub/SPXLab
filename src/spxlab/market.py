"""Pure market-state projection. No network or broker dependency."""
from copy import deepcopy


class MarketState:
    def __init__(self):
        self.generation = 0
        self.connected = False
        self.contracts = {}
        self.fields = {}
        self.types = {}
        self.quote_sides = {}
        self.subscriptions = {}

    def _side_update(self, cid, field, ref, req_id):
        expected=self.subscriptions.get(cid)
        if expected is not None and req_id!=expected:
            return
        if field not in ('bid','ask','bid_size','ask_size'):
            return
        side=field.removesuffix('_size')
        book=self.quote_sides.setdefault(cid,{})
        q=book.setdefault(side,{'price':None,'size':None,'confirmation':None})
        value=ref['value']
        import math
        finite=value is not None and math.isfinite(value)
        if field==side:
            # A price callback must be paired with its following side-size event.
            q.update(price=deepcopy(ref) if finite and value>=0 and (side=='bid' or value>0) else None,
                     size=None,confirmation=None)
        else:
            q['size']=deepcopy(ref)
            if not finite or value<=0:
                # Withdrawn quote: a subsequent size-only update cannot resurrect it.
                q.update(price=None,confirmation=None)
            elif q['price'] is not None and ref['seq']>q['price']['seq'] and ref['mono']>=q['price']['mono']:
                q['confirmation']=deepcopy(ref)
            else:
                q['confirmation']=None

    def apply(self, e):
        kind, p = e["event_type"], e["payload"]
        if kind == "CONNECTED":
            self.generation = e["generation"]
            self.connected = True
            self.fields, self.types = {}, {}
            self.quote_sides, self.subscriptions = {}, {}
        elif kind in ("DISCONNECTED", "DATA_LOST"):
            self.connected = False
            self.fields, self.types = {}, {}
            self.quote_sides, self.subscriptions = {}, {}
        elif kind == "CONTRACT":
            self.contracts[str(p["con_id"])] = p
        elif kind == 'SUBSCRIBED' and e['generation']==self.generation and p.get('req_id') is not None:
            cid=str(p['con_id'])
            self.subscriptions[cid]=p['req_id']
            self.quote_sides.pop(cid,None)
        elif kind == "MARKET_TYPE" and e["generation"] == self.generation and self.connected:
            cid = str(p["con_id"])
            old = self.types.get(cid)
            self.types[cid] = p["market_type"]
            if old is not None and old != p["market_type"]:
                self.fields.pop(cid, None)
                self.quote_sides.pop(cid,None)
        elif kind == "FIELD" and e["generation"] == self.generation and self.connected:
            self.fields.setdefault(str(p["con_id"]), {})[p["field"]] = {
                "value": p["value"], "mono": e["monotonic_ns"], "seq": e["seq"],
                "utc": e["recorded_at"], "generation": self.generation}
            ref=self.fields[str(p['con_id'])][p['field']]
            self._side_update(str(p['con_id']),p['field'],ref,p.get('req_id'))

    def snapshot(self):
        return deepcopy({"generation": self.generation, "connected": self.connected,
                         "contracts": self.contracts, "fields": self.fields,
                         "types": self.types})
