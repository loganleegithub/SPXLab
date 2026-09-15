"""Read-only shadow supervision. Never changes a decision or talks to a broker."""
import argparse
import fcntl
import json
import math
import os
import signal
import sqlite3
import time
import uuid
from datetime import datetime,timezone
from pathlib import Path

from spxlab.events import EventStore,atomic_json,utc_now


def epoch(value):
    stamp=datetime.fromisoformat(value)
    if stamp.tzinfo is None:raise ValueError('Timezone required')
    return stamp.timestamp()


class Monitor:
    def __init__(self,cfg):
        self.cfg=cfg;self.close=epoch(cfg['close_utc'])
        self.levels={str(x['value']):{'position':None,'candidate':None,'since':None} for x in cfg['levels']}
        self.phase=None;self.bad_since=None;self.feed_alerted=False;self.last_step=None

    def step(self,health,now,mono):
        alerts=[];remaining=self.close-now
        phase=('POST_CAPTURE' if remaining<=-600 else 'CLOSE_PENDING' if remaining<=0 else
               'T5' if remaining<=300 else 'T15' if remaining<=900 else 'T30' if remaining<=1800 else
               'T60' if remaining<=3600 else 'MONITORING')
        if self.phase!=phase:
            self.phase=phase
            alerts.append({'kind':'PHASE','phase':phase,'remaining_seconds':remaining})
        gap=self.last_step is not None and mono-self.last_step>5
        self.last_step=mono
        reason=None;ref=None;cid=None
        if gap and remaining>0:
            alerts.append({'kind':'SUPERVISION_GAP','reason':'Monitor polling paused for more than five seconds'})
        try:
            health_age=now-epoch(health['updated_at'])
            cid=next(cid for cid,c in health['contracts'].items() if c['symbol']=='SPX' and c['sec_type']=='IND')
            ref=health['fields'][cid]['last']
            price=ref['value'];age=now-epoch(ref['utc'])
            if not health['connected']:reason='DISCONNECTED'
            elif not 0<=health_age<=5:reason='STALE_COLLECTOR_HEALTH'
            elif health['types'].get(cid)!=1:reason='NOT_CONFIRMED_REALTIME'
            elif ref['generation']!=health['generation']:reason='OLD_GENERATION'
            elif price is None or not math.isfinite(price) or price<=0:reason='INVALID_SPX'
            elif not 0<=age<=5:reason='STALE_SPX'
        except (KeyError,StopIteration,ValueError,TypeError):
            reason='MISSING_OR_INVALID_HEALTH'
        good=reason is None and remaining>0
        if not good or gap:
            for level in self.levels.values():level.update(position=None,candidate=None,since=None)
        if remaining>0 and not good:
            if self.bad_since is None:self.bad_since=mono
            if mono-self.bad_since>=10 and not self.feed_alerted:
                self.feed_alerted=True
                alerts.append({'kind':'FEED_UNAVAILABLE','reason':reason})
        elif good:
            self.bad_since=None
            if self.feed_alerted:
                alerts.append({'kind':'FEED_RECOVERED','quote_seq':ref['seq'],'price':ref['value']})
                self.feed_alerted=False
            for spec in self.cfg['levels']:
                key=str(spec['value']);state=self.levels[key];delta=ref['value']-spec['value']
                side='ABOVE' if delta>=self.cfg['hysteresis_points'] else 'BELOW' if delta<=-self.cfg['hysteresis_points'] else None
                if state['position'] is None:
                    if side is not None:state['position']=side
                    continue
                if side is None or side==state['position']:
                    state.update(candidate=None,since=None);continue
                if state['candidate']!=side:
                    state.update(candidate=side,since=mono)
                elif mono-state['since']>=self.cfg['confirmation_seconds']:
                    alerts.append({'kind':'LEVEL_TRANSITION','level':spec['value'],'label':spec['label'],
                                   'from':state['position'],'to':side,'price':ref['value'],'quote_seq':ref['seq'],
                                   'confirmation_seconds':self.cfg['confirmation_seconds']})
                    state.update(position=side,candidate=None,since=None)
        fields=health.get('fields',{}).get(cid,{}) if cid else {}
        status={'updated_at':datetime.fromtimestamp(now,timezone.utc).isoformat(),
                'mode':'SHADOW_MONITOR_NO_POSITIONS','phase':phase,'minutes_to_close':round(remaining/60,2),
                'data_status':'LIVE' if good else 'SESSION_CLOSED' if remaining<=0 else reason,
                'current_spx':ref['value'] if good else None,'last_observation':ref,
                'collector_updated_at':health.get('updated_at'),'collector_events':health.get('event_count'),
                'reported_day_high':fields.get('high',{}).get('value'),
                'reported_day_low':fields.get('low',{}).get('value'),
                'reference_median':self.cfg['reference_median'],
                'distance_from_median':round(ref['value']-self.cfg['reference_median'],2) if good else None,
                'levels':[{**spec,'monitor_state':self.levels[str(spec['value'])]['position'],
                           'distance':round(ref['value']-spec['value'],2) if good else None} for spec in self.cfg['levels']],
                'real_positions':'NONE_USER_CONFIRMED','new_entries_allowed':False,
                'settlement_status':'PENDING_OFFICIAL_PM_SETTLEMENT'}
        return status,alerts


