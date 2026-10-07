# src/downloader/progress_handler.py

"""断点续传的进度存储。

设计目标：**内存占用与历史已下载规模解耦**。

旧实现会把指定缩放范围内"已处理"的瓦片全部读进一个 Python ``set``，
在亿级瓦片时直接导致内存爆炸（几 GB）。这里改为：

* 写入：只在内存保留一个小批量缓冲区，满一批就落库；
* 查询：按 (z, x, y) 顺序、用 keyset 分页流式读取，每页固定条数，
  读完即释放，不长期持有游标（也避免 WAL 长期无法 checkpoint）；
* 单个判断：``is_processed`` 走主键点查，O(1) 内存。

这样无论历史集合有多大，进程内存都只与"页大小 / 缓冲区大小"相关。
"""

import time
import threading
import sqlite3
from pathlib import Path
from typing import Iterator, Tuple

from loguru import logger

from ..config import config_manager
from .utils import ensure_directory


class ProgressHandler:
    """
    进度处理器：负责断点续传所需的"已处理瓦片"记录。
    """

    def __init__(self, downloader):
        self.downloader = downloader
        self.output_dir = downloader.output_dir
        self.enable_resume = downloader.enable_resume

        self.progress_file = None
        self._closed = False

        self.thread_local = threading.local()

        # 写入缓冲区（多线程共享，需加锁）
        self.batch_size = int(config_manager.get("download.progress_batch_size", 500))
        self.batch_buffer = []
        self._buf_lock = threading.Lock()

        # keyset 分页的每页条数
        self.page_size = int(config_manager.get("download.progress_page_size", 50000))

        if self.enable_resume:
            self.initialize()

    # ------------------------------------------------------------------ #
    # 连接 / 初始化
    # ------------------------------------------------------------------ #
    def _get_connection(self) -> sqlite3.Connection:
        """获取或创建当前线程的进度库连接。"""
        if not hasattr(self.thread_local, 'conn'):
            conn = sqlite3.connect(str(self.progress_file), check_same_thread=False)
            conn.execute('PRAGMA journal_mode=WAL;')
            conn.execute('PRAGMA synchronous=NORMAL;')
            conn.execute('PRAGMA busy_timeout=30000;')
            conn.execute('PRAGMA temp_store=MEMORY;')
            self.thread_local.conn = conn
            logger.debug(f"为线程 {threading.current_thread().name} 创建进度库连接")
        return self.thread_local.conn

    def initialize(self):
        """确定进度库路径并建表建索引。"""
        try:
            if self.downloader.is_mbtiles:
                progress_dir = Path(self.output_dir).parent
                progress_file = progress_dir / f"{Path(self.output_dir).stem}.progress.db"
            else:
                progress_file = Path(self.output_dir) / "progress.db"

            self.progress_file = progress_file
            ensure_directory(progress_file.parent)

            conn = self._get_connection()
            conn.execute(
                '''
                CREATE TABLE IF NOT EXISTS processed_tiles (
                    x INTEGER,
                    y INTEGER,
                    z INTEGER,
                    status TEXT,
                    PRIMARY KEY (x, y, z)
                )
                '''
            )
            # 按 (z, x, y) 顺序扫描的索引，支撑 keyset 分页/归并跳过
            conn.execute(
                'CREATE INDEX IF NOT EXISTS idx_processed_tiles_zxy '
                'ON processed_tiles (z, x, y)'
            )
            conn.commit()
            logger.info(f"进度数据库初始化成功: {progress_file}")
        except Exception as e:  # noqa: BLE001
            logger.error(f"初始化进度数据库失败: {e}")
            # 初始化失败时关闭断点续传，避免后续操作反复报错
            self.enable_resume = False

    # ------------------------------------------------------------------ #
    # 查询（均为 O(1) / O(page) 内存）
    # ------------------------------------------------------------------ #
    def is_processed(self, x: int, y: int, z: int) -> bool:
        """判断单个瓦片是否已处理（主键点查）。"""
        if not self.enable_resume or not self.progress_file:
            return False
        try:
            row = self._get_connection().execute(
                'SELECT 1 FROM processed_tiles WHERE x = ? AND y = ? AND z = ? LIMIT 1',
                (x, y, z),
            ).fetchone()
            return row is not None
        except Exception as e:  # noqa: BLE001
            logger.error(f"查询瓦片处理状态失败: {e}")
            return False

    def iter_processed_range(
        self,
        zoom: int,
        min_x: int,
        max_x: int,
        min_y: int,
        max_y: int,
    ) -> Iterator[Tuple[int, int]]:
        """
        在指定 zoom 和 x/y 范围内，按 (x, y) 升序流式产出已处理瓦片坐标。

        使用 keyset 分页：每页只加载 ``page_size`` 条，页与页之间不持有游标，
        因此内存占用恒定，且不会因长事务阻塞 WAL checkpoint。
        """
        if not self.enable_resume or not self.progress_file:
            return
        if max_x < min_x or max_y < min_y:
            return

        conn = self._get_connection()
        last = None
        while True:
            try:
                if last is None:
                    rows = conn.execute(
                        'SELECT x, y FROM processed_tiles '
                        'WHERE z = ? AND x BETWEEN ? AND ? AND y BETWEEN ? AND ? '
                        'ORDER BY x, y LIMIT ?',
                        (zoom, min_x, max_x, min_y, max_y, self.page_size),
                    ).fetchall()
                else:
                    last_x, last_y = last
                    rows = conn.execute(
                        'SELECT x, y FROM processed_tiles '
                        'WHERE z = ? AND x BETWEEN ? AND ? AND y BETWEEN ? AND ? '
                        'AND (x > ? OR (x = ? AND y > ?)) '
                        'ORDER BY x, y LIMIT ?',
                        (
                            zoom, min_x, max_x, min_y, max_y,
                            last_x, last_x, last_y, self.page_size,
                        ),
                    ).fetchall()
            except Exception as e:  # noqa: BLE001
                logger.error(f"流式读取已处理瓦片失败: {e}")
                return

            if not rows:
                return
            for x, y in rows:
                yield x, y
            if len(rows) < self.page_size:
                return
            last = rows[-1]

    # ------------------------------------------------------------------ #
    # 记录 / 保存
    # ------------------------------------------------------------------ #
    def mark_tile_processed(self, x: int, y: int, z: int, status: str):
        """标记瓦片已处理（内存缓冲区满则批量落库）。"""
        if not self.enable_resume or not self.progress_file:
            return
        with self._buf_lock:
            self.batch_buffer.append((x, y, z, status))
            full = len(self.batch_buffer) >= self.batch_size
        if full:
            self._batch_process_tiles()

    def _batch_process_tiles(self):
        """把缓冲区中的瓦片批量写入进度库。"""
        with self._buf_lock:
            if not self.batch_buffer:
                return
            batch = self.batch_buffer
            self.batch_buffer = []
        self._write_rows(batch)

    def _write_rows(self, rows, max_retries: int = 5):
        """带重试地写入一批进度记录。"""
        if not rows:
            return
        delay = 0.1
        for attempt in range(max_retries):
            try:
                conn = self._get_connection()
                conn.executemany(
                    'INSERT OR REPLACE INTO processed_tiles (x, y, z, status) '
                    'VALUES (?, ?, ?, ?)',
                    rows,
                )
                conn.commit()
                return
            except sqlite3.OperationalError as e:
                if 'database is locked' in str(e) and attempt < max_retries - 1:
                    time.sleep(delay)
                    delay *= 1.5
                else:
                    logger.error(f"写入进度失败: {e}")
                    return
            except Exception as e:  # noqa: BLE001
                logger.error(f"写入进度失败: {e}")
                return

    def save_progress(self):
        """把内存缓冲区中的进度落盘（不遍历任何全量集合）。"""
        self._batch_process_tiles()

    def close(self):
        """关闭当前线程连接（幂等）。"""
        if self._closed:
            return
        self._closed = True
        try:
            self._batch_process_tiles()
        except Exception:
            pass
        if hasattr(self.thread_local, 'conn'):
            try:
                self.thread_local.conn.commit()
                self.thread_local.conn.close()
            except Exception as e:  # noqa: BLE001
                logger.error(f"关闭进度数据库连接失败: {e}")
            del self.thread_local.conn
