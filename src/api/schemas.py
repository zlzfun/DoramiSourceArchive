"""跨 Router 共享的请求模型（阶段1 共享 helper 模块化）。

放置被多个域 Router 复用的小型 pydantic 请求体，避免各 Router 互相 import 或回头
依赖 api.app 成环。当前仅 BatchOpParams（articles 批量删除、vector 批量向量化/删除共用）。
"""

from typing import List

from pydantic import BaseModel


class BatchOpParams(BaseModel):
    ids: List[str]
