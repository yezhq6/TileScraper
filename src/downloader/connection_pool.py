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
        # 记录每个 key 对应的库文件，避免同 key 复用到别的库的连接
        self.paths: Dict[tuple, Path] = {}
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
        path = Path(path)
        # 全程持锁：旧实现把 `return self.connections[key]` 放在锁外，
        # 与 close_all_connections 并发时可能 KeyError 或拿到正在关闭的连接
        with self.lock:
            existing = self.connections.get(key)
            if existing is not None and self.paths.get(key) == path:
                return existing
            if existing is not None:
                # 同一个 key 指向了另一个库：先关掉旧连接（当前调用方不会走到，
                # 但保留这层校验避免将来复用时串库）
                logger.warning(f"连接池 key={key} 的库路径变化，重建连接")
                self._safe_close(existing)
                self.connections.pop(key, None)
                self.paths.pop(key, None)
            conn = self._create_connection(path)
            self.connections[key] = conn
            self.paths[key] = path
            return conn

    @staticmethod
    def _safe_close(conn):
        if conn is None:
            return
        try:
            conn.close()
        except Exception:  # noqa: BLE001
            pass

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
                    self.paths.pop(key, None)
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
                    self.paths.pop(key, None)
                except Exception as e:
                    logger.error(f"关闭数据库连接失败: {e}")
        logger.debug("关闭所有数据库连接")
