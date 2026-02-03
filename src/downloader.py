# src/downloader.py
# 保持向后兼容性，导入新的模块结构

from .downloader.base import TileDownloader
from .downloader.performance import PerformanceMonitor
from .downloader.batch import BatchDownloader
from .downloader.utils import convert_path

__all__ = [
    'TileDownloader',
    'PerformanceMonitor',
    'BatchDownloader',
    'convert_path'
]