# src/downloader/connection_pool.py

import threading
import sqlite3
import time
from pathlib import Path
from typing import Dict, Optional
from loguru import logger

class ConnectionPool:
    """
    数据库连接池管理
    """
    
    def __init__(self):
        """
        初始化连接池
        """
        self.connections: Dict[tuple, sqlite3.Connection] = {}
        self.lock = threading.RLock()
    
    def get_connection(self, key: tuple, path: Path) -> sqlite3.Connection:
        """
        获取数据库连接
        
        Args:
            key: 连接标识符（线程ID, 缩放级别）
            path: 数据库文件路径
        
        Returns:
            sqlite3.Connection: 数据库连接
        """
        with self.lock:
            if key not in self.connections:
                conn = self._create_connection(path)
                self.connections[key] = conn
        return self.connections[key]
    
    def _create_connection(self, path: Path) -> sqlite3.Connection:
        """
        创建数据库连接
        
        Args:
            path: 数据库文件路径
        
        Returns:
            sqlite3.Connection: 数据库连接
        """
        max_retries = 5
        retry_delay = 1
        
        for attempt in range(max_retries):
            try:
                conn = sqlite3.connect(path, check_same_thread=False)
                
                # 优化SQLite性能
                conn.execute('PRAGMA journal_mode=WAL;')
                conn.execute('PRAGMA cache_size=1000000;')
                conn.execute('PRAGMA synchronous=NORMAL;')
                conn.execute('PRAGMA enable_shared_cache=1;')
                conn.execute('PRAGMA busy_timeout=30000;')
                conn.execute('PRAGMA temp_store=MEMORY;')
                conn.execute('PRAGMA mmap_size=268435456;')
                
                logger.debug(f"创建数据库连接: {path}")
                return conn
            except sqlite3.OperationalError as e:
                if "database is locked" in str(e):
                    logger.warning(f"数据库被锁定，尝试重试 ({attempt+1}/{max_retries})...")
                    time.sleep(retry_delay)
                    retry_delay *= 2
                else:
                    logger.error(f"创建数据库连接失败: {e}")
                    raise
            except Exception as e:
                logger.error(f"创建数据库连接失败: {e}")
                raise
        
        logger.error(f"创建数据库连接失败: 经过 {max_retries} 次尝试后仍然无法获取数据库锁")
        raise Exception(f"经过 {max_retries} 次尝试后仍然无法获取数据库锁")
    
    def close_connection(self, key: tuple):
        """
        关闭数据库连接
        
        Args:
            key: 连接标识符
        """
        with self.lock:
            if key in self.connections:
                try:
                    self.connections[key].close()
                    del self.connections[key]
                    logger.debug(f"关闭数据库连接: {key}")
                except Exception as e:
                    logger.error(f"关闭数据库连接失败: {e}")
    
    def close_all_connections(self):
        """
        关闭所有数据库连接
        """
        with self.lock:
            for key, conn in list(self.connections.items()):
                try:
                    conn.close()
                    del self.connections[key]
                except Exception as e:
                    logger.error(f"关闭数据库连接失败: {e}")
        logger.debug("关闭所有数据库连接")
