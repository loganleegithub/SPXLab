# 第二轮运行手册

状态：T0–T5离线工程已验收；T6为纽约2026-09-16观察模式，完整市场日尚未结束。授权见[U17/U18](governance/DECISIONS.md)。[实施和限制](ROUND2_IMPLEMENTATION.md)为当前交付入口。以下命令在 `/Users/logan/SPXLab` 执行；输出目录必须选未使用的新路径。历史002/003/004不改写。

## 离线命令

```sh
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python -m spxlab.cli evaluate --bundle examples/round2/value-optimum.json --output var/my-value-check
.venv/bin/python -m spxlab.cli evaluate --bundle examples/round2/cash.json --output var/my-cash-check
.venv/bin/python -m spxlab.cli evaluate --bundle examples/round2/bounds.json --output var/my-bounds-check
.venv/bin/python -m spxlab.cli dataset-check --manifest examples/round2/dataset-not-ready.json
.venv/bin/python -m spxlab.cli validate-plan --plan examples/round2/observe-2026-09-16.json
.venv/bin/python -m spxlab.cli shadow --plan examples/round2/synthetic-plan.json --events examples/round2/synthetic-events.json --directory var/my-synthetic-run
.venv/bin/python -m spxlab.cli replay-frozen --run var/my-synthetic-run --output var/my-synthetic-proof
.venv/bin/python -m spxlab.cli compare --study examples/round2/synthetic-study.json --output var/my-comparison
```

`evaluate`保存完整输入、JSON价值表和中文报告。`shadow --events`是显式合成夹具驱动器，仅接受 `SYNTHETIC_REPLAY_V1`；不是券商模拟或真实市场运行入口。`build-distribution --manifest --context --output`生成按交易日等权的简单经验残差分布，仍是EXPLORATORY/POINT_ONLY，不能自动取得DV1入场资格。`attribute --bundle --output`接受`evaluation.location_attribution`参数，输出固定结构下的位置、形状与成本恒等分解。样本CRPS、分位覆盖/宽度、离散PIT区间、直接兑付误差及重选消融见`diagnostics.py`，诊断与策略收益分开。

`compare`的内联行只允许标记为合成夹具；真实研究必须引用有hash的独立运行及结算文件。混合合成、观察、正式影子模式会失败。一次声明一个主比较、全部计划日和训练日，缺失保持null，不将反事实账本相加。

U21新增：`scripts/prepare_reviewed_dataset.py`整理经核对的真实来源/结算，并可生成单截面事后价值表；`value-shadow --plan --directory`为独立预注册的固定时点真实影子入口。模型通过新run的`model-inbox/`接入，核验artifact hash并保留实际接收时间。详细输入、命令及未完成的真实业务验收见[实战施工交付](research/PRACTICAL_BUILD_DELIVERY.md)。本节新增能力未部署到下面已经冻结的9月16日观察运行。

## 9月16日观察运行

冻结目录：[observe-r2-003](../var/2026-09-16/observe-r2-003/plan.json)。源码、运维脚本、依赖和环境记录在该目录`implementation/`与`run-manifest.json`；启动器自动使用它们，即使当前工作区后来有改动，也不静默切换实现。当前有效版本为PACKET_AND_SOURCE_V3，包含研究报告分类校验、完整行情包截止检查及含糊来源的G0资格检查。旧observe-r2-001及002已在开盘前停止并保留记录，不恢复它。

| 检查点 | 纽约9月16日 | 北京时间 |
|---|---|---|
| 预热/只读连接 | 09:25 | 9月16日21:25 |
| 常规开盘 | 09:30 | 21:30 |
| 同期评价开始 | 10:05 | 22:05 |
| 评价结束 | 15:30 | 9月17日03:30 |
| 当日SPXW PM目标 | 16:00 | 04:00 |
| 采集停止 | 16:10 | 04:10 |

每30秒评价，固定时点10:05；250ms为冻结的本地决策处理容差，不是行情网络延迟保证。迟到记`MISSED_DECISION_DEADLINE`，不回填。包内不评价；包跨过截止且当前投影含截止后字段时，记`POST_DEADLINE_PACKET_INPUT`，不使用未来投影决定。观察模式禁止意图，七个账本只记录状态/输入资格；不能将零意图解释成策略收益为零。

本机运行器已启动等待预热，并用限定进程寿命的`caffeinate`保持唤醒。Gateway预检已通过4001连接和当日合约时段核对；盘中订阅资格和连续报价仍待真实验收。机器/Gateway断电或认证失效可能造成不可恢复的数据缺口；人工认证由用户完成，系统不操作账户。

