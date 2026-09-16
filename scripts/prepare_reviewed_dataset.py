#!/usr/bin/env python3
"""Prepare reviewed originals; optionally join one historical quote frame."""
import argparse
import json
from pathlib import Path
from spxlab.events import atomic_json, utc_now
from spxlab.practical import prepare_reviewed_dataset, retrospective_value_report, render_practical_report
from spxlab.research_cli import fresh_output


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--inputs',required=True)
    parser.add_argument('--output',required=True)
    parser.add_argument('--quote-bundle')
    args=parser.parse_args()
    load=lambda path:json.loads(Path(path).read_text())
    result=prepare_reviewed_dataset(load(args.inputs))
    out=fresh_output(args.output)
    atomic_json(out/'manifest.json',result['manifest'])
    atomic_json(out/'preparation.json',result)
    if args.quote_bundle:
        bundle=load(args.quote_bundle)
        report=retrospective_value_report(result,bundle)
        report['generated_at']=utc_now()
        atomic_json(out/'quote-bundle.json',bundle)
        atomic_json(out/'value-report.json',report)
        (out/'REPORT.md').write_text(render_practical_report(report))
    print(json.dumps({'output':str(out),'strict_days':result['check']['independent_days'],
                      'diagnostic_days':result['diagnostic_independent_days'],'rejected_inputs':result['rejected_inputs']},ensure_ascii=False))

if __name__=='__main__':main()
