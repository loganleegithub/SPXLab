# 阅读覆盖与静态证据复核

日期：2026-09-15 · 状态：本轮实际检查记录。机器摘要：[verification.json](verification.json)。

## 1. 阅读范围

完整阅读了交接 README、NEW_PROJECT_BRIEF、01–05 五份主方案、SOURCES、PACKAGE_CHECKS、配置和两个契约文件、证据索引及作者材料说明；三份历史报告及两份探针源码也已通读。

三张图片逐张查看。全部 JSON 解析并核对相关字段；历史行情数组检查覆盖、长度、时间唯一性和有限数值，37 日账本逐行复算。两份 HTML 全文件读取作静态结构检查，检查 SPX 概率锥内容、图片引用及与隐藏产品混杂的情况；没有执行页面脚本，没有把其他产品全部隐藏内容重新做业务审计。

读取了清单和来源映射，并按映射核对 19 个原始文件的哈希。本轮没有解压或复制交接包到 SPXLab；压缩包仅作内存读取比对。

## 2. 原基线身份

外部位置：[交接目录入口](/Users/logan/Codex/2026-09-15/yan/outputs/spx-project-handoff/README.md)。

| 对象 | SHA256 |
|---|---|
| 原 MANIFEST.json | `cde7ac2093f4eed3649643e5a2092a3bc8f4ab0361580f52efbfcbad02db13cb` |
| 原 ZIP | `ac9f07f5cf2a55c34bc81c9d2989e0b55e220ad5e4c6d310d4c00e534dfa9b74` |

清单包含 35 项，不包含清单自身；ZIP 为 36 个文件。目录另有 `.DS_Store`，不属于清单或交付内容。这解释了数量口径，不将系统元数据视作新增证据。

## 3. 十九份既有资产逐项说明

所有位置均在原交接目录，未迁入项目。下列“已检查”只代表本轮的阅读或静态复核。

