# src/downloader/base.py

import time
import os
import signal
import sys
import threading
import queue
import sqlite3
from pathlib import Path
from queue import Queue, Empty
from typing import List, Tuple, Dict, Optional, Callable
from loguru import logger

from ..providers import ProviderManager, TileProvider
from ..exceptions import DownloadError, MBTilesError, ProgressError
from ..config import config_manager
from .utils import convert_path, ensure_directory
from .performance import PerformanceMonitor
from .mbtiles import MBTilesManager
from .progress import ProgressManager
from .worker import WorkerManager
from .transaction import TransactionManager
from .connection_pool import ConnectionPool
from ..utils.error_handler import handle_error, safe_execute


class TileDownloader:
    """
    核心下载器：负责接收 (x, y, z) 任务，并发下载
    """

    def __init__(
        self,
        provider_name: str,
        output_dir: str = "tiles",
        max_threads: int = 8,  # 增加默认线程数到8
        retries: int = 3,      # 减少重试次数到2
        delay: float = 0.05,   # 减少延迟到0.05秒
        timeout: int = 10,
        is_tms: bool = False,
        progress_callback: Callable = None,
        enable_resume: bool = True,  # 是否启用断点续传
        tile_format: str = None,  # 瓦片格式
        save_format: str = "directory",  # 保存格式：directory 或 mbtiles
        scheme: str = "xyz",  # MBTiles文件的scheme，默认xyz
        enable_performance_monitor: bool = False  # 是否启用性能监控
    ):
        """
        初始化下载器
        
        Args:
            provider_name: 瓦片提供商名称
            output_dir: 输出目录
            max_threads: 最大线程数
            retries: 重试次数
            delay: 重试延迟
            timeout: 超时时间
            is_tms: 是否使用TMS坐标系
            progress_callback: 进度回调函数
            enable_resume: 是否启用断点续传
            tile_format: 瓦片格式
            save_format: 保存格式
            scheme: MBTiles文件的scheme
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
        
        # 状态计数器
        self.downloaded_count = 0
        self.failed_count = 0
        self.skipped_count = 0
        self.total_tasks = 0
        self.total_bytes = 0
        
        # 事务管理
        self.transaction_counter = 0
        self.transaction_batch_size = 1000  # 每1000个瓦片提交一次事务
        
        # 事件和队列
        self.stop_event = threading.Event()
        self.pause_event = threading.Event()
        self.pause_event.set()  # 初始状态为非暂停
        self.task_queue = Queue()
        self.mbtiles_write_queue = Queue(maxsize=10000)  # 设置队列大小限制
        
        # 线程本地存储
        self.thread_local = threading.local()
        
        # MBTiles 处理器
        self.mbtiles_handler = None
        
        # 性能监控
        self.enable_performance_monitor = enable_performance_monitor
        self.performance_monitor = None
        if self.enable_performance_monitor:
            self.performance_monitor = PerformanceMonitor()
        
        # 初始化提供商
        try:
            self.provider = ProviderManager.get_provider(provider_name)
            # 如果指定了瓦片格式，覆盖提供商的默认格式
            if tile_format:
                self.provider.extension = tile_format
            logger.info(f"成功初始化提供商: {provider_name}")
        except Exception as e:
            logger.error(f"初始化提供商失败: {e}")
            raise
        
        # 处理输出路径
        self.output_path = Path(output_dir)
        if save_format == "mbtiles":
            # 确保路径存在
            ensure_directory(self.output_path.parent)
            self.is_mbtiles = True
            self.enable_sharding = "{z}" in str(output_dir)  # 检查是否使用分库
            
            # 初始化MBTiles管理器
            self.mbtiles_manager = MBTilesManager(str(output_dir), scheme)
            
            # 初始化 MBTiles 处理器
            from .mbtiles_handler import MBTilesHandler
            self.mbtiles_handler = MBTilesHandler(self)
        else:
            # 目录模式
            self.is_mbtiles = False
            self.enable_sharding = False
            ensure_directory(self.output_path)
        
        # 初始化进度管理器
        from .progress_handler import ProgressHandler
        self.progress_manager = ProgressHandler(self)
        
        # 初始化信号处理器
        from .signal_handler import SignalHandler
        self.signal_handler = SignalHandler(self)



    def add_task(self, x: int, y: int, zoom: int):
        """
        添加任务到队列，启用断点续传时跳过已处理的瓦片
        
        Args:
            x: 瓦片x坐标
            y: 瓦片y坐标
            zoom: 缩放级别
        """
        # 启用断点续传且瓦片已处理，则跳过
        if self.enable_resume and (x, y, zoom) in self.progress_manager.processed_tiles:
            logger.debug(f"[跳过已处理] 任务: z={zoom}, x={x}, y={y}")
            self.skipped_count += 1
            return
        
        self.task_queue.put((x, y, zoom))
        logger.debug(f"添加任务: z={zoom}, x={x}, y={y}")

    def add_tasks(self, tiles: List[Tuple[int, int, int]]):
        """
        批量添加任务到队列
        
        Args:
            tiles: 瓦片列表，每个元素是(x, y, z)元组
        """
        # 批量检查已处理的瓦片，减少数据库查询次数
        if self.enable_resume and self.progress_manager.progress_conn:
            # 构建批量查询
            tiles_to_add = []
            batch_size = 1000
            
            for i in range(0, len(tiles), batch_size):
                batch = tiles[i:i+batch_size]
                # 构建查询参数
                params = []
                placeholders = []
                for x, y, z in batch:
                    params.extend([x, y, z])
                    placeholders.append('(?, ?, ?)')
                
                # 批量查询已处理的瓦片
                query = f'SELECT x, y, z FROM processed_tiles WHERE (x, y, z) IN ({', '.join(placeholders)})'
                try:
                    cursor = self.progress_manager.progress_conn.execute(query, params)
                    processed = set((x, y, z) for x, y, z in cursor.fetchall())
                    
                    # 添加未处理的瓦片
                    for tile in batch:
                        if tile not in processed:
                            tiles_to_add.append(tile)
                        else:
                            self.skipped_count += 1
                except Exception as e:
                    logger.error(f"批量检查瓦片失败: {e}")
                    # 失败时回退到逐个检查
                    for x, y, z in batch:
                        self.add_task(x, y, z)
        else:
            # 回退到逐个添加
            tiles_to_add = tiles
        
        # 批量添加任务到队列
        for tile in tiles_to_add:
            self.task_queue.put(tile)
        
        logger.info(f"批量添加 {len(tiles_to_add)} 个任务到队列")

    def add_tasks_for_bbox(
        self,
        west: float,
        south: float,
        east: float,
        north: float,
        min_zoom: int,
        max_zoom: int,
        batch_size: int = 20000  # 增加默认批次大小到20000个
    ):
        """
        根据经纬度范围添加任务，支持多个缩放级别，流式处理避免内存占用过高
        优化版：提高添加任务的速度，减少内存使用
        
        Args:
            west: 西边界经度
            south: 南边界纬度
            east: 东边界经度
            north: 北边界纬度
            min_zoom: 最小缩放级别
            max_zoom: 最大缩放级别
            batch_size: 每批处理的任务数量，默认20000个
        """
        # 尝试导入 psutil，获取内存使用情况
        def get_memory_usage():
            """获取当前进程的内存使用情况"""
            try:
                import psutil
                import os
                process = psutil.Process(os.getpid())
                return process.memory_info().rss / 1024 / 1024  # 返回MB
            except ImportError:
                # 如果 psutil 不可用，返回默认值
                return 0.0
            except Exception as e:
                # 其他异常，返回默认值
                logger.warning(f"获取内存使用情况失败: {e}")
                return 0.0
        
        logger.info(
            f"计算 bbox 瓦片: west={west}, south={south}, east={east}, north={north}, "
            f"min_zoom={min_zoom}, max_zoom={max_zoom}, is_tms={self.is_tms}"
        )

        total_tiles = 0
        added_tasks = 0
        skipped_tasks = 0
        memory_usage = get_memory_usage()
        if memory_usage > 0:
            logger.info(f"开始添加任务，当前内存使用: {memory_usage:.2f} MB")
        else:
            logger.info("开始添加任务")
        
        # 加载并过滤已处理的瓦片，只保留用户指定缩放级别的瓦片
        if self.enable_resume:
            # 动态加载进度文件中用户指定缩放级别的已处理瓦片
            logger.info("从进度文件加载用户指定缩放级别的已处理瓦片...")
            self.progress_manager.processed_tiles = self.progress_manager.load_processed_tiles_for_zoom_range(min_zoom, max_zoom)
            logger.info(f"已加载 {len(self.progress_manager.processed_tiles)} 个指定缩放级别的已处理瓦片")
        
        # 导入tile_math模块，避免在循环中重复导入
        from ..tile_math import TileMath
        
        for zoom in range(min_zoom, max_zoom + 1):
            # 检查是否已收到停止信号
            if self.stop_event.is_set():
                logger.info("收到停止信号，停止添加任务")
                break
            
            # 直接使用生成器方式计算瓦片，避免一次性生成所有瓦片坐标
            tile_gen = TileMath.calculate_tiles_in_bbox(
                west, south, east, north, zoom, is_tms=self.is_tms
            )
            
            # 计算当前缩放级别的瓦片数量
            xy_tiles = list(tile_gen)
            tile_count = len(xy_tiles)
            total_tiles += tile_count
            logger.info(f"缩放级别 {zoom}: 找到 {tile_count} 个瓦片")
            
            # 检查瓦片数量是否过大
            if tile_count > 1000000:
                logger.warning(f"缩放级别 {zoom}: 瓦片数量 ({tile_count}) 非常大，可能会占用大量内存")
                # 动态调整批次大小，瓦片数量越大，批次越小
                adjusted_batch_size = min(batch_size, max(5000, 2000000 // (tile_count // 200000 + 1)))
                logger.info(f"自动调整批次大小为: {adjusted_batch_size}")
            else:
                adjusted_batch_size = batch_size
            
            # 流式添加当前缩放级别的任务
            batch_count = 0
            zoom_added = 0
            zoom_skipped = 0
            
            # 批量处理任务，减少日志输出
            for i, (x, y) in enumerate(xy_tiles):
                # 检查是否已收到停止信号
                if self.stop_event.is_set():
                    logger.info("收到停止信号，停止添加任务")
                    break
                
                # 检查瓦片是否已处理
                if self.enable_resume and (x, y, zoom) in self.progress_manager.processed_tiles:
                    # 直接使用内存集合检查，避免数据库查询
                    self.skipped_count += 1
                    zoom_skipped += 1
                    skipped_tasks += 1
                    continue
                
                self.task_queue.put((x, y, zoom))
                added_tasks += 1
                zoom_added += 1
                batch_count += 1
                
                # 每批添加后短暂休眠，避免内存占用过高
                if batch_count >= adjusted_batch_size:
                    current_memory = get_memory_usage()
                    memory_delta = current_memory - memory_usage
                    
                    # 减少日志输出频率
                    if memory_delta > 200:  # 内存增长超过200MB
                        logger.info(f"缩放级别 {zoom}: 已添加 {batch_count} 个瓦片任务, 内存使用: {current_memory:.2f} MB (+{memory_delta:.2f} MB)")
                        time.sleep(0.005)  # 减少休眠时间
                    elif memory_delta > 100:
                        logger.debug(f"缩放级别 {zoom}: 已添加 {batch_count} 个瓦片任务, 内存使用: {current_memory:.2f} MB (+{memory_delta:.2f} MB)")
                        time.sleep(0.001)  # 进一步缩短休眠时间
                    
                    batch_count = 0
                    # 更新内存使用基准
                    memory_usage = current_memory
            
            logger.info(f"缩放级别 {zoom}: 已添加 {zoom_added} 个任务，跳过 {zoom_skipped} 个已处理任务")
            
            # 清理当前缩放级别的瓦片列表，释放内存
            del xy_tiles
        
        # 设置总任务数为所有瓦片的总数（包括已处理和未处理的）
        self.total_tasks = total_tiles
        final_memory = get_memory_usage()
        logger.info(f"总计: {total_tiles} 个瓦片, 已添加 {added_tasks} 个任务, 跳过 {skipped_tasks} 个已处理任务, 最终内存使用: {final_memory:.2f} MB")

    def start(self):
        """
        开始下载任务
        """
        try:
            logger.info(f"开始下载，线程数={self.max_threads}，provider={self.provider.name}")
            
            # 启动工作线程
            worker_manager = WorkerManager(self)
            worker_manager.start_workers()
            
            # 等待所有线程完成
            worker_manager.wait_for_completion()
            
            # 所有任务完成后，保存最终进度
            if self.enable_resume:
                try:
                    self._save_progress()
                    logger.info("下载完成，最终进度已保存")
                except Exception as save_error:
                    logger.error(f"保存最终进度失败: {save_error}")
            
            # 完成下载，确保所有MBTiles事务都已提交
            if self.is_mbtiles:
                try:
                    self._finalize_download()
                    logger.info("已完成下载，提交所有MBTiles事务")
                except Exception as finalize_error:
                    logger.error(f"完成下载失败: {finalize_error}")
        except Exception as e:
            logger.error(f"下载过程中发生异常: {e}")
            # 发生异常时也保存进度
            if self.enable_resume:
                try:
                    self._save_progress()
                    logger.info("异常发生，进度已保存")
                except Exception as save_error:
                    logger.error(f"保存进度失败: {save_error}")
            self.stop_event.set()
            raise

    def _mark_tile_processed(self, x: int, y: int, z: int, status: str):
        """
        标记瓦片为已处理
        
        Args:
            x: 瓦片x坐标
            y: 瓦片y坐标
            z: 缩放级别
            status: 处理状态，'success'、'failed'或'skipped'
        """
        # 使用进度管理器标记瓦片
        self.progress_manager.mark_tile_processed(x, y, z, status)
        
        # 更新计数
        if status == 'success':
            self.downloaded_count += 1
        elif status == 'failed':
            self.failed_count += 1
        elif status == 'skipped':
            self.skipped_count += 1

    def _save_progress(self):
        """
        保存进度到数据库
        """
        if self.enable_resume:
            try:
                self.progress_manager.save_progress(self.progress_manager.processed_tiles)
                logger.info("进度保存成功")
            except Exception as e:
                logger.error(f"保存进度失败: {e}")

    def _update_progress(self):
        """
        更新下载进度
        """
        if self.progress_callback and self.total_tasks > 0:
            # 使用已处理的任务数作为下载进度
            processed = self.downloaded_count + self.failed_count + self.skipped_count
            self.progress_callback(processed, self.total_tasks, self.total_bytes)

    def get_statistics(self) -> Dict[str, int]:
        """
        获取下载统计信息
        
        Returns:
            Dict[str, int]: 下载统计信息
        """
        processed = self.downloaded_count + self.failed_count + self.skipped_count
        remaining = max(0, self.total_tasks - processed)
        return {
            "downloaded": self.downloaded_count,
            "failed": self.failed_count,
            "skipped": self.skipped_count,
            "total": self.total_tasks,
            "remaining": remaining
        }

    def get_performance_statistics(self) -> Optional[Dict]:
        """
        获取性能统计信息
        
        Returns:
            Optional[Dict]: 性能统计信息，如果性能监控未启用则返回None
        """
        if self.performance_monitor:
            stats = self.performance_monitor.get_statistics()
            self.performance_monitor.log_statistics()
            return stats
        return None

    def log_performance_statistics(self):
        """
        记录性能统计信息
        """
        if self.performance_monitor:
            self.performance_monitor.log_statistics()

    def pause(self):
        """
        暂停下载任务，优化响应速度和稳健性
        """
        logger.info("暂停下载任务")
        
        # 立即清除事件，使线程暂停
        self.pause_event.clear()
        
        # 记录暂停开始时间
        pause_start_time = time.time()
        
        # 等待所有线程进入暂停状态
        active_threads = threading.active_count()
        logger.info(f"暂停开始，当前活跃线程数: {active_threads}")
        
        # 保存当前进度
        if self.enable_resume:
            try:
                self._save_progress()
                logger.info("暂停下载时保存进度")
            except Exception as e:
                logger.error(f"保存进度失败: {e}")
        
        # 等待一小段时间，确保大部分线程已经暂停
        time.sleep(0.1)
        
        # 检查当前状态
        paused_time = time.time() - pause_start_time
        logger.info(f"暂停操作完成，耗时: {paused_time:.2f} 秒")

    def resume(self):
        """
        恢复下载任务，优化响应速度和稳健性
        """
        logger.info("恢复下载任务")
        
        # 记录恢复开始时间
        resume_start_time = time.time()
        
        # 设置事件，使线程继续
        self.pause_event.set()
        
        # 等待一小段时间，确保线程开始恢复
        time.sleep(0.05)
        
        # 检查当前状态
        resumed_time = time.time() - resume_start_time
        active_threads = threading.active_count()
        logger.info(f"恢复操作完成，耗时: {resumed_time:.2f} 秒，当前活跃线程数: {active_threads}")

    def is_paused(self) -> bool:
        """
        检查下载是否已暂停
        
        Returns:
            bool: 是否已暂停
        """
        return not self.pause_event.is_set()

    def stop(self):
        """
        停止下载任务
        """
        logger.info("停止下载任务")
        self.stop_event.set()

    def cancel(self):
        """
        取消下载任务，优化响应速度和稳健性
        """
        logger.info("取消下载任务")
        
        # 记录取消开始时间
        cancel_start_time = time.time()
        
        # 保存当前进度
        if self.enable_resume:
            try:
                self._save_progress()
                logger.info("取消下载时保存进度")
            except Exception as e:
                logger.error(f"保存进度失败: {e}")
        
        # 触发停止事件
        self.stop_event.set()
        
        # 重置暂停事件，确保下次下载不受影响
        self.pause_event.set()
        
        # 等待一小段时间，让线程有机会处理停止事件
        time.sleep(0.2)
        
        # 关闭MBTiles连接
        if self.is_mbtiles and self.mbtiles_handler:
            # 关闭 MBTiles 处理器
            try:
                self.mbtiles_handler.close()
                logger.debug("关闭 MBTiles 处理器")
            except Exception as e:
                logger.error(f"关闭 MBTiles 处理器失败: {e}")
            # 关闭MBTiles管理器
            try:
                self.mbtiles_manager.close_connections()
                logger.debug("关闭MBTiles管理器连接")
            except Exception as e:
                logger.error(f"关闭MBTiles管理器连接失败: {e}")
        
        # 关闭进度数据库连接
        self.progress_manager.close()
        
        # 检查当前状态
        cancelled_time = time.time() - cancel_start_time
        active_threads = threading.active_count()
        logger.info(f"取消操作完成，耗时: {cancelled_time:.2f} 秒，当前活跃线程数: {active_threads}")
        
        # 返回统计信息
        return self.get_statistics()



    def _finalize_download(self):
        """
        完成下载，确保所有事务都已提交
        """
        if self.is_mbtiles and self.mbtiles_handler:
            try:
                self.mbtiles_handler._finalize_download()
                logger.info("已完成下载，提交所有MBTiles事务")
            except Exception as finalize_error:
                logger.error(f"完成下载失败: {finalize_error}")
