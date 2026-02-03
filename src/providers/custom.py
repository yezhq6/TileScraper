# src/providers/custom.py

from pathlib import Path
from typing import Union
from .base import TileProvider, TileProviderType


class CustomTileProvider(TileProvider):
    """
    自定义瓦片提供商
    """

    def __init__(
        self,
        name: str,
        url_template: str,
        subdomains: list = None,
        min_zoom: int = 0,
        max_zoom: int = 23,
    ):
        """
        初始化自定义瓦片提供商
        
        Args:
            name: 提供商名称
            url_template: URL模板
            subdomains: 子域名列表
            min_zoom: 最小缩放级别
            max_zoom: 最大缩放级别
        """
        super().__init__(
            name=name,
            provider_type=TileProviderType.CUSTOM,
            url_template=url_template,
            min_zoom=min_zoom,
            max_zoom=max_zoom,
            subdomains=subdomains or [],
            attribution="Custom Provider",
        )

    def get_tile_url(self, x: int, y: int, zoom: int) -> str:
        """
        获取瓦片URL
        
        Args:
            x: 瓦片x坐标
            y: 瓦片y坐标
            zoom: 缩放级别
            
        Returns:
            str: 瓦片URL
        """
        # 替换 URL 中的占位符
        url = self.url_template
        
        # 处理不同类型的占位符
        if "{q}" in url:
            # 需要 QuadKey
            from .bing import BingTileProvider
            quadkey = BingTileProvider.tile_to_quadkey(x, y, zoom)
            url = url.replace("{q}", quadkey)
        
        # 替换基本占位符
        url = url.replace("{z}", str(zoom))
        url = url.replace("{x}", str(x))
        
        url = url.replace("{y}", str(y))
        
        # 处理子域名
        if "{s}" in url and self.subdomains:
            s = self.subdomains[(x + y) % len(self.subdomains)]
            url = url.replace("{s}", s)
        
        return url

    def get_tile_path(
        self, x: int, y: int, zoom: int, base_dir: Union[str, Path]
    ) -> Path:
        """
        获取瓦片保存路径
        
        Args:
            x: 瓦片x坐标
            y: 瓦片y坐标
            zoom: 缩放级别
            base_dir: 基础目录
            
        Returns:
            Path: 瓦片保存路径
        """
        base_dir = Path(base_dir)
        # 如果是TMS坐标系，调整y坐标的顺序，使其与QGIS的TMS下载结果一致
        if self.is_tms:
            n = 2 ** zoom
            y = (n - 1) - y
        return base_dir / str(zoom) / str(x) / f"{y}.{self.extension}"  # 使用动态提取的扩展名
