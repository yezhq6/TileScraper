# src/exceptions.py

class TileHarvesterError(Exception):
    """
    TileHarvester 基础异常类
    """
    pass

class DownloadError(TileHarvesterError):
    """
    下载错误
    """
    pass

class MBTilesError(TileHarvesterError):
    """
    MBTiles 操作错误
    """
    pass

class ProgressError(TileHarvesterError):
    """
    进度管理错误
    """
    pass

class ConfigurationError(TileHarvesterError):
    """
    配置错误
    """
    pass

class ProviderError(TileHarvesterError):
    """
    提供商错误
    """
    pass

class ValidationError(TileHarvesterError):
    """
    数据验证错误
    """
    pass
