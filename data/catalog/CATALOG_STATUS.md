# 清单状态（2026-09-07）

## 当前规模

- 107 个年度 CSV
- 67 个会议 CSV，40 个期刊 CSV
- 122,571 条论文记录，归并为 121,742 个统一论文实体
- 112,006 条最终清单记录
- 10,565 条滚动清单记录
- 114,255 条记录已有摘要
- 单文件 `paper_id` 重复：0
- 跨文件 `(venue, year, paper_id)` 重复：0
- 必填字段/DOI 格式校验问题：0
- 采集错误：0
- 跨 venue DOI 别名：829 组，均为 SIGGRAPH/SIGGRAPH Asia 与 TOG 的重复出版标识

## 当前覆盖范围

会议：AAAI、IJCAI、NeurIPS、ICML、ICLR、COLM、MLSys、ACL、EMNLP、NAACL、CVPR、ICCV、ECCV、3DV、KDD、SIGIR、WWW、ACM MM、SIGGRAPH、SIGGRAPH Asia。

期刊：AIJ、TPAMI、IJCV、JMLR、TMLR、TOG、TIP、TVCG、TKDE、TOIS。

年份从 2023 开始。没有举办或尚未形成清单的年份不会生成空 CSV，例如 ECCV 仅偶数年举办，ICCV 仅奇数年举办。

## 来源层级

### 官方直接采集

- CVPR/ICCV：CVF Open Access `All Papers`
- ECCV：ECVA Papers
- ICML 2023—2025：PMLR 正式 Volume
- NeurIPS：NeurIPS Proceedings 年度论文集
- ACL/EMNLP/NAACL：ACL Anthology 官方 XML，仅保留主会议 Long/Short/Main
- IJCAI：IJCAI Proceedings，排除 Sister Conference Reprint、Doctoral Consortium、Demo
- COLM：COLM 官方 Accepted Papers
- JMLR：JMLR 年度 Volume
- TMLR：TMLR 官方 Accepted Papers
- MLSys：Proceedings of Machine Learning and Systems

### 官方目录加第二来源元数据

- AAAI：AAAI 官方年度 proceedings 负责限定卷期和 Technical/Special Track；Crossref/Hugging Face 提供逐篇 DOI、标题、作者和 OJS PDF 地址。
- AIJ、TPAMI、IJCV、TOG、TIP、TVCG、TKDE、TOIS：按 ISSN 从 Crossref 获取出版商注册元数据，并保留出版商期刊主页。
- SIGGRAPH、SIGGRAPH Asia、3DV：`ai-conferences` 提供结构化元数据，每条记录必须具有 ACM/IEEE/CVF 官方 DOI 或论文链接。

### 第三方清单并经官方页面核验

- ICLR：`ai-conferences` 的 OpenReview accepted-paper 元数据作为主轨道集合，与 ICLR 官方虚拟会场逐标题比对；标题更名通过官方详情页中的 OpenReview ID 复核。
- ICML 2026：同上，但仍属于滚动清单；最终应由后续 PMLR Volume 替换。
- KDD、SIGIR、WWW、ACM MM：DBLP 年度目录负责枚举，逐条保留 ACM/IEEE DOI 或出版商链接；已排除 Keynote、Workshop、Tutorial、Demo、Doctoral Consortium 等非主论文轨道。

详细集合差异见 `reports/cross_source_audit.csv`。ICLR/ICML 官方虚拟会场页面在部分年份还包含 Journal-to-Conference、Blog、Position Paper 等展示轨道，因此官方页面独有标题没有自动并入主会议清单。

## 2026 处理方式

2026 清单按来源成熟度分别标记：已形成可核验正式清单的文件为 `final`，仍在更新的录用列表或期刊滚动卷为 `rolling`。当前 2026 年共有 34,481 条记录，其中 23,916 条为 `final`、10,565 条为 `rolling`。滚动记录可用于前沿检索，但不能作为冻结的最终 proceedings 数量。未举办、尚未录用或尚未形成可核验目录的 venue 不生成空 CSV。每次更新都应重新运行采集器，并审核差集报告。

## 后续 PDF 下载验收

下载器应以 CSV 中的 `(venue, year, paper_id)` 为任务主键。某年度只有同时满足以下条件才算完成：

```text
expected_ids - valid_local_pdf_ids = empty
valid_local_pdf_ids - expected_ids = empty
duplicate_sha256 已逐项解释
```

不要用“本地 PDF 数量等于 CSV 行数”代替集合验收。
