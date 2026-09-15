# SPXLab 首轮运行手册

2026-09-15 · 实现 0.1.0 · 只读行情与五账本假设执行。

最新运行是 `var/2026-09-15/diagnostic-003`。用户要求重试后，加入SIDE_CONFIRMATION_V2和同流RAW_FIELD_V1控制；23项测试通过。002已停止重复采集，原结果及最终回放保留。以下002路径仍可用于原轮回放，当前健康/进程检查使用003。

## 安装与验证

在 `/Users/logan/SPXLab` 执行：

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.lock
.venv/bin/python -m pip install -e .
.venv/bin/python -m unittest discover -s tests -v
```

运行时依赖已锁定。当前28项测试包含最初15项核心验证、8项报价确认验证和5项监控验证：覆盖现金兑付、费用、报价时效与数量、断线与重连、冻结与包边界、选价、事件链和回放，以及监控中断与收盘边界。夹具测试不属于交易效果证据。

## 今日档案

- `var/2026-09-15/preflight`：此前约五分钟连通与字段采集检查，无事前交易决策。
- `var/2026-09-15/forecast-archive-001`：图、配文、来源审查和结构化预测；实际可用时间 15:33:11 UTC。
- `var/2026-09-15/diagnostic-002`：原始盘中诊断，11:40:00–11:40:15 纽约时间。
- `var/2026-09-15/diagnostic-003`：用户要求修复后另行预先固定的重试，11:55:00–11:55:15 纽约时间，含同流 V1 控制。
- `plan.json` 和 `implementation/`：冻结参数、全部运行代码及依赖版本快照。`TESTS.txt` 是开窗前验证。
- `events.sqlite`：SQLite WAL，原始字段和计时器、截止、派生决策均追加，逐条 SHA-256 链。存在单独的接收时间、单调时钟、连接世代及序号。
- `decision.json` / `decision-trace.json` / `decision-digest.json` / `REPORT.md`：可重建决策及报告。
- `health.json` / `process.json` / `process.log`：当前状态与进程信息。进程计划采集至 20:10 UTC（纽约16:10，北京次日04:10）。

行情入口为本机 Gateway `127.0.0.1:4001`。低层连接不主动获取账户/持仓；适配器发送白名单只允许行情订阅/取消、合约、服务器时间、行情类型与握手。交易、撤单消息被拦截；引擎不持有券商对象。本防护是本地适配器约束，不是券商账户权限的替代。

源记录优先 SQLite；Parquet 导出尚未实现。每个数据包处理完才检查组合，截止前冻结 SPX 和预测，1 秒后才可能形成假设成交。单腿天然价不保证真实组合单成交。实时性依据明确的 marketDataType 回调，不能只相信请求值或默认值。

本次合约详情返回 `20260915:0830-20260915:1500`、`US/Central`，即纽约09:30–16:00。实际确认的合约与时段保存在 CONTRACT 事件，不能将该时段硬套到未来半日市。

本任务 heartbeat `spx` 已按用户实时监控要求改为每分钟处理关键告警，独立行情监控器每秒更新。尾盘时间表、命令、故障与来源检查见 [监控手册](MONITORING.md)。北京时间9月16日04:00收盘后改为每15分钟核验结算，保持唤醒至04:30；结算与最终回放完成或达到当日12:00缺口报告截止后停止 heartbeat。

## 回放

```sh
.venv/bin/python -m spxlab.cli replay --directory var/2026-09-15/diagnostic-002
```

回放验证全部可见事件的哈希链，并逐项比较决策结果和派生轨迹。采集期间是一个一致性读取快照；进程结束后再做最终全量回放。若后续代码变更，可用该运行 `implementation/src` 设置 `PYTHONPATH` 执行原版本。不得覆盖原决策修正研究结果。

003新增 `control-decision.json`、`control-trace.json` 和 `CONTROL_REPORT.md`；同一个replay命令对003同时验证两引擎。控制账本是报价规则诊断，不能合并进主五账本。`execution-quality-review.json`仅解释执行区间观察，没有重选交易。

## 停止

读取 `process.json` 的 PID，确认进程命令包含对应运行目录后，发送 SIGTERM。它停止本次采集并释放本次保持唤醒的进程。窗口内停止形成未确定状态；不转换成“没有交易机会”。不关闭用户 Gateway，不改券商状态。

## 收盘与结算

先保留官方结算网页/响应作为本地原件，再创建证据 JSON：`target_date`、`series=SPXW_PM`、`status=CONFIRMED`、`value`、`source_url`、`asset_path`、`asset_sha256`、`retrieved_at`（带时区）和 `reviewed_by`。必须核对原件确实包含对应日期、SPXW PM 值；程序校验域名、哈希和时点不能替代内容审查。网页导航错误、陈旧数据或无法确认时继续 pending。

```sh
.venv/bin/python -m spxlab.cli settle --directory var/2026-09-15/diagnostic-002 --evidence /absolute/path/to/verified-settlement.json
```

每份结算证据写入单独追加日志 `settlements.sqlite`，生成带证据哈希的结果版本及 `SETTLED_REPORT.md`。主行情日志和事前决策不修改。纽约 16:00 之前不得填今日结算。日内最后报价、官方指数收盘和合约正式结算证据分别处理。

入口：[Cboe 周度结算](https://www.cboe.com/index_settlement_values/weeklys_settlement_values/)。不要使用月度页面的 SET（AM）、其他日期或 XSP 四舍五入值替代当天 SPXW PM。

费用场景来源：[IBKR 期权佣金](https://www.interactivebrokers.com/en/pricing/commissions-options.php)、[Cboe 交易所费用](https://www.interactivebrokers.com/en/accounts/fees/CBOEoptfee.php)。默认 SMART、public customer、每腿最低佣金，另含 SPXW 执行附加费、处理费和 Cboe ORF；另设每合约 $0.05 未确认监管费占位，不保证覆盖所有收费。账户类别、税费和固定订阅费用仍未知，因此即使结算完成也是**扣估算费用损益**。

## 尚未验收

稳定跨日运维、每日来源自动采集/修订及人工复核、交易所半日历、实际账户费制、严格目标日期资格、正式统计预注册、券商模拟及真实成交。用户已经授权的首轮工作可继续实现；这些缺口不转换成未经验证的策略优势结论。

## 撤回记录

`diagnostic-001` 在原定 11:36 窗口前撤回，原事件日志证明没有 CUTOFF。原因是复核完整公开费表后补入执行附加费、处理费和 ORF；新版本在 11:40 之前冻结，不覆盖旧计划。详见旧目录 `WITHDRAWN.json`。