| 资产 | 本轮检查 | 允许支持的结论与限制 |
|---|---|---|
| [早期可行性报告](/Users/logan/Codex/2026-09-15/yan/outputs/spx-project-handoff/evidence/reports/balder-spx-feasibility.md) | 全文阅读 | 理论、结构与探索性计算；非完整业绩 |
| [早期验证方案](/Users/logan/Codex/2026-09-15/yan/outputs/spx-project-handoff/evidence/reports/spx-validation-plan.md) | 全文阅读 | 决策演进；旧订阅状态、预算规则不直接继承 |
| [QC 实测报告](/Users/logan/Codex/2026-09-15/yan/outputs/spx-project-handoff/evidence/reports/quantconnect-spxw-validation-results.md) | 全文阅读 | 报告记载六月单日能力；没有云运行原始日志全文 |
| [QC 六月探针](/Users/logan/Codex/2026-09-15/yan/outputs/spx-project-handoff/evidence/probes/quantconnect-spxw-control-probe.py) | 全文和 AST 语法检查 | 静态无下单逻辑；未重新执行 |
| [QC 九月探针](/Users/logan/Codex/2026-09-15/yan/outputs/spx-project-handoff/evidence/probes/quantconnect-spxw-data-probe.py) | 全文和 AST 语法检查 | 目标日期与实际返回日期须核对；未重新执行 |
| [9 月 14 日头寸图](/Users/logan/Codex/2026-09-15/yan/outputs/spx-project-handoff/evidence/images/balder-spx-trade-2026-09-14.png) | 视觉检查 | 10 组 7600/7625/7650，显示成本与估值；无成交时刻 |
| [9 月 11 日头寸图](/Users/logan/Codex/2026-09-15/yan/outputs/spx-project-handoff/evidence/images/balder-spx-trades-2026-09-11.png) | 视觉检查 | 两个同中心不同翼宽结构；不能视为同步实验 |
| [概率锥图](/Users/logan/Codex/2026-09-15/yan/outputs/spx-project-handoff/evidence/images/balder-public-cone-2026-09-15.jpg) | 视觉检查 | 中位数 7626、50% 带 7603–7658；Tomorrow/日期含义不明 |
| [订阅观察摘要](/Users/logan/Codex/2026-09-15/yan/outputs/spx-project-handoff/evidence/raw/ibkr-subscription-confirmation-20260915.json) | 字段及能力范围核对 | 当时订阅页面和插件状态的记录；非本轮账户核验 |
| [Gateway 观察](/Users/logan/Codex/2026-09-15/yan/outputs/spx-project-handoff/evidence/raw/ibkr-gateway-subscription-check-20260915.json) | 字段、类型和时间核对 | 约 15 秒、单一期权、SPX 盘前冻结；缺逐字段完整事件流 |
| [IBKR 历史观察](/Users/logan/Codex/2026-09-15/yan/outputs/spx-project-handoff/evidence/raw/ibkr-history-check.json) | 全文件解析及数组核验 | SPX 60 日线/780 分钟线、未到期期权 405 分钟线；Last 非盘口 |
| [初始网页](/Users/logan/Codex/2026-09-15/yan/outputs/spx-project-handoff/evidence/raw/initial-snapshot/page.html) | 静态结构及 SPX 内容 | 当时抓取的内容；不证明首次发布 |
| [初始捕获清单](/Users/logan/Codex/2026-09-15/yan/outputs/spx-project-handoff/evidence/raw/initial-snapshot/manifest.json) | 时间、网页哈希、隔离标记核对 | 捕获时间不等于作者发布时间 |
| [较早网页](/Users/logan/Codex/2026-09-15/yan/outputs/spx-project-handoff/evidence/raw/balder-spx-2026-09-15.html) | 静态结构及 SPX 内容 | 与初始网页是独立文件；不能把同一图 URL 当同一不可变资产 |
| [提取文本](/Users/logan/Codex/2026-09-15/yan/outputs/spx-project-handoff/evidence/raw/balder-source-extract.txt) | 全文阅读 | 包含多个产品及旧浏览结果引用；旧工具引用不当作本轮引文 |
| [37 日账本](/Users/logan/Codex/2026-09-15/yan/outputs/spx-project-handoff/evidence/raw/spx-ledger.json) | 日期、区间覆盖复算 | 37 日；34/36 覆盖；center 为区间中点代理 |
| [早期指标](/Users/logan/Codex/2026-09-15/yan/outputs/spx-project-handoff/evidence/raw/ledger-metrics.json) | 字段及报告口径比较 | 探索性毛兑付与报告有口径差异，未作为已验证 V0 结果 |
| [9 月 14 日结构计算](/Users/logan/Codex/2026-09-15/yan/outputs/spx-project-handoff/evidence/raw/screenshot-trade-analysis.json) | 源图与主要算术核对 | 条件结算和二元简化均非真实成交记录 |
| [9 月 11 日结构计算](/Users/logan/Codex/2026-09-15/yan/outputs/spx-project-handoff/evidence/raw/sept11-screenshot-analysis.json) | 源图与主要算术核对 | 显示 ROI 字段与其他文件的百分比/小数口径需区分 |

## 4. 本轮实际复核结果

| 检查 | 结果 |
|---|---|
| 35 个清单文件长度与 SHA256 | 全部一致 |
| ZIP CRC 与 36 个对应文件的精确内容 | 通过；无差异 |
| 19 个原始来源文件哈希 | 全部与 provenance 原哈希一致 |
| 14 个 JSON 文档 | 可解析 |
| 预测 Schema 自身及启用格式检查的隔离例子 | 通过 |
| 把原未知目标直接改称 VALID | 被拒绝 |
| 给预测内容直接添加公共 event_id | 被拒绝，确认需要写清外层信封 |
| 完整目标/时间但缺中位数的 VALID 构造例子 | 结构通过，确认 V0 需要独立业务资格校验 |
| 规范化示例 3 个原始引用哈希 | 一致 |
| 两份探针 AST | 可解析；未运行 |
| 14 个十进制算术案例 | 14/14，详情在机器摘要 |
| 37 行公开账本 | 37 个唯一日期；68% 带 34 日，95% 带 36 日 |
| 原包 Markdown 本地链接 | 82 处，目标均存在 |
| 示例配置 | specification only、shadow；券商提交和实盘关闭；真实账户为空；费用/经济门槛未设 |

