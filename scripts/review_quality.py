"""Read-only diagnosis of a completed decision window; never reselects a trade."""
import json
import sys
from collections import Counter
from pathlib import Path

from spxlab.events import atomic_json, read_events
from spxlab.market import MarketState


def review(directory):
    directory=Path(directory)
    decision=json.loads((directory/'decision.json').read_text())
    state=MarketState();start=None;end=None;counts={};frames=0;timing={}
    strikes=set()
    for name,b in decision['books'].items():
        center=b.get('center')
        if center is None:continue
        centers=[center-5,center,center+5] if name in ('FP','FGP') else [center]
        for k in centers:strikes.update((k-25,k,k+25))
    for e in read_events(directory/'events.sqlite'):
        state.apply(e)
        if e['event_type']=='CUTOFF':
            start=e['payload']['scheduled_mono'];end=start+15_000_000_000
            timing={'cutoff_insertion_delay_ms':e['payload']['insertion_delay_ns']/1e6,
                    'cutoff_recorded_at':e['recorded_at'],'cutoff_seq':e['seq']}
        if start is None:continue
        if e['monotonic_ns']>=end:break
        if e['event_type'] not in ('MARKET_BARRIER','TIMER','HEARTBEAT'):continue
        frames+=1
        for cid,c in state.contracts.items():
            if c.get('sec_type')!='OPT' or c.get('strike') not in strikes:continue
            row=counts.setdefault(str(c['strike']),{'con_id':cid,'local_symbol':c['local_symbol'],
                         'fields':{},'quantity_counts':{},'complete_book_frames':0})
            complete=True
            for field in ('bid','ask','bid_size','ask_size'):
                out=row['fields'].setdefault(field,{'missing':0,'stale':0,'max_age_seconds':0,'min_age_seconds':None})
                f=state.fields.get(cid,{}).get(field)
                if f is None or f['value'] is None:
                    out['missing']+=1;complete=False;continue
                age=(e['monotonic_ns']-f['mono'])/1e9
                out['max_age_seconds']=max(out['max_age_seconds'],age)
                out['min_age_seconds']=age if out['min_age_seconds'] is None else min(out['min_age_seconds'],age)
                if age<0 or age>(1 if field.endswith('_size') else 2):
                    out['stale']+=1;complete=False
                if field.endswith('_size'):
                    counter=row['quantity_counts'].setdefault(field,{})
                    key=str(f['value']);counter[key]=counter.get(key,0)+1
            row['complete_book_frames']+=int(complete)
    output={'status':'POSTHOC_ENGINEERING_DIAGNOSIS','decision_modified':False,'frames':frames,
            'timing':timing,'contracts':counts,
            'caveat':'Receipt age measures the time since the last callback for each field. An unchanged exchange quote may remain live without a new callback. Stale in this experiment does not prove no tradable quote existed.'}
    atomic_json(directory/'quality-review.json',output)
    return output


if __name__=='__main__':
    out=review(sys.argv[1])
    print(json.dumps(out,ensure_ascii=False,indent=2))
