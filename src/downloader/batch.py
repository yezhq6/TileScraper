# src/downloader/batch.py

from typing import Dict
from ..tile_math import TileMath
from .base import TileDownloader


class BatchDownloader:
    """
    批量下载工具：提供高级接口
    """

    @staticmethod
    def download_single_tile(
        provider_name: str,
        lat: float,
        lon: float,
        zoom: int,
        output_dir: str = "tiles",
        is_tms: bool = False,
    ) -> Dict[str, int]:
        """
        下载单个瓦片
        
        Args:
            provider_name: 瓦片提供商名称
            lat: 纬度
            lon: 经度
            zoom: 缩放级别
            output_dir: 输出目录
            is_tms: 是否使用TMS坐标系
            
        Returns:
            Dict[str, int]: 下载统计信息
        """
        x, y = TileMath.latlon_to_tile(lat, lon, zoom, is_tms=is_tms)
        dl = TileDownloader(
            provider_name, output_dir, max_threads=1, is_tms=is_tms
        )
        dl.add_task(x, y, zoom)
        dl.start()
        return dl.get_statistics()

    @staticmethod
    def download_bbox(
        provider_name: str,
        west: float,
        south: float,
        east: float,
        north: float,
        min_zoom: int,
        max_zoom: int,
        output_dir: str = "tiles",
        max_threads: int = 4,
        is_tms: bool = False,
        enable_resume: bool = True,
        batch_size: int = 10000
    ) -> Dict[str, int]:
        """
        下载矩形区域瓦片
        
        Args:
            provider_name: 瓦片提供商名称
            west: 西边界经度
            south: 南边界纬度
            east: 东边界经度
            north: 北边界纬度
            min_zoom: 最小缩放级别
            max_zoom: 最大缩放级别
            output_dir: 输出目录
            max_threads: 最大线程数
            is_tms: 是否使用TMS坐标系
            enable_resume: 是否启用断点续传
            batch_size: 每批处理的任务数量
            
        Returns:
            Dict[str, int]: 下载统计信息
        """
        dl = TileDownloader(
            provider_name, 
            output_dir, 
            max_threads=max_threads, 
            is_tms=is_tms,
            enable_resume=enable_resume
        )
        dl.add_tasks_for_bbox(west, south, east, north, min_zoom, max_zoom, batch_size=batch_size)
        dl.start()
        return dl.get_statistics()
