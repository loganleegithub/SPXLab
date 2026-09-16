# 真实报价截面的事后价值诊断

**DIAGNOSTIC_ONLY：无意图、无成交、非当时可用估值。**

报价：2026-09-15T15:55:00.018372+00:00，同一事件序号 47024；重建完成：2026-09-16T10:14:04.618418+00:00。
训练：8 个此前独立日，2026-09-01 至 2026-09-14；严格时点合格日 0。
作者中位数 7604，发布 2026-09-15T13:50:44+00:00，系统取得 2026-09-15T15:33:11.512143+00:00；报价时现价 7582.91。
原始点数残差、每天等权、scale=1；图题目标日冲突及历史修订未知。约09:50的预测没有更新为报价时的条件分布，下面只能看敏感性。

| 中心 | 预期兑付点估值 | natural含费成本 | 点EV | 删一日兑付范围 | 盈亏平衡 | 最大损失美元 |
|---|---:|---:|---:|---|---|---:|
| 7585 | 12.9262 | 未知 | 未知 | 11.4114 … 14.7729 | 未知 | 未知 |
| 7600 | 11.7038 | 未知 | 未知 | 10.1329 … 13.3757 | 未知 | 未知 |
| 7605 | 8.1388 | 7.0595 | 1.0792 | 6.7729 … 9.3014 | 7587.0595 … 7622.9405 | 705.9500 |
| 7610 | 5.1888 | 5.5095 | -0.3208 | 4.1157 … 5.9300 | 7590.5095 … 7629.4905 | 550.9500 |

删一日范围不是置信区间；点EV未计固定数据费、排队及实际组合执行差异。盈亏平衡和最大损失仅适用于完整等翼结构与表列费用。

| 中心 | natural净借记 | 三腿中间价诊断 | 直接费用美元 |
|---|---:|---:|---:|
| 7585 | 未知 | 未知 | 未知 |
| 7600 | 未知 | 未知 | 未知 |
| 7605 | 7.0000 | 6.8750 | 5.9500 |
| 7610 | 5.4500 | 5.3250 | 5.9500 |

| 中心 | 锚点−5/原值/+5的预期兑付 | 限制/排除原因 |
|---|---|---|
| 7585 | 14.1338/12.9262/10.4262 | CONDITIONING_TIME_MISMATCH, COST_UNKNOWN, DISTRIBUTION_MISSING, INSUFFICIENT_SIZE, MODEL_NOT_READY, RETROSPECTIVE_RECONSTRUCTION, TARGET_DATE_ASSUMED, UNCERTAINTY_NOT_ELIGIBLE |
| 7600 | 8.1388/11.7038/14.1338 | CONDITIONING_TIME_MISMATCH, COST_UNKNOWN, DISTRIBUTION_MISSING, INSUFFICIENT_SIZE, MODEL_NOT_READY, RETROSPECTIVE_RECONSTRUCTION, TARGET_DATE_ASSUMED, UNCERTAINTY_NOT_ELIGIBLE |
| 7605 | 5.1888/8.1388/11.7038 | CONDITIONING_TIME_MISMATCH, COST_CAP, DISTRIBUTION_MISSING, MODEL_NOT_READY, RETROSPECTIVE_RECONSTRUCTION, TARGET_DATE_ASSUMED, UNCERTAINTY_NOT_ELIGIBLE |
| 7610 | 3.3350/5.1888/8.1388 | CONDITIONING_TIME_MISMATCH, DISTRIBUTION_MISSING, MODEL_NOT_READY, RETROSPECTIVE_RECONSTRUCTION, TARGET_DATE_ASSUMED, UNCERTAINTY_NOT_ELIGIBLE |

正式DV1未就绪；没有把点估值包装成价值下界。自然腿价不是组合成交承诺；中间价不计成交。
当前现价的同刻历史残差缺失，未生成现价锚点模型。现价中心只作为同一个作者分布下的候选位置。
逐日向前重建保存在JSON：训练日期均早于评分日，但来源实际于事后取得，因此不是盲样本外结果，也不是有成本的收益回测。