紧凑检查和恢复：

```sh
.venv/bin/python scripts/round2_check.py --directory var/2026-09-16/observe-r2-003
.venv/bin/python scripts/round2_start.py --directory var/2026-09-16/observe-r2-003
```

先检查再恢复，运行器持有排他文件锁。查看`health.json`的有效SPX及其时间，null不是零；`connected=true`本身不证明价格实时。重启回放已提交事件，新epoch终止旧待定意图，不重开窗口。异常写盘会终止writer，不保存未提交状态。订阅最多40条期权；当前候选最多4个中心、12个腿合约。数据缺失不缩小DV1/CHEAP主候选全集；独立现价诊断可继续。

心跳ID `spx-9-16`附于当前任务，每5分钟检查紧凑状态；只在变化有意义时反馈。09:55附近、12:30后、15:30后各检查一次来源，记录已检查时间，避免反复浏览。旧9月15日心跳保持暂停。正常收盘后不重启采集，不自动进入9月17日纽约会话。

## 来源录入与可用时间

来源适配口是`source-inbox/*.json`，运行器验证资产实际hash、保留原件副本，并将系统实际收到/校验的时间作为available_at下限。不得将网页发布时间或此前人工看到的时间当成系统已可用。来源需要原文、图、URL、取得时间，明确目标日期、SPXW PM及中位数含义。Tomorrow/Today矛盾尚未解决时保存旁证及原因，不能猜成VALIDATED或拿前一日7604代替。

可参考下面字段结构，但必须用真实取得的数值、时间、hash及资产路径，不能直接复制占位值进入当天档案：

```json
{
  "schema_version": 2,
  "forecast_id": "unique-source-revision-id",
  "source_product_id": "BALDER_SPX_PM_MEDIAN",
  "model_version": "MANUALLY_REVIEWED_POINT_SOURCE_V1",
  "target_session": "2026-09-16",
  "target_at": "2026-09-16T20:00:00+00:00",
  "series": "SPXW_PM",
  "statistic_type": "median",
  "median": "REPLACE_WITH_REVIEWED_NUMBER",
  "gamma": "UNKNOWN",
  "first_seen_at": "REPLACE_WITH_ACTUAL_RECEIPT",
  "validated_at": "REPLACE_WITH_ACTUAL_REVIEW_TIME",
  "raw_assets": [{"path": "/absolute/path/to/asset", "sha256": "REPLACE_WITH_HASH", "received_at": "REPLACE_WITH_RECEIPT", "source_url": "REPLACE_WITH_ACTUAL_POST_URL"}],
  "status": "VALIDATED",
  "reviewed_by": "REPLACE_WITH_REVIEWER",
  "supersedes": null
}
```

普通修订使用新forecast_id并引用supersedes；撤回用RETRACTED，保留原件。修订不改旧决策、限价或成交状态。合法中位数来源仍不是完整P分布。自动读图、未来转移模型和正式经济门槛不在本次观察中补造。

## 收盘、结算与最终证据

16:10后检查停止记录及`capture-seal.json`。最终回放只做一次，写新目录：

```sh
.venv/bin/python -m spxlab.cli replay-frozen --run var/2026-09-16/observe-r2-003 --output var/2026-09-16/observe-r2-final-proof
```

正式结算使用[Cboe SPXW PM周度结算](https://www.cboe.com/index_settlement_values/weeklys_settlement_values/)或对应S&P官方来源，保存真实原件。必须核对2026-09-16、SPXW PM及数值，不用AM SET、XSP、前日值或最后报价替代。16:00前不得结算；发布延迟则PENDING，每次检查至少隔15分钟。

结算证据字段：`target_session, series, value, status=CONFIRMED, source_url, asset_path, asset_sha256, retrieved_at, reviewed_by, supersedes`。第一次supersedes为null；更正填写上一次revision_id。日期采用当日历关闭时间，支持半日13:00。合成运行只允许SYNTHETIC_FIXTURE证据。

```sh
.venv/bin/python -m spxlab.cli settle-all --run var/2026-09-16/observe-r2-003 --evidence /absolute/path/to/reviewed-evidence.json
```

命令自动加载冻结实现，统一结算所有账本；相同证据幂等返回，更正另建revision，不改决策。固定成本未知和账户实际收费未知单独保留。完成正式证据、回放、缺口汇总及`docs/2026-09-16-ROUND2-CLOSEOUT.md`后暂停心跳；最迟北京时间9月17日12:00若仍有缺口，报告事实并暂停。结束时核对`process.json`，只清理本次仍存活的唤醒进程。
