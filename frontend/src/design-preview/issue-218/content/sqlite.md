<!-- r2:en -->
I wrote a small SQLite extension that adds a `vec_distance()` function and a brute-force top-k scan. It is about 300 lines of C, has no dependencies, and is fast enough for a few hundred thousand embeddings on a laptop.
<!-- r2:seg -->
It is not a replacement for a real vector index. If you need approximate search over millions of rows, use one. But for prototypes and personal tools, keeping vectors next to the rest of your data in one file is very convenient.
<!-- r2:zh -->
我写了一个小小的 SQLite 扩展，新增一个 `vec_distance()` 函数和一个暴力扫描的 top-k 查询。它只有大约 300 行 C 代码，没有任何依赖，在笔记本上处理几十万条向量也足够快。
<!-- r2:seg -->
它替代不了真正的向量索引。如果要在数百万行上做近似检索，请用专门的方案。但对原型和个人小工具来说，把向量和其它数据放在同一个文件里非常方便。
