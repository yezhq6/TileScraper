# src/downloader/transaction.py

import time
from loguru import logger
from typing import Dict, Optional

class TransactionManager:
    """
    事务管理器，负责处理MBTiles数据库的事务管理
    """
    
    def __init__(self):
        """
        初始化事务管理器
        """
        self.transaction_counter = 0
        self.batch_size = 100
    
    def should_commit(self) -> bool:
        """
        判断是否应该提交事务
        
        Returns:
            bool: 是否应该提交事务
        """
        self.transaction_counter += 1
        return self.transaction_counter % self.batch_size == 0
    
    def commit(self, conn, zoom: Optional[int] = None):
        """
        提交事务
        
        Args:
            conn: 数据库连接
            zoom: 缩放级别（用于日志）
        """
        try:
            conn.commit()
            if zoom is not None:
                logger.debug(f"提交缩放级别 {zoom} 的MBTiles事务")
            else:
                logger.debug("提交MBTiles事务")
        except Exception as e:
            logger.error(f"提交MBTiles事务失败: {e}")
    
    def reset(self):
        """
        重置事务计数器
        """
        self.transaction_counter = 0
        logger.debug("重置事务计数器")
