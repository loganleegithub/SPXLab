"""FIELD journal projections, prospective error ledger and local single page."""
from copy import deepcopy
from datetime import timedelta
import html
import json
from pathlib import Path

from .contracts import digest, stamp
from .engine import spot_quote
from .events import atomic_json, utc_now
from .field_model import MinuteTape, cdf, marginal, payoff, quantile

DERIVED_FIELD = {'FIELD_PREDICTION','FIELD_SCORE','FIELD_WAIT_SCORE'}


def event_risk(events, as_of, target_at):
    return [{**deepcopy(e),'phase':'BEFORE_EVENT' if stamp(as_of)<stamp(e['at']) else 'AFTER_EVENT',
             'crosses_target':stamp(as_of)<stamp(e['at'])<=stamp(target_at),
             'model_treatment':'NOT_SEPARATELY_PRICED'} for e in events]


class FieldTracker:
    def __init__(self, plan):
        self.tape = MinuteTape(plan['schedule'])
        self.diagnostic = {'state':'WARMUP','anchor_mode':'MARKET_ONLY','n_increments':0}
        self.predictions = []
        self.scores = []
        self.wait_scores = []
        self.decisions = []
        self.scored = set()
        self.wait_scored = set()
        self.last_pin = None
        self.history_reference = None
        self.last_score_second = None
        self.in_packet = False
        self.known_events = deepcopy(plan.get('known_events',[]))

    def apply(self, kind, p):
        if kind == 'FIELD_PREDICTION': self.predictions.append(deepcopy(p))
        elif kind == 'DECISION': self.decisions.append(deepcopy(p))
        elif kind == 'FIELD_SCORE':
            self.scores.append(deepcopy(p));self.scored.add((p['prediction_id'],p['horizon_minutes']))
        elif kind == 'FIELD_WAIT_SCORE':
            self.wait_scores.append(deepcopy(p));self.wait_scored.add(p['prediction_id'])

    def market_event(self, event, market, clock, epoch_change):
        kind = event['event_type']
        if epoch_change or kind in {'DISCONNECTED','DATA_LOST','RESTART'}:
            self.tape.break_path(clock.utc,'CLOCK_EPOCH_CHANGED' if epoch_change else kind)
            self.in_packet = False
        if kind == 'PACKET_STARTED': self.in_packet = True
        if kind == 'FIELD_MODEL_STATUS': self.diagnostic = deepcopy(event['payload']['diagnostic'])
        if kind == 'HISTORY_REFERENCE': self.history_reference = deepcopy(event['payload'])
        if kind == 'DISTRIBUTION':
            self.diagnostic = deepcopy(event['payload']['record']['diagnostic'])
            if self.diagnostic.get('pin') is not None: self.last_pin = self.diagnostic['pin']
        if kind == 'MARKET_BARRIER':
            spot,_ = spot_quote(market,clock.mono_ns)
            if spot: self.tape.add(spot)
            self.in_packet = False
            self.tape.advance(clock.utc)
        elif kind in {'TIMER','HEARTBEAT'} and not self.in_packet:
            self.tape.advance(clock.utc)

    def prediction(self, distribution, value, clock):
        if distribution is None or value['timing_issues']: return None
        if stamp(distribution['available_at']) > stamp(clock.utc) or distribution['diagnostic'].get('status') != 'READY': return None
        record = {'prediction_id':digest({'distribution':distribution,'valuation':digest(value)}),
            'as_of':value['as_of'],'model_hash':digest(distribution),'state':distribution['diagnostic']['state'],
            'spot':distribution['process']['spot'],'pin':distribution['diagnostic']['pin'],
            'distribution':deepcopy(distribution),'selected':value['selected'],
            'known_event_risk':event_risk(self.known_events,value['as_of'],distribution['target_at']),
            'candidates':[{k:r.get(k) for k in ('candidate_id','center','width','cost_points','payoff_point','point_edge','eligible')} for r in value['rows']],
            'timing':deepcopy(value['timing']),'short_horizons':{}}
        for horizon in (1,5):
            if distribution['process']['minutes'] >= horizon:
                mix = marginal(distribution['process'],horizon)
                record['short_horizons'][str(horizon)] = {'target_at':(stamp(distribution['conditioning_as_of'])+timedelta(minutes=horizon)).isoformat(),
                    'mean':sum(c['weight']*c['mean'] for c in mix),'interval80':[quantile(mix,.1),quantile(mix,.9)],'components':mix}
        return record

    def mature(self, market, clock, value=None):
        second = stamp(clock.utc).replace(microsecond=0)
        if value is None and self.last_score_second == second: return []
        self.last_score_second = second
        proposals = []
        spot,_ = spot_quote(market,clock.mono_ns)
        for pred in self.predictions:
            for label,forecast in pred['short_horizons'].items():
                h = int(label)
                if (pred['prediction_id'],h) in self.scored: continue
                due = stamp(forecast['target_at'])
                if stamp(clock.utc) < due: continue
                ref_time = stamp(spot['utc']) if spot else None
                valid = ref_time is not None and 0 <= (ref_time-due).total_seconds() <= 3
                if not valid and (stamp(clock.utc)-due).total_seconds() <= 3: continue
                actual = spot['value'] if valid else None
                state_correct = None
                if valid and pred['pin'] is not None:
                    change = abs(actual-pred['pin'])-abs(pred['spot']-pred['pin'])
                    if pred['state'] == 'CONVERGING_CANDIDATE': state_correct = change < 0
                    elif pred['state'] == 'EXPANDING_CANDIDATE': state_correct = change > 0
                hit = forecast['interval80'][0] <= actual <= forecast['interval80'][1] if valid else None
                proposals.append(('FIELD_SCORE',{'prediction_id':pred['prediction_id'],'horizon_minutes':h,
                    'target_at':forecast['target_at'],'evaluated_at':clock.utc,'outcome':actual,
                    'outcome_ref':deepcopy(spot) if valid else None,'status':'HIT' if hit else 'MISS' if valid else 'UNKNOWN',
                    'error_points':actual-forecast['mean'] if valid else None,'interval80_hit':hit,
                    'known_event_risk':event_risk(self.known_events,pred['as_of'],forecast['target_at']) if self.known_events else [],
                    'state_direction_correct':state_correct,'meaning':'预测区间命中/失配；非独立交易日、非盈利判断'}))
            timing = pred['timing']
            if pred['prediction_id'] in self.wait_scored or not timing.get('verify_at'): continue
            due = stamp(timing['verify_at'])
            if stamp(clock.utc) < due: continue
            delay = (stamp(clock.utc)-due).total_seconds()
            if value is None and delay <= 3: continue
            candidates = {r['candidate_id'] for r in timing.get('fits',[]) or pred['candidates']}
            reasons = []
            if value is None: reasons.append('VALUATION_MISSING')
            if delay > 3 or (value is not None and not 0 <= (stamp(value['as_of'])-due).total_seconds() <= 3):
                reasons.append('TARGET_TIME_MISMATCH')
            if (value or {}).get('timing_issues'): reasons.append('VALUATION_TIMING_INVALID')
            current = {r['candidate_id']:r for r in (value or {}).get('rows',[])}
            edges = []
            if not candidates: reasons.append('CANDIDATE_SET_EMPTY')
            for cid in sorted(candidates):
                row = current.get(cid)
                if not row or row['cost_points'] is None or not row['quote'] or row['quote']['reasons'] or 'INVALID_COMBO_DEBIT' in row['reasons']:
                    reasons.append(cid+':QUOTE_OR_COST_UNAVAILABLE')
                    continue
                # A known risk rejection needs no payoff model to retain cash.
                if set(row['reasons']) & {'COST_CAP','DOMINATED_COST'}: continue
                if not value.get('distribution_hash') or row.get('payoff_point') is None or row.get('actionable_edge') is None or set(row['reasons'])-{'VALUE_NOT_ABOVE_THRESHOLD'}:
                    reasons.append(cid+':MODEL_OR_PAYOFF_UNAVAILABLE')
                    continue
                edges.append(float(row['actionable_edge']))
            complete = not reasons
            observed = max([0.]+edges) if complete else None
            proposals.append(('FIELD_WAIT_SCORE',{'prediction_id':pred['prediction_id'],'target_at':timing['verify_at'],
                'evaluated_at':clock.utc,'predicted_C':timing['C_points'],'observed_H_same_candidates':observed,
                'error_points':observed-timing['C_points'] if observed is not None and timing['C_points'] is not None else None,
                'wait_better_than_then_H':observed > timing['H_points'] if observed is not None and timing['H_points'] is not None else None,
                'status':'OBSERVED' if complete else 'UNKNOWN_COVERAGE_OR_TARGET_TIME',
                'score_version':'FIELD_WAIT_SCORE_V2','reasons':reasons,'candidate_ids':sorted(candidates),
                'meaning':'同候选未来可观察点价值诊断，非未来成交或可实现收益'}))
        return proposals

    def summary(self):
        return {'model':deepcopy(self.diagnostic),'completed_bars':len(self.tape.bars),'gaps':len(self.tape.gaps),
            'predictions':len(self.predictions),'short_scores':len(self.scores),
            'short_hits':sum(r['status']=='HIT' for r in self.scores),
            'short_misses':sum(r['status']=='MISS' for r in self.scores),
            'short_unknown':sum(r['status']=='UNKNOWN' for r in self.scores),
            'wait_scores':len(self.wait_scores),'wait_observed':sum(r['status']=='OBSERVED' for r in self.wait_scores),
            'independent_sessions':1,'history_reference':self.history_reference}


