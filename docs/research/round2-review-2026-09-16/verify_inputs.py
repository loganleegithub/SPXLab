"""Verify supplied hashes, numeric reproduction, and a legacy execution example."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys

parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--root',type=Path,default=Path(__file__).resolve().parents[3])
parser.add_argument('--output-dir',type=Path,required=True)
parser.add_argument('--reference-dir',type=Path,default=Path('/Users/logan/Downloads/SPXLab_density_and_architecture_reference'))
args=parser.parse_args()
ROOT,OUT,BASE=args.root.resolve(),args.output_dir.resolve(),args.reference_dir.resolve()
for name in ('inputs-verified.json','legacy-limit-counterexample.json'):
    if (OUT/name).exists():
        raise SystemExit('Use a new output directory; refusing to overwrite '+str(OUT/name))
manifest=json.loads((BASE/'SPXLab_reference_manifest.json').read_text())
rows=[]
for f in manifest['files']:
    p=BASE/f['file']
    actual=hashlib.sha256(p.read_bytes()).hexdigest()
    rows.append({**f,'actual_sha256':actual,'verified':actual==f['sha256'] and p.stat().st_size==f['bytes']})
assert all(x['verified'] for x in rows)
original=json.loads((BASE/'SPXLab_math_verification.json').read_text())
rerun=json.loads((OUT/'reference-rerun.json').read_text())
runtime={'original':original.pop('runtime'),'rerun':rerun.pop('runtime')}
numeric_differences=[]
def compare(a,b,path=''):
    assert type(a)==type(b),(path,type(a),type(b))
    if isinstance(a,dict):
        assert a.keys()==b.keys(),path
        for k in a:compare(a[k],b[k],path+'/'+k)
    elif isinstance(a,list):
        assert len(a)==len(b),path
        for i,(x,y) in enumerate(zip(a,b)):compare(x,y,path+'/'+str(i))
    elif isinstance(a,float):
        assert abs(a-b)<=1e-10,(path,a,b)
        if a!=b:numeric_differences.append({'field':path,'absolute_error':abs(a-b)})
    else:assert a==b,(path,a,b)
compare(original,rerun)
attached=[Path('/Users/logan/Downloads/SPXLab_ChatGPT-v1.md'),Path('/Users/logan/Downloads/SPXLab_architecture_blueprint.md')]
checks={'reference_manifest':rows,
        'attachments':[{'path':str(p),'sha256':hashlib.sha256(p.read_bytes()).hexdigest()} for p in attached],
        'standalone_blueprint_matches_package':attached[1].read_bytes()==(BASE/'SPXLab_architecture_blueprint.md').read_bytes(),
        'numeric_reproduction':{'absolute_tolerance':1e-10,'passed':True,
                                'changed_float_fields':len(numeric_differences),
                                'max_absolute_error':max((x['absolute_error'] for x in numeric_differences),default=0)},
        'runtime':runtime,'repo_commit':subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip()}
(OUT/'inputs-verified.json').write_text(json.dumps(checks,indent=2)+'\n')
sys.path.insert(0,str(ROOT/'tests'))
from test_core import Feed,config,T
f=Feed().setup();e=f.start();e.on_event(f.event('MARKET_BARRIER'),f.state)
initial=e.books['F']['intent']['debit_points']
f.refresh(T+1_100_000_000)
f.event('FIELD',{'con_id':7580,'field':'ask','value':111.88},T+1_100_000_000)
e.on_event(f.event('MARKET_BARRIER',mono=T+1_100_000_000),f.state)
b=e.books['F']
assert b['status']=='ASSUMED_FILLED'
assert float(b['fill']['debit_points'])>float(initial)
example={'classification':'SYNTHETIC_CURRENT_CODE_COUNTEREXAMPLE','initial_debit':initial,
         'later_fill_debit':b['fill']['debit_points'],'later_all_in':b['fill']['all_in_points'],
         'budget':config()['max_all_in_points'],'status':b['status'],
         'meaning':'Legacy model limits total budget, not initial quote price; preserve its original interpretation'}
(OUT/'legacy-limit-counterexample.json').write_text(json.dumps(example,indent=2)+'\n')
print(json.dumps({'numeric_reproduction':checks['numeric_reproduction'],'legacy_example':example},indent=2))
