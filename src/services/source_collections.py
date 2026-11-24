"""源合集(策展合集)注册表——发现页的策展视图 + 批量订阅动作。

定调(docs/archive/source-collections-wave-plan.md §0):合集是目录呈现层的策展视图,
**不是订阅实体**——订阅粒度仍是 source_id,feed/MCP/未读/隐藏/内容交付链路
全部不感知合集。「订阅合集」= 一次性批量订阅其当前成员;合集后续新增成员
不会自动推给已订阅者(无绑定记录,「跟随合集」留作观察后的二期)。

策展存储在策展源目录文件 ``src/sources/catalog/collections.toml``(issue #262 R2;此前是本模块的
``SOURCE_COLLECTIONS`` 代码常量):成员本来就是要改目录 + 部署才能加的策展源,合集与源同一 commit,
git 即审计。成员必须是目录里的现役策展源,目录加载时校验(引用了不存在或已下线的源,进程起不来)。
将来若出现非代码路径的策展者,再演进为 KV + 管理面 CRUD(照 source_visibility 五元素范式)。

命名纪律:`collection` 一词在本项目已被「采集任务 collection-jobs」占用,
后端模块一律 source_collections,API 路径域 /api/reader/collections,UI 中文词「合集」。
"""

from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple

from services import source_catalog


@dataclass(frozen=True)
class SourceCollection:
    collection_id: str            # kebab 稳定标识(API 路径段/前端 key)
    name: str
    description: str              # 内容式一句话(取名规范沿 node_audit_playbook)
    provenance_note: str          # 策展来源注;受限 markdown 子集(**加粗** 与 [文字](http(s) 链接)),前端白名单渲染
    source_ids: Tuple[str, ...]   # 成员,有序;同一源可出现在多个合集


def list_collections() -> Tuple[SourceCollection, ...]:
    """全部合集,按目录文件里的顺序。(目录进程内只读一次,这里不再另设缓存。)"""
    return tuple(
        SourceCollection(
            collection_id=entry.id,
            name=entry.name,
            description=entry.description,
            provenance_note=entry.provenance_note,
            source_ids=tuple(entry.sources),
        )
        for entry in source_catalog.collection_entries()
    )


def get_collection(collection_id: str) -> Optional[SourceCollection]:
    for collection in list_collections():
        if collection.collection_id == collection_id:
            return collection
    return None


def serialize_collection(collection: SourceCollection) -> Dict[str, Any]:
    """API 轻载荷:成员的订阅态/卡片数据由前端与 GET /api/reader/sources 目录 join。"""
    return {
        "collection_id": collection.collection_id,
        "name": collection.name,
        "description": collection.description,
        "provenance_note": collection.provenance_note,
        "source_ids": list(collection.source_ids),
    }
