<!-- r2:en -->
## Abstract

Dense attention scales quadratically with sequence length, which makes long-context inference expensive. We propose **block-sparse routing**, in which each query block attends to a small, learned subset of key blocks. The cost drops from $O(n^2)$ to roughly $O(n\sqrt{n})$, and on 128K-token retrieval and QA benchmarks the gap to dense attention stays within one point.
<!-- r2:seg -->
## 1 Method

Given query blocks $Q_i$ and key blocks $K_j$, a lightweight router scores every pair and keeps the top $k$ key blocks for each query block:

$$
s_{ij} = \frac{\bar{q}_i^{\top} \bar{k}_j}{\sqrt{d}}, \qquad \mathcal{S}_i = \mathrm{TopK}_j\,(s_{ij})
$$

Attention is then computed only inside $\mathcal{S}_i$, plus a fixed local window. The router is trained jointly with the model and adds less than 2% parameters.
<!-- r2:seg -->
## 2 Results

On a 7B model with 128K-token inputs, routing keeps quality close to dense attention while cutting attention FLOPs by more than an order of magnitude:

| Task | Dense | Block-sparse | Attention FLOPs |
| --- | --- | --- | --- |
| Needle retrieval | 98.1 | 97.6 | 7% |
| Multi-document QA | 61.4 | 60.7 | 7% |
| Summarization | 34.2 | 33.1 | 7% |

The router occasionally misses evidence that is spread thinly across many blocks. Appendix B discusses this failure mode and a simple fallback.
<!-- r2:zh -->
## 摘要

稠密注意力的计算量随序列长度呈平方增长，使长上下文推理代价高昂。我们提出**分块稀疏路由**：每个查询块只关注一小部分经学习选出的键块。计算量由 $O(n^2)$ 降至约 $O(n\sqrt{n})$；在 128K token 的检索与问答基准上，与稠密注意力的差距保持在 1 个百分点以内。
<!-- r2:seg -->
## 1 方法

给定查询块 $Q_i$ 与键块 $K_j$，一个轻量路由器为每一对打分，并为每个查询块保留得分最高的 $k$ 个键块：

$$
s_{ij} = \frac{\bar{q}_i^{\top} \bar{k}_j}{\sqrt{d}}, \qquad \mathcal{S}_i = \mathrm{TopK}_j\,(s_{ij})
$$

随后只在 $\mathcal{S}_i$ 以及一个固定的局部窗口内计算注意力。路由器与模型联合训练，新增参数不到 2%。
<!-- r2:seg -->
## 2 实验结果

在输入长度为 128K token 的 7B 模型上，路由方案的质量与稠密注意力接近，注意力计算量则减少一个数量级以上：

| 任务 | 稠密注意力 | 分块稀疏 | 注意力计算量 |
| --- | --- | --- | --- |
| 大海捞针检索 | 98.1 | 97.6 | 7% |
| 多文档问答 | 61.4 | 60.7 | 7% |
| 摘要 | 34.2 | 33.1 | 7% |

当证据零散地分布在大量块中时，路由器偶尔会漏掉；附录 B 讨论了这一失败情形和一个简单的兜底办法。
