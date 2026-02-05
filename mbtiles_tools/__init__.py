#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
mbtiles_tools包

功能特性：
1. 目录结构转换为MBTiles（支持jpg、png、jpeg格式）
2. MBTiles转换为目录结构
3. MBTiles合并功能
4. MBTiles按zoom拆分提取功能
5. 支持XYZ和TMS坐标系统转换
6. 支持多线程处理
7. 自动检测和处理坐标系统差异

版本：1.0
"""

from mbtiles_tools.core import MBTilesConverter, CoordinateConverter, parse_zoom_levels
from .cli import main

__all__ = ['MBTilesConverter', 'CoordinateConverter', 'parse_zoom_levels', 'main']
__version__ = '1.0'
