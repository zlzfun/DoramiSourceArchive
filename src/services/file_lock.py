"""跨平台文件锁(issue #133):POSIX 委托 ``fcntl.flock``,Windows 用 ``msvcrt.locking`` 锁文件首字节。

生产只有 Linux(Docker / 裸机 PM2),Windows 只是开发机跑测试;但六处调用点(播客产物 CAS 与预留标记、
TTS 回执池、对象存储缓存与状态文件、日报生成、备份、存储维护)都是**跨进程互斥**,所以 Windows 也给真锁,
不做空实现——空实现会让锁静默失效。

语义差异(只在 Windows):无共享锁,``LOCK_SH`` 按独占处理;阻塞模式由 CRT 重试约 10 s 后放弃,同样抛
``BlockingIOError``;锁的是文件偏移 0 的一个字节(允许超出文件末尾),调用后文件位置原样还原。
调用点统一 ``except BlockingIOError`` 判争用,两个平台一致。**仓库内不要再裸 ``import fcntl``。**
"""
from __future__ import annotations

import errno
import os
from typing import Any

try:  # POSIX
    import fcntl as _fcntl
except ImportError:  # Windows
    _fcntl = None

if _fcntl is not None:
    LOCK_SH, LOCK_EX, LOCK_NB, LOCK_UN = _fcntl.LOCK_SH, _fcntl.LOCK_EX, _fcntl.LOCK_NB, _fcntl.LOCK_UN
else:
    LOCK_SH, LOCK_EX, LOCK_NB, LOCK_UN = 1, 2, 4, 8

# msvcrt.locking 争用时:LK_NBLCK 抛 EACCES,LK_LOCK 重试用尽抛 EDEADLOCK
_CONTENTION_ERRNOS = {errno.EACCES, errno.EAGAIN, errno.EDEADLK, getattr(errno, "EDEADLOCK", errno.EDEADLK)}


def _fileno(fd: Any) -> int:
    return fd if isinstance(fd, int) else fd.fileno()


def _flock_msvcrt(msvcrt: Any, fd: Any, operation: int) -> None:
    """Windows 实现(msvcrt 以参数传入,便于在 POSIX 上用假模块测试)。"""
    fileno = _fileno(fd)
    position = os.lseek(fileno, 0, os.SEEK_CUR)
    os.lseek(fileno, 0, os.SEEK_SET)
    try:
        if operation & LOCK_UN:
            try:
                msvcrt.locking(fileno, msvcrt.LK_UNLCK, 1)
            except OSError:
                pass  # 未持锁时解锁:与 flock(LOCK_UN) 一样静默
            return
        mode = msvcrt.LK_NBLCK if operation & LOCK_NB else msvcrt.LK_LOCK
        try:
            msvcrt.locking(fileno, mode, 1)
        except OSError as exc:
            if exc.errno in _CONTENTION_ERRNOS:
                raise BlockingIOError(errno.EAGAIN, "file lock is held by another process") from exc
            raise
    finally:
        os.lseek(fileno, position, os.SEEK_SET)


def flock(fd: Any, operation: int) -> None:
    """与 ``fcntl.flock`` 同签名:``fd`` 可以是文件描述符或带 ``fileno()`` 的文件对象。"""
    if _fcntl is not None:
        _fcntl.flock(fd, operation)
        return
    import msvcrt  # noqa: WPS433  Windows-only

    _flock_msvcrt(msvcrt, fd, operation)
