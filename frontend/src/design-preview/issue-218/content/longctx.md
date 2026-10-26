<!-- r2:en -->
Long-context models are often judged by a single "needle in a haystack" score. That number is easy to compute and easy to misread: finding one planted sentence says little about whether a model can combine facts spread across a document.

We suggest three complementary checks:

1. **Retrieval**: can the model quote the right passage?
2. **Aggregation**: can it combine several passages into one answer?
3. **Abstention**: does it say so when the answer is not in the context?
<!-- r2:seg -->
In our runs, models that scored above 95% on single-needle retrieval still dropped to around 60% on aggregation once the relevant passages were more than 30K tokens apart. Report all three numbers, and report them by distance.
<!-- r2:zh -->
长上下文模型常常只用一个“大海捞针”分数来评判。这个数字容易算，也容易被误读：找到一句埋进去的话，并不能说明模型能把分散在全文各处的事实组合起来。

我们建议做三项互补的检查：

1. **检索**：模型能否引用出正确的段落？
2. **聚合**：能否把几段内容合成一个答案？
3. **弃答**：答案不在上下文里时，它会不会如实说明？
<!-- r2:seg -->
在我们的测试中，单针检索得分超过 95% 的模型，一旦相关段落相距超过 3 万 token，聚合任务的得分就会跌到 60% 左右。三个数字都要报告，并且要按距离分别报告。
