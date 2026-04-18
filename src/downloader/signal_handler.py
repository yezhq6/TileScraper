# src/downloader/signal_handler.py

import signal
import sys
import threading
from loguru import logger


class SignalHandler:
    """
    信号处理器：负责处理系统信号
    """

    def __init__(self, downloader):
        """
        初始化信号处理器
        
        Args:
            downloader: TileDownloader 实例
        """
        self.downloader = downloader
        
        # 注册信号处理
        if threading.current_thread() == threading.main_thread():
            signal.signal(signal.SIGINT, self._signal_handler)
            signal.signal(signal.SIGTERM, self._signal_handler)
            logger.debug("信号处理器已初始化")

    def _signal_handler(self, signum, frame):
        """
        信号处理函数
        
        Args:
            signum: 信号编号
            frame: 帧对象
        """
        logger.info(f"收到信号 {signum}，正在停止下载...")
        self.downloader.stop()
        sys.exit(0)
