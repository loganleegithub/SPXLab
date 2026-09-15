"""Deterministic five-book shadow decisions. Pure functions; no broker APIs."""
from __future__ import annotations

from copy import deepcopy
from decimal import Decimal, ROUND_FLOOR, ROUND_CEILING
from datetime import datetime

D = lambda v: Decimal(str(v))
BOOKS = ("S", "F", "FG", "FP", "FGP")


def nearest_center(value):
    low = (D(value)/5).to_integral_value(rounding=ROUND_FLOOR)*5
    return int(low if D(value)-low <= D('2.5') else low+5)


def payoff(spot, center, width=25):
    return max(D(width)-abs(D(spot)-D(center)), D(0))


def estimate_fee(prices, cfg):
    """IBKR public-customer scenario, not confirmed account charges.

    COMBO order minimum applies separately to each leg. Regulatory reserve
    is explicitly an assumption, never presented as a published tariff.
    """
    if cfg.get('kind') != 'IBKR_PUBLIC_CUSTOMER_SCENARIO':
        return None
    amounts = [D(x) for x in prices]
    quantities = [1, 2, 1]
    commission = sum(max(D('1.00'), D('0.65')*n) for n in quantities)
    exchange = sum((D('.45') if p >= 1 else D('.36'))*n
                   for p, n in zip(amounts, quantities))
    clearing, cat, taf = D('.025')*4, D('.0003')*4, D('.00329')*2
    execution, processing, orf = D('.14')*4, D('.0025')*4, D('.01248')*4
    sec = (D('.0000206')*amounts[1]*100*2).quantize(D('.01'), rounding=ROUND_CEILING)
    reserve = D(cfg['regulatory_reserve_per_contract_usd'])*4
    total = commission+exchange+clearing+cat+taf+sec+reserve+execution+processing+orf
    return {'estimated_usd': str(total.quantize(D('.01'), rounding=ROUND_CEILING)),
            'components_usd': {k: str(v) for k,v in {
                'commission_leg_minima': commission, 'exchange': exchange,
                'clearing': clearing, 'cat': cat, 'taf': taf, 'sec': sec,
                'execution_surcharge': execution, 'trade_processing': processing,
                'cboe_on_exchange_orf': orf,
                'unconfirmed_regulatory_reserve': reserve}.items()},
            'applicability': 'UNCONFIRMED_ACCOUNT_SCENARIO', 'version': cfg['version']}


def fresh(state, cid, field, now, max_age):
    if not state.connected:
        return None, 'DISCONNECTED'
    if state.types.get(str(cid)) != 1:
        return None, 'NOT_CONFIRMED_REALTIME'
    f = state.fields.get(str(cid), {}).get(field)
    if f is None or f['value'] is None:
        return None, 'MISSING_'+field
    if f['generation'] != state.generation:
        return None, 'OLD_GENERATION'
    age = (now-f['mono'])/1e9
    if age < 0 or age > max_age:
        return None, 'STALE_'+field
    return f, None


def spot_quote(state, now):
    for cid, c in state.contracts.items():
        if c['symbol'] == 'SPX' and c['sec_type'] == 'IND':
            f, reason = fresh(state, cid, 'last', now, 2)
            if f is not None and D(f['value']) > 0:
                return f, None
            return None, reason or 'INVALID_SPX'
    return None, 'NO_SPX_CONTRACT'


def confirmed_side(state,cid,side,now,max_age):
    if not state.connected:
        return None,'DISCONNECTED'
    if state.types.get(str(cid))!=1:
        return None,'NOT_CONFIRMED_REALTIME'
    q=state.quote_sides.get(str(cid),{}).get(side)
    if not q or q['price'] is None or q['confirmation'] is None:
        return None,'UNCONFIRMED_'+side
    if any(q[k]['generation']!=state.generation for k in ('price','size','confirmation')):
        return None,'OLD_GENERATION'
    age=(now-q['confirmation']['mono'])/1e9
    if age<0 or age>max_age:
        return None,'STALE_SIDE_'+side
    return q,None


