"""services.file_lock:POSIX 委托 fcntl;Windows 分支用假 Win32 API 验证 flags / 锁区 / 错误映射(issue #133)。"""
from __future__ import annotations

import errno
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from services import file_lock  # noqa: E402


@pytest.mark.skipif(file_lock._fcntl is None, reason="POSIX only")
def test_posix_path_delegates_to_fcntl_and_reports_contention(tmp_path):
    path = tmp_path / "lock"
    with path.open("a+b") as first, path.open("a+b") as second:
        file_lock.flock(first, file_lock.LOCK_EX | file_lock.LOCK_NB)
        with pytest.raises(BlockingIOError):
            file_lock.flock(second.fileno(), file_lock.LOCK_EX | file_lock.LOCK_NB)
        file_lock.flock(first, file_lock.LOCK_UN)
        file_lock.flock(second, file_lock.LOCK_EX | file_lock.LOCK_NB)  # 释放后可得


class _FakeWin32:
    """记录调用;lock_result / error_code 控制失败路径。"""

    def __init__(self, *, lock_result=True, error_code=0):
        self.lock_result = lock_result
        self.error_code = error_code
        self.calls = []

    def handle_of(self, fileno):
        return 1000 + fileno

    def lock(self, handle, flags, offset, length):
        self.calls.append(("lock", handle, flags, offset, length))
        return self.lock_result

    def unlock(self, handle, offset, length):
        self.calls.append(("unlock", handle, offset, length))
        return False  # 未持锁时的失败也要被静默

    def last_error(self):
        return self.error_code

    def os_error(self, code):
        return OSError(code, f"win32 error {code}")


@pytest.mark.parametrize(
    ("operation", "flags"),
    [
        (file_lock.LOCK_EX, file_lock.LOCKFILE_EXCLUSIVE_LOCK),
        (file_lock.LOCK_EX | file_lock.LOCK_NB, file_lock.LOCKFILE_EXCLUSIVE_LOCK | file_lock.LOCKFILE_FAIL_IMMEDIATELY),
        (file_lock.LOCK_SH, 0),  # 真共享锁:不带 EXCLUSIVE
        (file_lock.LOCK_SH | file_lock.LOCK_NB, file_lock.LOCKFILE_FAIL_IMMEDIATELY),
    ],
)
def test_windows_branch_maps_operations_to_lockfileex_flags(operation, flags):
    fake = _FakeWin32()
    file_lock._flock_win32(fake, 7, operation)
    # 锁区固定在 2^62 偏移 1 字节:永不与数据相交,强制锁不影响任何句柄的数据 I/O(codex R1 P2-1)
    assert fake.calls == [("lock", 1007, flags, 1 << 62, 1)]


def test_windows_branch_accepts_file_objects_and_unlocks_silently(tmp_path):
    with (tmp_path / "lock").open("a+b") as handle:
        fake = _FakeWin32()
        file_lock._flock_win32(fake, handle, file_lock.LOCK_EX)
        file_lock._flock_win32(fake, handle, file_lock.LOCK_UN)  # unlock 返回 False 也不抛
        assert fake.calls[0][1] == 1000 + handle.fileno()
        assert fake.calls[1] == ("unlock", 1000 + handle.fileno(), 1 << 62, 1)


@pytest.mark.parametrize("code", [file_lock.ERROR_LOCK_VIOLATION, file_lock.ERROR_IO_PENDING])
def test_windows_branch_maps_contention_to_blocking_io_error(code):
    fake = _FakeWin32(lock_result=False, error_code=code)
    with pytest.raises(BlockingIOError):
        file_lock._flock_win32(fake, 7, file_lock.LOCK_EX | file_lock.LOCK_NB)


def test_windows_branch_raises_other_win32_errors_as_os_error():
    fake = _FakeWin32(lock_result=False, error_code=6)  # ERROR_INVALID_HANDLE
    with pytest.raises(OSError) as info:
        file_lock._flock_win32(fake, 7, file_lock.LOCK_EX)
    assert not isinstance(info.value, BlockingIOError) and info.value.errno == 6


def test_blocking_mode_never_sets_fail_immediately():
    """阻塞模式交给 LockFileEx 真阻塞、无超时,调用方不会在未获锁时进入临界区(codex R1 P2-2)。"""
    fake = _FakeWin32()
    file_lock._flock_win32(fake, 7, file_lock.LOCK_EX)
    file_lock._flock_win32(fake, 7, file_lock.LOCK_SH)
    assert all(not (call[2] & file_lock.LOCKFILE_FAIL_IMMEDIATELY) for call in fake.calls)
