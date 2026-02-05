#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
工具函数模块

提供通用的工具函数
"""

import os
from pathlib import Path


def ensure_directory(path):
    """
    确保目录存在，如果不存在则创建
    
    Args:
        path: 目录路径
    """
    Path(path).mkdir(parents=True, exist_ok=True)


def parse_zoom_levels(zoom_arg):
    """
    解析缩放级别参数
    
    Args:
        zoom_arg: 缩放级别参数，可以是单个值或范围，如 "14" 或 "8-15"
        
    Returns:
        list: 缩放级别列表
    """
    if not zoom_arg:
        return None
    
    zoom_levels = []
    for arg in zoom_arg:
        if '-' in arg:
            # 处理范围，如 8-15
            start, end = arg.split('-')
            try:
                start_zoom = int(start)
                end_zoom = int(end)
                zoom_levels.extend(range(start_zoom, end_zoom + 1))
            except ValueError:
                pass
        else:
            # 处理单个值，如 14
            try:
                zoom_levels.append(int(arg))
            except ValueError:
                pass
    
    # 去重并排序
    return sorted(list(set(zoom_levels)))


def convert_path(path):
    """
    转换路径，支持Windows路径和Linux路径
    
    Args:
        path: 输入的路径，可以是Windows路径（如D:/codes）或Linux路径（如/mnt/d/codes）
        
    Returns:
        str: 转换后的路径
    """
    # 检查是否为Windows路径（包含盘符和反斜杠）
    if len(path) > 1 and path[1] == ':' and ('\\' in path or '/' in path):
        # 转换Windows路径到WSL2路径
        # 将盘符转换为/mnt/[小写盘符]
        drive_letter = path[0].lower()
        # 替换反斜杠为正斜杠
        wsl_path = path[2:].replace('\\', '/')
        # 构建完整的WSL路径
        full_path = f"/mnt/{drive_letter}/{wsl_path.lstrip('/')}"
        return full_path
    else:
        # 直接返回Linux路径
        return path
