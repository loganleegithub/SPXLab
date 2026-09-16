# SPXLab

SPX 概率预测与自动交易研究项目。最终路线：持续采集 → 影子验证 → 模拟执行 → 有限资金自动交易 → 有条件扩展。

**当前状态：9 月 15 日盘中工程诊断已完成官方结算与最终回放。SPXW PM 为 7585.73，今日无确认的影子成交。用户没有真实仓位。**

**第二轮施工：T0–T5离线工程检查通过，60项测试通过。** [实施记录](docs/ROUND2_IMPLEMENTATION.md)包含价值工具、来源/数据资格、固定限价影子闭环、冻结回放、结算更正和日级评价。[纽约9月16日观察验收](docs/ROUND2_RUNBOOK.md)已冻结并进入预热前等待；完整市场日仍待完成，正式P模型和经济优势未建立。

2026-09-15 用户明确要求“尽快开始施工，验证今天的交易”。已建立独立 Python 环境、只读 IB 行情采集、事件档案、五账本决策、费用估算、结算证据录入和精确回放。原交接包仍保持原位。当前没有券商下单功能。

## 今日运行

- **[今日系统复盘与作者新帖分析](docs/2026-09-15-RETROSPECTIVE.md)**：完整操作时间线、入场限制、定价差距及收盘后评论；[下一轮旧提案](docs/governance/NEXT_SHADOW_PLAN.md)已被第二轮接受方案整合；下列为9月15日历史结果。
- **[收盘结算与五账本总结](docs/2026-09-15-CLOSEOUT.md)**：预测误差 18.27 点、终值区间命中，分别保留各运行和控制账本；未知成交仍为未知。
- [入场是否过严](docs/governance/ENTRY_CONSTRAINT_REVIEW.md)：作者旧帖复核、成本上限与意图寿命的证据；原冻结账本不变。
- [监控最后状态](var/2026-09-15/monitor-close/LIVE.md)、[监控规则与结束记录](docs/MONITORING.md)：采集及监控已按计划结束。
- **最新窗口：[004 主/控制结果](var/2026-09-15/diagnostic-004/COMPARISON.md)**。10 秒与 3 秒主/控制均规则拒绝，没有触发等待差异；最终 2,212,596 条事件回放通过。

- [施工与验收状态](docs/STATUS.md)：今天得到什么、数据契约问题、哪些已实现及仍未验收。
- [运行报告](var/2026-09-15/diagnostic-002/REPORT.md)：纽约 11:40（北京时间 23:40）15 秒诊断，3个规则拒绝、2个数据条件未确定，没有假设成交。
- [最新事前参数](var/2026-09-15/diagnostic-004/plan.json)、[报价修复契约](docs/governance/QUOTE_CONFIRMATION_V2.md)、[实现基线](docs/governance/IMPLEMENTATION_BASELINE.md)。
- [运行手册](docs/RUNBOOK.md)：命令、证据、停止与结算流程。

当天 10:05 窗口已错过。盘中诊断单列，作者目标日期仍带解释假设；费用尚为公开费率场景，不能宣称正式策略验证或实际净收益。

## 从这里阅读

| 文档 | 回答的问题 |
|---|---|
| [收敛门与无偏收益的机制研究](docs/research/CONVERGENCE_GATE_AND_UNBIASED_EDGE.md) | 隐藏门可能是什么算法，如何证伪，“无偏”究竟指什么？[研究宪法](docs/governance/STRATEGY_CHARTER.md)已确认分布价值优先、方向贡献单列，具体算法待验证。 |
| [Balder 策略重建与复现方案](docs/governance/BALDER_STRATEGY_RECONSTRUCTION.md) | 独立蝶式怎样筛选、定价，哪些能借鉴，哪些仍未公开？ |
| [X 来源与阅读覆盖](docs/governance/BALDER_SOURCE_INDEX.md) | 初轮 72 个详情、40 个日内预测，及收盘后 11 个详情复查的实际范围与重叠？ |
| [交接审查](docs/governance/HANDOFF_REVIEW.md) | 原方案哪些可以保留，哪些缺口会改变实现或研究结论？ |
| [首轮及后续方案](docs/governance/DEVELOPMENT_PLAN.md) | 第一轮具体交付什么，如何验收，后续怎样推进？ |
| [决策记录](docs/governance/DECISIONS.md) | 人类已经决定什么，还需要讨论什么？ |
| [文档治理](docs/governance/GOVERNANCE.md) | 哪份文档负责哪类事实，怎样更新、冻结和追溯？ |
| [官方开发依据](docs/governance/OFFICIAL_BASELINE.md) | Codex 与 GPT-6 Astra 的官方建议如何用于本项目？ |
| [证据复核](docs/governance/EVIDENCE_REVIEW.md) | 阅读覆盖、核对结果和本次检查的实际限度是什么？ |
| [新增预测图文审查](docs/governance/samples/2026-09-15-user-submission/README.md) | 7604 中位数、94% 触碰及今日/Tomorrow 分别能说明什么？ |
| [代理工作约定](AGENTS.md) | 后续 Codex 任务怎样遵守本项目边界？ |

## 已确认的首轮目标

人类已选择：**优先研究独立 SPX 蝶式，首轮一并交付现价、预测中点、预测＋筛选、预测＋选价、预测＋筛选＋选价五个完整影子账本，包含费用、结算和一日闭环。** 用户已有作者订阅，本次已通过外部 Chrome 读取 X 订阅正文。持续自动采集方式尚未建立，不假定另有邮件或私有 API。

五账本交付范围已确认，工程调试采用记录在实施基线中的 G0/P0；正式研究参数仍需预注册。既有 V0 是我们的固定基线，不代表作者未公开算法。有效预测档案及扣费后的增量价值继续作为研究主线。

一日闭环、连续运行验收、正式实验预注册、策略优势验证是不同成果。首轮完成不意味着已经证明盈利，也不自动进入券商模拟或实盘。

## 外部交接基线

只读参考：[交接入口](/Users/logan/Codex/2026-09-15/yan/outputs/spx-project-handoff/README.md)、[原启动说明](/Users/logan/Codex/2026-09-15/yan/outputs/spx-project-handoff/NEW_PROJECT_BRIEF.md)、[原压缩包](/Users/logan/Codex/2026-09-15/yan/outputs/spx-project-handoff-v1.0.zip)。

这些链接目前依赖本机路径。是否及如何归档到项目，在方案定稿时决定；原启动说明里的导入和开发命令不代表本轮授权。
