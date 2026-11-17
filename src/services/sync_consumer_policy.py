"""Archive Sync 之外仍要守的两条本机规则(issue #263 起不再有整机 consumer 模式)。

历史上这里实现的是「接收端围栏」:一旦拉取别人,本机的公共源采集与分析整体停掉,并且按源
归属(``collection_authority_id``)拦本机操作。issue #263 把两者都拆了——拉取不改变本机能做
什么,源上的归属印记降级为簿记。留下的只有:

- 读者自定 RSS 的规则:源须启用、功能总闸须开;带凭据的源与关了分析的源不经 MaaS。这组规则
  原本只在 consumer 模式下由这里把关(别的节点由 user_sources / article_analysis 各自的路径把关,
  其中带凭据的源还要走候选证据清除);本波保持两类节点的现状不变,所以它只在「已接入上游」的
  节点上生效。统一这组规则另记 backlog。
- v1 兼容通道的互斥:本机做过 v2 同步(库里有同步写入的行)后,不再接受 v1 归档导入与 v1 定时。

模块名沿用旧名,约 30 处调用点不动。
"""

from __future__ import annotations

import json

from sqlmodel import Session, select

from models.db import (
    AppSettingRecord,
    ArticleAnalysisRecord,
    ArticleRecord,
    MediaAssetRecord,
    SourceConfigRecord,
    SourceStateRecord,
)


# 仅供迁移与旧数据清理引用:issue #263 的迁移会删除这条 KV,运行期不再读写它。
V2_CONSUMER_MODE_KEY = "remote_sync:v2_consumer_mode"
TAXONOMY_AUTHORITY_ID_KEY = "taxonomy:authority_id"


REMOTE_SYNC_STATE_KEY = "remote_sync:state"  # 与 services.remote_sync 同一条 KV
EVENTS_CAPABILITY = "events_v1"  # 与 services.archive_sync_v2.EVENTS_CAPABILITY 同值


def _v2_targets(session: Session) -> list[dict]:
    record = session.get(AppSettingRecord, REMOTE_SYNC_STATE_KEY)
    if record is None or not record.value:
        return []
    try:
        state = json.loads(record.value)
    except (TypeError, json.JSONDecodeError):
        return []
    targets = state.get("targets") if isinstance(state, dict) else None
    if not isinstance(targets, dict):
        return []
    return [
        target
        for target in targets.values()
        if isinstance(target, dict) and str(target.get("v2_authority_id") or "").strip()
    ]


def upstream_established(session: Session) -> bool:
    """本机是否已与某个上游建立 v2 同步(只读配置事实,不是模式:不限制本机任何操作)。

    用途只有默认值:有上游的节点上新出现的公共源,分析默认关(上游的分析结果会同步过来)。
    """

    return bool(_v2_targets(session))


def upstream_provides_events(session: Session) -> bool:
    """已建立的上游是否协商了事件流(决定本机还没有任何事件时用谁的)。"""

    return any(
        EVENTS_CAPABILITY in (target.get("v2_capabilities") or [])
        for target in _v2_targets(session)
    )


def taxonomy_governed_remotely(session: Session) -> bool:
    """本机的 taxonomy 是否来自上游(同步来的词表只读,不在本机治理与自动激活)。"""

    record = session.get(AppSettingRecord, TAXONOMY_AUTHORITY_ID_KEY)
    return bool(record is not None and (record.value or "").strip())


def v2_receiver_state_present(session: Session) -> bool:
    """本机是否持有同步写入的数据(用于挡住 v1 兼容通道)。

    v1 归档导入按「没有印记」写文章,会绕过 v2 的合并规则与删除标记;库里一旦有 v2 同步写入的
    行,就不再接受 v1 导入与 v1 定时。
    """

    authority_columns = (
        ArticleRecord.analysis_authority_id,
        ArticleAnalysisRecord.authority_id,
        SourceConfigRecord.collection_authority_id,
        SourceStateRecord.authority_id,
        MediaAssetRecord.sync_authority_id,
    )
    for column in authority_columns:
        if session.exec(select(column).where(column != "").limit(1)).first() is not None:
            return True
    return taxonomy_governed_remotely(session)


def _is_custom_source(source: SourceConfigRecord | None, source_id: str) -> bool:
    return bool(
        source is not None
        and (
            bool((source.owner_username or "").strip())
            or str(source_id or "").startswith("user_rss_")
        )
    )


def local_source_operation_allowed(
    session: Session,
    source_id: str,
    *,
    operation: str,
) -> bool:
    """本机能否对这个源做采集 / 分析 / 管理。

    公共源恒为真:采不采由采集任务与手动运行决定,析不析由源的分析开关与文章是否为本机版本
    决定,都不在这里拦。读者自定 RSS 在已接入上游的节点上仍由这里把关(与改动前完全一致):源须
    启用且功能总闸开着才采集与分析;带凭据的源照常由本机采集,但不经 MaaS 分析。没有上游的节点
    上,这些规则由各自的业务路径把关,这里不重复拦。
    """

    if operation not in {"collection", "analysis", "governance"}:
        raise ValueError(f"unsupported local source operation: {operation}")
    source_id = str(source_id or "").strip()
    source = session.get(SourceConfigRecord, source_id) if source_id else None
    if not _is_custom_source(source, source_id):
        return True
    if operation == "governance" or not upstream_established(session):
        return True

    # Import locally to keep this policy module independent of the user-source
    # service's write paths while sharing its credential and feature switches.
    from services import user_sources

    if not source.is_active or not user_sources.feature_enabled(session):
        return False
    # Signed/private custom RSS remains a local collection responsibility; it
    # is only forbidden from leaving the node through MaaS analysis.
    if operation == "analysis" and (
        user_sources.source_is_credentialed(source)
        or not source.ai_analysis_enabled
    ):
        return False
    return True