def render(status,books,cfg):
    price=status['current_spx']
    lines=['# SPX 收盘监控','',f"更新：{status['updated_at']} · 状态：{status['data_status']} · 阶段：{status['phase']}",'',
           f"**SPX：{price if price is not None else '当前报价不可用'}** · 距纽约收盘 {status['minutes_to_close']} 分钟。",'',
           '用户已确认没有真实仓位。当前只监督既有影子实验，不新增入场、不把意图当成交。','',
           f"距原预测中位数7604：{status['distance_from_median']} 点。收盘前的距离不是最终预测误差。",'',
           '| 参考位 | 含义 | 当前距离 |','|---|---|---|']
    lines += [f"| {x['value']} | {x['label']} | {x['distance']} |" for x in status['levels']]
    lines+=['','这些是已归档原帖的参考位；不是实时重算Gamma，也不自动触发交易。7575/7633是终值区间边界，不是日内必守边界。',
            '','## 已冻结账本','', '| 账本 | 状态 | 原因 |','|---|---|---|']
    lines += [f"| {name} | {b['status']} | {b.get('reason','—')} |" for name,b in books.items()]
    lines+=['','## 收盘行动','',
            '- 数据不可用：暂停对当前市场的判断，保留最后观测时间并等待恢复。',
            '- 最后30/15/5分钟：核对行情健康、源预测版本与全部账本，未确认成交继续保持未知。',
            '- 纽约16:00（北京时间09-16 04:00）：保存最后观测，不当作正式结算。',
            '- 取得官方SPXW PM值后：分别核对预测中位数误差、终值区间命中与账本状态；不能用无成交日证明策略收益。',
            '',f"行情监控每{cfg['poll_seconds']}秒更新；任务每分钟检查告警，关键通知可能有调度延迟。",'',
            '原始价格与决策：[重试结果](../diagnostic-003/COMPARISON.md)。']
    ref=status.get('last_observation')
    if ref:
        lines+=['',f"最后观测：{ref['value']}，时间{ref['utc']}；非正式结算值。"]
    return '\n'.join(lines)+'\n'


def run(cfg,directory):
    directory=Path(directory);directory.mkdir(parents=True,exist_ok=True)
    lock=(directory/'monitor.lock').open('a')
    fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    atomic_json(directory/'config.json',cfg)
    store=EventStore(directory/'events.sqlite');run_id=str(uuid.uuid4());monitor=Monitor(cfg)
    stopped=False
    def stop(*_):
        nonlocal stopped
        stopped=True
    for sig in (signal.SIGINT,signal.SIGTERM):signal.signal(sig,stop)
    store.append('MONITOR_STARTED',{'config':cfg},run_id=run_id)
    books=json.loads((Path(cfg['run_directory'])/'decision.json').read_text())['books']
    previous_quote=None
    try:
        while time.time()<epoch(cfg['stop_utc']) and not stopped:
            try:health=json.loads((Path(cfg['run_directory'])/'health.json').read_text())
            except (OSError,ValueError):health={}
            status,alerts=monitor.step(health,time.time(),time.monotonic())
            for alert in alerts:
                store.append('ALERT',alert,run_id=run_id)
            ref=status['last_observation']
            if ref and ref['seq']!=previous_quote:
                store.append('SPX_OBSERVATION',{'reference':ref,'data_status':status['data_status']},run_id=run_id)
                previous_quote=ref['seq']
            atomic_json(directory/'status.json',status)
            tmp=directory/'LIVE.md.tmp';tmp.write_text(render(status,books,cfg));os.replace(tmp,directory/'LIVE.md')
            time.sleep(cfg['poll_seconds'])
    finally:
        store.append('MONITOR_STOPPED',{'requested':stopped},run_id=run_id)
        atomic_json(directory/'stopped.json',{'recorded_at':utc_now(),'requested':stopped})
        store.close();lock.close()


def check(directory):
    directory=Path(directory)
    cursor=json.loads((directory/'notification-cursor.json').read_text()).get('seq',0) if (directory/'notification-cursor.json').exists() else 0
    db=sqlite3.connect(f'file:{directory.resolve()}/events.sqlite?mode=ro',uri=True)
    alerts=[]
    for seq,body in db.execute('SELECT seq,body FROM events WHERE seq>? ORDER BY seq',(cursor,)):
        e=json.loads(body)
        if e['event_type']=='ALERT':alerts.append({'seq':seq,'recorded_at':e['recorded_at'],**e['payload']})
    db.close()
    return {'status':json.loads((directory/'status.json').read_text()),'unseen_alerts':alerts}


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);sub=parser.add_subparsers(dest='cmd',required=True)
    p=sub.add_parser('run');p.add_argument('--config',required=True);p.add_argument('--directory',required=True)
    p=sub.add_parser('check');p.add_argument('--directory',required=True)
    p=sub.add_parser('ack');p.add_argument('--directory',required=True);p.add_argument('--seq',type=int,required=True)
    args=parser.parse_args()
    if args.cmd=='run':run(json.loads(Path(args.config).read_text()),args.directory)
    elif args.cmd=='check':print(json.dumps(check(args.directory),ensure_ascii=False,indent=2))
    else:
        current=Path(args.directory)/'notification-cursor.json'
        prior=json.loads(current.read_text()).get('seq',0) if current.exists() else 0
        atomic_json(current,{'seq':max(prior,args.seq),'recorded_at':utc_now()})
