<!-- r2:en -->
Speculative decoding promises faster generation without changing a model's outputs: a small **draft model** proposes several tokens, and the large **target model** verifies them in a single forward pass. When the guesses are right, you get several tokens for the price of one.

In practice the speedup depends on three things:

- **Acceptance rate**: how often the target model agrees with the draft.
- **Draft cost**: a draft that is too large eats the savings.
- **Batch size**: at high concurrency the GPU is already busy, so verification is no longer free.
<!-- r2:seg -->
## A minimal setup

Most serving stacks expose speculative decoding as a single option. With vLLM, for example:

```python
from vllm import LLM, SamplingParams

llm = LLM(
    model="meta-llama/Llama-3.1-70B-Instruct",
    speculative_config={
        "model": "meta-llama/Llama-3.2-1B-Instruct",
        "num_speculative_tokens": 4,
    },
)
outputs = llm.generate(["Explain KV caching in one paragraph."],
                       SamplingParams(temperature=0))
```

Start with `num_speculative_tokens` between 3 and 5, then measure. The [vLLM documentation](https://docs.vllm.ai/) lists the draft and target pairs that share a tokenizer, which is a hard requirement.
<!-- r2:seg -->
## Measured latency

We ran the same prompts on a single H100 with greedy decoding:

| Concurrency | Baseline (ms/token) | Speculative (ms/token) | Speedup |
| --- | --- | --- | --- |
| 1 | 21.4 | 9.8 | 2.2× |
| 8 | 24.9 | 14.1 | 1.8× |
| 32 | 31.2 | 27.5 | 1.1× |

> The gains fall off as concurrency rises: once the GPU is saturated, verifying draft tokens competes with real work.

If your traffic is mostly interactive, single-user sessions, speculative decoding is close to a free win. For high-throughput batch jobs, measure before you turn it on.
<!-- r2:zh -->
推测解码承诺在不改变模型输出的前提下加快生成：一个小的**草稿模型**先提出若干个 token，再由大的**目标模型**在一次前向计算中统一验证。猜对的时候，一次计算就能换来好几个 token。

实际能快多少，取决于三件事：

- **接受率**：目标模型认可草稿的频率。
- **草稿成本**：草稿模型太大，省下的时间会被吃掉。
- **批量大小**：高并发时 GPU 本已满载，验证不再是免费的。
<!-- r2:seg -->
## 最小可用配置

多数推理服务框架都把推测解码做成了一个开关。以 vLLM 为例：

```python
from vllm import LLM, SamplingParams

llm = LLM(
    model="meta-llama/Llama-3.1-70B-Instruct",
    speculative_config={
        "model": "meta-llama/Llama-3.2-1B-Instruct",
        "num_speculative_tokens": 4,
    },
)
outputs = llm.generate(["Explain KV caching in one paragraph."],
                       SamplingParams(temperature=0))
```

`num_speculative_tokens` 建议先取 3 到 5，再实测调整。[vLLM 文档](https://docs.vllm.ai/)列出了共用分词器的草稿与目标模型组合，这是硬性要求。
<!-- r2:seg -->
## 实测延迟

我们在单张 H100 上用贪心解码跑了同一组提示词：

| 并发数 | 基线（毫秒/token） | 推测解码（毫秒/token） | 加速比 |
| --- | --- | --- | --- |
| 1 | 21.4 | 9.8 | 2.2× |
| 8 | 24.9 | 14.1 | 1.8× |
| 32 | 31.2 | 27.5 | 1.1× |

> 并发越高，收益越小：GPU 一旦满载，验证草稿 token 就会和真实计算争抢资源。

如果流量以交互式的单用户会话为主，推测解码几乎是白捡的收益；面向高吞吐的批处理任务，启用前请先实测。
