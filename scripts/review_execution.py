"""Explain every evaluable frame in a recorded intent's execution window."""
import argparse
import json
from collections import Counter
from pathlib import Path

from spxlab.events import atomic_json, read_events
from spxlab.market import MarketState
from spxlab.engine import combo_quote, confirmed_side


def review(directory,book='FP'):
    directory=Path(directory)
    cfg=json.loads((directory/'plan.json').read_text())
    b=json.loads((directory/'decision.json').read_text())['books'][book]
    state=MarketState();events=[];timestamps={};reasons=Counter();side_counts=Counter();size_counts=Counter()
    for e in read_events(directory/'events.sqlite'):
        state.apply(e)
        if e['seq']==b['intent_seq']:timestamps['intent_utc']=e['recorded_at']
        if e['monotonic_ns']<b['eligible_mono']:continue
        if e['monotonic_ns']>=b['expires_mono']:break
        if e['event_type'] not in ('MARKET_BARRIER','TIMER','HEARTBEAT'):continue
        quote,reason=combo_quote(state,b['center'],cfg,e['monotonic_ns'])
        details=[];quantity_failures=[]
        for strike,side,need in ((b['center']-25,'ask',1),(b['center'],'bid',2),(b['center']+25,'ask',1)):
            cid=next(cid for cid,c in state.contracts.items() if c['sec_type']=='OPT'
                     and c['strike']==strike and c['right']=='C' and c['expiry'][:8]==cfg['expiry'])
            for s in ('bid','ask'):
                q,why=confirmed_side(state,cid,s,e['monotonic_ns'],1 if s==side else 2)
                if why:
                    ref=state.quote_sides.get(cid,{}).get(s,{}).get('confirmation')
                    details.append({'strike':strike,'side':s,'reason':why,
                        'confirmation_age_s':(e['monotonic_ns']-ref['mono'])/1e9 if ref else None})
                    side_counts[f'{strike}.{s} {why}']+=1
            size=state.quote_sides.get(cid,{}).get(side,{}).get('size')
            if size is not None and size['value'] is not None and size['value']<need:
                quantity_failures.append({'strike':strike,'side':side,'size':size['value'],
                                          'required':need,'size_seq':size['seq']})
                size_counts[f'{strike}.{side} size={size["value"]} required={need}']+=1
        events.append({'seq':e['seq'],'utc':e['recorded_at'],'reason':reason,'details':details,
                       'cost':quote['all_in_points'] if quote else None,'quantity_failures':quantity_failures})
        reasons[reason or 'QUALIFIED']+=1
    out={'intent':{k:b[k] for k in ('center','intent_seq','intent_mono','eligible_mono','expires_mono')},
         'timestamps':timestamps,'eligible_window_s':(b['expires_mono']-b['eligible_mono'])/1e9,
         'reasons':dict(reasons),'events':events,'all_side_failures':dict(side_counts),'all_quantity_failures':dict(size_counts)}
    atomic_json(directory/'execution-quality-review.json',out)
    return out


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory');parser.add_argument('--book',default='FP');args=parser.parse_args()
    out=review(args.directory,args.book)
    print(json.dumps({k:v for k,v in out.items() if k!='events'},ensure_ascii=False,indent=2))
