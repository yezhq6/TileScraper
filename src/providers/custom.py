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
        获取瓦片URL。

        坐标约定：传入的 ``y`` 始终是 **XYZ** 行号（``tile_math`` 不做翻转）。
        是否翻转只取决于服务端约定：

        * ``is_tms=True``——服务端按 TMS 取行，``{y}`` 需要翻转成 ``2^z-1-y``；
        * 模板里的 ``{-y}`` 始终表示"翻转后的行号"（Leaflet 的写法）。
        """
        url = self.url_template
        n = 2 ** zoom
        y_flipped = (n - 1) - y

        # 处理不同类型的占位符
        if "{q}" in url:
            # 需要 QuadKey
            from .bing import BingTileProvider
            quadkey = BingTileProvider.tile_to_quadkey(x, y, zoom)
            url = url.replace("{q}", quadkey)

        # 替换基本占位符
        url = url.replace("{z}", str(zoom))
        url = url.replace("{x}", str(x))

        # {-y} 等价于 TMS 行号，总是翻转；{y} 仅在 is_tms 时翻转
        url = url.replace("{-y}", str(y_flipped))
        url = url.replace("{y}", str(y_flipped if self.is_tms else y))

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
        # 落盘一律使用 XYZ 行号：任务坐标本身就是 XYZ（tile_math 不翻转），
        # is_tms 只影响"向服务端请求哪一行"，不该改变本地目录布局。
        return base_dir / str(zoom) / str(x) / f"{y}.{self.extension}"