def combo_quote(state, center, cfg, now):
    width = cfg['width']
    cids=[]
    for strike in (center-width, center, center+width):
        matching=[cid for cid,c in state.contracts.items()
                  if c['sec_type']=='OPT' and c['symbol']=='SPX'
                  and c['trading_class']=='SPXW' and c['right']=='C'
                  and c['expiry'][:8]==cfg['expiry'] and c['currency']=='USD'
                  and c['multiplier']=='100' and D(c['strike'])==strike]
        if len(matching)!=1:
            return None, 'CONTRACT_NOT_UNIQUE_OR_PREWARMED'
        cids.append(matching[0])
    refs, prices, marks = [], [], []
    for cid, side, quantity in zip(cids, ('ask','bid','ask'), (1,2,1)):
        fields={}
        confirmations={}
        if cfg.get('quote_policy','RAW_FIELD_V1')=='SIDE_CONFIRMATION_V2':
            for s in ('bid','ask'):
                q,reason=confirmed_side(state,cid,s,now,1 if s==side else 2)
                if q is None:
                    return None,reason
                fields[s]=q['price']
                confirmations[s]=q['confirmation']
                if s==side:
                    fields[side+'_size']=q['size']
        else:
            for field in ('bid','ask', side+'_size'):
                f, reason=fresh(state,cid,field,now,1 if field.endswith('_size') else 2)
                if f is None:
                    return None, reason
                fields[field]=f
        bid, ask = D(fields['bid']['value']), D(fields['ask']['value'])
        if bid < 0 or ask <= 0 or ask < bid:
            return None, 'INVALID_OR_CROSSED_BOOK'
        if D(fields[side+'_size']['value']) < quantity:
            return None, 'INSUFFICIENT_SIZE'
        refs.append({'con_id':cid,'fields':deepcopy(fields)})
        if confirmations:
            refs[-1]['side_confirmation_refs']=deepcopy(confirmations)
        prices.append(fields[side]['value'])
        marks.append(confirmations[side]['mono'] if confirmations else fields[side]['mono'])
    if max(marks)-min(marks)>1_000_000_000:
        return None, 'CROSS_LEG_TIME_SKEW'
    debit=D(prices[0])-2*D(prices[1])+D(prices[2])
    if not 0 < debit < D(width):
        return None, 'INVALID_COMBO_DEBIT'
    fees=estimate_fee(prices,cfg['fees'])
    if fees is None:
        return None,'FEE_MODEL_MISSING'
    cost=debit+D(fees['estimated_usd'])/100
    midpoint=(D(refs[0]['fields']['bid']['value'])+D(refs[0]['fields']['ask']['value']))/2 \
        -(D(refs[1]['fields']['bid']['value'])+D(refs[1]['fields']['ask']['value'])) \
        +(D(refs[2]['fields']['bid']['value'])+D(refs[2]['fields']['ask']['value']))/2
    return {'center':center,'width':width,'con_ids':cids,'debit_points':str(debit),
            'all_in_points':str(cost),'fees':fees,'quote_refs':refs,
            'midpoint_points_diagnostic':str(midpoint)},None


