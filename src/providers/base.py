# src/providers/base.py

from enum import Enum
from pathlib import Path
from typing import Union


class TileProviderType(Enum):
    """
    瓦片提供商类型枚举
    """
    OSM = "osm"
    BING = "bing"
    CUSTOM = "custom"


class TileProvider:
    """
    抽象基类，具体的 OSM / Bing 等继承它
    """

    def __init__(
        self,
        name: str,
        provider_type: TileProviderType,
        url_template: str,
        min_zoom: int,
        max_zoom: int,
        subdomains: list,
        attribution: str = "",
        tile_format: str = None,
    ):
        """
        初始化瓦片提供商
        
        Args:
            name: 提供商名称
            provider_type: 提供商类型
            url_template: URL模板
            min_zoom: 最小缩放级别
            max_zoom: 最大缩放级别
            subdomains: 子域名列表
            attribution: 版权信息
            tile_format: 瓦片格式
        """
        self.name = name
        self.provider_type = provider_type
        self.url_template = url_template
        self.min_zoom = min_zoom
        self.max_zoom = max_zoom
        self.subdomains = subdomains or []
        self.attribution = attribution
        # 使用指定的格式或从URL模板提取扩展名
        self.extension = self._extract_extension(tile_format)
        # TMS坐标系标志
        self.is_tms = False
    
    def set_tile_format(self, tile_format: str):
        """
        设置瓦片格式
        
        Args:
            tile_format: 瓦片格式，如jpeg, jpg, png
        """
        self.extension = tile_format.lower()

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
        raise NotImplementedError

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
        raise NotImplementedError

    def _extract_extension(self, tile_format: str = None) -> str:
        """
        从URL模板中提取瓦片文件扩展名，或使用指定的格式
        
        Args:
            tile_format: 瓦片格式，如jpeg, jpg, png
            
        Returns:
            str: 提取的扩展名（不带点），默认为jpg
        """
        # 如果指定了格式，直接使用
        if tile_format:
            ext = tile_format.lower()
            # 标准化扩展名：jpg和jpeg视为相同，统一为jpg
            if ext == 'jpeg':
                return 'jpg'
            return ext
            
        # 否则从URL模板提取
        import re
        # 查找URL中的文件扩展名模式
        match = re.search(r'\.([a-zA-Z0-9]+)(?:\?|$)', self.url_template)
        if match:
            # 转换为小写
            ext = match.group(1).lower()
            # 标准化扩展名：jpg和jpeg视为相同，统一为jpg
            if ext == 'jpeg':
                return 'jpg'
            return ext
        # 默认使用jpg
        return 'jpg'
