# Analytics

Produces reproducible analyses of counts, distribution, trends, new concepts, topics, direction maturity/speed, and citation relationships, and returns the set of papers that make up each result.

This module provides the data and semantics the charts need; it does not decide which specific chart the frontend draws, and does not handle PDF processing or RAG question answering.

A `personal-themes-v1` weak-label baseline is currently implemented: at the granularity of the unified paper entity, it identifies LLMs, agent systems, image generation, and image restoration from explicit signals in titles and abstracts. Configuration lives in `config/topics.json`; each result stores its score, the matched terms, and the field they appeared in.

---

## 中文

负责数量、分布、趋势、新概念、主题、方向成熟速度和引用关系等可复现分析，并返回构成结果的论文集合。

本模块产生图表所需的数据与口径，不决定前端具体画成什么图，也不承担 PDF 处理和 RAG 问答。

当前已实现 `personal-themes-v1` 弱标签基线：以统一论文实体为单位，根据标题与摘要的显式信号识别大模型、Agent 系统、图像生成与恢复。配置在 `config/topics.json`，每条结果保存得分、命中词和所在字段。
