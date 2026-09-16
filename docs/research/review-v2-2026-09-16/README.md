# 外部v2审查的本地复核证据

基线：`cd71a0b10778cdd7598a07631161b4422f05e859`。主结论见[实战复核与建议](../REVIEW_V2_PRACTICAL_PLAN.md)。本目录不包含付费文章原文、原始行情或应用替换代码。

| 文件 | 含义 |
|---|---|
| identity.json | 外部14个清单文件校验及5份源码与本地的Git blob对应 |
| review_checks.log | 使用当前源码重跑外部16项检查，全部通过 |
| regression_targets.log | 使用当前源码重跑外部两个反例，均失败；缺陷尚未修改 |
| boundary-probes.json | 错结构、错时点仍返回CANDIDATE的实际结果 |
| repository-tests.log | 本地60项回归通过 |
| practical-probes.json | 点估值已能输出、实际交付时间契约、未来在途订阅风险及历史算术 |
| verify_practical_gaps.py | 上述有界离线探针；跳过Gateway初始化，只用假对象调用真实订阅函数 |
| data-inventory.json | 37行旧区间账本、40篇预测索引与9月15日来源的可用性盘点 |
| manifest.json | 本次输入与输出hash；不把hash当作历史时间证明 |

外部报告：`/Users/logan/Downloads/SPXLab_ChatGPT-v2.md`、`/Users/logan/Downloads/SPXLab_cd71a0b_architecture_audit.md`。
外部原包：`/Users/logan/Downloads/SPXLab_cd71a0b_review_evidence.zip`；本地逐字节解包目录为`var/review-v2-2026-09-16/external/SPXLab_review_cd71a0b`。

从仓库根目录复跑外部检查，使用当前源码；unittest导入避免执行外部脚本main中的文件改写：

```sh
PYTHONPATH="$PWD/src:$PWD/var/review-v2-2026-09-16/external/SPXLab_review_cd71a0b" .venv/bin/python -m unittest review_checks.IndependentChecks -v
PYTHONPATH="$PWD/src:$PWD/var/review-v2-2026-09-16/external/SPXLab_review_cd71a0b" .venv/bin/python -m unittest regression_targets.QuoteBindingRegressions -v
```

第二条在原基线上预期退出非零、2项FAIL；未来修复后应转绿。包内五份源码保持原样，不通过替换它们测试当前仓库。

```sh
.venv/bin/python docs/research/review-v2-2026-09-16/verify_practical_gaps.py --output var/my-review-probes.json
```

探针使用合成输入，只刻画有限的真实代码行为。历史算术另外引用本地已归档成本与结算；并非成交损益。上面的私有本地原件未随仓库提供，在其他机器复跑相应部分需要合法取得同一原件。未重复验证旧370万事件，不以本次60项回归替代原回放证据。
