# AIPaperbase Agent venue 范围与分层

本文件描述当前语料清单覆盖的会议、期刊、主要方向和项目优先级。范围是 2023 年至最新可核验年份。

“含金量”没有跨学科统一的线性排名。本项目采用三档：

- S：方向内最核心，求职和前沿追踪的最高优先级；
- A：顶级或强方向会议/期刊，岗位相关性很高；
- B：专业方向核心，综合声量较小，但对对应岗位非常重要。

同一档内部不强行排名。CCF 分级、国际学术声誉和中国大厂岗位相关性只是参考维度，不能替代对具体论文质量的判断。

## 会议

| 分档 | Venue | 主要方向 |
|---|---|---|
| S | NeurIPS | 机器学习、基础模型、生成模型、强化学习 |
| S | ICML | 机器学习理论与方法、基础模型 |
| S | ICLR | 深度学习、表示学习、生成模型、大模型 |
| S | CVPR | 计算机视觉、多模态、3D、生成视觉 |
| S | ICCV | 计算机视觉、多模态、3D |
| S | ECCV | 计算机视觉、多模态、3D |
| S | ACL | NLP、大语言模型、信息抽取、机器翻译 |
| S | KDD | 数据挖掘、推荐、广告、图学习、工业 AI |
| S | SIGIR | 搜索、检索、推荐、RAG |
| S | WWW | Web、推荐、搜索、图学习、社会计算 |
| S | SIGGRAPH | 计算机图形学、3D、渲染、生成式内容 |
| S | SIGGRAPH Asia | 计算机图形学、3D、渲染、生成式内容 |
| A | AAAI | 综合人工智能、规划、推理、机器学习 |
| A | IJCAI | 综合人工智能、智能体、推理、知识表示 |
| A | EMNLP | NLP、大语言模型、文本生成与评测 |
| A | NAACL | NLP、大语言模型、语言理解 |
| A | ACM MM | 多媒体、多模态、视频、跨模态检索 |
| A | MLSys | 大模型训练推理系统、AI Infra、部署优化 |
| A | COLM | 大语言模型基础、训练、推理与评测 |
| B | 3DV | 三维视觉、重建、点云、NeRF、3D 生成 |

## 期刊

| 分档 | Venue | 全称 | 主要方向 |
|---|---|---|---|
| S | TPAMI | IEEE Transactions on Pattern Analysis and Machine Intelligence | 视觉、模式识别、多模态、机器学习 |
| S | JMLR | Journal of Machine Learning Research | 机器学习理论、算法与系统 |
| S | AIJ | Artificial Intelligence | 推理、规划、智能体、知识表示 |
| S | IJCV | International Journal of Computer Vision | 计算机视觉、3D、视频理解 |
| S | TOG | ACM Transactions on Graphics | 图形学、3D、渲染、动画；承载大量 SIGGRAPH 论文 |
| S | TKDE | IEEE Transactions on Knowledge and Data Engineering | 数据挖掘、知识图谱、推荐、数据智能 |
| S | TOIS | ACM Transactions on Information Systems | 信息检索、搜索、推荐、用户建模 |
| A | TIP | IEEE Transactions on Image Processing | 图像处理、视觉底层任务、复原与生成 |
| A | TVCG | IEEE Transactions on Visualization and Computer Graphics | 3D、可视化、图形学、VR/AR |
| A | TMLR | Transactions on Machine Learning Research | 机器学习、大模型、可复现研究 |

## 3D 检索入口

3D 论文不能只从名称带“3D”的 venue 中寻找。当前项目的推荐检索顺序是：

1. CVPR、ICCV、ECCV：3D 感知、重建、NeRF、Gaussian Splatting、3D 生成；
2. SIGGRAPH、SIGGRAPH Asia、TOG：几何、渲染、动画、材质、生成式 3D；
3. 3DV：专门的三维视觉论文；
4. TPAMI、IJCV、TVCG：长文、系统性扩展和完整实验；
5. NeurIPS、ICML、ICLR：3D 表征、生成模型和基础方法。

SIGGRAPH/SIGGRAPH Asia 论文常以 TOG 文章形式出版。两边的 venue CSV 都保留，以支持按会议或期刊检索；下载时使用 `reports/cross_venue_duplicates.csv` 按 DOI 去重。
