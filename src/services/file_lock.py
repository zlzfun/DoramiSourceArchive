"""跨平台文件锁(issue #133):POSIX 委托 ``fcntl.flock``,Windows 经 ctypes 调 ``LockFileEx`` / ``UnlockFileEx``。

生产只有 Linux(Docker / 裸机 PM2),Windows 只是开发机跑测试;但六处调用点(播客产物 CAS 与预留标记、
TTS 回执池、对象存储缓存与状态文件、日报生成、备份、存储维护)都是**跨进程互斥**,所以 Windows 也给真锁,
不做空实现——空实现会让锁静默失效。

Windows 为什么不用 ``msvcrt.locking``(codex R1 两条 P2):它只有强制字节锁——锁在数据文件首字节会挡住
同进程另一个句柄读数据(播客上传 fd 持锁期间 ``_hash_file`` 重开读取会炸);没有共享锁;阻塞模式 CRT 重试
约 10 s 就放弃,调用方会在未获锁时进入临界区。``LockFileEx`` 三者都有:``LOCK_SH`` 是真共享锁,
不带 ``LOCKFILE_FAIL_IMMEDIATELY`` 即真阻塞、无超时;锁区固定为偏移 2^62 处 1 字节(允许锁超出 EOF 的区间),
数据读写永不与锁区相交,所以强制锁不会影响任何句柄的数据 I/O。``LOCK_NB`` 争用 = ``ERROR_LOCK_VIOLATION``
→ ``BlockingIOError``,与调用点既有的 ``except BlockingIOError`` 一致;其它 Win32 错误原样成 ``OSError``。

Windows 分支未在真实 Windows 上执行过,契约由假 API 单测守住(``tests/test_file_lock.py``)。
**仓库内不要再裸 ``import fcntl``。**
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

# Win32 常量(与平台无关的数值,便于在 POSIX 上测试映射)
LOCKFILE_FAIL_IMMEDIATELY = 0x00000001
LOCKFILE_EXCLUSIVE_LOCK = 0x00000002
ERROR_LOCK_VIOLATION = 33
ERROR_IO_PENDING = 997
# 锁区:偏移 2^62 处 1 字节,永不与真实数据相交(Windows 字节锁是强制锁)
LOCK_OFFSET = 1 << 62
LOCK_LENGTH = 1


def _fileno(fd: Any) -> int:
    return fd if isinstance(fd, int) else fd.fileno()


class _Win32LockApi:
    """LockFileEx / UnlockFileEx 的薄封装;测试用同接口的假对象替换。"""

    def __init__(self) -> None:
        import ctypes
        import msvcrt
        from ctypes import wintypes

        class OVERLAPPED(ctypes.Structure):
            _fields_ = [
                ("Internal", ctypes.c_void_p),
                ("InternalHigh", ctypes.c_void_p),
                ("Offset", wintypes.DWORD),
                ("OffsetHigh", wintypes.DWORD),
                ("hEvent", wintypes.HANDLE),
            ]

        self._ctypes = ctypes
        self._overlapped_type = OVERLAPPED
        self._get_osfhandle = msvcrt.get_osfhandle
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.LockFileEx.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD, ctypes.POINTER(OVERLAPPED)]
        kernel32.LockFileEx.restype = wintypes.BOOL
        kernel32.UnlockFileEx.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD, ctypes.POINTER(OVERLAPPED)]
        kernel32.UnlockFileEx.restype = wintypes.BOOL
        self._kernel32 = kernel32

    def _overlapped(self, offset: int):
        ov = self._overlapped_type()
        ov.Offset = offset & 0xFFFFFFFF
        ov.OffsetHigh = (offset >> 32) & 0xFFFFFFFF
        return ov

    def handle_of(self, fileno: int) -> int:
        return self._get_osfhandle(fileno)

    def lock(self, handle: int, flags: int, offset: int, length: int) -> bool:
        ov = self._overlapped(offset)
        return bool(self._kernel32.LockFileEx(handle, flags, 0, length, 0, self._ctypes.byref(ov)))

    def unlock(self, handle: int, offset: int, length: int) -> bool:
        ov = self._overlapped(offset)
        return bool(self._kernel32.UnlockFileEx(handle, 0, length, 0, self._ctypes.byref(ov)))

    def last_error(self) -> int:
        return self._ctypes.get_last_error()

    def os_error(self, code: int) -> OSError:
        return self._ctypes.WinError(code)


_win32_api: Any = None


def _flock_win32(api: Any, fd: Any, operation: int) -> None:
    """Windows 实现(api 以参数传入,便于在 POSIX 上用假对象测试)。"""
    handle = api.handle_of(_fileno(fd))
    if operation & LOCK_UN:
        api.unlock(handle, LOCK_OFFSET, LOCK_LENGTH)  # 未持锁时失败:与 flock(LOCK_UN) 一样静默
        return
    flags = 0
    if operation & LOCK_EX:
        flags |= LOCKFILE_EXCLUSIVE_LOCK
    if operation & LOCK_NB:
        flags |= LOCKFILE_FAIL_IMMEDIATELY
    if api.lock(handle, flags, LOCK_OFFSET, LOCK_LENGTH):
        return
    code = api.last_error()
    if code in (ERROR_LOCK_VIOLATION, ERROR_IO_PENDING):
        raise BlockingIOError(errno.EAGAIN, "file lock is held by another process")
    raise api.os_error(code)


def flock(fd: Any, operation: int) -> None:
    """与 ``fcntl.flock`` 同签名:``fd`` 可以是文件描述符或带 ``fileno()`` 的文件对象。"""
    if _fcntl is not None:
        _fcntl.flock(fd, operation)
        return
    global _win32_api
    if _win32_api is None:
        _win32_api = _Win32LockApi()
    _flock_win32(_win32_api, fd, operation)
