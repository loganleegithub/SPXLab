# 第二轮方案的可复核证据

2026-09-16。研究检查工具，不是生产策略模块；全部数学分布/报价反例为合成。真实市场部分只读既有002/003/004事件库并运行冻结代码，不接券商、不重写旧结果。

入口：[审查结论](../ROUND2_REVIEW.md)、[施工方案](../../governance/ROUND2_CONSTRUCTION_PLAN.md)。

## 文件

| 文件 | 用途 |
|---|---|
| [inputs-verified.json](inputs-verified.json) | 附件SHA-256、包内4文件长度/hash、重复blueprint、输出数值复现与运行环境差异 |
| [project-tests.log](project-tests.log) | 仓库35项测试，本次实际重跑 |
| [reference-tests.log](reference-tests.log) / [reference-rerun.json](reference-rerun.json) | 未修改附件程序，15项测试与全部演示结果 |
| [verify_review.py](verify_review.py) / [independent-checks.log](independent-checks.log) / [independent-checks.json](independent-checks.json) | 12项独立核验，包括150个随机积分/导数参数组与识别反例 |
| [verify_inputs.py](verify_inputs.py) / [legacy-limit-counterexample.json](legacy-limit-counterexample.json) | 文件/数值比较、现行预算成交语义反例 |
| [audit_frozen.py](audit_frozen.py) / [frozen-replay.json](frozen-replay.json) | 3,707,491条真实事件的只读冻结回放，主控制结果/轨迹与保护文件hash |
| [artifact-manifest.json](artifact-manifest.json) | 本交付证据文件hash及外部原件位置；不含其自身hash |
| [delivery-validation.json](delivery-validation.json) | 本地链接、检查脚本语法、汇总一致性与未修改生产范围的核对 |

## 复跑

在项目根目录执行；所有复跑输出请使用**新目录**，不要覆盖本次结果。项目测试不需SciPy：

```sh
.venv/bin/python -B -m unittest discover -s tests -v
.venv/bin/python -B docs/research/round2-review-2026-09-16/audit_frozen.py \
  --root /Users/logan/SPXLab --output /absolute/new-directory/frozen-replay.json
```

数学参考使用临时隔离Python环境，NumPy2.3.5、SciPy1.17.0。本次Python3.12.13；原附件记录3.13.5。可以在独立环境安装这两个锁定版本后运行：

```sh
python -B /Users/logan/Downloads/SPXLab_density_and_architecture_reference/SPXLab_density_optimizer_demo.py \
  --self-test --output /absolute/new-directory/reference-rerun.json
python -B docs/research/round2-review-2026-09-16/verify_review.py \
  --reference /Users/logan/Downloads/SPXLab_density_and_architecture_reference/SPXLab_density_optimizer_demo.py \
  --output /absolute/new-directory/independent-checks.json
```

完成新目录的reference-rerun后，用项目Python核对文件、数值和旧限价反例：

```sh
.venv/bin/python -B docs/research/round2-review-2026-09-16/verify_inputs.py \
  --root /Users/logan/SPXLab --output-dir /absolute/new-directory
```

工具拒绝覆盖已存在的核验输出。它调用仓库原Feed做合成反例，项目源码有变时应以本次HEAD复核。

原始附件保持Downloads位置，未批量导入参考包。未来路径改变后需按hash找回原件；这份验证目录不声称已自包含附件全部源码或3.7百万条行情。浮点结果允许声明的1e−10绝对容差，决策类别和选择必须一致。

本地原行情许可/隐私边界与未来共享许可分开。本次没有上传原件，也未将成交假设升级为实际成交。