def terminal_scores(engine, settled):
    """Score the originally published distributions, never recompute forecasts."""
    from .field_model import normal_call
    import math
    actual = float(settled['settlement_evidence']['value'])
    rows=[]
    for pred in engine.field.predictions:
        mix=pred['distribution']['components']
        absolute=lambda mean,sd:2*normal_call(mean,sd,0)-mean
        crps=sum(c['weight']*absolute(c['mean']-actual,c['sd']) for c in mix)-.5*sum(
            a['weight']*b['weight']*absolute(a['mean']-b['mean'],math.hypot(a['sd'],b['sd'])) for a in mix for b in mix)
        outcomes=[]
        for c in pred['candidates']:
            realized=max(float(c['width'])-abs(actual-float(c['center'])),0.)
            outcomes.append({**c,'actual_payoff':realized,
                'payoff_prediction_error':realized-float(c['payoff_point']) if c['payoff_point'] is not None else None,
                'hypothetical_net_at_decision_cost':realized-float(c['cost_points']) if c['cost_points'] is not None else None})
        chosen=next((c for c in outcomes if c['candidate_id']==pred['selected']),None)
        eligible=[c for c in outcomes if c['eligible'] and c['hypothetical_net_at_decision_cost'] is not None]
        best=max((c['hypothetical_net_at_decision_cost'] for c in eligible),default=None)
        rows.append({'prediction_id':pred['prediction_id'],'as_of':pred['as_of'],'terminal_outcome':actual,
            'known_event_risk':deepcopy(pred.get('known_event_risk',[])),
            'terminal_error':actual-sum(c['weight']*c['mean'] for c in mix),
            'terminal_interval80_hit':.1 <= cdf(mix,actual) <= .9,'crps_points':crps,'candidates':outcomes,
            'selection_regret_same_observed_eligible_set':best-chosen['hypothetical_net_at_decision_cost'] if chosen and best is not None else None})
    books=settled['books'];a,b=(books[k].get('net_pnl_estimated_fees_usd') for k in ('LOOKAHEAD','FIRST_POSITIVE'))
    return {'classification':'PROSPECTIVE_FIELD_MARKET_DAY','session':engine.plan['session'],'independent_days':1,
        'settlement_evidence':settled['settlement_evidence'],'terminal_predictions':rows,
        'short_scores':engine.field.scores,'wait_scores':engine.field.wait_scores,
        'paired_net_difference_usd':float(a)-float(b) if a is not None and b is not None else None,
        'require_matched_valuation':False,'reason':'TIMING_COMPARISON_SAME_SESSION',
        'error_categories':{
            'state_distribution':{'terminal_predictions':len(rows),'terminal_interval_misses':sum(not r['terminal_interval80_hit'] for r in rows)},
            'known_events':{e['id']:{phase:{'predictions':sum(any(c['id']==e['id'] and c['phase']==phase for c in r['known_event_risk']) for r in rows),
                'terminal_interval_misses':sum(not r['terminal_interval80_hit'] and any(c['id']==e['id'] and c['phase']==phase for c in r['known_event_risk']) for r in rows)}
                for phase in ('BEFORE_EVENT','AFTER_EVENT')} for e in engine.field.known_events},
            'selection':{'scope':'ex_post_same_observed_eligible_set_regret_not_executable_hindsight_profit'},
            'waiting':{'observed':sum(s['status']=='OBSERVED' for s in engine.field.wait_scores)},
            'execution_fees':{'book_statuses':{k:v['status'] for k,v in books.items()},'fees':'ESTIMATED_NOT_ACCOUNT_CONFIRMED'}},
        'trading_edge_established':False}


