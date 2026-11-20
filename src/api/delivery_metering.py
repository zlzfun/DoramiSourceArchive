"""令牌交付计量的接线(issue #312)。

交付路径(文章 feed / 热点事件榜 / 单订阅接口 / MCP 工具)在组好返回值之后到这里记一笔。
**请求路径只入队,不写库**:入队是一次不阻塞的 ``put_nowait``,写库由一个后台线程成批做。
这样计量的锁等待(库上有别的写事务时最多等 ``busy_timeout``)、慢、失败都落在后台线程上,
交付的耗时与结果不受影响;事件循环上的调用方(async 端点、MCP 工具)也可以直接调。

落账晚于交付,所以**事实在入队前就冻结**,后台线程只管照着写,不拿落账时的现状重新解释:
- 是谁的哪一枚令牌、账号的世代、订阅当时的名字 → 鉴权时解析出的 ``TokenIdentity``;
- 被拒的令牌是不是「已作废」→ 入队前在请求路径上读一次令牌表(只读,不等写锁);
  仍然有效的令牌用错了接口不入队;
- 写到哪个库、调用发生在什么时刻 → 入队时抓下。

队列有界:写库一直跟不上时新的计量直接丢弃并计数(``dropped``),不反压交付。进程退出前尽量
排空(``atexit``);没排完的那几笔随进程丢失——计量允许这个损失。本模块对外的三个入口
(``meter_call`` / ``meter_rejected`` / ``record_mcp_usage``)不抛任何异常。

口径与表见 ``services/token_usage``。测试里交付之后要先 ``flush()`` 再去读库。
"""
from __future__ import annotations

import atexit
import datetime
import logging
import queue
import threading
import time
from dataclasses import dataclass
from typing import Any, List, Optional

from sqlmodel import Session

from api import deps
from api.feed_service import resolve_token_identity
from api.tokens import hash_subscription_token
from services import token_usage
from services.token_usage import TokenIdentity

logger = logging.getLogger(__name__)

QUEUE_MAX = 10_000   # 待落账的上限;满了就丢新的
BATCH_MAX = 64       # 一个写事务最多合并多少笔

_KIND_CALL = "call"
_KIND_REJECTED = "rejected"


@dataclass(frozen=True)
class _Entry:
    engine: Any
    kind: str
    channel: str
    at: str
    identity: Optional[TokenIdentity] = None   # call
    token_hash: str = ""                       # rejected
    items: int = 0


_queue: "queue.Queue[_Entry]" = queue.Queue(maxsize=QUEUE_MAX)
_worker: Optional[threading.Thread] = None
_worker_lock = threading.Lock()
_dropped = 0


def dropped() -> int:
    """队列满而丢掉的计量笔数(进程启动以来)。"""
    return _dropped


def _ensure_worker() -> None:
    """后台线程不在就起一个。起不来(线程数耗尽等)只告警:入队照常,下次入队再试。"""
    global _worker
    if _worker is not None and _worker.is_alive():
        return
    try:
        with _worker_lock:
            if _worker is None or not _worker.is_alive():
                thread = threading.Thread(target=_run, name="delivery-metering", daemon=True)
                thread.start()
                _worker = thread
    except Exception:  # noqa: BLE001 - 计量不阻断交付
        logger.warning("令牌交付计量后台线程启动失败(不影响交付,下次入队再试)", exc_info=True)


def _enqueue(kind: str, channel: str, **fields: Any) -> None:
    global _dropped
    try:
        entry = _Entry(
            engine=deps.get_db_sink().engine, kind=kind, channel=channel,
            at=datetime.datetime.now().isoformat(), **fields,
        )
        try:
            _queue.put_nowait(entry)
        except queue.Full:
            _dropped += 1
            if _dropped == 1 or _dropped % 1000 == 0:
                logger.warning("令牌交付计量队列已满,已丢弃 %d 笔(不影响交付)", _dropped)
    except Exception:  # noqa: BLE001 - 计量不阻断交付
        logger.warning("令牌交付计量入队失败(不影响交付):%s", channel, exc_info=True)
    # 无论这一笔进没进队列都确认一下后台线程:满队列往往正是因为它没在跑。
    _ensure_worker()


def meter_call(identity: Optional[TokenIdentity], channel: str, items: int = 0) -> None:
    """记一次成功调用;identity 为空(没认出令牌)时什么都不做。"""
    if identity is None:
        return
    _enqueue(_KIND_CALL, channel, identity=identity, items=int(items or 0))


