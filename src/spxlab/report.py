"""Small, regenerable report. Never substitutes a quote for official settlement."""
from .engine import D

NAMES={'S':'现价','F':'预测中点','FG':'预测＋筛选','FP':'预测＋选价','FGP':'预测＋筛选＋选价'}


def render(result):
    lines=[f"# SPXLab 当日影子记录：{result['experiment_id']}", '',
           f"模式：`{result['mode']}`。决策截止：{result['cutoff_utc']}。",
           '', '这是假设执行的工程诊断；没有向券商发送委托。', '',
           '| 账本 | 状态 / 原因 | 中心 / 翼宽 | 支付点数 | 含估算费用点数 | 扣估算费用损益 USD |',
           '|---|---|---|---|---|---|']
    for name,b in result['books'].items():
        q=b.get('fill') or b.get('selection') or b.get('intent') or {}
        net=b.get('net_pnl_estimated_fees_usd')
        lines.append(f"| {name} {NAMES[name]} | {b['status']} / {b.get('reason','—')} | "
                     f"{q.get('center',b.get('center','—'))} / {q.get('width','—')} | "
                     f"{q.get('debit_points','—')} | {q.get('all_in_points','—')} | "
                     f"{net if net is not None else '待定 / 未知'} |")
    lines+=['',f"结算状态：`{result['settlement_status']}`。",'',
            '## 解释边界','',
            '- 今日 10:05 纽约窗口已错过；本记录只验证提前固定的盘中窗口。',
            '- 成交假设：单腿可见买卖盘合成，延迟后再次检查价格与数量；不证明组合单实际可成交。',
            '- 手续费使用公开费率场景；账户类别、税费和固定订阅成本尚未确认。损益为估算。',
            '- G0 只接受 LONG GAMMA；P0 从预测中心及其上下 5 点候选中选择成本最低者。两者是自有简化规则。',
            '- 缺失观察为 INDETERMINATE，不能当作没有机会、亏损为零或策略无效。',
            '- 五账本是并列反事实，不能把收益相加当作一个账户。']
    if result.get('quote_policy'):
        lines+=['',f"报价有效性版本：`{result['quote_policy']}`。"]
        if result['quote_policy']=='SIDE_CONFIRMATION_V2':
            lines+=['同侧数量更新确认已初始化且未撤回的同侧价格；原始价格时间不修改，确认事件另存。成交侧确认须在1秒内，对侧2秒内，跨腿确认差≤1秒；断线、类型变化、换订阅或撤回后必须重新初始化。']
    if result.get('execution_policy'):
        lines+=['',f"等待策略：`{result['execution_policy']}`；意图最长 {result['intent_lifetime_seconds']} 秒，仍受全局15秒窗口限制，至少等待1秒后复核。"]
    frozen=result.get('frozen_inputs') or {}
    if frozen:
        spot=frozen.get('spot') or {}
        forecast=frozen.get('forecast') or {}
        lines+=['','## 冻结输入','',f"- 同时点 SPX：{spot.get('value','未知')}；输入事件 {spot.get('seq','未知')}。",
                f"- 作者 median：{forecast.get('median','未知')}；预测可用时间：{forecast.get('available_at','未知')}。",
                f"- 原帖：{forecast.get('source_url','未知')}。",
                f"- 原始资料 SHA-256：`{forecast.get('image_sha256','未知')}`。",
                f"- 资料状态：`{forecast.get('status','未知')}`；复核者：{forecast.get('reviewed_by','未知')}。"]
        lines += [f'- 来源假设：{x}' for x in frozen.get('source_assumptions',[])]
    lines+=['','## 数据质量','']
    for name,b in result['books'].items():
        lines.append(f"- {name}：{b.get('quality_rejections',{})}；观察中断：{b.get('observation_gap',False)}。")
    evidence=result.get('settlement_evidence')
    if evidence:
        lines+=['','## 结算证据','',f"结算值：{evidence['value']}；来源：{evidence['source_url']}。"]
    fills=[(n,b['fill']) for n,b in result['books'].items() if 'fill' in b]
    if fills:
        lines+=['','## 额外成本敏感性','',
                '在本表费用估算之外，每增加 0.25 / 0.50 / 1.00 点执行成本，每份蝶式损益再减少 $25 / $50 / $100。',
                '固定数据订阅费未分摊；不能将本表解释为账户最终净收益。']
        for name,q in fills:
            cost=D(q['all_in_points']);w=D(q['width']);k=D(q['center'])
            lines.append(f"- {name}：假设最大亏损 ${cost*100}；最大收益 ${(w-cost)*100}；估算到期盈亏平衡 {k-w+cost} / {k+w-cost}。")
    return '\n'.join(lines)+'\n'
