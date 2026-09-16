"""Explicit V2 CLI; legacy entry points retain their original semantics."""
import asyncio
import json
from pathlib import Path

from .contracts import digest, require, validate_plan
from .events import atomic_json

ROOT = Path(__file__).resolve().parents[2]


def load(path):
    return json.loads(Path(path).read_text())


def fresh_output(path):
    path = Path(path)
    require(not path.exists(), 'Output already exists; select a new evidence directory')
    path.mkdir(parents=True)
    return path


def register(sub):
    pin=sub.add_parser('field-pin')
    pin.add_argument('--run',required=True);pin.add_argument('--text-file',required=True)
    pin.add_argument('--pin');pin.add_argument('--retract',action='store_true')
    pin.add_argument('--statistic-label',default='unspecified_point');pin.add_argument('--session')
    for name, options in {
        'evaluate':['bundle','output'], 'dataset-check':['manifest'], 'validate-plan':['plan'],
        'freeze':['plan','directory'], 'observe':['plan','directory'], 'shadow':['plan','directory','events'],
        'value-shadow':['plan','directory'], 'field-shadow':['plan','directory'],
        'replay-frozen':['run','output'], 'settle-all':['run','evidence'],
        'compare':['study','output'], 'build-distribution':['manifest','context','output'],
        'attribute':['bundle','output']}.items():
        parser = sub.add_parser(name)
        for option in options:
            parser.add_argument('--'+option, required=True)
        if name in {'observe', 'value-shadow', 'field-shadow'}:
            parser.add_argument('--client-id',type=int,default={'observe':27216,'value-shadow':27217,'field-shadow':27218}[name])
            parser.add_argument('--port',type=int,default=4001)


def execute(args):
    if args.command == 'field-pin':
        from .field_pin import submit_pin
        result=submit_pin(args.run,args.text_file,args.pin,statistic_label=args.statistic_label,retract=args.retract,session=args.session)
    elif args.command == 'evaluate':
        from .valuation import value_candidates,render_valuation
        b=load(args.bundle)
        result=value_candidates(b['distribution'],b['batch'],b['spec'],b['mode'])
        out=fresh_output(args.output)
        atomic_json(out/'input.json',b);atomic_json(out/'valuation.json',result)
        (out/'REPORT.md').write_text(render_valuation(result))
    elif args.command == 'dataset-check':
        from .dataset import dataset_check
        result=dataset_check(load(args.manifest))
    elif args.command == 'build-distribution':
        from .dataset import build_residual_distribution
        from .distribution import validate_distribution
        from .contracts import file_hash, stamp
        from .events import utc_now
        manifest,context=load(args.manifest),load(args.context)
        real=any(r['classification'] != 'SYNTHETIC_FIXTURE' for r in manifest['rows'])
        if real:
            require(stamp(context['available_at']) <= stamp(utc_now()), 'Future model completion cannot be asserted')
            context['available_at']=utc_now()
        result=build_residual_distribution(manifest,context)
        out=fresh_output(args.output)
        artifact=out/'model-artifact.json'
        atomic_json(artifact,{'model_version':result['model_version'],'manifest':manifest,
                             'anchor_kind':context['anchor_kind'],'time_bucket':context['time_bucket'],
                             'current_scale':context['scale']})
        result['provenance'].update(model_artifact_path=str(artifact.resolve()),model_artifact_hash=file_hash(artifact))
        if real:
            result['computed_at']=result['available_at']=utc_now()
        result=validate_distribution(result)
        atomic_json(out/'distribution.json',result)
    elif args.command == 'validate-plan':
        result=validate_plan(load(args.plan))
    elif args.command == 'freeze':
        from .frozen import freeze_run
        result=freeze_run(load(args.plan),args.directory,ROOT)
    elif args.command == 'observe':
        from .observer import run_observer
        asyncio.run(run_observer(load(args.plan),args.directory,ROOT,client_id=args.client_id,port=args.port))
        result={'directory':args.directory,'status':'OBSERVER_STOPPED'}
    elif args.command == 'shadow':
        from .frozen import freeze_run
        from .research import ResearchJournal
        plan=load(args.plan)
        require(plan['mode']=='SYNTHETIC_REPLAY_V1','This shadow driver accepts explicit synthetic fixtures only')
        plan=freeze_run(plan,args.directory,ROOT)
        j=ResearchJournal(Path(args.directory)/'events.sqlite',plan)
        inputs=load(args.events)
        try:
            require(inputs, 'At least one fixture event required')
            first=inputs[0]
            j.append('RESEARCH_PLAN',{'plan_hash':digest(plan)},clock_epoch=first['epoch'],mono=max(0,first['mono']-1),utc=first['utc'])
            for e in inputs:
                j.append(e['kind'],e['payload'],clock_epoch=e['epoch'],mono=e['mono'],utc=e['utc'],generation=e.get('generation',0))
            result=j.save(args.directory)
        finally:
            j.store.close()
    elif args.command == 'value-shadow':
        from .observer import run_value_shadow
        asyncio.run(run_value_shadow(load(args.plan),args.directory,ROOT,client_id=args.client_id,port=args.port))
        result={'directory':args.directory,'status':'VALUE_SHADOW_STOPPED','broker_orders_permitted':False}
    elif args.command == 'field-shadow':
        from .observer import run_field_shadow
        asyncio.run(run_field_shadow(load(args.plan),args.directory,ROOT,client_id=args.client_id,port=args.port))
        result={'directory':args.directory,'status':'FIELD_STOPPED','broker_orders_permitted':False}
    elif args.command == 'replay-frozen':
        from .frozen import replay_frozen
        result=replay_frozen(args.run,args.output)
    elif args.command == 'settle-all':
        from .frozen import settle_frozen
        result=settle_frozen(args.run,load(args.evidence))
    elif args.command == 'compare':
        from .evaluation import compare_study,compare_rows
        study=load(args.study)
        if 'rows' in study:
            require(study.get('classification') == 'SYNTHETIC_FIXTURE', 'Inline rows are fixture-only; real studies require hash-bound run settlements')
        result=compare_rows(study,study['rows']) if 'rows' in study else compare_study(study)
        out=fresh_output(args.output);atomic_json(out/'study.json',study);atomic_json(out/'comparison.json',result)
        lines=['# 第二轮日级比较','','证据类型：'+result['classification'],'','结论：'+result['conclusion']+'；未建立可交易优势。',
               '', '| 账本 | 已知/计划日 | 可观测子集美元 | 完整累计美元 |','|---|---|---|---|']
        for k,v in result['strategy_summaries'].items():
            lines.append(f"| {k} | {v['known_days']}/{v['planned_days']} | {v['observed_subset_sum_usd']} | {v['cumulative_usd']} |")
        (out/'REPORT.md').write_text('\n'.join(lines)+'\n')
    elif args.command == 'attribute':
        from .evaluation import location_attribution
        result=location_attribution(**load(args.bundle))
        out=fresh_output(args.output);atomic_json(out/'attribution.json',result)
    else:
        return False
    print(json.dumps(result,ensure_ascii=False,indent=2))
    return True
