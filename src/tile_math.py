# src/tile_math.py
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
        
        # 不再在这里翻转 y，因为在 CustomTileProvider.get_tile_path 方法中已经处理了 y 坐标的顺序
        # if is_tms:
        #     y_tile = (n - 1) - y_tile
        
        # 根据需要选择取整方式
        if use_ceil:
            # 向上取整，使用1e-10避免浮点精度问题
            x_tile = math.ceil(x_tile - 1e-10)
            y_tile = math.ceil(y_tile - 1e-10)
        else:
            # 向下取整
            x_tile = int(x_tile)
            y_tile = int(y_tile)
        
        return int(x_tile), int(y_tile)

    @staticmethod
    def tile_to_latlon(x: int, y: int, zoom: int, is_tms: bool = False):
        """
        瓦片坐标 -> 瓦片左上角经纬度 (lat, lon)
        
        Args:
            x: 瓦片x坐标
            y: 瓦片y坐标
            zoom: 缩放级别
            is_tms: 是否使用 TMS 坐标
            
        Returns:
            Tuple[float, float]: 经纬度 (lat, lon)
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
        
        Args:
            x: 瓦片x坐标
            y: 瓦片y坐标
            zoom: 缩放级别
            is_tms: 是否使用 TMS 坐标
            
        Returns:
            Tuple[float, float, float, float]: 边界框 (west, south, east, north)
        """
        # 左上角
        north, west = TileMath.tile_to_latlon(x, y, zoom, is_tms)
        # 右下角（x+1, y+1）
        south, east = TileMath.tile_to_latlon(x + 1, y + 1, zoom, is_tms)
        return west, south, east, north
        

    @staticmethod
    def calculate_tiles_in_bbox(
        west: float, south: float, east: float, north: float, zoom: int, is_tms: bool = False
    ) -> List[Tuple[int, int]]:
        """
        计算边界框内的瓦片坐标列表
        
        Args:
            west: 西边界经度
            south: 南边界纬度
            east: 东边界经度
            north: 北边界纬度
            zoom: 缩放级别
            is_tms: 是否使用 TMS 坐标
            
        Returns:
            List[Tuple[int, int]]: 瓦片坐标列表
        """
        # 计算当前缩放级别的瓦片总数，瓦片坐标范围是0到n-1
        n = 2 ** zoom
        max_valid_tile = n - 1

        # 1. 计算边界瓦片坐标
        # 统一使用Slippy Map的逻辑计算瓦片范围
        # 左上角使用向下取整：将经纬度转换为最接近的瓦片坐标
        min_x, min_y = TileMath.latlon_to_tile(north, west, zoom, is_tms, use_ceil=False)
        # 右下角使用向上取整：确保包含边界
        max_x, max_y = TileMath.latlon_to_tile(south, east, zoom, is_tms, use_ceil=True)

        # 先纠正一下顺序，保证 min <= max
        if min_x > max_x:
            min_x, max_x = max_x, min_x
        if min_y > max_y:
            min_y, max_y = max_y, min_y

        # 然后确保瓦片坐标在有效范围内 [0, max_valid_tile]
        min_x = max(0, min_x)
        min_y = max(0, min_y)
        max_x = min(max_valid_tile, max_x)
        max_y = min(max_valid_tile, max_y)

        tiles = []

        # 3. 生成所有边界范围内的瓦片
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
        
        Args:
            west: 西边界经度
            south: 南边界纬度
            east: 东边界经度
            north: 北边界纬度
            zoom: 缩放级别
            is_tms: 是否使用 TMS 坐标
            
        Yields:
            Tuple[int, int]: 瓦片坐标
        """
        # 计算当前缩放级别的瓦片总数，瓦片坐标范围是0到n-1
        n = 2 ** zoom
        max_valid_tile = n - 1

        # 1. 计算边界瓦片坐标
        min_x, min_y = TileMath.latlon_to_tile(north, west, zoom, is_tms, use_ceil=False)
        max_x, max_y = TileMath.latlon_to_tile(south, east, zoom, is_tms, use_ceil=True)

        # 先纠正一下顺序，保证 min <= max
        if min_x > max_x:
            min_x, max_x = max_x, min_x
        if min_y > max_y:
            min_y, max_y = max_y, min_y

        # 然后确保瓦片坐标在有效范围内 [0, max_valid_tile]
        min_x = max(0, min_x)
        min_y = max(0, min_y)
        max_x = min(max_valid_tile, max_x)
        max_y = min(max_valid_tile, max_y)

        # 生成所有边界范围内的瓦片
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
        
        Args:
            west: 西边界经度
            south: 南边界纬度
            east: 东边界经度
            north: 北边界纬度
            min_zoom: 最小缩放级别
            max_zoom: 最大缩放级别
            
        Returns:
            Dict[int, List[Tuple[int, int]]]: 各缩放级别的瓦片列表
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
        
        Args:
            tile_bbox: 瓦片边界框 (west, south, east, north)
            search_bbox: 搜索边界框 (west, south, east, north)
            
        Returns:
            bool: 是否相交
        """
        w1, s1, e1, n1 = tile_bbox
        w2, s2, e2, n2 = search_bbox

        # 不相交的四种情形
        if (w1 >= e2) or (e1 <= w2) or (s1 >= n2) or (n1 <= s2):
            return False
        return True
    
    @staticmethod
    def get_tile_center(x: int, y: int, zoom: int, is_tms: bool = False) -> Tuple[float, float]:
        """
        获取瓦片中心点的经纬度
        
        Args:
            x: 瓦片x坐标
            y: 瓦片y坐标
            zoom: 缩放级别
            is_tms: 是否使用 TMS 坐标
            
        Returns:
            Tuple[float, float]: 中心点经纬度 (lat, lon)
        """
        # 获取瓦片边界
        west, south, east, north = TileMath.get_tile_bbox(x, y, zoom, is_tms)
        # 计算中心点
        center_lat = (north + south) / 2
        center_lon = (west + east) / 2
        return center_lat, center_lon
    
    @staticmethod
    def calculate_tile_count(zoom: int) -> int:
        """
        计算指定缩放级别的瓦片总数
        
        Args:
            zoom: 缩放级别
            
        Returns:
            int: 瓦片总数
        """
        return (2 ** zoom) ** 2
    
    @staticmethod
    def validate_bbox(west: float, south: float, east: float, north: float) -> bool:
        """
        验证边界框是否有效
        
        Args:
            west: 西边界经度
            south: 南边界纬度
            east: 东边界经度
            north: 北边界纬度
            
        Returns:
            bool: 是否有效
        """
        # 检查经纬度范围
        if not (-180 <= west <= 180) or not (-180 <= east <= 180):
            return False
        if not (-85.0511 <= south <= 85.0511) or not (-85.0511 <= north <= 85.0511):
            return False
        # 检查边界顺序
        if west >= east or south >= north:
            return False
        return True


if __name__ == "__main__":
    # 简单自测：北京天安门
    lat, lon, zoom = 39.9042, 116.4074, 15
    x, y = TileMath.latlon_to_tile(lat, lon, zoom)
    print(f"({lat}, {lon}) @ z={zoom} -> tile=({x}, {y})")

    west, south, east, north = TileMath.get_tile_bbox(x, y, zoom)
    print("该瓦片 bbox:", west, south, east, north)

    tiles = TileMath.calculate_tiles_in_bbox(116.3, 39.8, 116.5, 40.0, 14)
    print("示例 bbox 内瓦片数量:", len(tiles))
    
    # 测试中心点计算
    center_lat, center_lon = TileMath.get_tile_center(x, y, zoom)
    print(f"瓦片中心点: ({center_lat}, {center_lon})")
    
    # 测试瓦片数量计算
    print(f"缩放级别 {zoom} 的瓦片总数: {TileMath.calculate_tile_count(zoom)}")
