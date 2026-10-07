# src/downloader/base.py

import time
import threading
from pathlib import Path
from queue import Queue, Empty, Full
from typing import List, Tuple, Dict, Optional, Callable

from loguru import logger

from ..providers import ProviderManager
from ..config import config_manager
from .utils import ensure_directory
from .performance import PerformanceMonitor
from .progress_handler import ProgressHandler
from .worker import WorkerManager
from .signal_handler import SignalHandler


class TileDownloader:
    """
    核心下载器：负责接收 (x, y, z) 任务，并发下载。

    任务来源有两种：
    * 调用 :meth:`add_task` / :meth:`add_tasks` 预置任务（适合少量任务）；
    * 调用 :meth:`add_tasks_for_bbox` 注册"按需生成"的瓦片源，
      :meth:`start` 时由生产者线程边生成边入队（适合大数据量，内存占用恒定）。
    """

    def __init__(
        self,
        provider_name: str,
        output_dir: str = "tiles",
        max_threads: int = 8,
        retries: int = 3,
        delay: float = 0.05,
        timeout: int = 10,
        is_tms: bool = False,
        progress_callback: Callable = None,
        enable_resume: bool = True,
        tile_format: str = None,
        save_format: str = "directory",
        scheme: str = "xyz",
        enable_performance_monitor: bool = False,
    ):
        """
        初始化下载器

        Args:
            provider_name: 瓦片提供商名称
            output_dir: 输出目录或 .mbtiles 文件路径
            max_threads: 最大线程数
            retries: 每个瓦片的重试次数
            delay: 重试基础延迟
            timeout: 网络超时
            is_tms: 是否使用 TMS 坐标系
            progress_callback: 进度回调 (processed, total, total_bytes)
            enable_resume: 是否启用断点续传
            tile_format: 覆盖 provider 的瓦片格式
            save_format: "directory" 或 "mbtiles"
            scheme: MBTiles scheme，默认 xyz
            enable_performance_monitor: 是否启用性能监控
        """
        # 基本参数
        self.provider_name = provider_name
        self.output_dir = output_dir
        self.max_threads = max_threads
        self.retries = retries
        self.delay = delay
        self.timeout = timeout
        self.is_tms = is_tms
        self.progress_callback = progress_callback
        self.enable_resume = enable_resume
        self.tile_format = tile_format
        self.save_format = save_format
        self.scheme = scheme

        # 状态计数器（加锁保证多线程安全）
        self._stats_lock = threading.Lock()
        self.downloaded_count = 0
        self.failed_count = 0
        self.skipped_count = 0
        self.total_tasks = 0
        self.total_bytes = 0

        # 事件与队列
        self.stop_event = threading.Event()
        self.pause_event = threading.Event()
        self.pause_event.set()  # 初始为非暂停
        self.all_tasks_added = threading.Event()

        queue_size = int(config_manager.get("download.task_queue_size", 20000))
        self.task_queue = Queue(maxsize=queue_size)
        self.mbtiles_write_queue = Queue(
            maxsize=int(config_manager.get("download.mbtiles_write_queue_size", 20000))
        )

        # 由生产者线程填充的任务源（可为 None，表示任务已预置）
        self._task_source = None
        self._producer_thread = None
        self._closed = False

        # 性能监控
        self.enable_performance_monitor = enable_performance_monitor
        self.performance_monitor = PerformanceMonitor() if enable_performance_monitor else None

        # 初始化提供商
        self.provider = ProviderManager.get_provider(provider_name)
        if tile_format:
            self.provider.extension = tile_format
        logger.info(f"成功初始化提供商: {provider_name}")

        # 输出路径
        self.output_path = Path(output_dir)
        if save_format == "mbtiles":
            ensure_directory(self.output_path.parent)
            self.is_mbtiles = True
            self.enable_sharding = "{z}" in str(output_dir)
            # MBTilesHandler 复用下载器的写队列，保证生产者与消费者使用同一队列
            from .mbtiles_handler import MBTilesHandler
            self.mbtiles_handler = MBTilesHandler(self)
        else:
            self.is_mbtiles = False
            self.enable_sharding = False
            ensure_directory(self.output_path)
            self.mbtiles_handler = None

        # 进度管理 / 信号处理
        self.progress_manager = ProgressHandler(self)
        self.signal_handler = SignalHandler(self)

    # ------------------------------------------------------------------ #
    # 任务入队
    # ------------------------------------------------------------------ #
    def add_task(self, x: int, y: int, z: int):
        """添加单个任务（启用断点续传时会跳过已处理瓦片）。"""
        if self.enable_resume and self.progress_manager.is_processed(x, y, z):
            return
        self.task_queue.put((x, y, z))
        self.total_tasks += 1

    def add_tasks(self, tiles: List[Tuple[int, int, int]]):
        """批量添加任务。"""
        tiles_to_add = []
        for tile in tiles:
            if self.enable_resume and self.progress_manager.is_processed(*tile):
                with self._stats_lock:
                    self.skipped_count += 1
            else:
                tiles_to_add.append(tile)
        for tile in tiles_to_add:
            self.task_queue.put(tile)
        self.total_tasks += len(tiles_to_add)
        logger.info(f"批量添加 {len(tiles_to_add)} 个任务到队列")

    def add_tasks_for_bbox(
        self,
        west: float,
        south: float,
        east: float,
        north: float,
        min_zoom: int,
        max_zoom: int,
    ):
        """
        根据经纬度范围注册瓦片源。

        与旧实现不同，这里不会一次性生成所有瓦片坐标，而是：
        1. 用 O(1) 的公式计算总任务数；
        2. 保存一个生成器函数，由 start() 的生产者线程边生成边入队，
           配合有界任务队列实现恒定内存占用。
        """
        from ..tile_math import TileMath

        logger.info(
            f"计算 bbox 瓦片: west={west}, south={south}, east={east}, north={north}, "
            f"min_zoom={min_zoom}, max_zoom={max_zoom}, is_tms={self.is_tms}"
        )

        total_tiles = 0
        for zoom in range(min_zoom, max_zoom + 1):
            total_tiles += TileMath.count_tiles_in_bbox(
                west, south, east, north, zoom, is_tms=self.is_tms
            )
        self.total_tasks = total_tiles
        logger.info(f"预计瓦片总数: {total_tiles}")

        def source():
            for zoom in range(min_zoom, max_zoom + 1):
                min_x, min_y, max_x, max_y = TileMath.bbox_tile_bounds(
                    west, south, east, north, zoom, is_tms=self.is_tms
                )
                if max_x < min_x or max_y < min_y:
                    continue
                # 该 zoom 内按 (x, y) 升序生成候选瓦片
                candidates = (
                    (x, y)
                    for x in range(min_x, max_x + 1)
                    for y in range(min_y, max_y + 1)
                )
                if self.enable_resume:
                    # 与进度库中按 (x, y) 升序的分页数据做归并跳过
                    existing = self.progress_manager.iter_processed_range(
                        zoom, min_x, max_x, min_y, max_y
                    )
                    yield from self._merge_and_skip(candidates, existing, zoom)
                else:
                    for x, y in candidates:
                        if self.stop_event.is_set():
                            return
                        yield (x, y, zoom)

        self._task_source = source

    def _merge_and_skip(self, candidates, existing, zoom):
        """
        把"待下载候选流"与"已处理流"做双指针归并，跳过已处理瓦片。

        两路都按 (x, y) 升序，因此内存只需要各一个当前元素（O(1)）。
        """
        current = next(existing, None)
        for tile in candidates:
            if self.stop_event.is_set():
                return
            while current is not None and current < tile:
                current = next(existing, None)
            if current == tile:
                with self._stats_lock:
                    self.skipped_count += 1
                current = next(existing, None)
                continue
            yield (tile[0], tile[1], zoom)

    def _run_task_source(self):
        """生产者线程：把瓦片源中的数据流入有界任务队列。"""
        try:
            for tile in self._task_source():
                if self.stop_event.is_set():
                    break
                self._enqueue_with_backpressure(tile)
        except Exception as e:  # noqa: BLE001
            logger.error(f"添加任务失败: {e}")
        finally:
            self.all_tasks_added.set()
            logger.info("任务添加完成")

    def _enqueue_with_backpressure(self, item) -> bool:
        """阻塞入队，队列满时等待消费者消费，同时响应停止信号。"""
        while not self.stop_event.is_set():
            try:
                self.task_queue.put(item, timeout=0.2)
                return True
            except Full:
                continue
        return False

    def _enqueue_mbtiles(self, item) -> bool:
        """把待写入的瓦片送入 MBTiles 写队列（背压 + 停止响应）。"""
        while not self.stop_event.is_set():
            try:
                self.mbtiles_write_queue.put(item, timeout=0.2)
                return True
            except Full:
                continue
        return False

    def pending_mbtiles_writes(self) -> int:
        """当前尚未写入磁盘的 MBTiles 瓦片数量。"""
        if not self.is_mbtiles:
            return 0
        return self.mbtiles_write_queue.qsize()

    # ------------------------------------------------------------------ #
    # 生命周期
    # ------------------------------------------------------------------ #
    def start(self):
        """启动下载：拉起工作线程 + 生产者线程，阻塞直到全部完成。"""
        logger.info(f"开始下载，线程数={self.max_threads}，provider={self.provider.name}")
        self.all_tasks_added.clear()

        worker_manager = WorkerManager(self)
        worker_manager.start_workers()

        producer = None
        if self._task_source is not None:
            producer = threading.Thread(
                target=self._run_task_source, name="TaskProducer", daemon=True
            )
            self._producer_thread = producer
            producer.start()
        else:
            # 任务已预置，标记生产结束，工作线程排空后即可退出
            self.all_tasks_added.set()

        try:
            worker_manager.wait_for_completion()
            if producer is not None:
                producer.join(timeout=5)

            if self.enable_resume:
                self._save_progress()
            if self.is_mbtiles:
                self._finalize_download()
            logger.info(f"下载结束: {self.get_statistics()}")
        except Exception as e:  # noqa: BLE001
            logger.error(f"下载过程中发生异常: {e}")
            if self.enable_resume:
                try:
                    self._save_progress()
                except Exception as save_error:
                    logger.error(f"保存进度失败: {save_error}")
            self.stop_event.set()
            raise
        finally:
            self._cleanup()

    def _cleanup(self):
        """幂等释放资源。"""
        if self._closed:
            return
        self._closed = True
        if self.is_mbtiles and self.mbtiles_handler:
            try:
                self.mbtiles_handler.close()
            except Exception as e:  # noqa: BLE001
                logger.error(f"关闭 MBTiles 处理器失败: {e}")
        try:
            self.progress_manager.close()
        except Exception as e:  # noqa: BLE001
            logger.error(f"关闭进度管理器失败: {e}")

    def pause(self):
        """暂停下载：清空 pause_event 并保存进度。"""
        logger.info("暂停下载任务")
        self.pause_event.clear()
        if self.enable_resume:
            self._save_progress()

    def resume(self):
        """恢复下载。"""
        logger.info("恢复下载任务")
        self.pause_event.set()

    def is_paused(self) -> bool:
        """是否处于暂停状态。"""
        return not self.pause_event.is_set()

    def stop(self):
        """请求停止（不阻塞）。"""
        logger.info("停止下载任务")
        self.stop_event.set()

    def cancel(self):
        """取消下载：停止线程、保存进度并释放资源。"""
        logger.info("取消下载任务")
        if self.enable_resume:
            self._save_progress()
        self.stop_event.set()
        self.pause_event.set()  # 释放仍在等待恢复的线程
        self._drain_task_queue()
        self._cleanup()
        return self.get_statistics()

    def _drain_task_queue(self):
        """清空尚未处理的任务队列。"""
        while True:
            try:
                self.task_queue.get_nowait()
                try:
                    self.task_queue.task_done()
                except ValueError:
                    pass
            except Empty:
                break

    # ------------------------------------------------------------------ #
    # 统计与进度
    # ------------------------------------------------------------------ #
    def _mark_tile_processed(self, x: int, y: int, z: int, status: str):
        """标记瓦片处理结果并更新计数。"""
        self.progress_manager.mark_tile_processed(x, y, z, status)
        with self._stats_lock:
            if status == 'success':
                self.downloaded_count += 1
            elif status == 'failed':
                self.failed_count += 1
            elif status == 'skipped':
                self.skipped_count += 1

    def _add_bytes(self, count: int):
        """累加已下载字节数（线程安全）。"""
        with self._stats_lock:
            self.total_bytes += count

    def _save_progress(self):
        """保存进度到数据库。"""
        if not self.enable_resume:
            return
        try:
            self.progress_manager.save_progress()
            logger.debug("进度保存成功")
        except Exception as e:  # noqa: BLE001
            logger.error(f"保存进度失败: {e}")

    def _update_progress(self):
        """触发进度回调。"""
        if self.progress_callback and self.total_tasks > 0:
            with self._stats_lock:
                processed = self.downloaded_count + self.failed_count + self.skipped_count
                total_bytes = self.total_bytes
            self.progress_callback(processed, self.total_tasks, total_bytes)

    def get_statistics(self) -> Dict[str, int]:
        """获取下载统计信息。"""
        with self._stats_lock:
            processed = self.downloaded_count + self.failed_count + self.skipped_count
            return {
                "downloaded": self.downloaded_count,
                "failed": self.failed_count,
                "skipped": self.skipped_count,
                "total": self.total_tasks,
                "remaining": max(0, self.total_tasks - processed),
            }

    def get_performance_statistics(self) -> Optional[Dict]:
        """获取性能统计信息（未启用时返回 None）。"""
        if self.performance_monitor:
            stats = self.performance_monitor.get_statistics()
            self.performance_monitor.log_statistics()
            return stats
        return None

    def log_performance_statistics(self):
        """记录性能统计信息。"""
        if self.performance_monitor:
            self.performance_monitor.log_statistics()

    def _finalize_download(self):
        """完成下载，确保所有 MBTiles 事务都已提交。"""
        if self.is_mbtiles and self.mbtiles_handler:
            try:
                self.mbtiles_handler.finalize()
            except Exception as e:  # noqa: BLE001
                logger.error(f"完成下载失败: {e}")
