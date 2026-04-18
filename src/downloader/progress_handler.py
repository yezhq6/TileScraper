# src/downloader/progress_handler.py

import threading
import sqlite3
from pathlib import Path
from loguru import logger

from .utils import ensure_directory


class ProgressHandler:
    """
    进度处理器：负责下载进度的管理和保存
    """

    def __init__(self, downloader):
        """
        初始化进度处理器
        
        Args:
            downloader: TileDownloader 实例
        """
        self.downloader = downloader
        self.output_dir = downloader.output_dir
        self.enable_resume = downloader.enable_resume
        
        # 进度相关
        self.processed_tiles = set()
        self.progress_file = None
        
        # 线程本地存储
        self.thread_local = threading.local()
        
        # 初始化进度管理器
        if self.enable_resume:
            self.initialize()

    def _get_connection(self):
        """
        获取或创建线程本地的数据库连接
        
        Returns:
            sqlite3.Connection: 数据库连接
        """
        if not hasattr(self.thread_local, 'conn'):
            # 为当前线程创建新的数据库连接
            conn = sqlite3.connect(str(self.progress_file), check_same_thread=False)
            self.thread_local.conn = conn
            logger.debug(f"为线程 {threading.current_thread().name} 创建数据库连接")
        return self.thread_local.conn

    def initialize(self):
        """
        初始化进度数据库
        """
        try:
            # 确定进度文件路径
            if self.downloader.is_mbtiles:
                # MBTiles 格式：在同一目录下创建 .progress.db
                progress_dir = Path(self.output_dir).parent
                progress_file = progress_dir / f"{Path(self.output_dir).stem}.progress.db"
            else:
                # 目录格式：在输出目录中创建 progress.db
                progress_file = Path(self.output_dir) / "progress.db"
            
            self.progress_file = progress_file
            
            # 确保目录存在
            ensure_directory(progress_file.parent)
            
            # 为当前线程创建连接并初始化数据库
            conn = self._get_connection()
            cursor = conn.cursor()
            
            # 创建处理状态表
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS processed_tiles (
                    x INTEGER,
                    y INTEGER,
                    z INTEGER,
                    status TEXT,
                    PRIMARY KEY (x, y, z)
                )
            ''')
            
            # 启用 WAL 模式以提高并发性能
            conn.execute('PRAGMA journal_mode=WAL;')
            conn.execute('PRAGMA synchronous=NORMAL;')
            conn.commit()
            
            logger.info(f"进度数据库初始化成功: {progress_file}")
        except Exception as e:
            logger.error(f"初始化进度数据库失败: {e}")
            # 失败时禁用断点续传
            self.enable_resume = False

    def load_processed_tiles_for_zoom_range(self, min_zoom, max_zoom):
        """
        加载指定缩放级别范围的已处理瓦片
        
        Args:
            min_zoom: 最小缩放级别
            max_zoom: 最大缩放级别
            
        Returns:
            set: 已处理的瓦片集合，每个元素是 (x, y, z) 元组
        """
        processed = set()
        
        if not self.enable_resume or not self.progress_file:
            return processed
        
        try:
            # 为当前线程获取连接
            conn = self._get_connection()
            cursor = conn.cursor()
            
            # 批量查询指定缩放级别的已处理瓦片
            query = '''
                SELECT x, y, z FROM processed_tiles 
                WHERE z >= ? AND z <= ?
            '''
            cursor.execute(query, (min_zoom, max_zoom))
            
            # 构建已处理瓦片集合
            for row in cursor.fetchall():
                processed.add((row[0], row[1], row[2]))
            
            logger.debug(f"加载了 {len(processed)} 个已处理瓦片")
        except Exception as e:
            logger.error(f"加载已处理瓦片失败: {e}")
        
        return processed

    def __init__(self, downloader):
        """
        初始化进度处理器
        
        Args:
            downloader: TileDownloader 实例
        """
        self.downloader = downloader
        self.output_dir = downloader.output_dir
        self.enable_resume = downloader.enable_resume
        
        # 进度相关
        self.processed_tiles = set()
        self.progress_file = None
        
        # 线程本地存储
        self.thread_local = threading.local()
        
        # 批量处理相关
        self.batch_size = 100  # 批量处理大小
        self.batch_buffer = []  # 批量处理缓冲区
        
        # 初始化进度管理器
        if self.enable_resume:
            self.initialize()

    def mark_tile_processed(self, x, y, z, status):
        """
        标记瓦片为已处理
        
        Args:
            x: 瓦片x坐标
            y: 瓦片y坐标
            z: 缩放级别
            status: 处理状态，'success'、'failed'或'skipped'
        """
        if not self.enable_resume or not self.progress_file:
            return
        
        # 添加到内存集合
        tile_key = (x, y, z)
        if tile_key not in self.processed_tiles:
            self.processed_tiles.add(tile_key)
            
            # 添加到批处理缓冲区
            self.batch_buffer.append((x, y, z, status))
            
            # 当缓冲区达到批量大小或每100个瓦片时，执行批量处理
            if len(self.batch_buffer) >= self.batch_size or self.downloader.transaction_counter % 100 == 0:
                self._batch_process_tiles()

    def _batch_process_tiles(self):
        """
        批量处理瓦片
        """
        if not self.batch_buffer:
            return
        
        max_retries = 5
        retry_delay = 0.1
        
        for attempt in range(max_retries):
            try:
                # 为当前线程获取连接
                conn = self._get_connection()
                cursor = conn.cursor()
                
                # 批量插入或更新已处理的瓦片
                cursor.executemany(
                    '''INSERT OR REPLACE INTO processed_tiles (x, y, z, status)
                    VALUES (?, ?, ?, ?)''',
                    self.batch_buffer
                )
                
                # 提交事务
                conn.commit()
                
                # 记录处理的瓦片数量
                processed_count = len(self.batch_buffer)
                
                # 清空缓冲区
                self.batch_buffer = []
                
                logger.debug(f"批量处理 {processed_count} 个瓦片")
                return
            except sqlite3.OperationalError as e:
                if 'database is locked' in str(e) and attempt < max_retries - 1:
                    logger.debug(f"数据库锁定，重试 {attempt+1}/{max_retries}")
                    import time
                    time.sleep(retry_delay)
                    retry_delay *= 1.5  # 指数退避
                else:
                    logger.error(f"批量处理瓦片失败: {e}")
                    break
            except Exception as e:
                logger.error(f"批量处理瓦片失败: {e}")
                break

    def save_progress(self, processed_tiles=None):
        """
        保存进度到数据库
        
        Args:
            processed_tiles: 已处理的瓦片集合，如果为None则使用内存中的集合
        """
        if not self.enable_resume or not self.progress_file:
            return
        
        # 首先处理批处理缓冲区中的剩余瓦片
        if self.batch_buffer:
            self._batch_process_tiles()
        
        max_retries = 5
        retry_delay = 0.1
        
        for attempt in range(max_retries):
            try:
                # 为当前线程获取连接
                conn = self._get_connection()
                cursor = conn.cursor()
                
                # 如果没有提供瓦片集合，使用内存中的集合
                if processed_tiles is None:
                    processed_tiles = self.processed_tiles
                
                # 批量插入或更新已处理的瓦片，使用更小的批处理大小
                batch_size = 1000  # 增大批处理大小
                batch = []
                
                for x, y, z in processed_tiles:
                    batch.append((x, y, z, 'success'))
                    
                    if len(batch) >= batch_size:
                        cursor.executemany(
                            '''INSERT OR REPLACE INTO processed_tiles (x, y, z, status)
                            VALUES (?, ?, ?, ?)''',
                            batch
                        )
                        conn.commit()
                        batch = []
                
                # 处理剩余的瓦片
                if batch:
                    cursor.executemany(
                        '''INSERT OR REPLACE INTO processed_tiles (x, y, z, status)
                        VALUES (?, ?, ?, ?)''',
                        batch
                    )
                    conn.commit()
                
                logger.info(f"进度保存成功，共 {len(processed_tiles)} 个瓦片")
                return
            except sqlite3.OperationalError as e:
                if 'database is locked' in str(e) and attempt < max_retries - 1:
                    logger.debug(f"数据库锁定，重试 {attempt+1}/{max_retries}")
                    import time
                    time.sleep(retry_delay)
                    retry_delay *= 1.5  # 指数退避
                else:
                    logger.error(f"保存进度失败: {e}")
                    break
            except Exception as e:
                logger.error(f"保存进度失败: {e}")
                break

    def close(self):
        """
        关闭进度数据库连接
        """
        # 线程本地连接会在线程结束时自动关闭
        # 这里可以添加其他清理逻辑
        logger.debug("进度数据库连接已关闭")
