# src/providers.py
# 保持向后兼容性，导入新的模块结构

from .providers.base import TileProvider, TileProviderType
from .providers.osm import OSMTileProvider
from .providers.bing import BingTileProvider
from .providers.custom import CustomTileProvider
from .providers.manager import ProviderManager

__all__ = [
    'TileProvider',
    'TileProviderType',
    'OSMTileProvider',
    'BingTileProvider',
    'CustomTileProvider',
    'ProviderManager'
]


if __name__ == "__main__":
    osm = ProviderManager.get_provider("osm")
    print("OSM 示例 URL:", osm.get_tile_url(13484, 6202, 14))

    bing = ProviderManager.get_provider("bing")
    print("Bing 示例 URL:", bing.get_tile_url(13484, 6202, 14))