def meter_rejected(token: Optional[str], channel: str) -> None:
    """一枚没通过校验的令牌:它若曾经有效、现已作废,在它名下记一次被拒;认不出的不留行。

    「现已作废」在这里(请求路径上)就判定:此刻仍是某人现用的令牌,说明它是用错了接口而不是
    作废了,不入队。这一步只读令牌表,不等写锁。
    """
    if not token:
        return
    try:
        token_hash = hash_subscription_token(token)
        with Session(deps.get_db_sink().engine) as session:
            if token_usage.token_is_live(session, token_hash):
                return
    except Exception:  # noqa: BLE001 - 计量不阻断交付
        logger.warning("令牌交付计量判定令牌状态失败(不影响交付):%s", channel, exc_info=True)
        return
    _enqueue(_KIND_REJECTED, channel, token_hash=token_hash)


def resolve_mcp_identity(token: str) -> Optional[TokenIdentity]:
    """MCP 工具鉴权通过的那一刻,把令牌解析成身份(``build_mcp_app(usage_identity=…)``);失败返回 None。"""
    try:
        return resolve_token_identity(token)
    except Exception:  # noqa: BLE001 - 计量不阻断交付
        logger.warning("令牌交付计量解析 MCP 令牌失败(不影响交付)", exc_info=True)
        return None


def record_mcp_usage(token: str, identity: Optional[TokenIdentity], tool: str, items: int, ok: bool) -> None:
    """MCP 工具的计量回调(``build_mcp_app(usage_recorder=…)``):成功按鉴权时的身份记,被拒按令牌记。"""
    try:
        channel = token_usage.mcp_channel(tool)
        if ok:
            meter_call(identity, channel, items)
        else:
            meter_rejected(token, channel)
    except Exception:  # noqa: BLE001 - 计量不阻断交付
        logger.warning("令牌交付计量 MCP 回调失败(不影响交付):%s", tool, exc_info=True)


def flush(timeout: float = 5.0) -> bool:
    """等已入队的计量全部落账(或放弃);返回是否在时限内排空。测试与进程退出前用。"""
    deadline = time.monotonic() + max(0.0, timeout)
    while _queue.unfinished_tasks:
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.005)
    return True


# ── 后台线程 ─────────────────────────────────────────────────────────────────

def _apply(session: Session, entry: _Entry) -> None:
    day = entry.at[:10]
    if entry.kind == _KIND_CALL:
        token_usage.record_call(session, entry.identity, entry.channel, items=entry.items, day=day, at=entry.at, commit=False)
    else:
        token_usage.record_rejected(session, entry.token_hash, entry.channel, day=day, at=entry.at, commit=False)


def _write(engine: Any, entries: List[_Entry]) -> None:
    """同一个库的一批计量合进一个写事务;整批失败时退回逐笔写,一笔坏的不连累其它。"""
    try:
        with Session(engine) as session:
            for entry in entries:
                _apply(session, entry)
            session.commit()
        return
    except Exception:  # noqa: BLE001 - 计量不阻断交付
        if len(entries) == 1:
            logger.warning("令牌交付计量写入失败(不影响交付):%s", entries[0].channel, exc_info=True)
            return
    for entry in entries:
        _write(engine, [entry])


def _take_batch(block: bool) -> List[_Entry]:
    batch: List[_Entry] = []
    try:
        batch.append(_queue.get() if block else _queue.get_nowait())
        while len(batch) < BATCH_MAX:
            batch.append(_queue.get_nowait())
    except queue.Empty:
        pass
    return batch


def _process(batch: List[_Entry]) -> None:
    try:
        by_engine: dict = {}
        for entry in batch:
            by_engine.setdefault(id(entry.engine), (entry.engine, []))[1].append(entry)
        for engine, entries in by_engine.values():
            _write(engine, entries)
    except Exception:  # noqa: BLE001 - 后台线程不能死
        logger.warning("令牌交付计量后台线程出错(不影响交付)", exc_info=True)
    finally:
        for _ in batch:
            _queue.task_done()


def _run() -> None:
    while True:
        _process(_take_batch(block=True))


def drain_now() -> int:
    """在当前线程里把队列里现有的计量落完账,返回处理了几笔。只给测试用(后台线程被停掉时)。"""
    done = 0
    while True:
        batch = _take_batch(block=False)
        if not batch:
            return done
        _process(batch)
        done += len(batch)


atexit.register(flush, 2.0)
