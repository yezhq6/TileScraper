# src/providers/bing.py

from pathlib import Path
from typing import Union
from .base import TileProvider, TileProviderType


class BingTileProvider(TileProvider):
    """
    Bing 地图，采用 QuadKey
    模板： https://ecn.{s}.tiles.virtualearth.net/tiles/a{q}.jpeg?g=1
    使用 https：HTTPS 部署时浏览器会拦截 http 瓦片（mixed content）。
    """

    def __init__(self):
        """
        初始化Bing瓦片提供商
        """
        super().__init__(
            name="bing",
            provider_type=TileProviderType.BING,
            url_template="https://ecn.{s}.tiles.virtualearth.net/tiles/a{q}.jpeg?g=1",
            min_zoom=1,
            max_zoom=23,
            subdomains=["t0", "t1", "t2", "t3"],
            attribution="© Microsoft Corporation",
        )

    @staticmethod
    def tile_to_quadkey(x: int, y: int, zoom: int) -> str:
        """
        将瓦片坐标转换为QuadKey
        
        Args:
            x: 瓦片x坐标
            y: 瓦片y坐标
            zoom: 缩放级别
            
        Returns:
            str: QuadKey
        """
        if zoom <= 0:
            # Bing 约定：z=0 的 quadkey 是 "0"（不是空串）
            return "0"
        quadkey = ""
        for i in range(zoom, 0, -1):
            digit = 0
            mask = 1 << (i - 1)
            if x & mask:
                digit += 1
            if y & mask:
                digit += 2
            quadkey += str(digit)
        return quadkey

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
        q = self.tile_to_quadkey(x, y, zoom)
        s = self.subdomains[(x + y) % len(self.subdomains)]
        return self.url_template.format(s=s, q=q)

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
        return base_dir / "bing" / str(zoom) / str(x) / f"{y}.{self.extension}"
