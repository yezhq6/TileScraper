# src/providers/manager.py

from typing import Dict, List
from .base import TileProvider
from .osm import OSMTileProvider
from .bing import BingTileProvider
from .custom import CustomTileProvider


class ProviderManager:
    """
    简单的 provider 注册 / 获取
    """

    _providers: Dict[str, TileProvider] = {}

    @classmethod
    def register_provider(cls, provider: TileProvider):
        """
        注册瓦片提供商
        
        Args:
            provider: 瓦片提供商实例
        """
        cls._providers[provider.name.lower()] = provider

    @classmethod
    def get_provider(cls, name: str) -> TileProvider:
        """
        获取瓦片提供商
        
        Args:
            name: 提供商名称
            
        Returns:
            TileProvider: 瓦片提供商实例
            
        Raises:
            ValueError: 未知的瓦片提供商
        """
        p = cls._providers.get(name.lower())
        if not p:
            raise ValueError(f"未知瓦片源: {name}")
        return p

    @classmethod
    def list_providers(cls) -> List[str]:
        """
        列出所有已注册的瓦片提供商
        
        Returns:
            List[str]: 瓦片提供商名称列表
        """
        return list(cls._providers.keys())
        
    @classmethod
    def create_custom_provider(
        cls,
        name: str,
        url_template: str,
        subdomains: list = None,
        min_zoom: int = 0,
        max_zoom: int = 23,
    ) -> TileProvider:
        """
        创建并返回一个自定义瓦片提供商
        
        Args:
            name: 提供商名称
            url_template: URL模板
            subdomains: 子域名列表
            min_zoom: 最小缩放级别
            max_zoom: 最大缩放级别
            
        Returns:
            TileProvider: 自定义瓦片提供商实例
        """
        provider = CustomTileProvider(
            name=name,
            url_template=url_template,
            subdomains=subdomains or [],
            min_zoom=min_zoom,
            max_zoom=max_zoom,
        )
        # 注册为临时提供商
        cls.register_provider(provider)
        return provider


# 注册默认 provider
ProviderManager.register_provider(OSMTileProvider())
ProviderManager.register_provider(BingTileProvider())
