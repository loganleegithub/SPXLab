# FIELD V1 交付与运行

2026-09-16 · U23 · 工程已实现并启动独立冻结进程；真实前瞻验收在今日交易时段完成前保持待验收。

**交付目标是连续判断及其对错记录。当前完成代码、历史输入、工程检查与冻结部署；不能把这些写成已经完成今日真实行情验收。** 本轮依据更正后的FIELD附件和[最小方案](FIELD_V1_PLAN.md)，不再采用此前蓝图作为施工清单。

## 已实现

- 独立FIELD_PAPER_V1：20–60个当前会话有效分钟增量，带符号及收缩κ、平方增量EWMA、背景/锚定厚尾混合；无有效Pin自动MARKET_ONLY。每30秒当前价格重新条件化，参数陈旧或数据缺口保持未知。
- 解析蝶式期望、真实自然价及估计费用、点EV与0.25点储备，预算资格单列。缺其他结构时保留缺失行，可选合格子集并标明范围。旧DV1的校准和完整候选要求不变。
- 120秒一步等待：同一P混合过程的条件更新；三腿中间价单独识别局部Q尺度，当前自然价差锚定。15/31节点差超过0.05点或行动两侧不一致时明确降级FIRST_POSITIVE。不是全天最优停止。
- LOOKAHEAD与FIRST_POSITIVE共享真实采集流，各最多一次固定净限价意图。1秒延迟、10秒TTL、625美元含费预算。下单协议仍关闭；未成交/未知不伪装成交。
- 中文FIELD.html：状态、模型依据、区间、候选、H/C、压力敏感性、全部原始预测与决策、成熟1/5分钟对错、等待核验、结算链接。事件日志保留原始引用；正式结算后统一评分终值预测和两个反事实账本。

压力项“噪声×1.5”指方差q乘1.5；Pin±5仅在有效Pin存在时计算。厚尾权重不是已估计跳跃概率，80%终值区间是模型分位区间，不是参数的统计置信界。

## 实际取得的历史数据

本机昨日4个运行提取2/17/69/179根分钟记录，保持各自缺口和原路径。IB另取得**2026-08-18至09-15共20个交易日、7,800根SPX分钟bar**，无盘中分钟缺口，19个跨日开盘跳空单列；3个顺序只读请求。资料在本机 `var/field-history-2026-09-16/`，实际取得时刻11:41:45 UTC。这属于今天取得的历史参考，不是历史盲回测或今日实时判断。

历史请求只在单独采集脚本准许精确消息20/25；主Collector白名单未扩。参照[IB历史限制](https://interactivebrokers.github.io/tws-api/historical_limitations.html)。模型只读取同刻波动参照，当前局部参数使用今日连续分钟路径。目标有歧义的8日Pin材料未用于主模型校准。

## 工程证据与限制

77项检查通过：原66项继续通过，新11项覆盖收敛/扩散/无Pin、缺口/陈旧参数、当前条件更新、混合过程塔式一致性、点估值准入与旧DV1隔离、正常ENTER/WAIT、Q故障降级、预算/一次意图、自动生产接收时钟、原始事件到假设成交及冻结回放、成熟预测命中/失配/未知。完整成交链目前仅为工程夹具，不能算真实机会。

代码未新增运行依赖。生产计算仅新增field_model/timing两个模块；field模块承载本模式记录和单页，其余复用原运行链。README改为当前入口；STATUS与U21交付注明历史适用范围。没有删除仍用于OBSERVE/DV1或历史回放的代码、冻结快照及证据。开发临时页面与缓存在上线后清理。

浏览器URL安全策略阻止本地FIELD页面导航，故只完成静态页面核对，**视觉验收未完成**。未通过其他浏览器/HTTP转发绕过该限制。

## 运行与恢复

部署记录：11:56UTC启动001；开盘前发现H将缺输入和负值压成0的展示口径，已修正并另冻结002。001只有预热前记录、无市场预测或意图，保留原快照，不计作交易样本。当前计划：纽约2026-09-16。FIELD独立目录 `var/2026-09-16/field-v1-002`，client ID 27218；原observe-r2-003继续原冻结运行。运行器09:25预热、09:30积累分钟、10:05起每30秒决策、15:30停止新意图；继续预测与评分至16:00，16:10停止采集。北京时间对应21:25/21:30/22:05/次日03:30/04:00/04:10。

从 `/Users/logan/SPXLab` 启动新授权运行的单条命令：

```sh
.venv/bin/python -m spxlab.cli field-shadow --plan examples/field/2026-09-16.json --directory var/2026-09-16/field-new-run
```

新目录自动冻结。实际已部署目录只用恢复命令，不另开目录重试当天意图：

```sh
.venv/bin/python scripts/round2_check.py --directory var/2026-09-16/field-v1-002
.venv/bin/python scripts/round2_start.py --directory var/2026-09-16/field-v1-002
```

启动器加载该运行的implementation快照，排他锁阻止第二个写入者，caffeinate只随该进程存在。恢复创建新epoch，在途意图变为未知，已使用的每日意图不会重开；模型需重新满足连续分钟预热。原始数据缺口不补造。模型自动生产，无需每日编辑model-inbox；有效Pin可走既有source-inbox及真实接收时钟。

单页在运行目录FIELD.html，紧凑状态在health.json/field-status.json，全部证据在events.sqlite。停止后回放并结算：

```sh
.venv/bin/python -m spxlab.cli replay-frozen --run var/2026-09-16/field-v1-002 --output var/2026-09-16/field-v1-final-proof
.venv/bin/python -m spxlab.cli settle-all --run var/2026-09-16/field-v1-002 --evidence /absolute/path/to/official-evidence.json
```

正式证据沿用[第二轮结算字段](ROUND2_RUNBOOK.md#收盘结算与最终证据)：当日SPXW PM、Cboe/S&P原件、hash、实际取得时刻、复核者；不能用AM SET或最后报价。SPXW到期通常16:00停止，半日13:00，参照[Cboe规格](https://www.cboe.com/tradable-products/sp-500/spx-options/spx-specifications)。统一结算生成SETTLED_REPORT.md和settlement-field-*.json，包含全部终值误差、CRPS、同候选事后选价遗憾、等待和执行/费用分类；其事后指标不是可实现收益。

正常收盘后不重启、不自动进入次日。官方数据未发布时保留PENDING并间隔至少15分钟核验。最终交付需补真实模型数、有效报价覆盖、H/C成功/降级、具体命中和失配样例、意图/成交实况、正式结算或确实缺失说明。

同任务5分钟心跳 `spx-field-v1-9-16` 已创建，正常不刷屏，盘中取得实测记录并在收盘完成核验后停止。旧观察任务的心跳当前暂停，但其冻结采集进程仍在；本轮不会切换它的代码。定时接续需要机器开机及桌面应用运行，见[官方定时任务说明](https://learn.chatgpt.com/docs/automations?surface=app)。
