#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
坐标转换工具模块

提供XYZ和TMS坐标系统之间的转换
"""


class CoordinateConverter:
    """
    坐标转换工具类
    """
    
    @staticmethod
    def xyz_to_tms(zoom, y_xyz):
        """
        将XYZ坐标系统的y坐标转换为TMS坐标系统的y坐标
        
        Args:
            zoom: 缩放级别
            y_xyz: XYZ坐标系统的y坐标
            
        Returns:
            int: TMS坐标系统的y坐标
        """
        return (2 ** zoom - 1) - y_xyz
    
    @staticmethod
    def tms_to_xyz(zoom, y_tms):
        """
        将TMS坐标系统的y坐标转换为XYZ坐标系统的y坐标
        
        Args:
            zoom: 缩放级别
            y_tms: TMS坐标系统的y坐标
            
        Returns:
            int: XYZ坐标系统的y坐标
        """
        return (2 ** zoom - 1) - y_tms
    
    @staticmethod
    def tile_to_latlon(zoom, x, y):
        """
        将瓦片坐标转换为经纬度
        
        Args:
            zoom: 缩放级别
            x: 瓦片X坐标
            y: 瓦片Y坐标（TMS格式）
            
        Returns:
            tuple: (纬度, 经度)
        """
        import math
        
        # 转换为XYZ格式的Y坐标
        y_xyz = (2 ** zoom - 1) - y
        
        # 计算经度
        lon = (x / (2 ** zoom)) * 360 - 180
        
        # 计算纬度
        n = math.pi - 2 * math.pi * y_xyz / (2 ** zoom)
        lat = math.degrees(math.atan(math.sinh(n)))
        
        return lat, lon
    
    @staticmethod
    def convert(zoom, y, from_scheme, to_scheme):
        """
        在不同坐标系统之间转换y坐标
        
        Args:
            zoom: 缩放级别
            y: 原始y坐标
            from_scheme: 原始坐标系统 ('xyz' 或 'tms')
            to_scheme: 目标坐标系统 ('xyz' 或 'tms')
            
        Returns:
            int: 转换后的y坐标
        """
        if from_scheme == to_scheme:
            return y
        
        if to_scheme == 'xyz':
            return CoordinateConverter.tms_to_xyz(zoom, y)
        else:
            return CoordinateConverter.xyz_to_tms(zoom, y)
