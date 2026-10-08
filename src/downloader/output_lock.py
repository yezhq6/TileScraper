# src/downloader/output_lock.py

"""输出目录锁：防止两个进程同时写同一份产物。

两个下载器（例如 Web 端 + 一个 CLI 实例，或两个 CLI 实例）指向同一个输出目录时：

* 会同时写同一个 ``progress.db``；
* MBTiles 模式下会同时开两个写线程插入同一个 ``tiles`` 表，
  这正是 "database is locked" 与写线程卡死的触发源。

这里用操作系统提供的 **advisory lock**（POSIX ``flock`` / Windows ``msvcrt.locking``）：

* 锁随文件描述符/进程退出自动释放，不需要处理"残留的 PID 锁文件"；
* 非阻塞获取（可选超时），拿不到就返回 False，由上层给出明确错误。

锁文件位置：

* 目录模式：``<output_dir>/.tilescraper.lock``
* MBTiles 模式：``<mbtiles 同级>/.<stem>.lock``
"""

import os
import time
from pathlib import Path

from loguru import logger

from .utils import ensure_directory

try:  # POSIX
    import fcntl
except ImportError:  # pragma: no cover - Windows
    fcntl = None

try:  # Windows
    import msvcrt
except ImportError:
    msvcrt = None


class OutputLock:
    """基于文件锁的输出目录互斥（同进程两个实例也会互斥）。"""

    def __init__(self, output_dir, is_mbtiles: bool = False,
                 timeout: float = 0.0, poll: float = 0.1):
        base = Path(output_dir)
        if is_mbtiles:
            self.path = base.parent / f".{base.stem}.lock"
        else:
            self.path = base / ".tilescraper.lock"
        self.timeout = max(0.0, float(timeout))
        self.poll = max(0.01, float(poll))
        self.holder_pid = None
        self._file = None

    # ------------------------------------------------------------------ #
    def _try_lock(self) -> bool:
        try:
            ensure_directory(self.path.parent)
        except OSError as e:  # noqa: PERF203 - 只读挂载等场景不应阻断下载
            logger.warning(f"无法创建锁文件目录 {self.path.parent}: {e}；跳过输出目录锁")
            return True
        try:
            handle = open(self.path, "a+", encoding="utf-8")
        except OSError as e:
            logger.warning(f"无法创建输出目录锁 {self.path}: {e}；跳过输出目录锁")
            return True

        try:
            handle.seek(0)
            if fcntl is not None:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            elif msvcrt is not None:
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            # 已被占用：读出占用者 PID 方便报错
            try:
                handle.seek(0)
                self.holder_pid = (handle.read() or "").strip() or None
            except OSError:
                self.holder_pid = None
            try:
                handle.close()
            except OSError:
                pass
            return False

        self._file = handle
        try:
            handle.seek(0)
            handle.truncate()
            handle.write(f"{os.getpid()}\n")
            handle.flush()
        except OSError:
            pass
        return True

    def acquire(self) -> bool:
        """尝试获取锁；timeout 秒内不成功则返回 False。"""
        deadline = time.time() + self.timeout
        while True:
            if self._try_lock():
                return True
            if time.time() >= deadline:
                return False
            time.sleep(self.poll)

    def release(self):
        """释放锁（幂等）。"""
        handle = self._file
        if handle is None:
            return
        self._file = None
        try:
            if fcntl is not None:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            elif msvcrt is not None:
                try:
                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                except OSError:
                    pass
        except OSError:
            pass
        try:
            handle.close()
        except OSError:
            pass

    def __del__(self):
        # 未显式 release 时（例如构造完下载器却没 start）兜底释放
        try:
            self.release()
        except Exception:  # noqa: BLE001
            pass
