"""Collect SPX minute references, never option history or account/order state."""
import argparse
import asyncio
from datetime import datetime,timedelta,timezone
import json
from pathlib import Path
import sqlite3

from ib_async import IB,Index

from spxlab.calendar import NY,session_schedule
from spxlab.collector import ALLOWED_MESSAGES
from spxlab.contracts import file_hash,require,stamp
from spxlab.engine import spot_quote
from spxlab.events import atomic_json,utc_now
from spxlab.field_model import MinuteTape
from spxlab.market import MarketState


def local_minutes(path,session):
    tape=MinuteTape(session_schedule(session));state=MarketState()
    db=sqlite3.connect(f'file:{path.resolve()}?mode=ro',uri=True)
    query="""SELECT seq,body,hash FROM events WHERE
      body LIKE '%"event_type":"CONNECTED"%' OR body LIKE '%"event_type":"DISCONNECTED"%' OR
      body LIKE '%"event_type":"DATA_LOST"%' OR body LIKE '%"con_id":416904%' ORDER BY seq"""
    count=0
    try:
        for seq,body,h in db.execute(query):
            e={**json.loads(body),'seq':seq,'hash':h};count+=1;state.apply(e)
            if e['event_type'] in ('DISCONNECTED','DATA_LOST'):tape.break_path(e['recorded_at'],e['event_type'])
            if e['event_type']=='FIELD':
                q,_=spot_quote(state,e['monotonic_ns'])
                if q:tape.add(q)
        tape.advance(session_schedule(session)['close_utc'])
    finally:db.close()
    return {'path':str(path.resolve()),'file_sha256':file_hash(path),'selected_event_count':count,
            'classification':'RETROSPECTIVE_LOCAL_EVENT_PROJECTION','full_chain_reverified':False,
            'constructed_at':utc_now(),'bars':tape.bars,'gaps':tape.gaps}


async def collect(output,root,session):
    output=Path(output);require(not output.exists(),'Use a fresh history directory');output.mkdir(parents=True)
    local=[]
    for path in sorted((Path(root)/'var/2026-09-15').glob('diagnostic-*/events.sqlite')):
        item=local_minutes(path,'2026-09-15');atomic_json(output/(path.parent.name+'-minutes.json'),item)
        local.append({'run':path.parent.name,'bars':len(item['bars']),'projection_sha256':file_hash(output/(path.parent.name+'-minutes.json'))})
    ib=IB();errors=[];requests=[];bars=[]
    send=ib.client.send
    # Verified against installed ib_async 2.1.0 client.py: 20 is historical
    # data request, 25 cancellation. Main collector's allowlist is unchanged.
    allowed=ALLOWED_MESSAGES|{20,25}
    def guarded(*fields,**kwargs):
        require(fields and fields[0] in allowed,'Read-only history protocol denied')
        return send(*fields,**kwargs)
    ib.client.send=guarded
    ib.errorEvent+=lambda req,code,msg,contract:errors.append({'req_id':req,'code':code,'message':msg,'received_at':utc_now()})
    try:
        ib.wrapper.clientId=27219
        await ib.client.connectAsync('127.0.0.1',4001,27219,timeout=8)
        details=await asyncio.wait_for(ib.reqContractDetailsAsync(Index('SPX','CBOE','USD')),8)
        require(len(details)==1,'SPX not uniquely identified')
        contract=details[0].contract;contract.exchange='CBOE'
        end=stamp(session_schedule(session)['open_utc'])
        for i in range(6):
            received_before=utc_now()
            chunk=await ib.reqHistoricalDataAsync(contract,endDateTime=end,durationStr='7 D',barSizeSetting='1 min',
                whatToShow='TRADES',useRTH=True,formatDate=2,keepUpToDate=False,timeout=45)
            rows=[{'start_at':b.date.isoformat(),'open':b.open,'high':b.high,'low':b.low,'close':b.close,
                   'volume':b.volume,'bar_count':b.barCount,'received_at':utc_now()} for b in chunk]
            atomic_json(output/f'ib-chunk-{i}.json',{'requested_at':received_before,'received_at':utc_now(),
                        'end_at':end.isoformat(),'contract_id':contract.conId,'rows':rows})
            requests.append({'index':i,'bars':len(rows),'end_at':end.isoformat(),'sha256':file_hash(output/f'ib-chunk-{i}.json')})
            bars.extend(rows)
            days={str(stamp(b['start_at']).astimezone(NY).date()) for b in bars}
            if len(days)>=20 or not rows:break
            end=stamp(rows[0]['start_at'])
            await asyncio.sleep(12) # sequential pacing, no repeated identical requests
    except (Exception,asyncio.TimeoutError) as ex:
        errors.append({'type':type(ex).__name__,'message':str(ex),'received_at':utc_now()})
    finally:ib.disconnect()
    unique={b['start_at']:b for b in bars}
    days=sorted({str(stamp(t).astimezone(NY).date()) for t in unique if str(stamp(t).astimezone(NY).date())<session})[-20:]
    selected=sorted([b for t,b in unique.items() if str(stamp(t).astimezone(NY).date()) in days],key=lambda b:b['start_at'])
    # Same clock-minute squared-return reference, no overnight returns.
    reference={}
    for a,b in zip(selected,selected[1:]):
        ta,tb=stamp(a['start_at']),stamp(b['start_at'])
        if tb-ta==timedelta(minutes=1) and ta.astimezone(NY).date()==tb.astimezone(NY).date():
            key=tb.astimezone(NY).strftime('%H:%M')
            reference.setdefault(key,[]).append((b['close']-a['close'])**2)
    summary={'classification':'RETROSPECTIVE_SPX_MINUTE_REFERENCE','acquired_at':utc_now(),
        'evaluation_session':session,'actual_sessions':days,'requested_sessions':20,'bars':len(selected),
        'local_projections':local,'requests':requests,'errors':errors,
        'reference_q_by_minute':{k:{'q':sum(v)/len(v),'n_days':len(v)} for k,v in reference.items()},
        'use':'同刻波动参照；不替换当前连续分钟参数，不声称历史盲测',
        'historical_data_url':'https://interactivebrokers.github.io/tws-api/historical_limitations.html'}
    atomic_json(output/'minutes.json',selected);atomic_json(output/'summary.json',summary)
    print(json.dumps({k:v for k,v in summary.items() if k!='reference_q_by_minute'},ensure_ascii=False,indent=2))


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--output',required=True);p.add_argument('--session',required=True)
    a=p.parse_args();asyncio.run(collect(a.output,Path(__file__).resolve().parents[1],a.session))