def write_page(directory, engine, *, health=None, settled=None):
    """Static same-origin-free page; no additional web server or account surface."""
    directory = Path(directory)
    result = engine.result(); value = engine.latest_valuation or {}
    summary = engine.field.summary(); model = summary['model']; timing = value.get('timing',{})
    source = model.get('anchor_mode','MARKET_ONLY')
    esc = lambda x:html.escape(str(x))
    def num(x): return '未知' if x is None else f'{float(x):.3f}'
    translations = {'WARMUP':'分钟数据预热','MIXED':'混合／无锚定收敛证据',
        'CONVERGING_CANDIDATE':'收敛候选','EXPANDING_CANDIDATE':'扩散候选',
        'OBSERVING':'等待','INTENT_PERSISTED':'纸上入场候选／等待后续报价',
        'ASSUMED_FILLED':'已假设成交','EXPIRED_NO_FILL':'意图到期未成交',
        'FILL_UNKNOWN':'执行结果未知','CLOSED_ABSTAIN':'今日结束／未入场','CLOSED_UNKNOWN':'今日结束／数据有缺口'}
    sections = []
    for key,b in result['books'].items():
        decision = b.get('last_decision',{})
        sections.append(f'<article><h3>{key}</h3><strong>{translations.get(b["status"],b["status"])}</strong>'
            f'<p>意图 {b["intent_count"]}/1 · 决策 {b["decisions"]}</p><p>{esc(", ".join(decision.get("reasons",[])))}</p></article>')
    rows = []
    reasons_cn = {'POSITIVE_MODEL_EDGE':'模型点价值为正','NO_MODEL_EDGE':'模型不喜欢当前价格',
        'INPUT_UNAVAILABLE':'本结构输入暂缺','RISK_BUDGET_REJECT':'超过资金预算','WITHIN_BUDGET':'预算内','UNKNOWN':'未知'}
    for r in value.get('rows',[]):
        sense = r.get('sensitivity',{})
        rows.append('<tr>'+''.join(f'<td>{esc(x)}</td>' for x in [
            f'{r["center"]-r["width"]} / {r["center"]}×−2 / {r["center"]+r["width"]}',
            num(r['payoff_point']),num(r['cost_points']),num(r.get('point_edge')),num(r.get('actionable_edge')),
            num(r['max_loss_usd']),reasons_cn.get(r.get('economic_state'),r.get('economic_state')),
            reasons_cn.get(r.get('risk_state'),r.get('risk_state')),
            f'+.25: {num(sense.get("extra_cost_025"))}; +.50: {num(sense.get("extra_cost_050"))}; Pin−5/+5: {num(sense.get("pin_minus_5"))}/{num(sense.get("pin_plus_5"))}; 方差×1.5: {num(sense.get("noise_variance_x150"))}',
            ', '.join(r['reasons']) or '合格'])+'</tr>')
    score_rows = ''.join(f'<tr><td>{esc(r["target_at"])}</td><td>{r["horizon_minutes"]}分钟</td><td>{r["status"]}</td><td>{num(r["error_points"])}</td></tr>' for r in engine.field.scores[-20:])
    decision_rows = ''.join('<tr>'+''.join('<td>'+esc(x)+'</td>' for x in [d['processed_at'],d['strategy_id'],
        translations.get(d['state'],d['state']),d.get('selected'),num((d.get('timing') or {}).get('H_points')),
        num((d.get('timing') or {}).get('C_points')),', '.join(d['reasons'])])+'</tr>' for d in engine.field.decisions)
    prediction_rows = ''.join('<tr>'+''.join('<td>'+esc(x)+'</td>' for x in [p['as_of'],p['spot'],p['state'],
        p['pin'] if p['pin'] is not None else 'MARKET_ONLY',
        [round(v,2) for v in p['distribution']['diagnostic'].get('interval80',[])]])+'</tr>' for p in engine.field.predictions)
    current_health = health or {}
    now = utc_now()
    from .forecast import eligibility, anchor_value
    from .calendar import decision_times
    received_source = engine.source(now)
    usable = received_source if not eligibility(received_source,now,engine.plan['schedule']['close_utc']) else None
    source_info = ('已接收本日Pin '+num(anchor_value(usable)) if usable else '当前来源模式 MARKET_ONLY')
    if received_source:
        source_info += f' · 原统计标签 {received_source["statistic_type"]} · 状态 {received_source["status"]} · 实际接收 {received_source["first_seen_at"]} · 可用 {received_source["available_at"]} · 版本 {received_source["forecast_id"]}'
    next_grid = next((t for t in decision_times({**engine.plan['schedule'],'entry_end_utc':engine.plan['schedule']['close_utc']}) if stamp(t)>stamp(now)),None)
    event_notes = '；'.join(e['label']+' · '+e['at']+' · '+('终值预测跨越该事件' if e['crosses_target'] else '已过事件时点')
        for e in event_risk(engine.field.known_events,now,engine.plan['schedule']['close_utc']))
    phase = ('今日采集结束' if current_health.get('stop_requested') or stamp(now) >= stamp(engine.plan['schedule']['capture_end_utc']) else
             '开盘前等待' if stamp(now) < stamp(engine.plan['schedule']['open_utc']) else
             '数据中断／等待恢复' if not current_health.get('connected') or current_health.get('spot_issue') else '真实行情接收中')
    settlement_text = ('正式SPXW PM结算 '+str(settled['settlement_evidence']['value'])+'；'+
        '；'.join(k+' 估计费后影子净额 $'+str(b.get('net_pnl_estimated_fees_usd')) for k,b in settled['books'].items())
        if settled else '正式结算待发布／核对，终值和收益保持PENDING')
    settlement_link = '<a href="SETTLED_REPORT.md">正式结算与终值评分</a>' if settled else '正式结算待发布／核对'
    content = f'''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta http-equiv="refresh" content="10">
<title>SPXLab · FIELD 真实行情检验</title><style>
body{{font:15px/1.55 system-ui,sans-serif;background:#101827;color:#e8edf5;margin:0;padding:28px}}main{{max-width:1600px;margin:auto}}
h1{{font-size:28px;margin-bottom:6px}}h2{{font-size:19px;color:#91bbff}}p{{color:#bcc9dc}}.tag{{color:#94edc0}}.cards{{display:flex;gap:18px;flex-wrap:wrap}}article{{background:#1b2940;padding:16px;border-radius:8px;min-width:230px;flex:1}}
table{{border-collapse:collapse;width:100%;font-size:13px}}th,td{{padding:10px;border-bottom:1px solid #34415a;text-align:left}}.table{{overflow-x:auto}}.note{{color:#f4ca79}}a{{color:#9ac0ff}}details{{margin:16px 0}}pre{{white-space:pre-wrap;overflow-wrap:anywhere}}</style>
<main><span class="tag">FIELD_PAPER_V1 · 真实行情 / 探索性模型 / 影子账本</span><h1>SPX 现场判断</h1>
<p>本模式无真实仓位、无券商订单。<strong>{phase}</strong> · 更新 {esc(now)} · 会话 {result['session']} · SPX {num(current_health.get('current_spx'))}</p>
<p>行情时点 {esc(current_health.get('spot_observed_at'))} · 模型条件时点 {esc(model.get('spot_at'))} · 决策时点 {esc(value.get('as_of'))}</p>
<p class="note">{esc(event_notes)}{'。当前模型未专门定价事件风险；固定厚尾先验不代表事件风险已受控。' if event_notes else ''}</p>
<p>{esc(source_info)} · 下一评价 {esc(next_grid)}。来源修订不重置每日意图次数；下方显示最近一次模型实际使用的来源。</p>
<h2>{translations.get(model.get('state'),'模型等待')} · {'不使用作者Pin（MARKET_ONLY）' if source=='MARKET_ONLY' else '使用有效Pin '+num(model.get('pin'))}</h2>
<p>模型80%终值区间 {esc([round(x,2) for x in model.get('interval80',[])])}（模型分位区间，非统计置信界）</p>
<div class="cards"><article>有效分钟增量 <strong>{model.get('n_increments',0)} / 至少20</strong><p>κ原始 {num(model.get('kappa_raw'))}；收缩 {num(model.get('kappa_shrunk'))}；κτ {num(model.get('kappa_tau'))}</p></article>
<article>近期方差 <strong>{num(model.get('q'))} 点²/分钟</strong><p>背景/锚定终值标准差 {num(model.get('background_sd'))} / {num(model.get('anchored_sd'))}</p><p>历史同刻方差参照 {num((model.get('historical_same_minute_reference') or {}).get('q'))}</p></article>
<article>剩余期限 <strong>{num(model.get('remaining_minutes'))} 分钟</strong><p>距Pin {num(model.get('distance_to_pin'))} · 最近Pin变化 {num(model.get('pin_change'))}</p></article></div>
<p class="note">权重与厚尾为试运行假设，未校准。预算625美元/组；两个账本为独立反事实，不能相加成账户收益。</p>
<h2>现在进入，还是继续等待</h2><p>H={num(timing.get('H_points'))}点 · C={num(timing.get('C_points'))}点 · {esc(timing.get('status','等待模型'))} · {esc(', '.join(timing.get('reasons',[])))}</p>
<div class="cards">{''.join(sections)}</div><h2>当前真实报价价值表</h2>
<p>候选覆盖 {esc(value.get('coverage',{}))} · {esc(value.get('selection_scope','尚未评价'))}。点EV为模型估计；执行仅以事后合格报价作假设。</p>
<div class="table"><table><tr>{''.join('<th>'+x+'</th>' for x in ['三腿行权价','期望兑付','含费成本','点EV','扣储备EV','最大损失$','经济判断','风险资格','敏感性EV','输入/拒绝原因'])}</tr>{''.join(rows)}</table></div>
<h2>已经判断对与错的地方</h2><p>已发布 {summary['predictions']} 次预测 · 短期区间命中 {summary['short_hits']} · 失配 {summary['short_misses']} · 未知 {summary['short_unknown']} · 等待核验 {summary['wait_observed']}/{summary['wait_scores']}。这些时点只属于1个交易日。</p>
<table><tr><th>目标时间</th><th>期限</th><th>结果</th><th>点预测误差</th></tr>{score_rows}</table>
<p>终值与影子收益：{esc(settlement_text)}。<a href="FIELD_REPORT.md">日报与缺失说明</a> · {settlement_link}</p>
<details><summary>当天全部原始预测（{len(engine.field.predictions)}条）</summary><table><tr><th>时点</th><th>当时SPX</th><th>当时状态</th><th>Pin</th><th>80%终值模型区间</th></tr>{prediction_rows}</table></details>
<details><summary>当天全部决策（{len(engine.field.decisions)}条）</summary><table><tr><th>时点</th><th>账本</th><th>行动</th><th>选中结构</th><th>H</th><th>C</th><th>原因</th></tr>{decision_rows}</table></details>
<details><summary>原始输入、模型、报价及全部决策</summary><p>events.sqlite 追加档案；decision.json 当前账本；最新模型及价值如下。</p><pre>{esc(json.dumps({'model':model,'valuation':value},ensure_ascii=False,indent=2))}</pre></details>
</main></html>'''
    tmp = directory/'FIELD.html.tmp';tmp.write_text(content);tmp.replace(directory/'FIELD.html')
    atomic_json(directory/'field-status.json',{'updated_at':now,**summary,'timing':timing})
    (directory/'FIELD_REPORT.md').write_text(f'# FIELD {result["session"]} 前瞻记录\n\n截至 {now}。真实模式：{result["mode"]}。\n\n'
        f'自动预测 {summary["predictions"]} 次；1/5分钟区间命中 {summary["short_hits"]}、失配 {summary["short_misses"]}、未知 {summary["short_unknown"]}。'
        f'等待模型已核验 {summary["wait_observed"]}/{summary["wait_scores"]} 次。独立市场日仅1日。\n\n'
        + settlement_text+'。费用是估计场景，非账户实际收费。\n\n'
        + '\n'.join(f'- {k}: {b["status"]}；意图 {b["intent_count"]} 次。' for k,b in result['books'].items())+'\n')
