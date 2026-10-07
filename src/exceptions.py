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
