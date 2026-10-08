# src/exceptions.py


class TileScraperError(Exception):
    """
    TileScraper 基础异常类
    """
    pass


class DownloadError(TileScraperError):
    """
    下载错误
    """
    pass


class MBTilesError(TileScraperError):
    """
    MBTiles 操作错误
    """
    pass


class ProgressError(TileScraperError):
    """
    进度管理错误
    """
    pass


class ConfigurationError(TileScraperError):
    """
    配置错误
    """
    pass


class ProviderError(TileScraperError):
    """
    提供商错误
    """
    pass


class ValidationError(TileScraperError):
    """
    数据验证错误
    """
    pass


class OutputLockedError(TileScraperError):
    """
    输出目录已被另一个下载任务占用

    两个进程同时写同一个 ``progress.db`` / ``.mbtiles`` 会互相抢锁甚至丢数据，
    因此在构造下载器时会先取输出目录锁，拿不到就抛这个异常。
    """
    pass
