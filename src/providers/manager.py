# src/providers/manager.py

import threading
from typing import Dict, List, Optional
from .base import TileProvider
from .bing import BingTileProvider
from .custom import CustomTileProvider


class ProviderManager:
    """
    瓦片提供商管理器：负责注册、获取和管理瓦片提供商

    注意：内部表是进程级共享状态，因此所有读写都加锁。内置源的名字不允许被
    自定义源覆盖（否则一个带 ``provider_name="bing"`` 的下载请求就能永久劫持
    内置 Bing）。调用方若需要并发使用不同 URL，应直接拿
    :meth:`create_custom_provider` 返回的实例，而不是先注册再按名字取。
    """

    _providers: Dict[str, TileProvider] = {}
    _builtin_names = set()
    _lock = threading.RLock()

    @classmethod
    def register_provider(cls, provider: TileProvider, is_builtin: bool = False):
        """
        注册瓦片提供商

        Args:
            provider: 瓦片提供商实例
            is_builtin: 是否内置源（内置名不允许被自定义源覆盖）
        """
        with cls._lock:
            key = provider.name.lower()
            if is_builtin:
                cls._builtin_names.add(key)
            cls._providers[key] = provider

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
        with cls._lock:
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
        with cls._lock:
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

        Raises:
            ValueError: 名称与内置瓦片源冲突
        """
        key = (name or "custom").strip().lower() or "custom"
        with cls._lock:
            if key in cls._builtin_names:
                raise ValueError(
                    f"不允许用自定义源覆盖内置瓦片源: {name}；请换一个 provider_name"
                )
        provider = CustomTileProvider(
            name=key,
            url_template=url_template,
            subdomains=subdomains or [],
            min_zoom=min_zoom,
            max_zoom=max_zoom,
        )
        # 注册为临时提供商
        cls.register_provider(provider)
        return provider
    
    @classmethod
    def provider_exists(cls, name: str) -> bool:
        """
        检查提供商是否存在
        
        Args:
            name: 提供商名称
            
        Returns:
            bool: 是否存在
        """
        with cls._lock:
            return name.lower() in cls._providers
    
    @classmethod
    def get_provider_info(cls, name: str) -> Optional[dict]:
        """
        获取提供商信息
        
        Args:
            name: 提供商名称
            
        Returns:
            Optional[dict]: 提供商信息，如果不存在返回None
        """
        with cls._lock:
            provider = cls._providers.get(name.lower())
        if provider:
            return provider.get_info()
        return None
    
    @classmethod
    def get_all_providers_info(cls) -> List[dict]:
        """
        获取所有提供商信息
        
        Returns:
            List[dict]: 所有提供商信息列表
        """
        with cls._lock:
            providers = list(cls._providers.values())
        return [provider.get_info() for provider in providers]


# 注册默认 provider
ProviderManager.register_provider(BingTileProvider(), is_builtin=True)
