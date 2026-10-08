# src/downloader/mbtiles_handler.py

import time
import threading
import sqlite3
from pathlib import Path
from queue import Empty

from loguru import logger

from .connection_pool import ConnectionPool
from .utils import ensure_directory
from ..config import config_manager

_TILES_TABLE_SQL = '''
    CREATE TABLE IF NOT EXISTS tiles (
        zoom_level INTEGER,
        tile_column INTEGER,
        tile_row INTEGER,
        tile_data BLOB,
        PRIMARY KEY (zoom_level, tile_column, tile_row)
    )
'''

_METADATA_TABLE_SQL = '''
    CREATE TABLE IF NOT EXISTS metadata (
        name TEXT,
        value TEXT,
        PRIMARY KEY (name)
    )
'''


class MBTilesHandler:
    """
    MBTiles 处理器：负责把下载线程产生的瓦片异步写入 MBTiles 数据库。

    生产/消费模型：
    * 下载线程把 (z, x, mbtiles_row, data) 放入 ``downloader.mbtiles_write_queue``；
    * 单个写入线程批量消费该队列并写库，避免多线程同时写 SQLite 造成锁竞争。

    注意：写入队列与下载器共享同一个对象，这是旧版本的关键缺陷
    （生产者与消费者使用了两个不同的队列，导致瓦片从未落库）。
    """

    def __init__(self, downloader):
        self.downloader = downloader
        self.output_path = Path(downloader.output_dir)
        self.scheme = downloader.scheme
        self.provider = downloader.provider
        self.is_mbtiles = downloader.is_mbtiles
        self.enable_sharding = downloader.enable_sharding

        # 与下载器共用同一个写队列
        self.mbtiles_write_queue = downloader.mbtiles_write_queue
        self.mbtiles_batch_size = int(
            config_manager.get("download.mbtiles_batch_size", 500)
        )
        self.mbtiles_paths = {}
        self._initialized_paths = set()
        self._closed = False

        # 写入健康度：用于让上层感知"写盘失败 / 写线程卡死"，而不是静默丢数据
        self._write_lock = threading.Lock()
        self._write_failures = 0
        self._last_write_activity = time.time()
        self.mbtiles_writer_error = None
        # commit 成功后才把瓦片标记为已完成（见 _write_and_ack）
        self.ack_enabled = bool(getattr(downloader, "mbtiles_ack", False))

        # 非分库模式下的单连接（仅写入线程使用）
        self.mbtiles_conn = None

        # 分库模式下按 zoom 使用独立连接
        self.connection_pool = ConnectionPool()

        # 断点续传校验用的独立连接：按线程缓存，便于并发点查。
        # （写线程与多个工作线程都会调用 has_tile，共用一条 sqlite 连接不安全）
        self._verify_local = threading.local()
        self._verify_lock = threading.Lock()
        self._verify_conns = []

        if self.is_mbtiles and not self.enable_sharding:
            self._init_mbtiles()

        if self.is_mbtiles:
            self._start_mbtiles_writer()

    # ------------------------------------------------------------------ #
    # 初始化
    # ------------------------------------------------------------------ #
    def _init_mbtiles(self):
        """初始化非分库 MBTiles 数据库及表结构。"""
        max_retries = 5
        retry_delay = 1

        for attempt in range(max_retries):
            try:
                self.mbtiles_conn = sqlite3.connect(self.output_path, check_same_thread=False)
                self._configure_connection(self.mbtiles_conn)
                self._ensure_schema(self.mbtiles_conn)
                logger.info(f"MBTiles数据库初始化完成: {self.output_path}")
                return
            except sqlite3.OperationalError as e:
                self._safe_close(self.mbtiles_conn)
                self.mbtiles_conn = None
                if "database is locked" in str(e) and attempt < max_retries - 1:
                    logger.warning(f"MBTiles数据库被锁定，重试 ({attempt + 1}/{max_retries})...")
                    time.sleep(retry_delay)
                    retry_delay *= 2
                else:
                    logger.error(f"MBTiles数据库初始化失败: {e}")
                    raise
            except Exception as e:  # noqa: BLE001
                self._safe_close(self.mbtiles_conn)
                self.mbtiles_conn = None
                logger.error(f"MBTiles数据库初始化失败: {e}")
                raise

    @staticmethod
    def _configure_connection(conn: sqlite3.Connection):
        """
        为 SQLite 连接设置适合批量写入的参数。

        取值来自 `config.yaml` 的 `database` 段（以前这里是硬编码，导致那段配置
        改了不生效）。数值/枚举都会做轻量校验，非法值回退到内置默认。
        """
        valid_journal = {"DELETE", "TRUNCATE", "PERSIST", "MEMORY", "WAL", "OFF"}
        valid_sync = {"OFF", "NORMAL", "FULL", "EXTRA"}

        def _enum(key, allowed, fallback):
            value = str(config_manager.get(f"database.{key}", fallback) or fallback).upper()
            return value if value in allowed else fallback

        def _int(key, fallback):
            try:
                return int(config_manager.get(f"database.{key}", fallback))
            except (TypeError, ValueError):
                return fallback

        pragmas = (
            f"PRAGMA journal_mode={_enum('journal_mode', valid_journal, 'WAL')};",
            f"PRAGMA cache_size={_int('cache_size', 2000000)};",
            f"PRAGMA synchronous={_enum('synchronous', valid_sync, 'NORMAL')};",
            f"PRAGMA busy_timeout={_int('busy_timeout', 30000)};",
            f"PRAGMA mmap_size={_int('mmap_size', 536870912)};",
            'PRAGMA wal_autocheckpoint=5000;',
            'PRAGMA temp_store=MEMORY;',
            'PRAGMA auto_vacuum=NONE;',
            'PRAGMA foreign_keys=OFF;',
        )
        for pragma in pragmas:
            try:
                conn.execute(pragma)
            except sqlite3.Error:
                # 个别 pragma 在部分 SQLite 版本不可用，忽略即可
                pass

    def _ensure_schema(self, conn: sqlite3.Connection, zoom=None):
        """创建 tiles / metadata 表并写入基础元数据。"""
        cursor = conn.cursor()
        cursor.execute(_TILES_TABLE_SQL)
        cursor.execute(_METADATA_TABLE_SQL)

        description = 'Generated by TileScraper'
        if zoom is not None:
            description = f'Generated by TileScraper for zoom level {zoom}'

        metadata = [
            ('name', 'TileScraper'),
            ('type', 'baselayer'),
            ('version', '1.0'),
            ('description', description),
            ('format', self.provider.extension),
            ('scheme', self.scheme),
        ]
        cursor.executemany(
            'INSERT OR REPLACE INTO metadata (name, value) VALUES (?, ?)',
            metadata,
        )
        conn.commit()

    # ------------------------------------------------------------------ #
    # 写入线程
    # ------------------------------------------------------------------ #
    def _start_mbtiles_writer(self):
        """启动 MBTiles 写入线程。"""
        self.mbtiles_writer_stop_event = threading.Event()
        self.mbtiles_writer_thread = threading.Thread(
            target=self._mbtiles_writer, name="MBTilesWriter", daemon=True
        )
        self.mbtiles_writer_thread.start()
        logger.info("MBTiles写入线程已启动")

    def _stop_mbtiles_writer(self):
        """停止 MBTiles 写入线程（应先确保队列已排空）。"""
        if self.mbtiles_writer_thread and self.mbtiles_writer_thread.is_alive():
            self.mbtiles_writer_stop_event.set()
            # 哨兵一定会被消费（写线程的循环只在拿到哨兵或空闲且已置停止位时退出）
            self.mbtiles_write_queue.put(None)
            self.mbtiles_writer_thread.join(timeout=5)
            if self.mbtiles_writer_thread.is_alive():
                logger.warning("MBTiles 写入线程未在 5s 内退出，可能仍在写盘")
            else:
                logger.info("MBTiles写入线程已停止")

    def _mbtiles_writer(self):
        """写线程主体：批量消费写队列，直到收到停止哨兵。"""
        name = threading.current_thread().name
        logger.debug(f"{name} 启动")
        buffers = {}

        while True:
            try:
                task = self.mbtiles_write_queue.get(timeout=1)
            except Empty:
                # 超时空闲：把缓冲区里剩余的瓦片刷盘；
                # 若已请求停止且确实空闲，则退出
                self._flush_all(buffers)
                if self.mbtiles_writer_stop_event.is_set():
                    break
                continue

            try:
                if task is None:
                    self._flush_all(buffers)
                    break
                z, x, row, data, y = task
                buffers.setdefault(z, []).append((x, row, data, y))
                if len(buffers[z]) >= self.mbtiles_batch_size:
                    self._write_and_ack(z, buffers[z])
                    buffers[z] = []
                # 队列空了就把剩余数据刷盘（task_done 之前完成，便于 join 语义）
                if self.mbtiles_write_queue.empty():
                    self._flush_all(buffers)
            except Exception as e:  # noqa: BLE001
                logger.error(f"{name} - 写入任务失败: {e}")
                import traceback
                traceback.print_exc()
            finally:
                self.mbtiles_write_queue.task_done()

        self._flush_all(buffers)
        logger.debug(f"{name} 结束")

    def _flush_all(self, buffers: dict):
        """把各 zoom 缓冲区的剩余瓦片全部写盘。"""
        for zoom, tiles in list(buffers.items()):
            if tiles:
                self._write_and_ack(zoom, tiles)
                buffers[zoom] = []

    # ------------------------------------------------------------------ #
    # 提交确认（ack）
    # ------------------------------------------------------------------ #
    def _write_and_ack(self, zoom: int, tiles: list) -> bool:
        """
        写盘，并按需回写进度。

        ``ack_enabled`` 打开时，"标记成功"只在写线程 commit 成功之后发生，
        因此 ``progress.db`` 永远不会领先于实际产物 —— 即使进程被强杀，
        最坏情况也只是"少记了已提交的瓦片"（下次重下），而不会"记了却没写"（丢数据）。
        """
        ok = self._write_batch(zoom, tiles)
        if self.ack_enabled:
            if ok:
                self._ack_success(zoom, tiles)
            else:
                self._ack_failure(zoom, tiles)
        return ok

    def _ack_success(self, zoom: int, tiles: list):
        d = self.downloader
        total_bytes = 0
        for _x, _row, data, y in tiles:
            d._mark_tile_processed(_x, y, zoom, 'success')
            total_bytes += len(data)
        d._add_bytes(total_bytes)
        d._update_progress()

    def _ack_failure(self, zoom: int, tiles: list):
        d = self.downloader
        for _x, _row, _data, y in tiles:
            d._mark_tile_processed(_x, y, zoom, 'failed')
        d._update_progress()

    @property
    def write_failures(self) -> int:
        """写入失败的批次数（>0 表示有瓦片未落盘）。"""
        with self._write_lock:
            return self._write_failures

    def _record_write_failure(self, zoom: int, count: int, error):
        with self._write_lock:
            self._write_failures += 1
            self.mbtiles_writer_error = error
        logger.error(
            f"批量写入 MBTiles 失败（zoom={zoom}，{count} 个瓦片未落盘）: {error}"
        )

    def writer_failed(self) -> bool:
        """写线程是否已异常退出（用于让入队方及时放弃，避免永久阻塞）。"""
        thread = getattr(self, "mbtiles_writer_thread", None)
        return thread is not None and not thread.is_alive()

    def stalled_for(self) -> float:
        """距离上一次成功写盘过去了多少秒（>0 表示可能卡住）。"""
        return max(0.0, time.time() - self._last_write_activity)

    def _write_batch(self, zoom: int, tiles: list, max_retries: int = 5) -> bool:
        """
        把一批瓦片写入对应 zoom 的 MBTiles 并提交。

        历史缺陷：遇到 "database is locked" 时**递归**调用自身重试，层级没有上限，
        最终抛 RecursionError；而且异常发生在 except 处理函数里，外层再也接不住，
        会把整个写线程打死。这里改为有界循环重试，并把失败记录下来。

        Returns:
            True 已提交；False 该批瓦片未落盘（已计入 write_failures）。
        """
        if not tiles:
            return True
        rows = [(zoom, x, row, data) for x, row, data, _y in tiles]
        delay = 0.2

        for attempt in range(max_retries):
            try:
                if self.enable_sharding:
                    conn = self._get_mbtiles_connection(zoom)
                else:
                    conn = self.mbtiles_conn
                if conn is None:
                    self._record_write_failure(zoom, len(rows), "MBTiles 连接不可用")
                    return False
                conn.executemany(
                    'INSERT OR REPLACE INTO tiles '
                    '(zoom_level, tile_column, tile_row, tile_data) VALUES (?, ?, ?, ?)',
                    rows,
                )
                conn.commit()
                self._last_write_activity = time.time()
                return True
            except sqlite3.OperationalError as e:
                if 'locked' in str(e).lower() and attempt < max_retries - 1:
                    logger.warning(
                        f"MBTiles数据库被锁定，重试 ({attempt + 1}/{max_retries})..."
                    )
                    time.sleep(delay)
                    delay *= 1.5
                    continue
                self._record_write_failure(zoom, len(rows), e)
                return False
            except Exception as e:  # noqa: BLE001
                self._record_write_failure(zoom, len(rows), e)
                return False
        return False

    def _get_mbtiles_connection(self, zoom: int) -> sqlite3.Connection:
        """分库模式下按 zoom 获取连接（仅写线程调用）。"""
        thread_id = threading.get_ident()
        pool_key = (thread_id, zoom)
        mbtiles_path = Path(str(self.output_path).replace("{z}", str(zoom)))
        self.mbtiles_paths[zoom] = mbtiles_path
        ensure_directory(mbtiles_path.parent)

        conn = self.connection_pool.get_connection(pool_key, mbtiles_path)
        if mbtiles_path not in self._initialized_paths:
            self._ensure_schema(conn, zoom=zoom)
            self._initialized_paths.add(mbtiles_path)
        return conn

    # ------------------------------------------------------------------ #
    # 断点续传校验
    # ------------------------------------------------------------------ #
    def _mbtiles_path_for_zoom(self, zoom: int) -> Path:
        """得到某个 zoom 对应的 MBTiles 文件路径（兼容分库模式）。"""
        if self.enable_sharding:
            return Path(str(self.output_path).replace("{z}", str(zoom)))
        return Path(self.output_path)

    def _get_verify_connection(self, path: Path):
        """
        获取（惰性创建、按线程缓存）用于存在性校验的连接。

        库文件不存在时返回 None。连接按线程隔离：生产者线程与各工作线程
        会并发调用 :meth:`has_tile`，共用一条 sqlite 连接并不安全。
        """
        path = Path(path)
        conns = getattr(self._verify_local, "conns", None)
        if conns is None:
            conns = {}
            self._verify_local.conns = conns

        conn = conns.get(path)
        if conn is not None:
            return conn
        if not path.exists():
            return None
        try:
            conn = sqlite3.connect(str(path), check_same_thread=False)
            conn.execute('PRAGMA busy_timeout=30000;')
        except sqlite3.Error as e:
            logger.warning(f"打开 MBTiles 校验连接失败 {path}: {e}")
            return None
        conns[path] = conn
        with self._verify_lock:
            self._verify_conns.append(conn)
        return conn

    def has_tile(self, zoom: int, x: int, y: int) -> bool:
        """
        校验某个瓦片是否真的已存在于 MBTiles 中。

        断点续传时不能只信 ``progress.db``：库文件被删掉或某些行丢失时，
        进度库会声称"已完成"，从而永远不再下载。这里以 tiles 表为准。

        Args:
            y: 任务坐标（始终是 XYZ 行号）；tiles 表里存的是 TMS 行号，这里做翻转。
        """
        if not self.is_mbtiles:
            return False
        row = (2 ** zoom) - 1 - y
        conn = self._get_verify_connection(self._mbtiles_path_for_zoom(zoom))
        if conn is None:
            return False
        try:
            found = conn.execute(
                'SELECT 1 FROM tiles '
                'WHERE zoom_level = ? AND tile_column = ? AND tile_row = ? LIMIT 1',
                (zoom, x, row),
            ).fetchone()
            return found is not None
        except sqlite3.Error as e:
            logger.warning(f"校验 MBTiles 瓦片存在性失败: {e}")
            return False

    def _close_verify_connections(self):
        with self._verify_lock:
            conns = list(self._verify_conns)
            self._verify_conns.clear()
        for conn in conns:
            self._safe_close(conn)

    # ------------------------------------------------------------------ #
    # 收尾
    # ------------------------------------------------------------------ #
    @staticmethod
    def _drain_timeout():
        """落盘等待超时（秒）；配置为 0 或非法值时表示无限等待。"""
        value = config_manager.get("download.mbtiles_drain_timeout", 0) or 0
        try:
            value = float(value)
        except (TypeError, ValueError):
            value = 0
        return value if value > 0 else None

    def wait_drained(self, timeout=None) -> bool:
        """
        等待写队列被完全消费并落盘。

        Args:
            timeout: 最长等待秒数；None 表示只要写线程还活着就无限等待。

        Returns:
            True 已全部落盘；False 写线程异常退出或超时（此时仍有数据未落盘）。
        """
        if not self.is_mbtiles:
            return True
        deadline = None if not timeout else time.time() + float(timeout)
        while self.mbtiles_write_queue.unfinished_tasks > 0:
            writer = getattr(self, "mbtiles_writer_thread", None)
            if writer is not None and not writer.is_alive():
                logger.error(
                    "MBTiles 写入线程已退出，但仍有 "
                    f"{self.mbtiles_write_queue.unfinished_tasks} 个瓦片未落盘"
                )
                return False
            if deadline is not None and time.time() > deadline:
                logger.error(
                    f"等待 MBTiles 落盘超时（仍有 "
                    f"{self.mbtiles_write_queue.unfinished_tasks} 个瓦片未写入）"
                )
                return False
            time.sleep(0.05)
        return True

    def _commit_all(self) -> bool:
        """提交所有已打开连接上的事务。"""
        try:
            if self.enable_sharding:
                for conn in list(self.connection_pool.connections.values()):
                    conn.commit()
            elif self.mbtiles_conn:
                self.mbtiles_conn.commit()
            return True
        except Exception as e:  # noqa: BLE001
            logger.error(f"提交 MBTiles 事务失败: {e}")
            return False

    def finalize(self) -> bool:
        """
        等待所有瓦片落盘并提交事务。

        Returns:
            True 全部落盘且无写入失败；False 表示有瓦片未落盘（超时/写线程退出/
            写入失败），调用方应据此告警而不是当作成功。
        """
        drained = self.wait_drained(self._drain_timeout())
        committed = self._commit_all()
        failures = self.write_failures
        if failures:
            logger.error(f"本次下载共有 {failures} 批 MBTiles 写入失败，数据不完整")
        return drained and committed and failures == 0

    def close(self):
        """关闭写入线程与数据库连接（幂等）。

        只有在写队列确认排空后才关闭连接。否则宁可保留连接、让写线程继续
        （进程退出前还有机会落盘），也不要在写线程仍写入时关库导致数据丢失。
        """
        if self._closed:
            return
        self._closed = True

        drained = True
        if self.is_mbtiles:
            # 先排空（不把写入失败算作"排空失败"，否则连接永远不会关）
            drained = self.wait_drained(self._drain_timeout())
            self._commit_all()
            if self.write_failures:
                logger.error(
                    f"MBTiles 有 {self.write_failures} 批写入失败，相关瓦片未落盘"
                )
            if drained:
                self._stop_mbtiles_writer()
            else:
                logger.error(
                    "MBTiles 收尾未完成，保留数据库连接与写线程，避免丢失未落盘数据"
                )

        if drained:
            try:
                self.connection_pool.close_all_connections()
            except Exception as e:  # noqa: BLE001
                logger.error(f"关闭 MBTiles 连接池失败: {e}")

            self._safe_close(self.mbtiles_conn)
            self.mbtiles_conn = None

        self._close_verify_connections()

    @staticmethod
    def _safe_close(conn):
        if conn is None:
            return
        try:
            conn.close()
        except Exception:
            pass
