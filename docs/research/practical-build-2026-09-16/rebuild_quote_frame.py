"""Read and hash-check the original 003 prefix; write one new diagnostic bundle."""
import argparse
import json
from pathlib import Path

from spxlab.calendar import session_schedule
from spxlab.contracts import Clock, file_hash, require
from spxlab.events import atomic_json, read_events, utc_now
from spxlab.market import MarketState
from spxlab.quotes import candidate_universe, quote_universe

ROOT = Path(__file__).resolve().parents[3]


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',required=True)
    output=Path(parser.parse_args().output)
    require(not output.exists(), 'Use a new output file')
    base=ROOT/'var/2026-09-15/diagnostic-003'
    decision=json.loads((base/'decision.json').read_text())
    plan=json.loads((base/'plan.json').read_text())
    state=MarketState();seq=decision['frozen_inputs']['cutoff_seq']
    for event in read_events(base/'events.sqlite'):
        state.apply(event)
        if event['seq']==seq:break
    require(event['seq']==seq, 'Original cutoff not found')
    clock=Clock(seq,event['monotonic_ns'],event['recorded_at'],'LEGACY_READONLY_RECONSTRUCTION')
    spot=decision['frozen_inputs']['spot']['value'];source=decision['frozen_inputs']['forecast']
    batch=quote_universe(state,candidate_universe(spot,source['median']),clock,
        {'session':'2026-09-15','schedule':session_schedule('2026-09-15'),'fees':plan['fees']})
    from decimal import Decimal
    for row in batch['rows']:
        if row['quote_refs']:
            refs=row['quote_refs']
            mid=sum(Decimal((1,-2,1)[i])*(Decimal(str(r['fields']['ask']['value']))+
                Decimal(str(r['fields']['bid']['value'])))/2 for i,r in enumerate(refs))
            row['midpoint_diagnostic_points']=str(mid)
    atomic_json(output,{'batch':batch,'source':source,'spot':spot,'generated_at':utc_now(),
        'evidence':{'database':str((base/'events.sqlite').resolve()),'prefix_last_seq':seq,'prefix_last_hash':event['hash'],
                    'decision_sha256':file_hash(base/'decision.json'),
                    'selection':'Original frozen 003 cutoff; no optimization of replay frame'}})
    print(output)


if __name__=='__main__':main()
