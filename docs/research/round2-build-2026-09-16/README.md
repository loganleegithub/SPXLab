# 第二轮实现验收证据

2026-09-16；这是离线工程和预检证据，真实纽约9月16日的完整日验收仍待发生。主入口：[实施记录](../../ROUND2_IMPLEMENTATION.md)、[运行手册](../../ROUND2_RUNBOOK.md)。原始行情与运行快照保留在项目var目录，不作为可再分发数据包提交。

| 证据 | 实际证明的范围 |
|---|---|
| [tests.log](tests.log) | 60项单元/集成测试：原35项及新增25项；数学、资格、执行、时钟/完整包边界、恢复、结算、日级评价 |
| [value-optimum](value-optimum/REPORT.md)、[cash](cash/REPORT.md)、[bounds](bounds/REPORT.md) | 三种可复跑合成价值输出，不是市场机会 |
| [synthetic-decision](synthetic-decision.json)、[synthetic-settlement](synthetic-settlement.json) | 合成共同候选→锁定限价→假设成交→全账本结算；来源门未知保持null |
| [synthetic-replay](synthetic-replay.json)、[当前代码改动后的快照分派](frozen-dispatch-after-current-change.json) | 独立只读进程加载旧快照；不用当前实现代替；旧结果不覆盖 |
| [旧002](legacy-002.json)、[旧003](legacy-003.json)、[旧004](legacy-004.json) | 349,553 / 1,145,342 / 2,212,596条事件及主控制结果/轨迹匹配 |
| [历史保护核验](protected-history.json) | 前轮审查记录的运行保护文件hash全部未变 |
| [方案历史核验](plan-history-verification.json) | 被接受的原方案完整副本与原hash一致；活跃方案仅推进状态和引用 |
| [数据就绪](dataset-readiness.json) | 无已导入的合格标准化成熟样本；DATA_NOT_READY，未声称没有历史行情 |
| [日级比较](comparison/REPORT.md) | 负差与缺失日保留，不计算伪完整资金曲线；证据类型SYNTHETIC_FIXTURE |
| [Gateway预检](gateway-preflight.json) | 本机只读连接及当日SPXW PM合约/日历一致；不证明盘中流连续性 |
| [最终性能](benchmark-packet-v2.json) | 历史峰值回调形状映射到合成12腿/4候选投影；本机吞吐/分段时延/无静默丢失，非网络延迟 |
| [性能日志回放](benchmark-packet-v2-replay.json) | 最终性能运行的完整事件和派生结果一致，使用其冻结源码 |
| [最终冻结计划](observe-frozen-plan-v3.json)、[开盘前健康](observer-preopen-check-v3.json) | 已冻结、启动等待09:25纽约预热；观察模式，无意图 |
| [旧预热运行最终回放](superseded-preopen-replay.json) | 001在包边界修正后于开盘前停止，源码/停止记录保留；不算另一个市场日 |
| [交付检查](delivery-checks.json)、[产物清单](artifact-manifest.json) | 文档链接、代码/示例/证据hash与有效观察快照；不替代T6真实验收 |

`benchmark.json`为早期仅持久化测试，`benchmark-full-projection.json`/`benchmark-final.json`为后续完整投影版本；全部保留，不择优替换性能结果。性能复测覆盖PACKET_BOUNDARY_V2，其后V3只收紧含糊来源G0资格；观察路径及持久化未变，最终60项功能测试覆盖V3。完整投影性能运行的原SQLite和快照在 `var/round2-tests/peak-004`。

生产依赖没有新增；采用Python标准库确定性实现，不将参考包的SciPy演示变成实时依赖。历史研究目录与外部附件保持原状；其中施工指令不自动生效。没有Git推送、外部消息、券商模拟/实盘或自动下一研究轮。
