"""services.file_lock:POSIX 委托 fcntl;Windows 分支用假 msvcrt 验证争用映射、解锁与位置还原(issue #133)。"""
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


class _FakeMsvcrt:
    LK_UNLCK, LK_LOCK, LK_NBLCK = 0, 1, 2

    def __init__(self, *, held=False, fail_errno=None):
        self.held = held
        self.fail_errno = fail_errno
        self.calls = []

    def locking(self, fd, mode, nbytes):
        self.calls.append((fd, mode, nbytes, os.lseek(fd, 0, os.SEEK_CUR)))
        if mode == self.LK_UNLCK:
            if not self.held:
                raise OSError(errno.EACCES, "not locked")
            self.held = False
            return
        if self.fail_errno is not None:
            raise OSError(self.fail_errno, "boom")
        if self.held:
            raise OSError(errno.EACCES if mode == self.LK_NBLCK else errno.EDEADLK, "busy")
        self.held = True


def test_windows_branch_locks_first_byte_and_restores_position(tmp_path):
    path = tmp_path / "lock"
    path.write_bytes(b"0123456789")
    fd = os.open(path, os.O_RDWR)
    try:
        os.lseek(fd, 5, os.SEEK_SET)
        fake = _FakeMsvcrt()
        file_lock._flock_msvcrt(fake, fd, file_lock.LOCK_EX | file_lock.LOCK_NB)
        assert fake.calls == [(fd, fake.LK_NBLCK, 1, 0)]  # 锁偏移 0 的一个字节
        assert os.lseek(fd, 0, os.SEEK_CUR) == 5  # 位置还原
        assert fake.held
        file_lock._flock_msvcrt(fake, fd, file_lock.LOCK_UN)
        assert fake.calls[-1][1] == fake.LK_UNLCK and not fake.held
        # 未持锁时解锁静默(与 flock(LOCK_UN) 一致)
        file_lock._flock_msvcrt(fake, fd, file_lock.LOCK_UN)
        # 文件对象也可以
        with path.open("a+b") as handle:
            file_lock._flock_msvcrt(fake, handle, file_lock.LOCK_EX)
            assert fake.calls[-1][1] == fake.LK_LOCK
    finally:
        os.close(fd)


@pytest.mark.parametrize("operation", [file_lock.LOCK_EX | file_lock.LOCK_NB, file_lock.LOCK_EX, file_lock.LOCK_SH | file_lock.LOCK_NB])
def test_windows_branch_maps_contention_to_blocking_io_error(tmp_path, operation):
    fd = os.open(tmp_path / "lock", os.O_RDWR | os.O_CREAT)
    try:
        fake = _FakeMsvcrt(held=True)
        with pytest.raises(BlockingIOError):
            file_lock._flock_msvcrt(fake, fd, operation)
        assert os.lseek(fd, 0, os.SEEK_CUR) == 0
    finally:
        os.close(fd)


def test_windows_branch_reraises_non_contention_errors(tmp_path):
    fd = os.open(tmp_path / "lock", os.O_RDWR | os.O_CREAT)
    try:
        fake = _FakeMsvcrt(fail_errno=errno.EBADF)
        with pytest.raises(OSError) as info:
            file_lock._flock_msvcrt(fake, fd, file_lock.LOCK_EX | file_lock.LOCK_NB)
        assert not isinstance(info.value, BlockingIOError) and info.value.errno == errno.EBADF
    finally:
        os.close(fd)
