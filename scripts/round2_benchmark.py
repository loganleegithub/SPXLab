"""Replay a representative historical peak into an isolated V2 journal.

Historical data stays RETROSPECTIVE_ENGINEERING; this is a throughput and
integrity exercise, not a new prospective shadow strategy result.
"""
import argparse
from collections import Counter,deque
from datetime import timedelta
import json
from pathlib import Path
import sqlite3
import time

from spxlab.contracts import digest,stamp,validate_plan
from spxlab.events import atomic_json,read_events
from spxlab.research import ResearchJournal


def benchmark(source,plan,output):
    output=Path(output)
    if output.exists():raise ValueError('Select a new output directory')
    window=deque();best=[];counts=Counter();previous=None;peak=0
    # Input scanning is read-only. Keep the densest one-second callback window.
    for e in read_events(Path(source)/'events.sqlite'):
        if e['event_type']=='FIELD':
            counts[int(e['monotonic_ns']/1e9)]+=1
            window.append(e)
            while window and e['monotonic_ns']-window[0]['monotonic_ns']>1_000_000_000:
                window.popleft()
            if len(window)>peak:best=list(window);peak=len(window)
    p=validate_plan(plan);p['run_id']='round2-peak-benchmark';p['mode']='SYNTHETIC_REPLAY_V1'
    p['source_product_id']='BENCHMARK_FIXTURE'
    from spxlab.frozen import freeze_run
    p=freeze_run(p,output,Path(__file__).resolve().parents[1])
    j=ResearchJournal(output/'events.sqlite',p)
    start=time.monotonic_ns();utc=p['schedule']['fixed_utc']
    j.append('RESEARCH_PLAN',{'plan_hash':digest(p)},clock_epoch='benchmark',mono=start,utc=utc)
    j.append('CONNECTED',{},clock_epoch='benchmark',mono=start,utc=utc,generation=1)
    from spxlab.quotes import candidate_universe
    universe=candidate_universe(7590,7605)
    strikes=sorted({k for c in universe for k in (c['center']-25,c['center'],c['center']+25)})
    for cid,k in [(100,0)]+list(enumerate(strikes,101)):
        payload={'con_id':cid,'symbol':'SPX','sec_type':'OPT' if k else 'IND','strike':k,'right':'C','expiry':'20260916','trading_class':'SPXW','multiplier':'100','currency':'USD'}
        for kind,data in [('CONTRACT',payload),('SUBSCRIBED',{'con_id':cid,'req_id':cid}),('MARKET_TYPE',{'con_id':cid,'market_type':1})]:
            j.append(kind,data,clock_epoch='benchmark',mono=start,utc=utc,generation=1)
    f={'schema_version':2,'forecast_id':'benchmark','source_product_id':'BENCHMARK_FIXTURE','model_version':'SYNTHETIC',
       'target_session':'2026-09-16','target_at':p['schedule']['close_utc'],'series':'SPXW_PM','statistic_type':'median',
       'median':7605,'gamma':'UNKNOWN','first_seen_at':utc,'validated_at':utc,'status':'VALIDATED','reviewed_by':'Fixture','supersedes':None,
       'raw_assets':[{'sha256':'a'*64,'received_at':utc,'source_url':'https://example.test/benchmark'}]}
    j.append('FORECAST',{'record':f},clock_epoch='benchmark',mono=start,utc=utc,generation=1)
    j.append('FIELD',{'con_id':100,'req_id':100,'field':'last','value':7590},clock_epoch='benchmark',mono=start,utc=utc,generation=1)
    # Four centers = at most twelve distinct option legs; retain all events,
    # including irrelevant subscriptions, to test raw persistence pressure.
    n=0;begin=time.monotonic()
    for iteration in range(10):
        for e in best:
            n+=1
            cid=101+(n%len(strikes))
            j.append('FIELD',{**e['payload'],'con_id':cid,'req_id':cid},clock_epoch='benchmark',mono=start+n,
                     utc=(stamp(utc)+timedelta(microseconds=n)).isoformat(),generation=1)
    elapsed=time.monotonic()-begin
    j.append('EVALUATE',{'scheduled_at':utc},clock_epoch='benchmark',mono=start+n+1,
             utc=(stamp(utc)+timedelta(seconds=2)).isoformat())
    result=j.save(output);metrics=j.metrics();j.store.close()
    verified=sum(1 for _ in read_events(output/'events.sqlite'))
    report={'classification':'SYNTHETIC_PROJECTION_USING_HISTORICAL_EVENT_SHAPE','source':str(source),'run_manifest':str(output/'run-manifest.json'),'historical_peak_fields_rolling_second':peak,
            'projection_option_contract_count':len(strikes),'evaluated_candidate_count':j.engine.latest_valuation['coverage']['planned'],
            'full_universe_evaluation_ms':next(r['reduction_ms'] for r in reversed(j.stage_samples) if r['kind']=='EVALUATE'),
            'repetitions':10,'field_events_written':n,'events_verified':verified,
            'elapsed_write_seconds':elapsed,'sustained_fields_per_second':n/elapsed,
            'throughput_above_observed_peak':n/elapsed>peak,'silent_drops':0 if verified>=n else n-verified,
            'metrics':metrics,'late_decision_reasons':result['books'][p['strategies'][0]['id']]['last_decision']['reasons'],
            'intents':sum(b['intent_count'] for b in result['books'].values()),
            'limitation':'Historical maximum FIELD burst remapped into declared synthetic 12-leg projection, repeated as fast as possible; not exchange-to-machine latency or guaranteed live scheduling'}
    atomic_json(output/'benchmark.json',report)
    return report


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--source',required=True);p.add_argument('--plan',required=True);p.add_argument('--output',required=True)
    a=p.parse_args();print(json.dumps(benchmark(a.source,json.loads(Path(a.plan).read_text()),a.output),indent=2))