class Engine:
    def __init__(self, cfg):
        self.cfg=deepcopy(cfg)
        self.books={name:{'status':'WAITING_CUTOFF','book':name} for name in BOOKS}
        self.cutoff=None
        self.end=None
        self.done=False
        self.frozen=None
        self.trace=[]
        self._new=[]

    def record(self, kind, **payload):
        item={'event_type':kind,'payload':payload}
        self.trace.append(item)
        self._new.append(item)

    def reject(self, book, reason, status='NO_TRADE'):
        self.books[book].update(status=status,reason=reason)
        self.record('BOOK_REJECTED',book=book,status=status,reason=reason)

    def freeze(self,e,state):
        if self.cutoff is not None:
            return
        self.cutoff=e['payload']['scheduled_mono']
        self.end=self.cutoff+int(self.cfg['window_seconds']*1e9)
        spot,reason=spot_quote(state,self.cutoff)
        forecast=self.cfg['forecast']
        cutoff_utc=datetime.fromisoformat(self.cfg['cutoff_utc'])
        forecast_ok=(forecast['statistic']=='median' and forecast['target_date']==self.cfg['target_date']
                     and datetime.fromisoformat(forecast['available_at'])<=cutoff_utc
                     and forecast['status'] in self.cfg['allowed_forecast_statuses'])
        self.frozen={'spot':deepcopy(spot),'forecast':deepcopy(forecast),
                     'generation':state.generation,'cutoff_seq':e['seq'],
                     'source_assumptions':forecast.get('assumptions',[])}
        self.record('FROZEN_INPUTS',**self.frozen)
        if spot is None:
            for b in BOOKS:
                self.reject(b,reason or 'MISSING_SPX', 'INDETERMINATE')
            return
        sc=nearest_center(spot['value'])
        fc=nearest_center(forecast['median']) if forecast_ok else None
        for b in BOOKS:
            self.books[b].update(status='SELECTING',center=sc if b=='S' else fc,
                                 generation=state.generation,quality_rejections={},
                                 saw_qualified=False,observation_gap=False)
            if b!='S':
                self.books[b]['source_assumptions']=forecast.get('assumptions',[])
            if b!='S' and not forecast_ok:
                self.reject(b,'FORECAST_NOT_ELIGIBLE','INDETERMINATE')
            elif b in ('FG','FGP') and forecast['gamma']!='LONG':
                self.reject(b,'G0_SHORT' if forecast['gamma']=='SHORT' else 'G0_UNKNOWN',
                            'NO_TRADE' if forecast['gamma']=='SHORT' else 'INDETERMINATE')

    def on_event(self,e,state):
        self._new=[]
        kind=e['event_type']
        now=e['monotonic_ns']
        if kind=='CUTOFF':
            self.freeze(e,state)
            return self._new
        if self.cutoff is None or self.done:
            return self._new
        if kind in ('DISCONNECTED','DATA_LOST','CONNECTED'):
            for book in self.books.values():
                if book['status'] in ('SELECTING','INTENT'):
                    book['observation_gap']=True
        if kind not in ('CUTOFF','MARKET_BARRIER','TIMER','HEARTBEAT','RUN_STOPPED'):
            return self._new
        # Do not create a fill at the end boundary or after a stopped observation.
        if now>=self.end or kind=='RUN_STOPPED':
            self.finish('WINDOW_CLOSED' if now>=self.end else 'SERVICE_STOPPED')
            return self._new
        if now<self.cutoff:
            return self._new
        quotes={}
        def quote(k):
            if k not in quotes:
                quotes[k]=combo_quote(state,k,self.cfg,now)
            return quotes[k]
        for name in BOOKS:
            book=self.books[name]
            if book['status'] not in ('SELECTING','INTENT'):
                continue
            if state.generation!=book['generation'] or not state.connected:
                book['observation_gap']=True
                continue
            if book['status']=='INTENT':
                if now>=book['expires_mono']:
                    self.reject(name,'OBSERVATION_GAP' if book['observation_gap'] else 'NO_QUALIFIED_FILL',
                                'INDETERMINATE' if book['observation_gap'] else 'ASSUMED_NO_FILL')
                    continue
                if now<book['eligible_mono']:
                    continue
                q,reason=quote(book['center'])
                if q is None:
                    book['observation_gap']=True
                    self.quality(book,reason)
                    continue
                if D(q['all_in_points'])<=D(self.cfg['max_all_in_points']):
                    book.update(status='ASSUMED_FILLED',fill=q,fill_seq=e['seq'],fill_utc=e['recorded_at'])
                    self.record('ASSUMED_FILL',book=name,input_seq=e['seq'],quote=q)
                continue
            candidates=[book['center']+k for k in (-5,0,5)] if name in ('FP','FGP') else [book['center']]
            results=[quote(k) for k in candidates]
            if any(q is None for q,_ in results):
                for q,reason in results:
                    if q is None:
                        self.quality(book,reason)
                continue
            options=[q for q,_ in results]
            book['saw_qualified']=True
            self.record('CANDIDATES',book=name,input_seq=e['seq'],quotes=options)
            if name in ('FP','FGP'):
                median=D(self.cfg['forecast']['median'])
                q=min(options,key=lambda q:(D(q['all_in_points']),abs(D(q['center'])-median),q['center']))
            else:
                q=options[0]
            book.update(selection=deepcopy(q), candidate_centers=candidates)
            # Selection is a single first-qualified decision, not a rolling bargain hunt.
            if D(q['all_in_points'])>D(self.cfg['max_all_in_points']):
                self.reject(name,'COST_CAP')
                continue
            book.update(status='INTENT',center=q['center'],intent=q,intent_seq=e['seq'],
                        intent_mono=now,eligible_mono=now+int(self.cfg['latency_seconds']*1e9),
                        expires_mono=min(self.end,now+int(self.cfg['intent_lifetime_seconds']*1e9)))
            self.record('SHADOW_INTENT',book=name,input_seq=e['seq'],quote=q,
                        eligible_mono=book['eligible_mono'],expires_mono=book['expires_mono'])
        return self._new

    def quality(self,book,reason):
        counts=book['quality_rejections']
        counts[reason]=counts.get(reason,0)+1

    def finish(self,reason):
        for name,book in self.books.items():
            if book['status']=='SELECTING':
                self.reject(name,'NO_COMPLETE_QUALIFIED_OBSERVATION','INDETERMINATE')
            elif book['status']=='INTENT':
                gap=book['observation_gap'] or reason=='SERVICE_STOPPED'
                self.reject(name,'OBSERVATION_GAP' if gap else 'NO_QUALIFIED_FILL',
                            'INDETERMINATE' if gap else 'ASSUMED_NO_FILL')
        self.done=True
        self.record('WINDOW_COMPLETE',reason=reason)

    def result(self):
        result={'experiment_id':self.cfg['experiment_id'],'mode':self.cfg['mode'],
                'cutoff_utc':self.cfg['cutoff_utc'],'target_date':self.cfg['target_date'],
                'done':self.done,'frozen_inputs':self.frozen,'books':deepcopy(self.books),
                'settlement_status':'PENDING_OFFICIAL_PM_SETTLEMENT',
                'fee_status':'UNCONFIRMED_ACCOUNT_SCENARIO',
                'fixed_costs_status':self.cfg.get('fixed_costs',{'status':'UNKNOWN'})}
        if 'quote_policy' in self.cfg:
            result['quote_policy']=self.cfg['quote_policy']
        return result


def settle(result, evidence):
    """Settlement evidence is appended separately; it never reselects trades."""
    if evidence['target_date']!=result['target_date'] or evidence['series']!='SPXW_PM':
        raise ValueError('Settlement date/series mismatch')
    if evidence['status']!='CONFIRMED' or not evidence.get('source_url') or not evidence.get('asset_sha256'):
        raise ValueError('Official settlement evidence must have a confirmed source and asset hash')
    out=deepcopy(result)
    out['settlement_evidence']=evidence
    out['settlement_status']='CONFIRMED_VALUE_ESTIMATED_FEES'
    for b in out['books'].values():
        if b['status']=='ASSUMED_FILLED':
            q=b['fill'];value=payoff(evidence['value'],q['center'],q['width'])
            pnl=(value-D(q['debit_points']))*100-D(q['fees']['estimated_usd'])
            b['payoff_points']=str(value)
            b['net_pnl_estimated_fees_usd']=str(pnl)
        elif b['status'] in ('NO_TRADE','ASSUMED_NO_FILL'):
            b['net_pnl_estimated_fees_usd']='0'
        else:
            b['net_pnl_estimated_fees_usd']=None
    return out
