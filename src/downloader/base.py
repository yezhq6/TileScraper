# src/downloader/base.py

import time
import os
import signal
import sys
import threading
from pathlib import Path
from queue import Queue, Empty
from typing import List, Tuple, Dict, Optional, Callable
import requests
from loguru import logger

from ..providers import ProviderManager, TileProvider
from .utils import convert_path, ensure_directory
from .performance import PerformanceMonitor


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
        try:
            self.provider: TileProvider = ProviderManager.get_provider(provider_name)
            self.provider.is_tms = is_tms
            self.is_tms = is_tms
            
            # 设置瓦片格式
            if tile_format:
                self.provider.set_tile_format(tile_format)
            
            # 保存格式
            self.save_format = save_format.lower()
            self.is_mbtiles = self.save_format == "mbtiles"
            self.scheme = scheme.lower()  # 保存scheme参数
            self.enable_sharding = False  # 是否启用MBTiles分库
            self.mbtiles_paths = {}  # 按缩放级别存储MBTiles文件路径
            self.mbtiles_connections = {}  # 按缩放级别存储MBTiles连接
            
            # 转换输出路径，支持Windows路径和Linux路径
            self.output_path = convert_path(output_dir)
            
            # 确定数据文件夹位置
            if self.is_mbtiles:
                # MBTiles模式：数据文件夹与MBTiles文件同级
                self.data_dir = self.output_path.parent / "aux"
            else:
                # 目录模式：数据文件夹与输出目录同级
                self.data_dir = self.output_path / "aux"
            
            ensure_directory(self.data_dir)
            logger.info(f"数据文件夹已创建: {self.data_dir}")
            
            # 根据保存格式设置输出目录
            if self.is_mbtiles:
                # 检查是否启用分库
                if "{z}" in str(self.output_path):
                    # 启用分库，输出路径包含{z}占位符
                    self.enable_sharding = True
                    self.output_dir = self.output_path.parent
                    logger.info("启用MBTiles分库策略，按缩放级别分库")
                else:
                    # 单库模式：MBTiles文件与aux文件夹同级
                    self.output_dir = self.output_path.parent
                    self.mbtiles_path = self.output_path
                    logger.info(f"MBTiles文件将存储在: {self.mbtiles_path}")
            else:
                # 目录格式：output_path就是输出目录
                self.output_dir = self.output_path
                self.mbtiles_path = None
            
            self.max_threads = max_threads
            self.retries = retries
            self.delay = delay
            self.timeout = timeout
            self.enable_resume = enable_resume
            self.enable_performance_monitor = enable_performance_monitor

            self.task_queue: Queue = Queue()
            self.stop_event = threading.Event()
            self.pause_event = threading.Event()  # 暂停事件
            self.pause_event.set()  # 初始状态为运行（未暂停）

            self.downloaded_count = 0
            self.failed_count = 0
            self.skipped_count = 0
            self.total_tasks = 0
            self.total_bytes = 0  # 添加total_bytes属性，用于统计下载的总字节数
            self.progress_callback = progress_callback
            
            # 事务管理
            self.transaction_counter = 0  # 事务计数器，用于批量提交
            self.transaction_batch_size = 1000  # 每1000个瓦片提交一次事务
            
            # MBTiles相关
            self.mbtiles_conn = None
            self.mbtiles_cursor = None
            # 添加锁来保护MBTiles操作，避免多个线程同时使用游标
            self.mbtiles_lock = threading.Lock()
            
            # 进度持久化相关
            self.progress_file = self.data_dir / f"{provider_name}_progress.db"  # 使用SQLite数据库存储进度
            self.processed_tiles = set()  # 已处理的瓦片集合，格式为(x,y,z)
            self.last_saved_tiles_count = 0  # 上一次保存时的瓦片数量，用于增量保存
            self.progress_lock = threading.Lock()  # 保护进度文件写入
            self.progress_conn = None  # SQLite进度数据库连接
            self.progress_cursor = None  # SQLite进度数据库游标

            # 性能监控
            self.performance_monitor = PerformanceMonitor() if self.enable_performance_monitor else None

            # 创建输出目录
            ensure_directory(self.output_dir)
            
            # 初始化进度数据库
            if self.enable_resume:
                self._init_progress_db()
                # 不加载进度，等到 add_tasks_for_bbox 方法中需要时再加载
            
            # 初始化MBTiles数据库（如果是MBTiles格式）
            if self.is_mbtiles and not self.enable_sharding:
                self._init_mbtiles()
            
            # 注册信号处理
            self._register_signal_handlers()
            
            logger.info(f"初始化下载器: provider={provider_name}, threads={max_threads}, output_path={self.output_path}, save_format={self.save_format}")
            logger.info(f"断点续传: {'已启用' if self.enable_resume else '已禁用'}")
            logger.info(f"性能监控: {'已启用' if self.enable_performance_monitor else '已禁用'}")
        except Exception as e:
            logger.error(f"初始化下载器失败: {e}")
            # 初始化失败时也尝试保存进度
            if self.enable_resume:
                try:
                    self._save_progress()
                except:
                    pass
            raise

    def _load_processed_tiles_for_zoom_range(self, min_zoom: int, max_zoom: int) -> set:
        """
        从进度文件中加载用户指定缩放级别的已处理瓦片
        
        Args:
            min_zoom: 最小缩放级别
            max_zoom: 最大缩放级别
            
        Returns:
            set: 已处理的瓦片集合，格式为(x,y,z)
        """
        processed_tiles = set()
        
        if not self.progress_file.exists():
            logger.info(f"未找到进度文件: {self.progress_file}")
            return processed_tiles
        
        try:
            # 尝试从SQLite数据库加载进度
            if self.progress_conn and self.progress_cursor:
                logger.info("从SQLite数据库加载指定缩放级别的已处理瓦片")
                
                # 使用分页查询，减少内存使用
                batch_size = 10000
                offset = 0
                while True:
                    self.progress_cursor.execute(
                        'SELECT x, y, z FROM processed_tiles WHERE z >= ? AND z <= ? LIMIT ? OFFSET ?',
                        (min_zoom, max_zoom, batch_size, offset)
                    )
                    tiles = self.progress_cursor.fetchall()
                    
                    if not tiles:
                        break
                    
                    # 将瓦片添加到集合
                    processed_tiles.update((x, y, z) for x, y, z in tiles)
                    offset += batch_size
                    logger.debug(f"已加载 {len(processed_tiles)} 个指定缩放级别的已处理瓦片")
            else:
                # 回退到从JSON文件加载
                import json
                logger.info("从JSON文件加载指定缩放级别的已处理瓦片")
                
                # 读取文件内容
                with open(self.progress_file, 'r', encoding='utf-8') as f:
                    progress = json.load(f)
                
                # 加载已处理的瓦片
                processed_tiles_list = progress.get('processed_tiles', [])
                
                # 过滤用户指定缩放级别的瓦片
                for tile in processed_tiles_list:
                    if isinstance(tile, (list, tuple)) and len(tile) == 3:
                        try:
                            x, y, z = map(int, tile)
                            if min_zoom <= z <= max_zoom:
                                processed_tiles.add((x, y, z))
                        except (ValueError, TypeError):
                            continue
                
                logger.debug(f"已加载 {len(processed_tiles)} 个指定缩放级别的已处理瓦片")
                
        except Exception as e:
            logger.error(f"加载指定缩放级别的已处理瓦片失败: {e}")
            # 加载失败时返回空集合
            processed_tiles = set()
        
        return processed_tiles
    
    def _load_progress(self):
        """
        从文件加载已下载的进度，增加错误处理和兼容性，优化内存使用
        """
        # 此方法已被 _load_processed_tiles_for_zoom_range 替代
        pass
    
    def _init_mbtiles(self):
        """
        初始化MBTiles数据库，创建所需的表结构
        """
        import sqlite3
        import time
        
        max_retries = 5
        retry_delay = 1  # 秒
        
        for attempt in range(max_retries):
            try:
                # 连接或创建MBTiles数据库，使用check_same_thread=False允许跨线程使用
                self.mbtiles_conn = sqlite3.connect(self.mbtiles_path, check_same_thread=False)
                self.mbtiles_cursor = self.mbtiles_conn.cursor()
                
                # 优化SQLite配置
                # 启用WAL模式，提高并发性能
                self.mbtiles_conn.execute('PRAGMA journal_mode=WAL;')
                # 增加缓存大小，约1GB
                self.mbtiles_conn.execute('PRAGMA cache_size=1000000;')
                # 同步模式设为NORMAL，权衡安全性和性能
                self.mbtiles_conn.execute('PRAGMA synchronous=NORMAL;')
                # 启用共享缓存
                self.mbtiles_conn.execute('PRAGMA enable_shared_cache=1;')
                # 启用自动检查点，定期合并WAL文件
                self.mbtiles_conn.execute('PRAGMA wal_autocheckpoint=1000;')
                # 设置锁定超时
                self.mbtiles_conn.execute('PRAGMA busy_timeout=30000;')  # 30秒
                
                # 创建tiles表
                self.mbtiles_cursor.execute('''
                    CREATE TABLE IF NOT EXISTS tiles (
                        zoom_level INTEGER,
                        tile_column INTEGER,
                        tile_row INTEGER,
                        tile_data BLOB,
                        PRIMARY KEY (zoom_level, tile_column, tile_row)
                    )
                ''')
                
                # 创建metadata表
                self.mbtiles_cursor.execute('''
                    CREATE TABLE IF NOT EXISTS metadata (
                        name TEXT,
                        value TEXT,
                        PRIMARY KEY (name)
                    )
                ''')
                
                # 插入基本metadata
                metadata = [
                    ('name', 'TileHarvester'),
                    ('type', 'baselayer'),
                    ('version', '1.0'),
                    ('description', 'Generated by TileHarvester'),
                    ('format', self.provider.extension),
                    ('scheme', self.scheme),  # 添加scheme字段
                ]
                
                self.mbtiles_cursor.executemany(
                    'INSERT OR REPLACE INTO metadata (name, value) VALUES (?, ?)',
                    metadata
                )
                
                # 提交事务
                self.mbtiles_conn.commit()
                
                logger.info(f"MBTiles数据库初始化完成: {self.mbtiles_path}")
                logger.info("SQLite优化配置已应用: WAL模式、缓存大小1GB、同步模式NORMAL")
                return
            except sqlite3.OperationalError as e:
                if "database is locked" in str(e):
                    logger.warning(f"MBTiles数据库被锁定，尝试重试 ({attempt+1}/{max_retries})...")
                    if self.mbtiles_conn:
                        try:
                            self.mbtiles_conn.close()
                        except:
                            pass
                    time.sleep(retry_delay)
                    retry_delay *= 2  # 指数退避
                else:
                    logger.error(f"MBTiles数据库初始化失败: {e}")
                    if self.mbtiles_conn:
                        try:
                            self.mbtiles_conn.close()
                        except:
                            pass
                    raise
            except Exception as e:
                logger.error(f"MBTiles数据库初始化失败: {e}")
                if self.mbtiles_conn:
                    try:
                        self.mbtiles_conn.close()
                    except:
                        pass
                raise
        
        # 所有重试都失败
        logger.error(f"MBTiles数据库初始化失败: 经过 {max_retries} 次尝试后仍然无法获取数据库锁")
        raise Exception(f"经过 {max_retries} 次尝试后仍然无法获取数据库锁")
    
    def _init_progress_db(self):
        """
        初始化SQLite进度数据库
        """
        try:
            import sqlite3
            
            # 连接或创建SQLite数据库
            self.progress_conn = sqlite3.connect(self.progress_file, check_same_thread=False)
            self.progress_cursor = self.progress_conn.cursor()
            
            # 优化SQLite配置，使用WAL模式提高性能
            self.progress_conn.execute('PRAGMA journal_mode=WAL;')
            self.progress_conn.execute('PRAGMA cache_size=100000;')  # 约100MB缓存
            self.progress_conn.execute('PRAGMA synchronous=NORMAL;')
            self.progress_conn.execute('PRAGMA wal_autocheckpoint=1000;')  # 定期检查点，合并WAL文件
            
            # 创建元数据表
            self.progress_cursor.execute('''
                CREATE TABLE IF NOT EXISTS metadata (
                    key TEXT PRIMARY KEY,
                    value TEXT
                )
            ''')
            
            # 创建瓦片表
            self.progress_cursor.execute('''
                CREATE TABLE IF NOT EXISTS processed_tiles (
                    x INTEGER,
                    y INTEGER,
                    z INTEGER,
                    status TEXT,
                    timestamp REAL,
                    PRIMARY KEY (x, y, z)
                )
            ''')
            
            # 创建索引，提高查询性能
            self.progress_cursor.execute('CREATE INDEX IF NOT EXISTS idx_processed_tiles_status ON processed_tiles(status);')
            self.progress_cursor.execute('CREATE INDEX IF NOT EXISTS idx_processed_tiles_timestamp ON processed_tiles(timestamp);')
            
            # 提交事务
            self.progress_conn.commit()
            
            logger.info(f"进度数据库初始化完成: {self.progress_file}")
        except Exception as e:
            logger.error(f"初始化进度数据库失败: {e}")
            if self.progress_conn:
                self.progress_conn.close()
            self.progress_conn = None
            self.progress_cursor = None
    
    def _register_signal_handlers(self):
        """
        注册信号处理函数，在程序崩溃时保存进度
        """
        def signal_handler(sig, frame):
            logger.info(f"收到信号 {sig}，正在保存进度...")
            if self.enable_resume:
                try:
                    self._save_progress()
                    logger.info("进度已保存，程序退出")
                except Exception as e:
                    logger.error(f"保存进度失败: {e}")
            # 对于SIGINT，允许程序正常退出
            if sig == signal.SIGINT:
                sys.exit(0)
        
        # 注册信号处理
        try:
            signal.signal(signal.SIGINT, signal_handler)  # Ctrl+C
            signal.signal(signal.SIGTERM, signal_handler)  # 终止信号
        except Exception as e:
            logger.warning(f"注册信号处理失败: {e}")
    
    def _save_progress(self):
        """
        将当前进度保存到文件，使用锁保护避免并发写入，优化内存使用
        """
        if not self.enable_resume:
            return
        
        with self.progress_lock:
            try:
                # 检查已处理瓦片数量，避免内存使用过高
                tile_count = len(self.processed_tiles)
                logger.debug(f"保存进度: 已处理瓦片数量={tile_count}")
                
                # 如果使用SQLite进度数据库
                if self.progress_conn and self.progress_cursor:
                    logger.info(f"使用SQLite数据库保存进度: {tile_count} 个瓦片")
                    
                    # 计算增量保存的瓦片数量
                    new_tiles_count = tile_count - self.last_saved_tiles_count
                    
                    if new_tiles_count > 0:
                        logger.info(f"增量保存 {new_tiles_count} 个新处理的瓦片")
                        
                        # 使用更高效的批量插入方法
                # 对于大批量瓦片，使用生成器来减少内存使用
                def tile_generator():
                    """瓦片生成器，用于批量插入"""
                    current_timestamp = time.time()
                    for tile in self.processed_tiles:
                        if len(tile) == 3:
                            yield (tile[0], tile[1], tile[2], 'success', current_timestamp)
                
                # 使用更大的批量大小
                batch_size = 100000
                tile_gen = tile_generator()
                
                max_retries = 3
                retry_delay = 0.5  # 秒
                
                for attempt in range(max_retries):
                    try:
                        # 使用单个事务处理所有插入，减少磁盘I/O
                        self.progress_conn.execute('BEGIN TRANSACTION;')
                        
                        try:
                            # 批量插入已处理的瓦片
                            batch = []
                            inserted_count = 0
                            
                            for tile_data in tile_gen:
                                batch.append(tile_data)
                                if len(batch) >= batch_size:
                                    # 使用executemany进行批量插入
                                    result = self.progress_cursor.executemany(
                                        '''INSERT OR IGNORE INTO processed_tiles 
                                        (x, y, z, status, timestamp) 
                                        VALUES (?, ?, ?, ?, ?)''',
                                        batch
                                    )
                                    inserted_count += result.rowcount if hasattr(result, 'rowcount') else len(batch)
                                    batch = []
                            
                            # 插入剩余的瓦片
                            if batch:
                                result = self.progress_cursor.executemany(
                                    '''INSERT OR IGNORE INTO processed_tiles 
                                    (x, y, z, status, timestamp) 
                                    VALUES (?, ?, ?, ?, ?)''',
                                    batch
                                )
                                inserted_count += result.rowcount if hasattr(result, 'rowcount') else len(batch)
                            
                            # 提交所有插入
                            self.progress_conn.commit()
                            logger.info(f"成功批量插入 {inserted_count} 个瓦片")
                        except Exception as e:
                            self.progress_conn.rollback()
                            logger.error(f"批量插入瓦片失败: {e}")
                            raise
                        break
                    except sqlite3.OperationalError as e:
                        if "database is locked" in str(e):
                            logger.warning(f"进度数据库被锁定，尝试重试 ({attempt+1}/{max_retries})...")
                            if attempt < max_retries - 1:
                                import time
                                time.sleep(retry_delay)
                                retry_delay *= 2  # 指数退避
                                # 重置生成器
                                tile_gen = tile_generator()
                            else:
                                logger.error(f"进度保存失败: 经过 {max_retries} 次尝试后仍然无法获取数据库锁")
                                raise
                        else:
                            raise
                    
                    # 保存元数据
                    metadata = [
                        ('downloaded_count', str(self.downloaded_count)),
                        ('failed_count', str(self.failed_count)),
                        ('skipped_count', str(self.skipped_count)),
                        ('total_tasks', str(self.total_tasks)),
                        ('total_bytes', str(self.total_bytes)),
                        ('timestamp', str(time.time())),
                        ('save_format', self.save_format),
                        ('scheme', self.scheme),
                        ('tile_count', str(tile_count))
                    ]
                    
                    max_retries = 3
                    retry_delay = 0.5  # 秒
                    
                    for attempt in range(max_retries):
                        try:
                            # 使用单个事务处理元数据更新
                            self.progress_conn.execute('BEGIN TRANSACTION;')
                            
                            try:
                                self.progress_cursor.executemany(
                                    'INSERT OR REPLACE INTO metadata (key, value) VALUES (?, ?)',
                                    metadata
                                )
                                self.progress_conn.commit()
                            except Exception as e:
                                self.progress_conn.rollback()
                                logger.error(f"保存元数据失败: {e}")
                                raise
                            break
                        except sqlite3.OperationalError as e:
                            if "database is locked" in str(e):
                                logger.warning(f"进度数据库被锁定，尝试重试 ({attempt+1}/{max_retries})...")
                                if attempt < max_retries - 1:
                                    import time
                                    time.sleep(retry_delay)
                                    retry_delay *= 2  # 指数退避
                                else:
                                    logger.error(f"元数据保存失败: 经过 {max_retries} 次尝试后仍然无法获取数据库锁")
                                    raise
                            else:
                                raise
                    
                    logger.info(f"进度已保存到SQLite数据库: {self.progress_file}")
                else:
                    # 回退到JSON文件保存
                    import json
                    logger.info(f"使用JSON文件保存进度: {tile_count} 个瓦片")
                    
                    # 对于大量瓦片，使用流式写入
                    if tile_count > 1000000:
                        logger.info(f"瓦片数量较大 ({tile_count})，使用流式方法保存进度")
                        self._save_progress_streaming()
                        return
                    
                    # 计算增量保存的瓦片数量
                    new_tiles_count = tile_count - self.last_saved_tiles_count
                    
                    if new_tiles_count > 0:
                        logger.info(f"增量保存 {new_tiles_count} 个新处理的瓦片到JSON文件")
                        
                        # 创建临时文件，写入成功后再替换原文件
                        temp_file = self.progress_file.with_suffix('.tmp')
                        
                        # 确保输出目录存在
                        ensure_directory(self.output_dir)
                        
                        # 使用流式写入方法，提高大文件保存速度
                        logger.info(f"使用流式方法保存JSON进度文件: {tile_count} 个瓦片")
                        
                        try:
                            # 流式写入JSON文件
                            with open(temp_file, 'w', encoding='utf-8') as f:
                                # 写入JSON头部
                                f.write('{\n')
                                f.write('  "version": "1.2",\n')
                                f.write(f'  "downloaded_count": {self.downloaded_count},\n')
                                f.write(f'  "failed_count": {self.failed_count},\n')
                                f.write(f'  "skipped_count": {self.skipped_count},\n')
                                f.write(f'  "total_tasks": {self.total_tasks},\n')
                                f.write(f'  "total_bytes": {self.total_bytes},\n')
                                f.write(f'  "tile_count": {tile_count},\n')
                                f.write(f'  "timestamp": {time.time()},\n')
                                f.write(f'  "save_format": "{self.save_format}",\n')
                                f.write(f'  "scheme": "{self.scheme}",\n')
                                f.write('  "memory_usage": {\n')
                                f.write(f'    "processed_tiles_size": {tile_count * 24},\n')
                                f.write(f'    "estimated_total": {tile_count * 24 + 1024 * 1024}\n')
                                f.write('  },\n')
                                f.write('  "processed_tiles": [\n')
                                
                                # 分块写入瓦片数据，使用更大的批次大小
                                tile_iter = iter(self.processed_tiles)
                                first = True
                                batch_size = 100000  # 每批写入100000个瓦片，减少IO操作
                                batch = []
                                
                                for tile in tile_iter:
                                    batch.append(tile)
                                    if len(batch) >= batch_size:
                                        # 写入当前批次
                                        if not first:
                                            f.write(',\n')
                                        f.write(',\n'.join(f'    [{tile[0]}, {tile[1]}, {tile[2]}]' for tile in batch))
                                        first = False
                                        # 清空批次，释放内存
                                        batch = []
                                        # 刷新缓冲区，确保数据写入磁盘
                                        f.flush()
                                
                                # 写入剩余的瓦片
                                if batch:
                                    if not first:
                                        f.write(',\n')
                                    f.write(',\n'.join(f'    [{tile[0]}, {tile[1]}, {tile[2]}]' for tile in batch))
                                
                                # 写入JSON尾部
                                f.write('\n  ]\n')
                                f.write('}\n')
                            
                            # 原子性替换文件
                            temp_file.replace(self.progress_file)
                            
                            logger.info(f"进度已保存到JSON文件: {self.progress_file}")
                        except Exception as e:
                            logger.error(f"流式保存JSON进度文件失败: {e}")
                            # 清理临时文件
                            if temp_file.exists():
                                try:
                                    temp_file.unlink()
                                except Exception as cleanup_error:
                                    logger.error(f"清理临时文件失败: {cleanup_error}")
                    else:
                        logger.info("没有新处理的瓦片，跳过保存JSON进度文件")
                
                # 更新上一次保存的瓦片数量
                self.last_saved_tiles_count = tile_count
            except Exception as e:
                logger.error(f"保存进度失败: {e}")
    
    def _save_progress_streaming(self):
        """
        使用流式方法保存进度，减少内存使用
        """
        import json
        temp_file = self.progress_file.with_suffix('.tmp')
        
        try:
            # 确保输出目录存在
            ensure_directory(self.output_dir)
            
            # 流式写入JSON文件
            with open(temp_file, 'w', encoding='utf-8') as f:
                # 写入JSON头部
                f.write('{\n')
                f.write('  "version": "1.2",\n')
                f.write(f'  "downloaded_count": {self.downloaded_count},\n')
                f.write(f'  "failed_count": {self.failed_count},\n')
                f.write(f'  "skipped_count": {self.skipped_count},\n')
                f.write(f'  "total_tasks": {self.total_tasks},\n')
                f.write(f'  "total_bytes": {self.total_bytes},\n')
                f.write(f'  "tile_count": {len(self.processed_tiles)},\n')
                f.write(f'  "timestamp": {time.time()},\n')
                f.write(f'  "save_format": "{self.save_format}",\n')
                f.write(f'  "scheme": "{self.scheme}",\n')
                f.write('  "memory_usage": {\n')
                f.write(f'    "processed_tiles_size": {len(self.processed_tiles) * 24},\n')
                f.write(f'    "estimated_total": {len(self.processed_tiles) * 24 + 1024 * 1024}\n')
                f.write('  },\n')
                f.write('  "processed_tiles": [\n')
                
                # 分块写入瓦片数据
                tile_iter = iter(self.processed_tiles)
                first = True
                batch_size = 50000  # 每批写入50000个瓦片，减少IO操作
                batch = []
                
                for tile in tile_iter:
                    batch.append(tile)
                    if len(batch) >= batch_size:
                        # 写入当前批次
                        if not first:
                            f.write(',\n')
                        f.write(',\n'.join(f'    [{tile[0]}, {tile[1]}, {tile[2]}]' for tile in batch))
                        first = False
                        # 清空批次，释放内存
                        batch = []
                        # 刷新缓冲区，确保数据写入磁盘
                        f.flush()
                
                # 写入剩余的瓦片
                if batch:
                    if not first:
                        f.write(',\n')
                    f.write(',\n'.join(f'    [{tile[0]}, {tile[1]}, {tile[2]}]' for tile in batch))
                
                # 写入JSON尾部
                f.write('\n  ]\n')
                f.write('}\n')
            
            # 原子性替换文件
            temp_file.replace(self.progress_file)
            
            # 更新上一次保存的瓦片数量
            self.last_saved_tiles_count = len(self.processed_tiles)
            logger.info(f"进度已通过流式方法保存到: {self.progress_file}")
        except Exception as e:
            logger.error(f"流式保存进度失败: {e}")
            # 清理临时文件
            if temp_file.exists():
                try:
                    temp_file.unlink()
                except Exception as cleanup_error:
                    logger.error(f"清理临时文件失败: {cleanup_error}")
    
    def _is_tile_processed(self, x: int, y: int, z: int) -> bool:
        """
        检查瓦片是否已处理
        
        Args:
            x: 瓦片x坐标
            y: 瓦片y坐标
            z: 缩放级别
            
        Returns:
            bool: 是否已处理
        """
        return (x, y, z) in self.processed_tiles
    
    def _mark_tile_processed(self, x: int, y: int, z: int, status: str):
        """
        标记瓦片为已处理
        
        Args:
            x: 瓦片x坐标
            y: 瓦片y坐标
            z: 缩放级别
            status: 处理状态，'success'、'failed'或'skipped'
        """
        tile_key = (x, y, z)
        
        # 只有新处理的瓦片才更新计数
        if tile_key not in self.processed_tiles:
            # 对于大批量瓦片，限制processed_tiles集合的大小
            # 当集合大小超过阈值时，切换到数据库模式
            max_tiles_in_memory = 1000000  # 100万个瓦片约占用24MB内存
            if len(self.processed_tiles) >= max_tiles_in_memory:
                logger.info(f"内存中的瓦片数量达到阈值 ({max_tiles_in_memory})，切换到数据库模式")
                # 不再将瓦片添加到内存集合，直接依赖数据库
                # 但仍然需要更新计数
                if status == 'success':
                    self.downloaded_count += 1
                elif status == 'failed':
                    self.failed_count += 1
                elif status == 'skipped':
                    self.skipped_count += 1
            else:
                self.processed_tiles.add(tile_key)
                
                # 更新计数
                if status == 'success':
                    self.downloaded_count += 1
                elif status == 'failed':
                    self.failed_count += 1
                elif status == 'skipped':
                    self.skipped_count += 1
        else:
            # 已处理的瓦片只增加跳过计数
            if status == 'skipped':
                self.skipped_count += 1
    
    def add_task(self, x: int, y: int, zoom: int):
        """
        添加任务到队列，启用断点续传时跳过已处理的瓦片
        
        Args:
            x: 瓦片x坐标
            y: 瓦片y坐标
            zoom: 缩放级别
        """
        # 启用断点续传且瓦片已处理，则跳过
        if self.enable_resume and self._is_tile_processed(x, y, zoom):
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
        for x, y, z in tiles:
            self.add_task(x, y, z)

    def add_tasks_for_bbox(
        self,
        west: float,
        south: float,
        east: float,
        north: float,
        min_zoom: int,
        max_zoom: int,
        batch_size: int = 10000  # 每批处理的任务数量，默认10000个
    ):
        """
        根据经纬度范围添加任务，支持多个缩放级别，流式处理避免内存占用过高
        
        Args:
            west: 西边界经度
            south: 南边界纬度
            east: 东边界经度
            north: 北边界纬度
            min_zoom: 最小缩放级别
            max_zoom: 最大缩放级别
            batch_size: 每批处理的任务数量，默认10000个
        """
        # 尝试导入 psutil，获取内存使用情况
        def get_memory_usage():
            """获取当前进程的内存使用情况"""
            try:
                import psutil
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
        filtered_processed_tiles = set()
        if self.enable_resume:
            # 动态加载进度文件中用户指定缩放级别的已处理瓦片
            logger.info("从进度文件加载用户指定缩放级别的已处理瓦片...")
            filtered_processed_tiles = self._load_processed_tiles_for_zoom_range(min_zoom, max_zoom)
            logger.info(f"已加载 {len(filtered_processed_tiles)} 个指定缩放级别的已处理瓦片")
            # 更新processed_tiles集合
            self.processed_tiles = filtered_processed_tiles
        
        for zoom in range(min_zoom, max_zoom + 1):
            # 检查是否已收到停止信号
            if self.stop_event.is_set():
                logger.info("收到停止信号，停止添加任务")
                break
            
            # 直接使用生成器方式计算瓦片，避免一次性生成所有瓦片坐标
            def tile_generator():
                """瓦片坐标生成器"""
                from ..tile_math import TileMath
                return TileMath.calculate_tiles_in_bbox(
                    west, south, east, north, zoom, is_tms=self.is_tms
                )
            
            # 计算当前缩放级别的瓦片数量
            xy_tiles = list(tile_generator())
            tile_count = len(xy_tiles)
            total_tiles += tile_count
            logger.info(f"缩放级别 {zoom}: 找到 {tile_count} 个瓦片")
            
            # 检查瓦片数量是否过大
            if tile_count > 1000000:
                logger.warning(f"缩放级别 {zoom}: 瓦片数量 ({tile_count}) 非常大，可能会占用大量内存")
                # 动态调整批次大小，瓦片数量越大，批次越小
                adjusted_batch_size = min(batch_size, max(1000, 1000000 // (tile_count // 100000 + 1)))
                logger.info(f"自动调整批次大小为: {adjusted_batch_size}")
            else:
                adjusted_batch_size = batch_size
            
            # 流式添加当前缩放级别的任务
            batch_count = 0
            zoom_added = 0
            zoom_skipped = 0
            for i, (x, y) in enumerate(xy_tiles):
                # 检查是否已收到停止信号
                if self.stop_event.is_set():
                    logger.info("收到停止信号，停止添加任务")
                    break
                
                # 检查瓦片是否已处理
                if self.enable_resume and (x, y, zoom) in self.processed_tiles:
                    logger.debug(f"[跳过已处理] 任务: z={zoom}, x={x}, y={y}")
                    self.skipped_count += 1
                    zoom_skipped += 1
                    skipped_tasks += 1
                    continue
                
                self.task_queue.put((x, y, zoom))
                logger.debug(f"添加任务: z={zoom}, x={x}, y={y}")
                added_tasks += 1
                zoom_added += 1
                batch_count += 1
                
                # 每批添加后短暂休眠，避免内存占用过高
                if batch_count >= adjusted_batch_size:
                    current_memory = get_memory_usage()
                    memory_delta = current_memory - memory_usage
                    logger.debug(f"缩放级别 {zoom}: 已添加 {batch_count} 个瓦片任务, 内存使用: {current_memory:.2f} MB (+{memory_delta:.2f} MB)")
                    
                    # 如果内存使用增长过快，增加休眠时间
                    if memory_delta > 100:  # 内存增长超过100MB
                        time.sleep(0.01)
                    else:
                        time.sleep(0.001)  # 进一步缩短休眠时间，提高大批量瓦片下载的效率
                    
                    batch_count = 0
                    # 更新内存使用基准
                    memory_usage = current_memory
            
            logger.info(f"缩放级别 {zoom}: 已添加 {zoom_added} 个任务，跳过 {zoom_skipped} 个已处理任务")
            
            # 清理当前缩放级别的瓦片列表，释放内存
            del xy_tiles
        
        # 设置总任务数为当前任务的总瓦片数
        self.total_tasks = total_tiles
        final_memory = get_memory_usage()
        logger.info(f"总计: {total_tiles} 个瓦片, 已添加 {added_tasks} 个任务, 跳过 {skipped_tasks} 个已处理任务, 最终内存使用: {final_memory:.2f} MB")

    def start(self):
        """
        开始下载任务
        """
        try:
            logger.info(f"开始下载，线程数={self.max_threads}，provider={self.provider.name}")
            
            # 计算实际需要的线程数
            # 基础线程数：不超过配置的最大线程数、不超过系统CPU核心数*2
            # 对于大批量下载，适当增加线程数以充分利用网络带宽
            cpu_cores = os.cpu_count() or 4
            base_threads = min(
                self.max_threads, 
                cpu_cores * 4,  # 对于网络IO密集型任务，使用更多线程
                64  # 增加最大线程数到64，提高大批量下载的并发能力
            )
            # 确保至少有1个线程
            actual_threads = max(1, base_threads)
            
            logger.info(f"实际使用线程数: {actual_threads}, CPU核心数: {cpu_cores}")
            
            # 预创建线程池，提高线程启动效率
            self.worker_threads = []
            
            # 创建线程
            for i in range(actual_threads):
                t = threading.Thread(target=self._worker, name=f"Downloader-{i+1}", daemon=True)
                self.worker_threads.append(t)
                t.start()
                # 移除线程启动延迟，使用批量启动方式

            # 批量启动完成后记录
            logger.info(f"已启动 {actual_threads} 个下载线程，将持续处理任务")
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

    def _worker(self):
        """
        工作线程，处理下载任务，增强异常处理和错误恢复
        """
        thread_name = threading.current_thread().name
        logger.info(f"{thread_name} 启动")
        
        processed_in_batch = 0  # 用于定期保存进度的计数器
        session = None  # 复用requests会话，减少连接开销
        
        # 确保time模块可用
        import time
        
        # 线程状态追踪
        thread_start_time = time.time()
        processed_tasks = 0
        failed_tasks = 0
        
        try:
            # 创建可复用的requests会话
            session = self._create_request_session()
            
            while not self.stop_event.is_set():
                # 先检查是否暂停
                if not self.pause_event.is_set():
                    logger.info(f"{thread_name} - 任务已暂停，等待恢复")
                    self.pause_event.wait()  # 无限等待直到恢复
                    if self.stop_event.is_set():
                        break
                    continue
                
                try:
                    # 获取任务，设置超时
                    task = self.task_queue.get(timeout=0.2)  # 进一步缩短超时时间，提高响应速度
                    if task is None:
                        try:
                            self._update_progress()
                            self.task_queue.task_done()
                        except ValueError:
                            # 避免 task_done() 被调用过多
                            logger.warning("任务队列可能已被清空，跳过 task_done() 调用")
                        continue
                    
                    x, y, z = task
                    logger.debug(f"{thread_name} 处理任务: z={z}, x={x}, y={y}")
                except Empty:
                    # 无任务，检查是否需要停止
                    if self.stop_event.is_set():
                        break
                    time.sleep(0.1)
                    continue
                
                try:
                    # 再次检查是否暂停
                    if not self.pause_event.is_set():
                        logger.info(f"{thread_name} - 任务已暂停，将任务放回队列")
                        # 将任务重新放回队列，以便在恢复时继续处理
                        self.task_queue.put((x, y, z))
                        try:
                            self.task_queue.task_done()
                        except ValueError:
                            # 避免 task_done() 被调用过多
                            logger.warning("任务队列可能已被清空，跳过 task_done() 调用")
                        # 无限等待直到恢复
                        self.pause_event.wait()
                        if self.stop_event.is_set():
                            break
                        continue
                    
                    # zoom 范围检查
                    if not (self.provider.min_zoom <= z <= self.provider.max_zoom):
                        logger.warning(
                            f"{thread_name} - zoom {z} 超出 [{self.provider.min_zoom}, {self.provider.max_zoom}]，跳过"
                        )
                        self._mark_tile_processed(x, y, z, 'skipped')
                        self._update_progress()
                        try:
                            self.task_queue.task_done()
                        except ValueError:
                            # 避免 task_done() 被调用过多
                            logger.warning("任务队列可能已被清空，跳过 task_done() 调用")
                        processed_in_batch += 1
                        continue

                    # 只有非MBTiles模式才检查文件是否存在和创建目录
                    file_path = None
                    if not self.is_mbtiles:
                        # 检查文件是否已存在
                        file_path = self.provider.get_tile_path(x, y, z, self.output_dir)
                        if file_path.exists():
                            logger.debug(f"{thread_name} - [跳过] 已存在: {file_path}")
                            self._mark_tile_processed(x, y, z, 'skipped')
                            self._update_progress()
                            try:
                                self.task_queue.task_done()
                            except ValueError:
                                # 避免 task_done() 被调用过多
                                logger.warning("任务队列可能已被清空，跳过 task_done() 调用")
                            processed_in_batch += 1
                            continue

                        # 创建父目录
                        ensure_directory(file_path.parent)
                    
                    # 获取下载URL
                    url = self.provider.get_tile_url(x, y, z)
                    
                    ok = False
                    for attempt in range(self.retries):
                        # 检查是否需要停止
                        if self.stop_event.is_set():
                            logger.info(f"{thread_name} - 收到停止信号，取消当前任务")
                            break
                        
                        # 检查是否暂停
                        if not self.pause_event.is_set():
                            logger.info(f"{thread_name} - 任务已暂停，等待恢复")
                            self.pause_event.wait(timeout=1)  # 等待直到恢复或超时
                            
                            # 如果超时后仍然暂停，跳过当前任务
                            if not self.pause_event.is_set():
                                logger.info(f"{thread_name} - 任务仍然暂停，跳过当前任务")
                                self._mark_tile_processed(x, y, z, 'skipped')
                                self._update_progress()
                                try:
                                    self.task_queue.task_done()
                                except ValueError:
                                    # 避免 task_done() 被调用过多
                                    logger.warning("任务队列可能已被清空，跳过 task_done() 调用")
                                processed_in_batch += 1
                                return
                        
                        try:
                            logger.debug(f"{thread_name} - 下载: {url} (尝试 {attempt+1}/{self.retries})")
                            
                            # 使用已创建的会话对象
                            response = None
                            try:
                                # 分块下载，以便在下载过程中检查暂停/停止信号
                                response = session.get(url, stream=True, timeout=5, allow_redirects=True)
                                
                                # 检查响应状态
                                if response.status_code != 200:
                                    logger.warning(f"{thread_name} - [HTTP {response.status_code}] {url}")
                                    if response.status_code in [403, 404]:
                                        # 403禁止访问或404不存在，直接跳过
                                        logger.warning(f"{thread_name} - 永久错误，停止重试: {url}")
                                        break
                                    continue
                                
                                # 验证响应内容
                                content_type = response.headers.get('Content-Type', '')
                                if not ('image' in content_type or 'jpeg' in content_type or 'png' in content_type):
                                    logger.warning(f"{thread_name} - 非图片响应: {url}, Content-Type: {content_type}")
                                    continue
                                
                                # 记录下载开始时间
                                download_start_time = time.time()
                                
                                # 分块读取响应内容，增大chunk size提高下载速度
                                tile_data = b''
                                for chunk in response.iter_content(chunk_size=8192):  # 增大chunk size到8KB
                                    # 检查是否需要停止
                                    if self.stop_event.is_set():
                                        logger.info(f"{thread_name} - 收到停止信号，取消当前下载")
                                        ok = False
                                        break
                                    
                                    # 检查是否暂停
                                    if not self.pause_event.is_set():
                                        logger.info(f"{thread_name} - 任务已暂停，将任务放回队列")
                                        # 将任务重新放回队列，以便在恢复时继续处理
                                        self.task_queue.put((x, y, z))
                                        try:
                                            self.task_queue.task_done()
                                        except ValueError:
                                            # 避免 task_done() 被调用过多
                                            logger.warning("任务队列可能已被清空，跳过 task_done() 调用")
                                        # 无限等待直到恢复
                                        self.pause_event.wait()
                                        if self.stop_event.is_set():
                                            return
                                        ok = False
                                        break
                                    
                                    if chunk:
                                        tile_data += chunk
                                
                                # 如果在分块读取过程中被停止，退出循环
                                if self.stop_event.is_set():
                                    break
                                
                                # 计算下载时间
                                download_duration = time.time() - download_start_time
                                
                                # 验证下载的数据
                                if len(tile_data) == 0:
                                    logger.error(f"{thread_name} - 下载数据为空: {url}")
                                    continue
                                
                                total_bytes = len(tile_data)
                                
                                if self.is_mbtiles:
                                    # 保存到MBTiles数据库
                                    try:
                                        # MBTiles中的tile_row是从顶部开始计数的，需要转换
                                        mbtiles_row = (2 ** z) - 1 - y
                                        
                                        max_retries = 3
                                        retry_delay = 0.5  # 秒
                                        
                                        for attempt in range(max_retries):
                                            try:
                                                if self.enable_sharding:
                                                    # 分库模式：根据缩放级别选择MBTiles文件
                                                    mbtiles_conn = self._get_mbtiles_connection(z)
                                                    # 使用锁保护MBTiles操作，避免多个线程同时使用游标
                                                    with self.mbtiles_lock:
                                                        # 直接使用连接执行SQL，避免递归使用同一个游标
                                                        mbtiles_conn.execute(
                                                            '''INSERT OR REPLACE INTO tiles 
                                                            (zoom_level, tile_column, tile_row, tile_data) 
                                                            VALUES (?, ?, ?, ?)''',
                                                            (z, x, mbtiles_row, tile_data)
                                                        )
                                                        
        
                                                else:
                                                    # 单库模式
                                                    # 使用锁保护MBTiles操作，避免多个线程同时使用游标
                                                    with self.mbtiles_lock:
                                                        # 直接使用连接执行SQL，避免递归使用同一个游标
                                                        self.mbtiles_conn.execute(
                                                            '''INSERT OR REPLACE INTO tiles 
                                                            (zoom_level, tile_column, tile_row, tile_data) 
                                                            VALUES (?, ?, ?, ?)''',
                                                            (z, x, mbtiles_row, tile_data)
                                                        )
                                                        

                                            
                                                logger.info(f"{thread_name} - 下载成功: MBTiles [{z}/{x}/{y}] ({total_bytes} 字节) - 耗时: {download_duration:.3f}秒")
                                                break
                                            except sqlite3.OperationalError as e:
                                                if "database is locked" in str(e):
                                                    logger.warning(f"{thread_name} - MBTiles数据库被锁定，尝试重试 ({attempt+1}/{max_retries})...")
                                                    if attempt < max_retries - 1:
                                                        import time
                                                        time.sleep(retry_delay)
                                                        retry_delay *= 2  # 指数退避
                                                    else:
                                                        logger.error(f"{thread_name} - MBTiles保存失败: 经过 {max_retries} 次尝试后仍然无法获取数据库锁")
                                                        raise
                                                else:
                                                    raise
                                            except Exception as e:
                                                logger.error(f"{thread_name} - MBTiles保存失败: {e}")
                                                # 尝试重新初始化MBTiles连接
                                                try:
                                                    self._init_mbtiles()
                                                    logger.info(f"{thread_name} - MBTiles连接已重新初始化")
                                                except Exception as init_error:
                                                    logger.error(f"{thread_name} - MBTiles重新初始化失败: {init_error}")
                                                continue
                                    except Exception as e:
                                        logger.error(f"{thread_name} - MBTiles保存失败: {e}")
                                        continue
                                else:
                                    # 保存到文件系统
                                    try:
                                        with open(file_path, "wb") as f:
                                            f.write(tile_data)
                                        
                                        logger.info(f"{thread_name} - 下载成功: {file_path} ({total_bytes} 字节) - 耗时: {download_duration:.3f}秒")
                                    except IOError as e:
                                        logger.error(f"{thread_name} - 文件写入错误 {file_path} - {e}")
                                        # 尝试创建目录
                                        try:
                                            ensure_directory(file_path.parent)
                                            # 再次尝试写入
                                            with open(file_path, "wb") as f:
                                                f.write(tile_data)
                                            logger.info(f"{thread_name} - 重试文件写入成功: {file_path}")
                                        except Exception as retry_error:
                                            logger.error(f"{thread_name} - 重试文件写入失败: {retry_error}")
                                            continue
                                
                                self.total_bytes += total_bytes  # 更新总字节数
                                self._mark_tile_processed(x, y, z, 'success')
                                processed_tasks += 1
                                
                                # 记录性能数据
                                if self.performance_monitor:
                                    self.performance_monitor.record_download(download_duration, total_bytes)
                                
                                # 事务管理：每1000个瓦片提交一次事务
                                if self.is_mbtiles:
                                    self.transaction_counter += 1
                                    
                                    # 达到批量大小，提交事务
                                    if self.transaction_counter % self.transaction_batch_size == 0:
                                        if self.enable_sharding:
                                            mbtiles_conn = self._get_mbtiles_connection(z)
                                            with self.mbtiles_lock:
                                                mbtiles_conn.commit()
                                                logger.debug(f"批量提交事务: {self.transaction_batch_size} 个瓦片")
                                        else:
                                            with self.mbtiles_lock:
                                                self.mbtiles_conn.commit()
                                                logger.debug(f"批量提交事务: {self.transaction_batch_size} 个瓦片")
                                
                                ok = True
                                break
                            finally:
                                if response:
                                    response.close()
                        except requests.exceptions.ConnectionError as e:
                            logger.error(f"{thread_name} - 连接错误 {url} - {e}")
                            # 尝试重建会话
                            try:
                                session = self._create_request_session()
                                logger.info(f"{thread_name} - 会话已重建")
                            except Exception as session_error:
                                logger.error(f"{thread_name} - 会话重建失败: {session_error}")
                        except requests.exceptions.Timeout as e:
                            logger.error(f"{thread_name} - 超时错误 {url} - {e}")
                        except requests.exceptions.RequestException as e:
                            logger.error(f"{thread_name} - 请求错误 {url} - {e}")
                        except IOError as e:
                            logger.error(f"{thread_name} - 文件写入错误 {file_path} - {e}")
                        except Exception as e:
                            logger.error(f"{thread_name} - 未知错误 {url} - {e}")
                            import traceback
                            traceback.print_exc()
                        
                        # 检查是否需要停止
                        if self.stop_event.is_set():
                            logger.info(f"{thread_name} - 收到停止信号，停止重试")
                            break
                        
                        # 指数退避重试，增加最大延迟限制
                        retry_delay = min(
                            self.delay * (2 ** attempt) * (0.5 + 0.5 * (hash(url) % 2)),
                            5  # 进一步减少最大延迟到5秒，以便更快响应暂停/停止信号
                        )
                        
                        # 在重试延迟期间定期检查暂停/停止信号
                        start_time = time.time()
                        while time.time() - start_time < retry_delay:
                            if self.stop_event.is_set():
                                logger.info(f"{thread_name} - 收到停止信号，停止重试")
                                break
                            if not self.pause_event.is_set():
                                logger.info(f"{thread_name} - 任务已暂停，将任务放回队列")
                                # 将任务重新放回队列，以便在恢复时继续处理
                                self.task_queue.put((x, y, z))
                                try:
                                    self.task_queue.task_done()
                                except ValueError:
                                    # 避免 task_done() 被调用过多
                                    logger.warning("任务队列可能已被清空，跳过 task_done() 调用")
                                # 无限等待直到恢复
                                self.pause_event.wait()
                                if self.stop_event.is_set():
                                    return
                                return
                            time.sleep(0.1)

                    if not ok:
                        logger.error(f"{thread_name} - 下载失败: {url}")
                        self._mark_tile_processed(x, y, z, 'failed')
                        failed_tasks += 1

                    # 移除任务完成后的延迟，提高下载速度
                except Exception as e:
                    logger.error(f"{thread_name} - 任务处理错误: z={z}, x={x}, y={y} - {e}")
                    import traceback
                    traceback.print_exc()
                    self._mark_tile_processed(x, y, z, 'failed')
                    failed_tasks += 1
                    self._update_progress()
                    try:
                        self.task_queue.task_done()
                    except ValueError:
                        # 避免 task_done() 被调用过多
                        logger.warning("任务队列可能已被清空，跳过 task_done() 调用")
                    processed_in_batch += 1
                else:
                    # 更新进度
                    self._update_progress()
                    # 标记任务完成 - 只调用一次
                    try:
                        self.task_queue.task_done()
                    except ValueError:
                        # 避免 task_done() 被调用过多
                        logger.warning("任务队列可能已被清空，跳过 task_done() 调用")
                    processed_in_batch += 1
                
                # 每处理200个瓦片保存一次进度，减少IO操作
                if self.enable_resume and processed_in_batch >= 200:
                    self._save_progress()
                    processed_in_batch = 0
                    
        except Exception as e:
            logger.error(f"{thread_name} - 线程内部异常: {e}")
            import traceback
            traceback.print_exc()
            # 确保线程不会因异常退出
            time.sleep(0.5)
        
        # 线程结束前保存进度
        if self.enable_resume:
            try:
                self._save_progress()
                logger.info(f"{thread_name} - 线程结束前保存进度")
            except Exception as save_error:
                logger.error(f"{thread_name} - 线程结束前保存进度失败: {save_error}")
        
        # 关闭会话，释放资源
        if session:
            try:
                session.close()
            except Exception as close_error:
                logger.error(f"{thread_name} - 关闭会话失败: {close_error}")
        
        # 记录线程统计信息
        thread_duration = time.time() - thread_start_time
        logger.info(f"{thread_name} 结束 - 运行时间: {thread_duration:.2f} 秒, 处理任务: {processed_tasks}, 失败任务: {failed_tasks}")
    
    def _create_request_session(self):
        """
        创建并配置请求会话，增强错误处理和网络性能
        """
        session = requests.Session()
        
        # 优化请求头
        session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
            'Accept': 'image/*',
            'Accept-Encoding': 'gzip, deflate',
            'Connection': 'keep-alive'
        })
        
        # 禁用代理，提高连接速度
        session.proxies = {'http': None, 'https': None}
        
        # 禁用重定向跟随，减少不必要的网络请求
        session.max_redirects = 3
        
        # 配置连接超时
        session.timeout = 10
        
        # 启用连接池，优化参数
        adapter = requests.adapters.HTTPAdapter(
            pool_connections=500,  # 进一步增加连接池大小
            pool_maxsize=500,      # 进一步增加最大连接数
            pool_block=False,      # 非阻塞模式，避免连接池满时阻塞
            max_retries=requests.adapters.Retry(
                total=3,            # 总重试次数
                backoff_factor=0.5,  # 退避因子
                status_forcelist=[429, 500, 502, 503, 504],  # 需要重试的状态码
                allowed_methods=["GET"]  # 只对GET请求重试
            )
        )
        
        # 挂载适配器
        session.mount('http://', adapter)
        session.mount('https://', adapter)
        
        logger.debug("创建新的请求会话，配置连接池和重试机制")
        return session
    
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
    
    def _get_mbtiles_connection(self, zoom):
        """
        根据缩放级别获取对应的MBTiles连接
        
        Args:
            zoom: 缩放级别
            
        Returns:
            sqlite3.Connection: MBTiles数据库连接
        """
        if zoom not in self.mbtiles_connections:
            # 构建当前缩放级别的MBTiles文件路径
            mbtiles_path = Path(str(self.output_path).replace("{z}", str(zoom)))
            self.mbtiles_paths[zoom] = mbtiles_path
            
            # 确保输出目录存在
            ensure_directory(mbtiles_path.parent)
            
            # 创建并初始化MBTiles数据库
            import sqlite3
            import time
            
            max_retries = 5
            retry_delay = 1  # 秒
            
            for attempt in range(max_retries):
                try:
                    conn = sqlite3.connect(mbtiles_path, check_same_thread=False)
                    
                    # 优化SQLite配置
                    conn.execute('PRAGMA journal_mode=WAL;')
                    conn.execute('PRAGMA cache_size=500000;')  # 约500MB缓存
                    conn.execute('PRAGMA synchronous=NORMAL;')
                    conn.execute('PRAGMA enable_shared_cache=1;')
                    conn.execute('PRAGMA busy_timeout=30000;')  # 30秒
                    
                    # 创建表结构
                    cursor = conn.cursor()
                    cursor.execute('''
                        CREATE TABLE IF NOT EXISTS tiles (
                            zoom_level INTEGER,
                            tile_column INTEGER,
                            tile_row INTEGER,
                            tile_data BLOB,
                            PRIMARY KEY (zoom_level, tile_column, tile_row)
                        )
                    ''')
                    cursor.execute('''
                        CREATE TABLE IF NOT EXISTS metadata (
                            name TEXT,
                            value TEXT,
                            PRIMARY KEY (name)
                        )
                    ''')
                    
                    # 插入基本metadata
                    metadata = [
                        ('name', 'TileHarvester'),
                        ('type', 'baselayer'),
                        ('version', '1.0'),
                        ('description', f'Generated by TileHarvester for zoom level {zoom}'),
                        ('format', self.provider.extension),
                        ('scheme', self.scheme),
                    ]
                    cursor.executemany(
                        'INSERT OR REPLACE INTO metadata (name, value) VALUES (?, ?)',
                        metadata
                    )
                    
                    # 提交事务
                    conn.commit()
                    
                    # 存储连接
                    self.mbtiles_connections[zoom] = conn
                    
                    logger.info(f"初始化MBTiles数据库: {mbtiles_path}")
                    return conn
                except sqlite3.OperationalError as e:
                    if "database is locked" in str(e):
                        logger.warning(f"MBTiles数据库被锁定，尝试重试 ({attempt+1}/{max_retries})...")
                        if 'conn' in locals():
                            try:
                                conn.close()
                            except:
                                pass
                        time.sleep(retry_delay)
                        retry_delay *= 2  # 指数退避
                    else:
                        logger.error(f"MBTiles数据库初始化失败: {e}")
                        if 'conn' in locals():
                            try:
                                conn.close()
                            except:
                                pass
                        raise
                except Exception as e:
                    logger.error(f"MBTiles数据库初始化失败: {e}")
                    if 'conn' in locals():
                        try:
                            conn.close()
                        except:
                            pass
                    raise
            
            # 所有重试都失败
            logger.error(f"MBTiles数据库初始化失败: 经过 {max_retries} 次尝试后仍然无法获取数据库锁")
            raise Exception(f"经过 {max_retries} 次尝试后仍然无法获取数据库锁")
        
        return self.mbtiles_connections[zoom]
    
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
        if self.is_mbtiles:
            if self.enable_sharding:
                # 关闭所有分库连接
                for zoom, conn in self.mbtiles_connections.items():
                    try:
                        conn.commit()
                        conn.close()
                        logger.debug(f"关闭缩放级别 {zoom} 的MBTiles连接")
                    except Exception as e:
                        logger.error(f"关闭MBTiles连接失败: {e}")
                self.mbtiles_connections.clear()
            else:
                # 关闭单库连接
                if hasattr(self, 'mbtiles_conn') and self.mbtiles_conn:
                    try:
                        self.mbtiles_conn.commit()
                        self.mbtiles_conn.close()
                        logger.debug("关闭MBTiles连接")
                    except Exception as e:
                        logger.error(f"关闭MBTiles连接失败: {e}")
        
        # 关闭进度数据库连接
        if self.progress_conn:
            try:
                self.progress_conn.commit()
                self.progress_conn.close()
                logger.debug("关闭进度数据库连接")
            except Exception as e:
                logger.error(f"关闭进度数据库连接失败: {e}")
            self.progress_conn = None
            self.progress_cursor = None
        
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
        if self.is_mbtiles:
            if self.enable_sharding:
                # 提交所有分库连接的事务
                for zoom, conn in self.mbtiles_connections.items():
                    try:
                        conn.commit()
                        logger.debug(f"提交缩放级别 {zoom} 的MBTiles事务")
                    except Exception as e:
                        logger.error(f"提交MBTiles事务失败: {e}")
            else:
                # 提交单库连接的事务
                if hasattr(self, 'mbtiles_conn') and self.mbtiles_conn:
                    try:
                        self.mbtiles_conn.commit()
                        logger.debug("提交MBTiles事务")
                    except Exception as e:
                        logger.error(f"提交MBTiles事务失败: {e}")
        
        # 重置事务计数器
        self.transaction_counter = 0
        logger.debug("重置事务计数器")
