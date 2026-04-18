# src/downloader/progress.py

import time
import sqlite3
import threading
from pathlib import Path
from typing import Set, Tuple, Optional
from loguru import logger
from ..config import config_manager


class ProgressManager:
    """
    进度管理器：负责断点续传和进度保存
    """

    def __init__(self, output_dir: str):
        """
        初始化进度管理器
        
        Args:
            output_dir: 输出目录
        """
        self.output_dir = output_dir
        # 初始化进度数据库路径
        if output_dir.endswith('.mbtiles'):
            # MBTiles格式，进度数据库放在与MBTiles文件同一目录下
            mbtiles_path = Path(output_dir)
            mbtiles_name = mbtiles_path.stem
            self.progress_path = mbtiles_path.parent / f"{mbtiles_name}.progress.db"
        else:
            # 普通目录格式，进度数据库在output_dir下
            self.progress_path = Path(output_dir) / ".progress.db"
        self.thread_local = threading.local()
        self.using_database_mode = False
        self.processed_tiles = set()

    def _get_connection(self):
        """
        获取当前线程的数据库连接
        
        Returns:
            sqlite3.Connection: 当前线程的数据库连接
        """
        if not hasattr(self.thread_local, 'conn'):
            # 确保进度目录存在
            from .utils import ensure_directory
            # 对于MBTiles格式，需要特殊处理：如果output_dir是文件路径，进度数据库应该在同一目录
            if self.output_dir.endswith('.mbtiles'):
                # 进度数据库放在与MBTiles文件同一目录下
                progress_dir = Path(self.output_dir).parent
                ensure_directory(progress_dir)
                # 进度数据库文件名使用MBTiles文件名前缀
                mbtiles_name = Path(self.output_dir).stem
                self.progress_path = progress_dir / f"{mbtiles_name}.progress.db"
            else:
                # 普通目录格式，进度数据库在output_dir下
                ensure_directory(self.progress_path.parent)
            
            # 为当前线程创建新的数据库连接
            self.thread_local.conn = sqlite3.connect(str(self.progress_path))
            cursor = self.thread_local.conn.cursor()
            
            # 创建进度表
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS processed_tiles (
                    x INTEGER,
                    y INTEGER,
                    z INTEGER,
                    status TEXT,
                    timestamp REAL,
                    PRIMARY KEY (x, y, z)
                )
            ''')
            cursor.execute('''
                CREATE INDEX IF NOT EXISTS idx_processed_tiles_z 
                ON processed_tiles (z)
            ''')
            self.thread_local.conn.commit()
            logger.debug(f"为线程 {threading.get_ident()} 创建数据库连接")
        return self.thread_local.conn

    def initialize(self):
        """
        初始化进度数据库
        """
        # 为当前线程初始化数据库连接
        try:
            conn = self._get_connection()
            logger.info(f"进度数据库初始化成功: {self.progress_path}")
        except Exception as e:
            logger.error(f"初始化进度数据库失败: {e}")

    def load_processed_tiles_for_zoom_range(self, min_zoom: int, max_zoom: int) -> Set[Tuple[int, int, int]]:
        """
        加载指定缩放级别范围的已处理瓦片
        
        Args:
            min_zoom: 最小缩放级别
            max_zoom: 最大缩放级别
            
        Returns:
            Set[Tuple[int, int, int]]: 已处理的瓦片集合
        """
        processed = set()

        try:
            # 获取当前线程的数据库连接
            conn = self._get_connection()
            cursor = conn.cursor()
            
            # 批量查询指定缩放级别的已处理瓦片
            query = '''
                SELECT x, y, z FROM processed_tiles 
                WHERE z >= ? AND z <= ?
            '''
            cursor.execute(query, (min_zoom, max_zoom))
            for row in cursor.fetchall():
                processed.add((row[0], row[1], row[2]))
            logger.info(f"从数据库加载了 {len(processed)} 个已处理瓦片")
        except Exception as e:
            logger.error(f"加载已处理瓦片失败: {e}")

        return processed

    def save_progress(self, processed_tiles: Set[Tuple[int, int, int]] = None):
        """
        保存进度到数据库
        
        Args:
            processed_tiles: 已处理的瓦片集合，如果为None则使用当前内存中的集合
        """
        try:
            # 获取当前线程的数据库连接
            conn = self._get_connection()
            
            # 如果没有提供processed_tiles，则使用当前内存中的集合
            tiles_to_save = processed_tiles if processed_tiles is not None else self.processed_tiles
            
            if not tiles_to_save:
                return
            
            # 批量插入已处理瓦片
            batch_size = 1000
            current_time = time.time()
            batch = []

            for tile in tiles_to_save:
                x, y, z = tile
                batch.append((x, y, z, 'success', current_time))
                
                if len(batch) >= batch_size:
                    self._insert_batch(batch, conn)
                    batch = []

            if batch:
                self._insert_batch(batch, conn)

            conn.commit()
            logger.info(f"进度保存成功，已处理 {len(tiles_to_save)} 个瓦片")
        except Exception as e:
            logger.error(f"保存进度失败: {e}")

    def _insert_batch(self, batch: list, conn):
        """
        批量插入瓦片到数据库
        
        Args:
            batch: 瓦片数据批次
            conn: 数据库连接
        """
        if not batch:
            return

        try:
            cursor = conn.cursor()
            cursor.executemany(
                '''INSERT OR IGNORE INTO processed_tiles 
                (x, y, z, status, timestamp) 
                VALUES (?, ?, ?, ?, ?)''',
                batch
            )
            # 每写入一批数据后，短暂休眠，避免数据库压力过大
            time.sleep(0.005)
        except sqlite3.OperationalError as e:
            if "database is locked" in str(e):
                logger.warning(f"进度数据库被锁定，尝试重试...")
                time.sleep(0.1)
                # 重试插入
                try:
                    cursor.executemany(
                        '''INSERT OR IGNORE INTO processed_tiles 
                        (x, y, z, status, timestamp) 
                        VALUES (?, ?, ?, ?, ?)''',
                        batch
                    )
                except Exception as retry_e:
                    logger.error(f"重试批量插入瓦片失败: {retry_e}")
            else:
                logger.error(f"批量插入瓦片失败: {e}")
        except Exception as e:
            logger.error(f"批量插入瓦片失败: {e}")

    def mark_tile_processed(self, x: int, y: int, z: int, status: str):
        """
        标记瓦片为已处理
        
        Args:
            x: 瓦片x坐标
            y: 瓦片y坐标
            z: 缩放级别
            status: 处理状态
        """
        tile_key = (x, y, z)

        # 只在内存中维护processed_tiles集合，不在这里写入数据库
        # 批量保存由调用方负责
        if tile_key not in self.processed_tiles:
            self.processed_tiles.add(tile_key)
            
            # 当内存中的瓦片数量达到阈值时，触发批量保存
            max_tiles_in_memory = config_manager.get("memory.max_tiles_in_memory", 100000)  # 10万个瓦片约占用2.4MB内存
            if len(self.processed_tiles) >= max_tiles_in_memory:
                # 只在第一次达到阈值时打印消息
                if not hasattr(self, 'memory_threshold_reached') or not self.memory_threshold_reached:
                    logger.info(f"内存中的瓦片数量达到阈值 ({max_tiles_in_memory})，触发批量保存")
                    self.memory_threshold_reached = True
                # 执行批量保存
                self.save_progress()
                # 清空内存中的集合，只保留最近处理的瓦片
                # 保留10%的瓦片作为缓冲，避免频繁保存
                self.processed_tiles = set(list(self.processed_tiles)[-int(max_tiles_in_memory * 0.1):])
                logger.debug(f"清空内存中的瓦片集合，保留 {int(max_tiles_in_memory * 0.1)} 个瓦片")

    def close(self):
        """
        关闭进度数据库连接
        """
        # 关闭当前线程的数据库连接
        if hasattr(self.thread_local, 'conn'):
            try:
                self.thread_local.conn.commit()
                self.thread_local.conn.close()
                logger.debug("关闭进度数据库连接")
            except Exception as e:
                logger.error(f"关闭进度数据库连接失败: {e}")
            delattr(self.thread_local, 'conn')