上述 Schema 反例只在内存中构造，没有改变源例子。它们验证结构与业务规则的责任边界，不是在不存在的系统上运行验收测试。

## 5. 可复核方法

机器摘要保存本次 Python、jsonschema 版本、源文件身份、检查范围及结果。核心完整性检查可在原路径只读重做：

```python
from pathlib import Path
import hashlib, json, zipfile
p = Path('/Users/logan/Codex/2026-09-15/yan/outputs/spx-project-handoff')
m = json.loads((p / 'MANIFEST.json').read_text())
for entry in m['files']:
    data = (p / entry['path']).read_bytes()
    assert len(data) == entry['bytes']
    assert hashlib.sha256(data).hexdigest() == entry['sha256']
with zipfile.ZipFile(p.with_name('spx-project-handoff-v1.0.zip')) as z:
    assert z.testzip() is None
    for f in p.rglob('*'):
        if f.is_file() and f.name != '.DS_Store':
            assert z.read('spx-project-handoff/' + str(f.relative_to(p))) == f.read_bytes()
```

这里只提供只读材料核对方法，不导入或运行原包程序。全面应用验收仍待开发后执行，不能把本节当作生产测试套件。

## 6. 明确未验证的事项

没有本轮账户权限刷新、Gateway 新连接、持续采集、云回测、券商委托、正式结算接入或正式影子研究。没有证明作者预测首次发布时间、历史完整性或可交易优势。没有重新审计来源报告中的全部外部研究，也没有第二位独立审查者。

因而本轮状态是“交接理解与材料检查完成，治理及开发方案待讨论”。对未来能力的要求留在开发方案及决策表，不写成已通过。

## 7. 原交接基线之外的新增样本

2026-09-15 用户随后提供一张新概率锥图与配文，已作[图文语义审查](samples/2026-09-15-user-submission/README.md)，并逐字节留存附件。新图标示中位数 7604，不替换原包中的 7626 图，也不能在缺少来源证据时认定两图是同一目标的先后版本。

## 8. 后续 X 订阅来源审查

用户更新访问条件：已有作者订阅，授权外部浏览器阅读 SPX 帖子和评论，并选择独立蝶式的筛选/定价为复现重点。已完成[策略重建](BALDER_STRATEGY_RECONSTRUCTION.md)和[覆盖索引](BALDER_SOURCE_INDEX.md)：138 个关键词去重命中，72 个有正文的详情 URL（含 40 个日内预测正文），以及页面实际可见的相关评论。搜索卡片、全文、评论与已检查图片的覆盖程度分别声明；不声称看完全部隐藏、删除、未索引评论或全部历史图片。

本次样本原帖已核验为 09-15 13:50:44 UTC 发布的显示记录，与用户图文对应；目标倾向当日纽约收盘，图题冲突保留。这是后续取得的来源证据，不证明本系统在 10:05 前接收，不追加虚构实时决策或正式收益。

本节为追加审查，不修改原 19 份证据及原包/ZIP。原 `verification.json` 仍是原交接的静态核验摘要，不代表网页内容或作者策略通过验证。新增文档仅做链接、状态、数量及静态算例检查，未运行应用、行情采集或交易实验。

本次文档检查结果：11 个 Markdown 文件的 70 个本地链接及相关锚点有效；来源表 40+32=72 个详情 URL 无重复；附件 SHA256 与原留存一致；等翼宽三腿到期兑付及两个成本算例通过静态核对；原交接 MANIFEST/ZIP 哈希与此前审计一致。用户后续确认五账本首轮完整交付，已同步 U08 与开发计划，未改变未获开工授权的状态。

样本审查核对了字段含义、条件下的中心映射和文档要求；原帖链接、发布时间、目标日期及事前可用性尚未核实。它不纳入本页“19 份既有资产”数量，原 `verification.json` 仍仅记录之前的交接静态复核，不冒充本次新增样本的运行测试。
