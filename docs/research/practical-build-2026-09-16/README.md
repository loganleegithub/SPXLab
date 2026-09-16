# U21施工证据

原审查及失败日志仍在[review-v2证据](../review-v2-2026-09-16/README.md)，未覆盖。本目录对应修复后的新结果。

- `unittest.log`：本地66项通过；新增检查针对报价失配、模型收件时钟/重启、在途腿保留/释放和真实资料整理。不是收益证据。
- `external-checks.log`：外部16项独立检查对本地源码通过。
- `external-regressions.log`：外部原始2个失败反例对修复后源码通过，未改外部测试。
- `model-cli-smoke.json`：合成manifest经真实CLI生成分布及有hash的模型原件；合成标签保持。
- `frozen-fixture.json`：当前源码新冻结、FIXED合成运行、一次意图及后续报价、合成结算、12事件独立回放；不是新实时驱动器的真实运行。
- `protected-state.json`：43个受保护文件与施工前hash一致；本日冻结观察快照校验通过，检查时尚在开盘前且0意图。
- `data-summary.json`：8个事后诊断日、0个严格时点合格日；逐行排除理由、报价前缀hash和关键算术。未公开付费原图或逐腿原始行情。
- `VALUE_REPORT.md`：真实003原冻结时点的中文诊断，原件及详细JSON只保存在本机。
- `rebuild_quote_frame.py`：复建同一个真实报价截面，核验日志前缀hash链并写新文件，不挑其他时刻。
- `files.sha256`：本目录交付文件清单；`implementation-sha256.json`列本次有关源码、脚本和测试的版本。

本机完整输入及输出：`var/practical-build-2026-09-16/inputs/`、`report-003/`；新合成冻结运行、模型CLI产物及回放位于同一上级目录。inputs中的source hash标识本次逐图/DOM转录，不能当作原图hash、无修订证明或历史系统接收证据。

复核命令：

```sh
.venv/bin/python -m unittest discover -s tests -v
PYTHONPATH="$PWD/src:$PWD/var/review-v2-2026-09-16/external/SPXLab_review_cd71a0b" .venv/bin/python -m unittest review_checks.IndependentChecks regression_targets.QuoteBindingRegressions -v
.venv/bin/python docs/research/practical-build-2026-09-16/rebuild_quote_frame.py --output var/practical-build-2026-09-16/quote-next.json
.venv/bin/python scripts/prepare_reviewed_dataset.py --inputs var/practical-build-2026-09-16/inputs/reviewed-inputs.json --quote-bundle var/practical-build-2026-09-16/quote-next.json --output var/practical-build-2026-09-16/report-next
```

实际真实影子验收、下一独立交易日、正式模型校准及经济评估仍未完成，见[交付说明](../PRACTICAL_BUILD_DELIVERY.md)。

发布核对补充：旧审查manifest记录的是U20时点方案hash。U21接受后仅更新方案头部状态/授权说明；与旧hash逐字节匹配的版本另存为`../review-v2-2026-09-16/REVIEW_PLAN_AT_U20.md`。该文件按原路径关系原样保存，阅读导航以当前方案为准。旧manifest和失败日志未回写。
