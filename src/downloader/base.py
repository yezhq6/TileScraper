# src/downloader/base.py

import copy
import os
import time
import threading
from pathlib import Path
from queue import Queue, Empty, Full
from typing import List, Tuple, Dict, Optional, Callable

from loguru import logger

from ..providers import ProviderManager
from ..config import config_manager
from .utils import ensure_directory, resolve_thread_count
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
        max_threads: int = None,
        retries: int = None,
        delay: float = 0.05,
        timeout: int = None,
        is_tms: bool = False,
        progress_callback: Callable = None,
        enable_resume: bool = True,
        tile_format: str = None,
        save_format: str = "directory",
        scheme: str = "xyz",
        enable_performance_monitor: bool = False,
        proxy: str = None,
        provider=None,
    ):
        """
        初始化下载器

        Args:
            provider_name: 瓦片提供商名称
            output_dir: 输出目录或 .mbtiles 文件路径
            max_threads: 最大线程数（None 时取 download.threads；"auto"/0 表示自动）
            retries: 每个瓦片的重试次数（None 时取 download.max_retries）
            delay: 重试基础延迟
            timeout: 网络超时秒数（None 时取 download.timeout）
            is_tms: 是否使用 TMS 坐标系
            progress_callback: 进度回调 (processed, total, total_bytes)
            enable_resume: 是否启用断点续传
            tile_format: 覆盖 provider 的瓦片格式
            save_format: "directory" 或 "mbtiles"
            scheme: MBTiles scheme，默认 xyz
            enable_performance_monitor: 是否启用性能监控
            proxy: 代理设置；None 取 download.proxy（""=直连，"env"=环境变量，其余=代理地址）
            provider: 直接指定 provider 实例（Web 并发场景下避免全局注册表串号）；
                None 时按 provider_name 从 ProviderManager 取
        """
        # 基本参数
        self.provider_name = provider_name
        self.output_dir = output_dir
        self.max_threads = max_threads
        self.retries = max(1, int(
            retries if retries is not None
            else config_manager.get("download.max_retries", 3)
        ))
        self.delay = delay
        self.timeout = int(
            timeout if timeout is not None
            else config_manager.get("download.timeout", 30)
        )
        self.proxy = proxy if proxy is not None else (
            config_manager.get("download.proxy", "") or ""
        )
        self.is_tms = is_tms
        self.progress_callback = progress_callback
        self.enable_resume = enable_resume
        self.tile_format = tile_format
        self.save_format = save_format
        self.scheme = scheme

        # 数据正确性相关开关
        # atomic_write：临时文件 + os.replace，避免写盘中途被杀留下半截文件
        self.atomic_write = bool(config_manager.get("download.atomic_write", True))
        # atomic_fsync：replace 前 fsync（更抗断电，但每个瓦片多一次同步写，默认关）
        self.atomic_fsync = bool(config_manager.get("download.atomic_fsync", False))
        # verify_artifacts：跳过前确认产物真的存在，防止"进度库说有、磁盘上没有"
        self.verify_artifacts = bool(config_manager.get("download.verify_artifacts", True))
        # validate_image：用文件头确认返回的确实是图片（挡住 200 错误页）
        self.validate_image = bool(config_manager.get("download.validate_image", True))
        # reject_blank_tiles：过滤纯色空白瓦片（需要 Pillow）
        self.reject_blank_tiles = bool(
            config_manager.get("download.reject_blank_tiles", False)
        )
        # adaptive_concurrency：遇 429/503 时全局限流冷却并动态降低并发
        self.adaptive_concurrency = bool(
            config_manager.get("download.adaptive_concurrency", True)
        )
        # mbtiles_ack：MBTiles 瓦片只在写线程 commit 成功后才标记为已完成，
        # 保证 progress.db 永不领先于实际产物（避免强杀后丢数据）
        self.mbtiles_ack = bool(config_manager.get("download.mbtiles_ack", True))
        # 入队去重：同一坐标在"已入队未取走"期间只保留一份（内存被队列容量封顶）
        self._pending_lock = threading.Lock()
        self._pending_tiles = set()
        # 残留临时文件清理：强杀会留下 .<瓦片名>.<随机>.part，重下该瓦片时顺手清掉
        self.cleanup_stale_parts = bool(
            config_manager.get("download.cleanup_stale_parts", True)
        )
        try:
            stale_hours = float(config_manager.get("download.stale_part_age_hours", 24) or 24)
        except (TypeError, ValueError):
            stale_hours = 24.0
        self.stale_part_age_seconds = max(0.0, stale_hours) * 3600.0

        # 状态计数器（加锁保证多线程安全）
        self._stats_lock = threading.Lock()
        self.downloaded_count = 0
        self.failed_count = 0
        self.skipped_count = 0
        self.redownload_count = 0
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
        # 写队列满且写线程长时间无进展时，放弃入队而不是永久阻塞
        try:
            self.mbtiles_stall_timeout = float(
                config_manager.get("download.mbtiles_stall_timeout", 300) or 300
            )
        except (TypeError, ValueError):
            self.mbtiles_stall_timeout = 300.0

        # 由生产者线程填充的任务源（可为 None，表示任务已预置）
        self._task_source = None
        self._producer_thread = None
        self._closed = False
        self._started = False

        # 自适应并发闸门（start() 时按实际线程数创建）
        self.limiter = None

        # 性能监控
        self.enable_performance_monitor = enable_performance_monitor
        self.performance_monitor = PerformanceMonitor() if enable_performance_monitor else None

        # 初始化提供商
        # 每个下载器持有 provider 的浅拷贝：避免设置 tile_format / is_tms 时
        # 改到 ProviderManager 里的全局单例（会永久影响后续其它下载任务）。
        base_provider = (
            provider if provider is not None
            else ProviderManager.get_provider(provider_name)
        )
        self.provider = copy.copy(base_provider)
        # is_tms 只影响"向服务端请求哪一行"（URL 里的 {y} 是否翻转）；
        # 任务坐标与本地落盘/DB 行始终按 XYZ→TMS 的固定规则处理
        self.provider.is_tms = bool(is_tms)
        if tile_format:
            self.provider.set_tile_format(tile_format)
        logger.info(f"成功初始化提供商: {provider_name}")

        # 输出路径
        self.output_path = Path(output_dir)
        # 输出目录锁：两个进程同时写同一份 progress.db / .mbtiles 会互相抢锁
        self._acquire_output_lock(save_format == "mbtiles")

        try:
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
        except BaseException:
            # 构造中途失败也要把锁还回去，否则该输出目录会被本进程占死
            self._release_output_lock()
            raise

    # ------------------------------------------------------------------ #
    # 输出目录锁
    # ------------------------------------------------------------------ #
    def _acquire_output_lock(self, is_mbtiles: bool):
        """获取输出目录锁；拿不到时抛 OutputLockedError。"""
        self.output_lock = None
        if not bool(config_manager.get("download.output_lock", True)):
            return
        try:
            timeout = float(config_manager.get("download.output_lock_timeout", 0) or 0)
        except (TypeError, ValueError):
            timeout = 0.0

        from .output_lock import OutputLock

        lock = OutputLock(self.output_path, is_mbtiles=is_mbtiles, timeout=timeout)
        if not lock.acquire():
            from ..exceptions import OutputLockedError

            holder = f"（占用进程 PID {lock.holder_pid}）" if lock.holder_pid else ""
            raise OutputLockedError(
                f"输出路径已被另一个下载任务占用{holder}: {self.output_path}；"
                f"请等待它结束，或改用其它输出路径。"
            )
        self.output_lock = lock
        logger.debug(f"已获取输出目录锁: {lock.path}")

    def _release_output_lock(self):
        lock = getattr(self, "output_lock", None)
        if lock is None:
            return
        self.output_lock = None
        try:
            lock.release()
        except Exception as e:  # noqa: BLE001
            logger.warning(f"释放输出目录锁失败: {e}")

    # ------------------------------------------------------------------ #
    # 任务入队
    # ------------------------------------------------------------------ #
    def _resume_decision(self, x: int, y: int, z: int) -> str:
        """
        决定一个瓦片是"跳过 / 下载 / 重下"。

        * ``skip``——进度库标为已完成，且产物确实存在；
        * ``redownload``——进度库标为已完成，但产物不存在（需重新下载）；
        * ``download``——进度库没有记录（或未启用续传）。
        """
        if not (self.enable_resume and self.progress_manager.is_processed(x, y, z)):
            return 'download'
        return 'skip' if self._artifact_is_present(x, y, z) else 'redownload'

    def add_task(self, x: int, y: int, z: int):
        """添加单个任务（启用断点续传时会跳过已处理瓦片，重复入队会被去重）。"""
        decision = self._resume_decision(x, y, z)
        if decision == 'skip':
            with self._stats_lock:
                self.skipped_count += 1
            self.total_tasks += 1
            return
        if not self._put_now_or_raise((x, y, z)):
            # 该坐标已在队列里（未处理完），不再重复计数
            return
        if decision == 'redownload':
            with self._stats_lock:
                self.redownload_count += 1
        self.total_tasks += 1

    def add_tasks(self, tiles: List[Tuple[int, int, int]]):
        """
        批量预置任务（适合少量任务）。

        预置任务发生在 :meth:`start` 之前，此时没有任何消费者，因此不能用
        阻塞式入队（队列一旦装满就会永久卡住）。这里的行为是：

        * 本次调用内重复的坐标会被去掉；
        * 与"已在队列中未处理"的坐标重复也会被去重（见 ``_pending_tiles``）；
        * 装不下队列容量时自动切换成"流式生产"，由 ``start()`` 的生产者线程
          按背压逐条入队（内存占用与队列容量同阶）。
        """
        # 本次调用内去重（保持稳定顺序，避免同一坐标被计数两次）
        tiles = list(dict.fromkeys(tiles))

        tiles_to_add = []
        for tile in tiles:
            decision = self._resume_decision(*tile)
            if decision == 'skip':
                with self._stats_lock:
                    self.skipped_count += 1
            else:
                if decision == 'redownload':
                    with self._stats_lock:
                        self.redownload_count += 1
                tiles_to_add.append(tile)
        # 总数要把"已跳过"的也算进去，否则 processed 会超过 total（进度出现 200%）
        self.total_tasks += len(tiles)

        if not tiles_to_add:
            return
        # 队列剩余容量按当前 qsize 计算，避免"加上已有任务后溢出"
        capacity_left = self.task_queue.maxsize - self.task_queue.qsize()
        if len(tiles_to_add) > capacity_left:
            logger.info(
                f"预置任务 {len(tiles_to_add)} 个超过队列剩余容量 "
                f"{capacity_left}，改用流式生产模式"
            )
            self._task_source = lambda: self._iter_tiles(tiles_to_add)
            return
        added = 0
        for tile in tiles_to_add:
            if self._put_now_or_raise(tile):
                added += 1
            else:
                # 与已入队瓦片重复：它此前已被计入 total，这里回退本次计数
                self.total_tasks -= 1
        logger.info(f"批量添加 {added} 个任务到队列")

    def add_failed_tasks(self) -> int:
        """
        只把进度库里 ``status='failed'`` 的瓦片重新入队（不重扫整个 bbox）。

        适合"跑完一次、只补失败瓦片"的场景。失败清单通常远小于全量，
        这里会先读进内存；入队仍走 :meth:`add_tasks`（含去重与背压/流式切换）。

        Returns:
            实际加入的任务数。
        """
        if not self.enable_resume:
            raise ValueError("断点续传已关闭，无法读取失败瓦片清单")
        tiles = list(self.progress_manager.iter_failed())
        if not tiles:
            logger.info("进度库中没有失败瓦片")
            return 0
        self.add_tasks(tiles)
        logger.info(f"从进度库补入 {len(tiles)} 个失败瓦片")
        return len(tiles)

    def _register_pending(self, tile) -> bool:
        """把坐标登记为"已入队待处理"；已存在返回 False（用于去重）。"""
        with self._pending_lock:
            if tile in self._pending_tiles:
                return False
            self._pending_tiles.add(tile)
            return True

    def _unregister_pending(self, tile):
        with self._pending_lock:
            self._pending_tiles.discard(tile)

    def forget_pending(self, tile):
        """工作线程取走任务后调用；不这样做集合会无界增长。"""
        self._unregister_pending(tile)

    def _put_now_or_raise(self, tile) -> bool:
        """
        非阻塞入队（带去重）。

        Returns:
            True 已入队；False 表示该坐标已在队列中。

        Raises:
            ValueError: 队列已满（给出可操作的提示，而不是永久阻塞调用方）。
        """
        if not self._register_pending(tile):
            return False
        try:
            self.task_queue.put_nowait(tile)
        except Full:
            self._unregister_pending(tile)
            raise ValueError(
                f"任务队列已满（download.task_queue_size={self.task_queue.maxsize}）："
                f"预置任务数量不能超过队列容量。大批量任务请改用 "
                f"add_tasks_for_bbox()（流式生产），或调大 config.yaml 里的 "
                f"download.task_queue_size。"
            ) from None
        return True

    def _iter_tiles(self, tiles):
        """把预置的任务列表变成可中断的流，供生产者线程消费。"""
        for tile in tiles:
            if self.stop_event.is_set():
                return
            yield tile

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

    def _artifact_is_present(self, x: int, y: int, z: int) -> bool:
        """
        校验"进度库说已完成"的瓦片是否真的还在。

        断点续传若只信 ``progress.db``，一旦产物被删除（目录被清理、mbtiles 被
        删掉、磁盘事故），这些瓦片就永远不会再下载。这里以实际产物为准：
        目录模式查文件是否存在，MBTiles 模式查 tiles 表里有没有对应行。
        """
        if not self.verify_artifacts:
            return True
        if self.is_mbtiles:
            if not self.mbtiles_handler:
                return False
            return self.mbtiles_handler.has_tile(z, x, y)
        return self.provider.get_tile_path(x, y, z, self.output_dir).exists()

    def _merge_and_skip(self, candidates, existing, zoom):
        """
        把"待下载候选流"与"已处理流"做双指针归并，跳过已处理瓦片。

        两路都按 (x, y) 升序，因此内存只需要各一个当前元素（O(1)）。

        对进度库声称"已处理"的瓦片，还会校验产物是否真的存在；产物缺失时
        不跳过，而是重新下载（并计入 ``redownload_count``）。
        """
        current = next(existing, None)
        for tile in candidates:
            if self.stop_event.is_set():
                return
            while current is not None and current < tile:
                current = next(existing, None)
            if current == tile:
                current = next(existing, None)
                if not self._artifact_is_present(tile[0], tile[1], zoom):
                    with self._stats_lock:
                        self.redownload_count += 1
                    # 进度库有记录但产物不存在 → 必须重新下载
                    yield (tile[0], tile[1], zoom)
                    continue
                with self._stats_lock:
                    self.skipped_count += 1
                continue
            yield (tile[0], tile[1], zoom)

    def _run_task_source(self):
        """生产者线程：把瓦片源中的数据流入有界任务队列。"""
        try:
            for tile in self._task_source():
                if self.stop_event.is_set():
                    break
                if not self._register_pending(tile):
                    # 与队列中已有（或正被处理）的瓦片重复：
                    # 不重复下载，但要在统计上"消费"掉它，否则 remaining 永远不为 0
                    with self._stats_lock:
                        self.skipped_count += 1
                    continue
                if not self._enqueue_with_backpressure(tile):
                    self._unregister_pending(tile)
                    break
        except Exception as e:  # noqa: BLE001
            logger.error(f"添加任务失败: {e}")
        finally:
            self.all_tasks_added.set()
            logger.info("任务添加完成")

    def _enqueue_with_backpressure(self, item) -> bool:
        """
        阻塞入队，队列满时等待消费者消费，同时响应停止信号。

        注意：调用方需先通过 ``_register_pending`` 登记（这样才能把"已入队"
        状态与去重集合保持一致）。
        """
        while not self.stop_event.is_set():
            try:
                self.task_queue.put(item, timeout=0.2)
                return True
            except Full:
                continue
        return False

    def _enqueue_mbtiles(self, item) -> bool:
        """
        把待写入的瓦片送入 MBTiles 写队列（背压 + 停止响应）。

        旧实现只等待队列有空位：一旦写线程异常退出，队列会被填满，随后所有
        工作线程与生产者线程都会永久阻塞在 ``put`` 上，``start()`` 再也回不来。
        这里额外检查写线程存活与写入进展，异常时返回 False（瓦片按失败处理），
        让下载能正常收尾而不是挂死。
        """
        handler = self.mbtiles_handler
        while not self.stop_event.is_set():
            if handler is not None and handler.writer_failed():
                logger.error("MBTiles 写入线程已退出，放弃写入剩余瓦片")
                return False
            try:
                self.mbtiles_write_queue.put(item, timeout=0.2)
                return True
            except Full:
                # 队列满：如果写线程长时间没有任何成功写入，说明它卡住了
                if handler is not None and handler.stalled_for() > self.mbtiles_stall_timeout:
                    logger.error(
                        f"MBTiles 写入线程 {self.mbtiles_stall_timeout:.0f}s 无进展，"
                        "放弃写入剩余瓦片"
                    )
                    return False
                continue
        return False

    def pending_mbtiles_writes(self) -> int:
        """当前尚未写入磁盘的 MBTiles 瓦片数量。"""
        if not self.is_mbtiles:
            return 0
        return self.mbtiles_write_queue.qsize()

    def mbtiles_write_failures(self) -> int:
        """MBTiles 写入失败的批次数。"""
        if not self.is_mbtiles or not self.mbtiles_handler:
            return 0
        return self.mbtiles_handler.write_failures

    # ------------------------------------------------------------------ #
    # 生命周期
    # ------------------------------------------------------------------ #
    def _make_limiter(self, threads: int):
        """按实际线程数创建自适应并发闸门（关闭时返回 None）。"""
        if not self.adaptive_concurrency:
            return None
        from .rate_limiter import ConcurrencyLimiter

        try:
            cooldown_max = float(
                config_manager.get("download.rate_limit_cooldown_max", 60) or 60
            )
        except (TypeError, ValueError):
            cooldown_max = 60.0
        try:
            min_threads = int(
                config_manager.get("download.rate_limit_min_threads", 1) or 1
            )
        except (TypeError, ValueError):
            min_threads = 1
        return ConcurrencyLimiter(
            limit=threads, min_limit=min_threads, cooldown_max=cooldown_max
        )

    def start(self):
        """启动下载：拉起工作线程 + 生产者线程，阻塞直到全部完成。"""
        logger.info(f"开始下载，线程数={self.max_threads}，provider={self.provider.name}")
        self.all_tasks_added.clear()
        self._started = True
        # 必须在工作线程启动前建好闸门，工作线程会直接读取 d.limiter
        self.limiter = self._make_limiter(resolve_thread_count(self.max_threads))

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
            self._write_failed_manifest()
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
        # 锁最后释放：确保写线程/连接都已收尾
        self._release_output_lock()

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
        """
        取消下载：请求停止、保存进度并清空待处理队列。

        注意：资源的真正收尾交给 :meth:`start` 的正常退出路径（它通常运行在
        另一个线程里）。如果在工作者线程仍持有连接/写队列时提前 ``_cleanup()``，
        会出现"连接被关掉后线程还在写"的竞态。只有从未 ``start()`` 过的下载器
        才需要在这里立即收尾。
        """
        logger.info("取消下载任务")
        self.stop_event.set()
        self.pause_event.set()  # 释放仍在等待恢复的线程
        if self.enable_resume:
            self._save_progress()
        self._drain_task_queue()
        if not self._started:
            self._cleanup()
        return self.get_statistics()

    def close(self):
        """显式释放资源（幂等）；用于从未 start() 或需要立即收尾的场景。"""
        self.stop()
        self.pause_event.set()
        self._drain_task_queue()
        self._cleanup()

    def _drain_task_queue(self):
        """清空尚未处理的任务队列（同步维护去重集合）。"""
        while True:
            try:
                tile = self.task_queue.get_nowait()
                self._unregister_pending(tile)
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
                # 进度库有记录但产物缺失、因此被重新下载的瓦片数
                "redownloaded": self.redownload_count,
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

    # ------------------------------------------------------------------ #
    # 失败清单
    # ------------------------------------------------------------------ #
    def failed_manifest_path(self) -> Path:
        """失败瓦片清单文件路径（目录模式在输出目录，MBTiles 在同级）。"""
        if self.is_mbtiles:
            return Path(self.output_path).parent / f"{Path(self.output_path).stem}.failed.txt"
        return Path(self.output_dir) / "failed_tiles.txt"

    def _write_failed_manifest(self):
        """把失败瓦片写成 ``z x y`` 清单，便于事后只补这些瓦片。"""
        if not self.enable_resume:
            return
        if not bool(config_manager.get("download.write_failed_manifest", True)):
            return
        path = self.failed_manifest_path()
        if self.failed_count <= 0:
            # 本次没有失败：清掉上一次留下的陈旧清单
            try:
                if path.exists():
                    path.unlink()
            except OSError:
                pass
            return
        tmp = path.with_name(path.name + ".tmp")
        try:
            ensure_directory(path.parent)
            count = 0
            with open(tmp, "w", encoding="utf-8") as f:
                f.write("# z x y  (TileScraper 失败瓦片清单)\n")
                for x, y, z in self.progress_manager.iter_failed():
                    f.write(f"{z} {x} {y}\n")
                    count += 1
            os.replace(tmp, path)
            logger.warning(f"有 {count} 个瓦片下载失败，清单已写入 {path}")
        except OSError as e:
            logger.error(f"写入失败清单失败: {e}")
