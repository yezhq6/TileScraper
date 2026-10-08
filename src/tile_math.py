# src/tile_math.py
"""瓦片坐标计算工具（Web Mercator / XYZ / TMS）。"""
import math
from typing import Tuple, List, Dict, Generator


class TileMath:
    """
    瓦片坐标计算工具类（Web Mercator / XYZ）
    """

    @staticmethod
    def latlon_to_tile(lat: float, lon: float, zoom: int, is_tms: bool = False, use_ceil: bool = False):
        """
        经纬度 -> 瓦片坐标 (x, y)

        Args:
            lat: 纬度
            lon: 经度
            zoom: 缩放级别
            is_tms: 是否使用 TMS 坐标
            use_ceil: 是否对结果向上取整（用于边界计算）

        Returns:
            Tuple[int, int]: 瓦片坐标 (x, y)
        """
        # 限制纬度避免溢出
        lat = max(min(lat, 85.0511), -85.0511)

        n = 2 ** zoom
        x_tile = (lon + 180.0) / 360.0 * n

        lat_rad = math.radians(lat)
        y_tile = (
            1.0 - math.log(math.tan(lat_rad) + 1.0 / math.cos(lat_rad)) / math.pi
        ) / 2.0 * n

        # 不再在这里翻转 y，翻转在具体 provider 的 get_tile_path 中处理
        if use_ceil:
            # 向上取整，使用 1e-10 避免浮点精度问题
            x_tile = math.ceil(x_tile - 1e-10)
            y_tile = math.ceil(y_tile - 1e-10)
        else:
            x_tile = int(x_tile)
            y_tile = int(y_tile)

        return int(x_tile), int(y_tile)

    @staticmethod
    def tile_to_latlon(x: int, y: int, zoom: int, is_tms: bool = False):
        """
        瓦片坐标 -> 瓦片左上角经纬度 (lat, lon)
        """
        n = 2 ** zoom

        # 如果输入是 TMS 坐标，先翻回 Slippy Map
        if is_tms:
            y = (n - 1) - y

        lon = x / n * 360.0 - 180.0
        lat_rad = math.atan(math.sinh(math.pi * (1 - 2 * y / n)))
        lat = math.degrees(lat_rad)

        return lat, lon

    @staticmethod
    def get_tile_bbox(x: int, y: int, zoom: int, is_tms: bool = False):
        """
        获取单个瓦片的地理范围 (west, south, east, north)
        """
        # 左上角
        north, west = TileMath.tile_to_latlon(x, y, zoom, is_tms)
        # 右下角（x+1, y+1）
        south, east = TileMath.tile_to_latlon(x + 1, y + 1, zoom, is_tms)
        return west, south, east, north

    @staticmethod
    def bbox_tile_bounds(
        west: float, south: float, east: float, north: float, zoom: int, is_tms: bool = False
    ) -> Tuple[int, int, int, int]:
        """
        计算边界框在指定缩放级别下覆盖的瓦片索引范围 (min_x, min_y, max_x, max_y)。

        返回的坐标已经过排序并裁剪到 [0, 2**zoom - 1] 的有效范围内。
        该方法是纯计算，不会构造任何瓦片列表，可用于超大范围的瓦片计数。
        """
        n = 2 ** zoom
        max_valid_tile = n - 1

        # 左上角向下取整，右下角向上取整，确保完全覆盖边界
        min_x, min_y = TileMath.latlon_to_tile(north, west, zoom, is_tms, use_ceil=False)
        max_x, max_y = TileMath.latlon_to_tile(south, east, zoom, is_tms, use_ceil=True)

        if min_x > max_x:
            min_x, max_x = max_x, min_x
        if min_y > max_y:
            min_y, max_y = max_y, min_y

        min_x = max(0, min_x)
        min_y = max(0, min_y)
        max_x = min(max_valid_tile, max_x)
        max_y = min(max_valid_tile, max_y)

        return min_x, min_y, max_x, max_y

    @staticmethod
    def count_tiles_in_bbox(
        west: float, south: float, east: float, north: float, zoom: int, is_tms: bool = False
    ) -> int:
        """
        计算边界框在指定缩放级别下覆盖的瓦片数量（不构造列表，O(1) 内存）。
        """
        min_x, min_y, max_x, max_y = TileMath.bbox_tile_bounds(
            west, south, east, north, zoom, is_tms
        )
        if max_x < min_x or max_y < min_y:
            return 0
        return (max_x - min_x + 1) * (max_y - min_y + 1)

    @staticmethod
    def calculate_tiles_in_bbox(
        west: float, south: float, east: float, north: float, zoom: int, is_tms: bool = False
    ) -> List[Tuple[int, int]]:
        """
        计算边界框内的瓦片坐标列表

        注意：对于大范围/高缩放级别，请优先使用
        ``calculate_tiles_in_bbox_generator`` 或 ``count_tiles_in_bbox``，
        以避免一次性把所有坐标读入内存。
        """
        min_x, min_y, max_x, max_y = TileMath.bbox_tile_bounds(
            west, south, east, north, zoom, is_tms
        )
        tiles = []
        for x in range(min_x, max_x + 1):
            for y in range(min_y, max_y + 1):
                tiles.append((x, y))
        return tiles

    @staticmethod
    def calculate_tiles_in_bbox_generator(
        west: float, south: float, east: float, north: float, zoom: int, is_tms: bool = False
    ) -> Generator[Tuple[int, int], None, None]:
        """
        计算边界框内的瓦片坐标生成器（内存优化版本）

        Yields:
            Tuple[int, int]: 瓦片坐标
        """
        min_x, min_y, max_x, max_y = TileMath.bbox_tile_bounds(
            west, south, east, north, zoom, is_tms
        )
        for x in range(min_x, max_x + 1):
            for y in range(min_y, max_y + 1):
                yield (x, y)

    @staticmethod
    def calculate_zoom_range_tiles(
        west: float,
        south: float,
        east: float,
        north: float,
        min_zoom: int,
        max_zoom: int,
    ) -> Dict[int, List[Tuple[int, int]]]:
        """
        多个 zoom 级别的瓦片集合
        """
        zoom_tiles = {}
        for z in range(min_zoom, max_zoom + 1):
            zoom_tiles[z] = TileMath.calculate_tiles_in_bbox(
                west, south, east, north, z
            )
        return zoom_tiles

    @staticmethod
    def is_bbox_intersect(tile_bbox, search_bbox):
        """
        检查两个边界框是否相交
        """
        w1, s1, e1, n1 = tile_bbox
        w2, s2, e2, n2 = search_bbox
        if (w1 >= e2) or (e1 <= w2) or (s1 >= n2) or (n1 <= s2):
            return False
        return True

    @staticmethod
    def get_tile_center(x: int, y: int, zoom: int, is_tms: bool = False) -> Tuple[float, float]:
        """
        获取瓦片中心点的经纬度
        """
        west, south, east, north = TileMath.get_tile_bbox(x, y, zoom, is_tms)
        center_lat = (north + south) / 2
        center_lon = (west + east) / 2
        return center_lat, center_lon

    @staticmethod
    def calculate_tile_count(zoom: int) -> int:
        """
        计算指定缩放级别的瓦片总数
        """
        return (2 ** zoom) ** 2

