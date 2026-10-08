# src/downloader/signal_handler.py

import signal
import threading
from loguru import logger


class SignalHandler:
    """
    信号处理器：负责把系统信号转成"优雅停止"请求。

    关键点：处理函数**只置位 stop_event**，绝不调用 ``sys.exit()``。
    旧实现里 ``sys.exit(0)`` 会在信号处理函数里直接抛出 ``SystemExit``，
    绕过 ``start()`` 的正常收尾路径：进度可能没保存、MBTiles 事务可能没提交，
    而工作线程（daemon）还在跑。现在改为请求停止后由 ``start()`` 自然返回，
    在那里完成进度保存与 MBTiles 提交。

    另外，``signal.signal`` 只能在主线程注册；Web 场景下下载器是在
    ``DownloadSession`` 线程里构造的，因此这里不注册。Web 的优雅退出由
    ``app.py`` 在启动服务器前于主线程注册（见 ``_install_graceful_shutdown``）。
    """

    def __init__(self, downloader):
        """
        初始化信号处理器

        Args:
            downloader: TileDownloader 实例
        """
        self.downloader = downloader

        # 注册信号处理（仅主线程可行）
        if threading.current_thread() == threading.main_thread():
            self._register()
            logger.debug("信号处理器已初始化")

    def _register(self):
        """注册 SIGINT / SIGTERM 处理函数；重复注册时覆盖为最新的下载器。"""
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                signal.signal(sig, self._signal_handler)
            except (ValueError, OSError) as e:  # noqa: BLE001
                logger.warning(f"注册信号 {sig} 处理失败: {e}")

    def _signal_handler(self, signum, frame):
        """
        信号处理函数：请求优雅停止。

        Args:
            signum: 信号编号
            frame: 帧对象
        """
        logger.info(
            f"收到信号 {signum}，正在停止下载（当前瓦片完成后退出并保存进度）..."
        )
        self.downloader.stop()
        # 释放仍在暂停等待的工作线程，让它们看到 stop_event
        self.downloader.pause_event.set()
