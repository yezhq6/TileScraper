# src/providers/osm.py

from pathlib import Path
from typing import Union
from .base import TileProvider, TileProviderType


class OSMTileProvider(TileProvider):
    """
    OpenStreetMap 标准 XYZ 瓦片
    """

    def __init__(self):
        """
        初始化OSM瓦片提供商
        """
        super().__init__(
            name="osm",
            provider_type=TileProviderType.OSM,
            url_template="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png",
            min_zoom=0,
            max_zoom=19,
            subdomains=["a", "b", "c"],
            attribution="© OpenStreetMap contributors",
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
        s = self.subdomains[(x + y) % len(self.subdomains)]
        return self.url_template.format(s=s, z=zoom, x=x, y=y)

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
        return base_dir / "osm" / str(zoom) / str(x) / f"{y}.{self.extension}"
