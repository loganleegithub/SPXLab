# SPXLab

真实 SPX 行情驱动的蝶式交易原型。当前主线 FIELD V1：分钟事实 → 局部收敛/扩散模型 → 真实报价价值 → 进入/等待 → 固定限价影子 → 结果评分。允许探索性模型先运行再接受检验。

本模式没有真实仓位或券商订单。模型尚未证明盈利；模拟成交、预测正确和可交易优势分别报告。

- [FIELD 当前交付、运行命令与验收](docs/FIELD_DELIVERY.md)
- [本轮最小重构方案](docs/FIELD_V1_PLAN.md)
- [FIELD 试运行配置](examples/field/2026-09-16.json)
- [决策与授权](docs/governance/DECISIONS.md)、[研究宪法](docs/governance/STRATEGY_CHARTER.md)

从项目根目录运行：

```sh
.venv/bin/python -m spxlab.cli field-shadow --plan examples/field/2026-09-16.json --directory var/2026-09-16/field-new-run
```

新目录会冻结算法、参数、费用和源码。实际日期必须在配置中明确；不能把示例命令用于重复本日意图或自动进入次日。已部署目录、恢复与收盘操作见交付文档。

FIELD 使用LOOKAHEAD主账本及FIRST_POSITIVE基线；无有效Pin仍运行MARKET_ONLY。预算每组含费6.25点，每账本最多一次意图。两个账本是独立反事实，不能相加为账户收益。中文单页由运行器生成于运行目录FIELD.html。

旧模式保留原始语义与冻结历史：

- [纽约9月16日OBSERVE观察运行](docs/ROUND2_RUNBOOK.md)：既有冻结进程不切换为FIELD。
- [U21报价修复及真实材料诊断](docs/research/PRACTICAL_BUILD_DELIVERY.md)：8日事后资料及固定时点DV1接入的历史交付。
- [9月15日正式结算与五账本](docs/2026-09-15-CLOSEOUT.md)、[复盘](docs/2026-09-15-RETROSPECTIVE.md)。
- [旧CLI运行手册](docs/RUNBOOK.md)、[研究机制](docs/research/CONVERGENCE_GATE_AND_UNBIASED_EDGE.md)。

验证：`.venv/bin/python -m unittest discover -s tests -v`。原始市场数据、运行库和来源资产保留在忽略Git的var/；文档与代码不包含账户凭据。
